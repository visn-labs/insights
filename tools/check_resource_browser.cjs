// Real Rust/worker UI with a held test lease and an explicit recovery marker.
// The actor owns no weights; this checks admission/status, not model accuracy.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {createHash}=require('node:crypto');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=path.resolve(process.env.VISN_RESOURCE_BROWSER_OUTPUT||'output/playwright/package5-resources');
 fs.mkdirSync(out,{recursive:false});fs.cpSync(path.join(root,'data/live-evaluation-2026-10-16/memory'),path.join(out,'data/memory'),{recursive:true});
 const codeFiles=['static/app.js','tools/workflow_resources.py','tools/semantic_memory_server.py','tools/check_resource_browser.cjs','target/debug/visn-phase0'];
 const hashes=()=>Object.fromEntries(codeFiles.map(name=>[name,createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex')]));const code=hashes();fs.writeFileSync(path.join(out,'runtime-code.json'),JSON.stringify(code,null,2));
 const resources=path.join(out,'resources'),processes=[],logs=[];fs.mkdirSync(resources);
 const environment={...process.env,VISN_RESOURCE_DIR:resources,VISN_MANAGED_LMSTUDIO:'false',HF_HUB_OFFLINE:'1',HF_HOME:path.join(root,'.retrieval/huggingface')};
 function launch(command,args,name,extra={}){const fd=fs.openSync(path.join(out,name+'.log'),'w');logs.push(fd);const child=spawn(command,args,{cwd:root,env:{...environment,...extra},stdio:['pipe',fd,fd]});processes.push(child);return child;}
 async function ready(url,child){for(let i=0;i<200;i++){if(child.exitCode!==null)throw Error('Service exited; inspect logs');try{if((await fetch(url)).ok)return;}catch{}await new Promise(r=>setTimeout(r,50));}throw Error('Service startup timed out');}
 let browser;
 try{
  const worker=launch(path.join(root,'.venv-retrieval/bin/python'),['tools/semantic_memory_server.py','--data-dir',path.join(out,'data'),'--port','8241'],'worker',{VISN_RESOURCE_WAIT_SECS:'.2'});
  await ready('http://127.0.0.1:8241/health',worker);
  const app=launch(path.join(root,'target/debug/visn-phase0'),[],'application',{VISN_BIND:'127.0.0.1:8240',VISN_DATA_DIR:path.join(out,'data'),VISN_RETRIEVAL_URL:'http://127.0.0.1:8241',VISN_RESOURCE_WAIT_SECS:'2'});
  await ready('http://127.0.0.1:8240/healthz',app);
  const actor=spawn(path.join(root,'.venv-retrieval/bin/python'),['-u','-c',"from workflow_resources import Residency;import sys;r=Residency('detector');r.acquire();print('READY',flush=True);sys.stdin.readline();r.release(lambda:None)"],{cwd:root,env:{...environment,PYTHONPATH:path.join(root,'tools')},stdio:['pipe','pipe','pipe']});processes.push(actor);
  await new Promise((resolve,reject)=>{actor.stdout.once('data',data=>data.toString().includes('READY')?resolve():reject(Error('Lease actor failed')));actor.once('error',reject);actor.once('exit',code=>{if(code)reject(Error('Lease actor exited'));});});
  browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8240/#/workspace');await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length>=3);
  await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-5007781d97c7');
  await page.waitForFunction(()=>document.querySelector('#memory-query-queue').textContent.includes('Shared resources are busy'));
  await page.locator('#memory-query-text').fill('activity');
  const response=page.waitForResponse(r=>r.url().endsWith('/memory-query')&&r.request().method()==='POST');
  await page.locator('#memory-query-button').click();const result=await (await response).json();
  assert.equal(result.retrieval_mode,'local_lexical_metadata');assert.ok(result.matches.length>0);
  assert.ok(result.retrieval_notes.some(note=>/unavailable|503/i.test(note)));assert.match(await page.locator('#memory-query-mode').innerText(),/Keyword only/);
  fs.writeFileSync(path.join(out,'busy-query.json'),JSON.stringify(result,null,2));
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.locator('#memory-query-queue').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`busy-${width}.png`)});}
  const exited=new Promise(resolve=>actor.once('exit',resolve));actor.stdin.end('\n');await exited;
  await page.reload();await page.waitForFunction(()=>document.querySelector('#memory-query-queue').textContent.includes('Model slot available'));
  fs.writeFileSync(path.join(resources,'external.json'),JSON.stringify({endpoint:'http://127.0.0.1:1/api/v1',uncertain:true}));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#memory-query-queue').textContent.includes('model-service recovery'));
  await page.locator('#memory-query-queue').scrollIntoViewIfNeeded();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const toggles=await page.locator('#memory-query-form .toggle-row').evaluateAll(items=>items.map(item=>({text:item.innerText,display:getComputedStyle(item).display,visibility:getComputedStyle(item).visibility,opacity:getComputedStyle(item).opacity})));
  assert.ok(toggles.every(item=>item.text&&item.display!=='none'&&item.visibility==='visible'&&item.opacity==='1'));
  fs.writeFileSync(path.join(out,'recovery-controls.json'),JSON.stringify(toggles,null,2));
  await page.screenshot({path:path.join(out,'recovery.png')});
  fs.unlinkSync(path.join(resources,'external.json')); // Test marker only; no external server was used.
  assert.deepEqual(errors,[]);assert.deepEqual(hashes(),code);const summary={passed:true,actualRustWorker:true,testLeaseActor:true,realModelInference:false,busyFallbackDisclosed:true,releasedStatus:true,recoveryStatus:true,responsiveWidths:[320,768,1440],errors};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(summary,null,2));console.log(summary);
 }finally{
  if(browser)await browser.close();
  for(const child of processes.reverse()){if(child.exitCode===null){child.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{child.kill('SIGKILL');resolve();},3000);child.once('exit',()=>{clearTimeout(timer);resolve();});});}}
  for(const fd of logs)fs.closeSync(fd);
 }
})().catch(error=>{console.error(error);process.exitCode=1;});

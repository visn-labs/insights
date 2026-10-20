// Real application/worker flow on isolated retained evidence; no mocked responses.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const out=process.env.VISN_INDEX_BROWSER_OUTPUT||'output/playwright/background-indexing';fs.mkdirSync(out,{recursive:false});
 const base=process.env.VISN_TEST_URL||'http://127.0.0.1:8220';
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#/workspace');await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length>=3);
  await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-4ff132722114');
  await page.waitForFunction(()=>!document.querySelector('#visual-index-start').disabled);
  await page.locator('#visual-index-start').focus();await page.keyboard.press('Enter');
  await page.locator('#visual-index-cancel').waitFor({state:'visible'});await page.locator('#visual-index-cancel').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#visual-index-status').textContent.startsWith('Cancelled'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length>=3);
  await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-4ff132722114');
  await page.locator('#visual-index-retry').waitFor({state:'visible'});await page.locator('#visual-index-retry').click();
  await page.waitForFunction(()=>document.querySelector('#visual-index-status').textContent.startsWith('Completed'),{},{timeout:900000});
  assert.match(await page.locator('#visual-index-status').innerText(),/4\/4 intervals prepared/);
  await page.locator('#memory-query-text').fill('vehicles on the road');
  const response=page.waitForResponse(r=>r.url().endsWith('/memory-query')&&r.request().method()==='POST',{timeout:900000});
  await page.locator('#memory-query-button').click();const result=await (await response).json();
  assert.equal(result.retrieval_mode,'hybrid_qwen_reranker');assert.ok(result.matches.length>0);
  assert.match(result.retrieval_notes[0],/4 cached embeddings/);fs.writeFileSync(out+'/query.json',JSON.stringify(result,null,2));
  const video=page.locator('#memory-camera-results video').first();
  await video.evaluate(async element=>{element.preload='metadata';element.load();await new Promise((resolve,reject)=>{if(element.readyState>=1)return resolve();element.addEventListener('loadedmetadata',resolve,{once:true});element.addEventListener('error',()=>reject(new Error('Evidence video could not load')),{once:true});});});
  assert.ok(await video.evaluate(element=>Number.isFinite(element.duration)&&element.duration>0));
  // The integration runner adds an explicit negative interval fixture to its
  // isolated manifests. Real worker decoding must expose incomplete coverage.
  await page.locator('#memory-job-select').selectOption('d4ce756e-b174-57f2-b85d-95acb3b0649f');
  await page.waitForFunction(()=>!document.querySelector('#visual-index-start').disabled);await page.locator('#visual-index-start').click();
  await page.waitForFunction(()=>document.querySelector('#visual-index-status').textContent.startsWith('Failed'),{},{timeout:900000});
  assert.match(await page.locator('#visual-index-status').innerText(),/1\/2 intervals prepared · 1 failed/);
  assert.ok(await page.locator('#visual-index-retry').isVisible());
  await page.locator('.visual-index-panel').scrollIntoViewIfNeeded();
  await page.screenshot({path:out+'/partial-failure.png'});
  await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-4ff132722114');
  await page.waitForFunction(()=>document.querySelector('#visual-index-status').textContent.startsWith('Completed'));
  for(const width of [320,768,1024,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
  await page.locator('.visual-index-panel').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/desktop.png'});
  await page.setViewportSize({width:320,height:1000});await page.locator('.visual-index-panel').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/mobile.png'});
  assert.deepEqual(errors,[]);const summary={passed:true,realApi:true,cancelReloadResume:true,keyboardActions:true,preparedIntervals:4,partialFailure:{syntheticInvalidInterval:true,completed:1,total:2,failed:1},visualRetrieval:true,evidenceMetadataLoaded:true,responsiveWidths:[320,768,1024,1440],errors};
  fs.writeFileSync(out+'/results.json',JSON.stringify(summary,null,2));console.log(summary);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

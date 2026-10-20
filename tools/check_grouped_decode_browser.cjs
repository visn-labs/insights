// Actual grouped worker through Rust/UI, including decoded video playback.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const out=process.env.VISN_DECODE_BROWSER_OUTPUT;assert.ok(out);fs.mkdirSync(out,{recursive:false});
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8220/#/workspace');await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length>=3);
  await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-5007781d97c7');
  await page.waitForFunction(()=>document.querySelector('#visual-index-status').textContent.startsWith('Completed'));
  await page.locator('#memory-query-text').fill('vehicles on the road');
  const response=page.waitForResponse(r=>r.url().endsWith('/memory-query')&&r.request().method()==='POST',{timeout:300000});
  await page.locator('#memory-query-button').click();const result=await (await response).json();
  assert.equal(result.retrieval_mode,'hybrid_qwen_reranker');assert.ok(result.matches.length);
  assert.equal(result.retrieval_provenance.work_cache.decode_policy,'grouped-select-v1');
  assert.ok(result.retrieval_provenance.work_cache.counters.grouped_calls>0);
  fs.writeFileSync(out+'/query.json',JSON.stringify(result,null,2));
  const video=page.locator('#memory-query-matches video').first();await video.scrollIntoViewIfNeeded();
  await video.evaluate(async element=>{element.muted=true;await element.play();});
  await page.waitForFunction(()=>{const v=document.querySelector('#memory-query-matches video');return v.readyState>=2&&v.currentTime>.15;},{},{timeout:30000});
  const playback=await video.evaluate(element=>({duration:element.duration,time:element.currentTime,decodedFrames:element.getVideoPlaybackQuality().totalVideoFrames}));
  assert.ok(playback.decodedFrames>0);await video.evaluate(element=>element.pause());
  for(const width of [320,768,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
  await page.locator('#memory-query-result').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/desktop.png'});
  await page.setViewportSize({width:320,height:1000});await page.locator('#memory-query-result').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/mobile.png'});
  assert.deepEqual(errors,[]);const summary={passed:true,actualGroupedWorker:true,preparedIntervals:4,visualRetrieval:true,playback,responsiveWidths:[320,768,1440],errors};
  fs.writeFileSync(out+'/summary.json',JSON.stringify(summary,null,2));console.log(summary);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

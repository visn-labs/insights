// Real worker + saved highway recording. Run after the direction probes (worker is serialized).
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.VISN_TEST_URL||'http://127.0.0.1:8197';
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/#/workspace');
 await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length>1);
 await page.locator('#memory-job-select').selectOption(process.env.VISN_TEST_JOB||'01a138f2-0df1-72b0-b49d-88451f598886');
 assert.equal(await page.locator('#memory-query-temporal').isChecked(),false);
 await page.locator('#memory-query-text').fill('Are vehicles approaching or moving away from the camera?');
 await page.locator('#memory-query-temporal').check();
 assert.equal(await page.locator('#memory-query-vlm').isDisabled(),true);
 const pending=page.waitForResponse(r=>r.url().endsWith('/api/v1/memory-query')&&r.request().method()==='POST',{timeout:600000});
 await page.locator('#memory-query-button').click();const response=await pending;assert.equal(response.status(),200);const result=await response.json();
 assert.equal(response.request().postDataJSON().temporal_enabled,true);
 assert.equal(result.retrieval_mode,'hybrid_qwen_reranker');assert.equal(result.interpretations.length,3);
 for(const value of result.interpretations){assert.equal(value.frames.length,8);assert.equal(value.sampled_timestamps_ms.length,8);assert.ok(value.observations.every(o=>o.timestamp_ms===value.sampled_timestamps_ms[o.frame_index]));}
 await page.locator('#memory-query-matches .sequence-evidence').first().waitFor();
 const evidence=page.locator('#memory-query-matches .sequence-evidence').first();await evidence.locator('summary').click();
 await evidence.scrollIntoViewIfNeeded();await page.waitForFunction(()=>[...document.querySelectorAll('#memory-query-matches .sequence-frames img')].slice(0,8).every(im=>im.complete&&im.naturalWidth>0));
 fs.mkdirSync('output/playwright/experiment2',{recursive:true});fs.writeFileSync('output/playwright/experiment2/highway-query.json',JSON.stringify(result,null,2));
 await page.screenshot({path:'output/playwright/experiment2/evidence.png'});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:'output/playwright/experiment2/mobile.png'});
 await page.locator('#memory-query-temporal').uncheck();assert.equal(await page.locator('#memory-query-vlm').isDisabled(),false);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({mode:result.retrieval_mode,interpreted:result.interpretations.length,answers:result.interpretations.map(i=>({status:i.status,answer:i.answer})),browserErrors:errors}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

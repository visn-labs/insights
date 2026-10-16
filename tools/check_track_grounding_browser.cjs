// Real UI submission with an explicit entity; no mocked model responses.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.VISN_TEST_URL||'http://127.0.0.1:8192',out=process.env.VISN_GROUNDING_OUTPUT||'output/playwright/track-grounding-v8';fs.mkdirSync(out,{recursive:true});
 await page.goto(base+'/#/workspace');await page.waitForFunction(()=>document.querySelector('#memory-job-select').options.length===3);
 await page.locator('#memory-job-select').selectOption('01a1433a-26bf-7e50-b482-5007781d97c7');
 await page.waitForFunction(()=>[...document.querySelector('#memory-query-entity').options].some(o=>o.value==='ba5d72eb-c513-5b74-b634-aa70dc0e14f9'));
 await page.locator('#memory-query-entity').selectOption('ba5d72eb-c513-5b74-b634-aa70dc0e14f9');await page.locator('#memory-query-temporal').check();
 assert.ok(await page.locator('#memory-query-long').isChecked());assert.ok(await page.locator('#memory-query-temporal').isChecked());
 await page.locator('#memory-query-text').fill('Is the selected car moving toward or away from the camera?');
 const pending=page.waitForResponse(r=>r.url().endsWith('/memory-query')&&r.request().method()==='POST',{timeout:900000});
 await page.locator('#memory-query-button').click();const response=await pending;assert.equal(response.status(),200);const request=response.request().postDataJSON();assert.equal(request.entity_id,'ba5d72eb-c513-5b74-b634-aa70dc0e14f9');assert.equal(request.temporal_enabled,true);
 const result=await response.json();fs.writeFileSync(out+'/api-response.json',JSON.stringify(result,null,2));assert.ok(result.interpretations.length>0,JSON.stringify(result.interpretation_notes));
 assert.equal(result.long_memory.entities.length,1);assert.ok(result.interpretations.every(i=>i.provenance.track_grounding.entity_id===request.entity_id));
 for(const value of result.interpretations){if(['blind-state-consistency-v9','track-visibility-quality-v10'].includes(value.provenance.prompt_version)){assert.ok(value.provenance.state_verification);if(value.status==='supported')assert.equal(value.provenance.state_verification.status,'passed');}}
 if(process.env.VISN_REPEAT_QUERY_CHECK==='1'){
  await page.waitForFunction(()=>!document.querySelector('#memory-query-button').disabled);
  const again=page.waitForResponse(r=>r.url().endsWith('/memory-query')&&r.request().method()==='POST',{timeout:900000});await page.locator('#memory-query-button').click();
  const repeatedResponse=await again;assert.equal(repeatedResponse.status(),200);const repeated=await repeatedResponse.json();fs.writeFileSync(out+'/api-response-repeat.json',JSON.stringify(repeated,null,2));
  const counters=repeated.retrieval_provenance.work_cache.counters;assert.equal(counters.query_calls,0);assert.equal(counters.rerank_calls,0);assert.equal(counters.query_hits,1);assert.ok(counters.rerank_hits>0);
  assert.deepEqual(repeated.matches,result.matches);assert.deepEqual(repeated.interpretations.map(i=>[i.status,i.answer,i.frames]),result.interpretations.map(i=>[i.status,i.answer,i.frames]));
 }
 await page.waitForFunction(()=>document.querySelectorAll('.sequence-evidence').length>0);let crops=0;
 for(const section of await page.locator('.sequence-evidence').all()){await section.locator('summary').click();crops+=await section.locator('img[alt^="Selected tracker crop"]').count();}
 const sections=await page.locator('.sequence-evidence').all();
 for(let i=0;i<result.interpretations.length;i++){const proof=result.interpretations[i].provenance.crop_evidence;if(proof?.mode==='source'){assert.equal(proof.no_upscale,true);assert.ok(proof.crop_sizes.filter(Boolean).every(([w,h])=>w<=768&&h<=768));assert.equal(await sections[i].locator('figcaption small').count(),result.interpretations[i].frames.filter(f=>f.target_crop).length);assert.ok((await sections[i].innerText()).includes('Source crop'));}}
 assert.ok(crops>=2,'Expected reviewable exact-PTS crops');if(['blind-state-consistency-v9','track-visibility-quality-v10'].includes(result.interpretations[0].provenance.prompt_version))assert.ok((await page.locator('.sequence-evidence').first().innerText()).includes('Separate visual-state check'));
 for(const width of [320,390,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
 await page.locator('.sequence-evidence').first().scrollIntoViewIfNeeded();await page.screenshot({path:out+'/desktop.png'});await page.setViewportSize({width:390,height:1000});await page.screenshot({path:out+'/mobile.png'});
 assert.deepEqual(errors,[]);const summary={realApi:true,entity:request.entity_id,interpretations:result.interpretations.length,crops,errors};fs.writeFileSync(out+'/results.json',JSON.stringify(summary,null,2));console.log(summary);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});

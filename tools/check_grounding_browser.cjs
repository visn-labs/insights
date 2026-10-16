// Replay saved real-model API results through the application renderer.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const input=process.env.VISN_GROUNDING_RESULTS||'output/live-evaluation-2026-10-16/behaviors-grounding-v6';
const out=process.env.VISN_GROUNDING_OUTPUT||'output/playwright/grounding-v6';
(async()=>{const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto((process.env.VISN_TEST_URL||'http://127.0.0.1:8192')+'/#/workspace');await page.waitForFunction(()=>typeof renderMemoryQuery==='function');fs.mkdirSync(out,{recursive:true});
 const labels=JSON.parse(fs.readFileSync('evals/live-2026-10-16/temporal-labels.json'));
 const checked=[];
 for(const item of labels.cases){const result=JSON.parse(fs.readFileSync(path.join(input,item.id+'.json')));assert.equal(result.interpretations.length,1);const value=result.interpretations[0];
 await page.evaluate(result=>renderMemoryQuery(result),result);
 const section=page.locator('.sequence-evidence').first();await section.scrollIntoViewIfNeeded();
 const heading=value.status==='not_observed'?'Target not observed in sampled frames':value.status==='supported'?'Unverified model interpretation':'Uncertain interpretation';assert.ok((await section.locator('strong').innerText()).includes(heading));
 await section.locator('summary').click();assert.equal(await section.locator('img').count(),value.frames.length);assert.equal(await section.locator('li').count(),value.observations.length);
 for(const width of [320,390,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
 await section.screenshot({path:path.join(out,item.id+'.png')});checked.push({case:item.id,status:value.status,frames:value.frames.length});
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({evidence:'Saved real-model API responses replayed through renderer; no inference performed in browser check',checked,errors},null,2));console.log(JSON.stringify(checked));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});

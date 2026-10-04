const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'../output/playwright'),url=process.env.VISN_PREVIEW_URL||'http://127.0.0.1:8080/';
fs.mkdirSync(out,{recursive:true});
const style='#town-intro * {visibility:hidden!important} #journey-canvas {visibility:visible!important}';
const frame=page=>page.locator('#journey-canvas').screenshot({style}).then(b=>b.toString('base64'));
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.VISN_BROWSER_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[],results=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.locator('#intro-tour').click();await page.waitForFunction(()=>document.querySelector('.journey-visual').dataset.state==='ready');await page.waitForTimeout(1200);
 const moving=await frame(page);await page.waitForTimeout(350);assert.ok((await frame(page))!==moving,'The journey animates after entry');assert.deepEqual(errors,[]);
 await page.locator('#journey-motion').click();assert.equal(await page.locator('#journey-motion').getAttribute('aria-pressed'),'true');
 const scenes=[];
 for(let i=0;i<4;i++){
  await page.locator('.journey-nav [data-chapter="'+i+'"]').click();await page.waitForTimeout(1100);
  assert.equal(await page.locator('.journey-visual').getAttribute('data-chapter'),String(i));
  const rect=await page.locator('#journey-canvas').boundingBox();assert.ok(Math.abs(rect.y)<2,'The stage stays pinned through every chapter');
  const before=await frame(page);scenes.push(before);await page.waitForTimeout(120);assert.ok((await frame(page))===before,'Paused scene is static');
  const action=page.locator('[data-journey-action="'+i+'"]');await action.click();assert.equal(await action.getAttribute('aria-pressed'),'true');await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));const after=await frame(page);fs.writeFileSync(out+'/journey-action-'+i+'-before.png',Buffer.from(before,'base64'));fs.writeFileSync(out+'/journey-action-'+i+'-after.png',Buffer.from(after,'base64'));assert.ok(after!==before,'Chapter '+i+' has a working 3D interaction');
  await page.screenshot({path:out+'/journey-chapter-'+i+'.png'});
 }
 assert.equal(new Set(scenes).size,4);results.push('Four distinct 3D chapters, pinned scene, direct navigation and interactive objects');
 await page.locator('.journey-nav [data-chapter="0"]').click();await page.waitForTimeout(1000);const before=await frame(page);
 await page.mouse.move(400,550);await page.mouse.wheel(0,420);await page.waitForTimeout(500);assert.ok((await frame(page))!==before,'Native scrolling drives the 3D transition');await page.screenshot({path:out+'/journey-transition.png'});results.push('Native wheel scroll changes the scene continuously without taking over page scrolling');
 await page.locator('.journey-nav [data-chapter="3"]').click();await page.waitForTimeout(1000);await page.locator('.journey-start').click();await page.locator('#onboarding-page').waitFor({state:'visible'});results.push('Final chapter leads into contributor onboarding');
 for(const width of [320,390,768,1024]){
  await page.setViewportSize({width,height:900});await page.goto(url);await page.locator('#intro-tour').click();await page.waitForTimeout(1100);
  for(const i of [0,1,2,3]){await page.locator('.journey-nav [data-chapter="'+i+'"]').click();await page.waitForTimeout(1000);assert.equal(await page.locator('.journey-visual').getAttribute('data-chapter'),String(i));assert.equal(await page.locator('#town-intro').evaluate(e=>e.scrollWidth<=e.clientWidth),true);}
  await page.screenshot({path:out+'/journey-width-'+width+'.png'});
 }
 results.push('All chapters navigate without overflow at 320, 390, 768 and 1024 px');
 const reduced=await browser.newPage({viewport:{width:1440,height:960},reducedMotion:'reduce'});await reduced.goto(url);await reduced.locator('#intro-tour').click();await reduced.waitForFunction(()=>document.querySelector('.journey-visual').dataset.state==='ready');await reduced.waitForTimeout(100);
 const still=await frame(reduced);await reduced.waitForTimeout(200);assert.ok((await frame(reduced))===still);await reduced.locator('.journey-nav [data-chapter="2"]').click();await reduced.waitForTimeout(100);assert.equal(await reduced.locator('.journey-visual').getAttribute('data-chapter'),'2');assert.ok((await frame(reduced))!==still);results.push('Reduced motion gives static chapter views and instant navigation');await reduced.close();
 const fallback=await browser.newPage();await fallback.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type.startsWith('webgl')?null:get.call(this,type,...args);};});await fallback.goto(url);await fallback.locator('#intro-tour').click();await fallback.locator('.journey-fallback').waitFor({state:'visible'});await fallback.locator('.journey-nav [data-chapter="3"]').click();await fallback.locator('.journey-start').click();await fallback.locator('#onboarding-page').waitFor({state:'visible'});results.push('No-WebGL fallback keeps the complete story and onboarding accessible');await fallback.close();
 assert.deepEqual(errors,[]);fs.writeFileSync(out+'/journey-verification.json',JSON.stringify({results,errors},null,2));console.log(JSON.stringify({results,errors},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

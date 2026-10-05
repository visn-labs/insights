const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'../output/playwright');fs.mkdirSync(out,{recursive:true});
const url=process.env.VISN_PREVIEW_URL||'http://127.0.0.1:8080/';
(async()=>{
const browser=await chromium.launch({executablePath:process.env.VISN_BROWSER_PATH||(process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined),headless:true});
const page=await browser.newPage({viewport:{width:1440,height:950}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(url);await page.waitForSelector('.logo-universe');
for(const phase of ['explode','collapse','hexagon','pathways','assembled']){await page.waitForFunction(p=>document.querySelector('.logo-universe').dataset.phase===p,phase,{timeout:30000});await page.screenshot({path:`${out}/logo-opening-${phase}.png`});}
assert.equal(await page.locator('.logo-universe [data-blade]').count(),6);
await page.waitForSelector('.logo-opening-controls',{state:'hidden'});
await page.locator('#intro-tour').click();await page.waitForTimeout(1500);
let samples=[];for(let i=0;i<4;i++){
await page.locator(`.journey-nav [data-chapter="${i}"]`).click();await page.waitForTimeout(1600);
samples.push(await page.locator('.logo-universe').evaluate(e=>({phase:e.dataset.phase,edges:+e.dataset.edges,spread:+e.dataset.spread,top:e.getBoundingClientRect().top,position:getComputedStyle(e).position})));
await page.screenshot({path:`${out}/logo-story-${i}.png`});}
assert.equal(samples[0].phase,'scatter');assert.ok(samples[2].edges>samples[1].edges);assert.equal(samples[3].phase,'assembled');assert.ok(samples.every(s=>s.top===0&&s.position==='fixed'));
const transform=await page.locator('.logo-universe [data-logo]').getAttribute('transform');
await page.locator('[data-journey-action="3"]').click();await page.waitForTimeout(100);
assert.notEqual(await page.locator('.logo-universe [data-logo]').getAttribute('transform'),transform,'Final logo responds to its interaction');
await page.locator('.journey-nav [data-chapter="1"]').click();await page.waitForTimeout(1300);
await page.locator('#journey-motion').click();
const still=await page.locator('.logo-universe svg').innerHTML();await page.waitForTimeout(250);
assert.ok(await page.locator('.logo-universe svg').innerHTML()===still,'Pause freezes every logo and network element');
await page.locator('#journey-motion').click();
const sphere=await page.locator('.logo-universe circle').first().evaluate(e=>{const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};});
await page.mouse.move(sphere.x+10,sphere.y);await page.waitForTimeout(100);
const moved=await page.locator('.logo-universe circle').first().evaluate(e=>{const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};});
assert.ok(Math.hypot(moved.x-sphere.x,moved.y-sphere.y)>2,'Pointer repels nearby spheres');
await page.locator('.journey-nav [data-chapter="3"]').click();await page.waitForTimeout(1300);
await page.locator('.journey-start').click();await page.waitForSelector('#onboarding-title',{state:'visible'});
for(const width of [320,390,768]){await page.setViewportSize({width,height:844});await page.goto(url);await page.locator('#logo-skip').click();await page.locator('#intro-tour').click();await page.waitForTimeout(1200);assert.ok(await page.evaluate(()=>document.querySelector('#town-intro').scrollWidth<=innerWidth+1));await page.screenshot({path:`${out}/logo-mobile-${width}.png`});}
await page.emulateMedia({reducedMotion:'reduce'});await page.goto(url);assert.equal(await page.locator('#logo-skip').isVisible(),false);await page.locator('#intro-tour').click();await page.screenshot({path:out+'/logo-reduced.png'});
const reducedStill=await page.locator('.logo-universe svg').innerHTML();await page.waitForTimeout(250);assert.ok(await page.locator('.logo-universe svg').innerHTML()===reducedStill,'Reduced motion remains still');
const fallback=await browser.newPage();await fallback.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type.startsWith('webgl')?null:get.call(this,type,...args);};});
await fallback.goto(url);await fallback.locator('#logo-skip').click();await fallback.locator('#intro-tour').click();await fallback.locator('.journey-nav [data-chapter="3"]').click();await fallback.waitForTimeout(1500);assert.equal(await fallback.locator('.logo-universe [data-blade]').count(),6);await fallback.locator('.journey-start').click();await fallback.locator('#onboarding-title').waitFor({state:'visible'});await fallback.close();
assert.deepEqual(errors,[]);fs.writeFileSync(out+'/journey-verification.json',JSON.stringify({samples,errors},null,2));console.log(JSON.stringify({samples,errors,checks:['opening sequence','exactly six blades','fixed background','network density increases','final logo','onboarding CTA','320/390/768 widths','reduced motion','pointer response','final logo interaction','pause','no WebGL required']},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

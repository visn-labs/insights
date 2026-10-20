const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.VISN_UI_URL||'http://127.0.0.1:8220';
const out=path.resolve(process.env.VISN_UI_OUT||'output/playwright/camera-reel-validation');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',args:['--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const report={checks:[],errors:[]};page.on('pageerror',e=>report.errors.push(e.message));
 const check=(name,ok)=>{assert.ok(ok,name);report.checks.push(name);};
 const move=async(p,value)=>{await p.evaluate(value=>{const e=document.querySelector('#camera-film-experience');scrollTo({top:e.getBoundingClientRect().top+scrollY+innerHeight*value,behavior:'instant'});},value);await p.waitForTimeout(500);};
 try{
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');
  const canvas=page.locator('#town-canvas');
  check('Only the requested two story chapters are present',await page.locator('.recording-chapter').count()===2);
  check('The former desk renderer is absent',await page.locator('#atelier-canvas').count()===0);
  check('The street is the initial view',await canvas.getAttribute('data-recording-view')==='street');
  await move(page,1.02);check('Camera chapter selects CAM 04 opposite CAM 01',await canvas.getAttribute('data-selected-camera')==='4'&&await canvas.getAttribute('data-recording-view')==='camera');
  check('Compact cassette fits entirely inside the housing',await canvas.getAttribute('data-internal-cassette')==='true'&&+await canvas.getAttribute('data-cassette-scale')<.3);check('First view retains a closed camera shell',+await canvas.getAttribute('data-cutaway')===0);
  const a=+await canvas.getAttribute('data-reel-angle');await page.waitForTimeout(450);check('Cinema reels rotate continuously',+await canvas.getAttribute('data-reel-angle')<a);
  await page.screenshot({path:path.join(out,'desktop-camera.png')});
  await move(page,2.5);const roll=+await canvas.getAttribute('data-reel-roll');check('Zoom traverses a continuous intermediate reel pose',roll>0&&roll<30);
  await move(page,2.98);check('Second view reaches a 30 degree right dip',+await canvas.getAttribute('data-reel-roll')===30);check('Projected film axis is actually 30 degrees on screen',Math.abs(+await canvas.getAttribute('data-projected-film-angle')-30)<.1);check('Same canvas enters the internal reel',await canvas.getAttribute('data-recording-view')==='reel');check('Macro view reveals the internal cutaway',+await canvas.getAttribute('data-cutaway')===1);
  const offset=await canvas.getAttribute('data-film-offset');await page.waitForTimeout(300);check('Physical film keeps travelling',offset!==await canvas.getAttribute('data-film-offset'));
  await page.screenshot({path:path.join(out,'desktop-reel.png')});
  await move(page,1.02);check('Reverse scrolling returns to the same camera',await canvas.getAttribute('data-recording-view')==='camera');
  await page.emulateMedia({reducedMotion:'reduce'});await page.waitForTimeout(150);const frozen=await canvas.getAttribute('data-reel-angle');await page.waitForTimeout(350);check('Reduced motion stops reel rotation',frozen===await canvas.getAttribute('data-reel-angle'));
  await move(page,2.98);check('Reduced motion retains the complete reel composition',await canvas.getAttribute('data-recording-view')==='reel');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto(base+'/#/home');await page.waitForTimeout(250);const stopped=await canvas.getAttribute('data-reel-angle');await page.waitForTimeout(300);check('Contributor route suspends the street renderer',stopped===await canvas.getAttribute('data-reel-angle'));
  const phone=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});await phone.goto(base);await move(phone,1.02);await phone.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');
  for(const [name,phase]of [['camera',1.02],['reel',2.98]]){await move(phone,phase);check('Phone '+name+' has no horizontal overflow',await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await phone.screenshot({path:path.join(out,'phone-'+name+'.png')});}
  const reduced=await browser.newPage({reducedMotion:'reduce',viewport:{width:390,height:844}});await reduced.goto(base);await move(reduced,1.02);check('Initial reduced-motion visit has a camera still',await reduced.locator('.camera-still').evaluate(e=>getComputedStyle(e).opacity==='1'));await move(reduced,2.98);check('Initial reduced-motion visit has a reel still',await reduced.locator('.reel-still').evaluate(e=>getComputedStyle(e).opacity==='1'));
  const noGL=await browser.newPage();await noGL.addInitScript(()=>{const old=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return /webgl/.test(type)?null:old.call(this,type,...args);};});await noGL.goto(base);await move(noGL,1.02);check('No-WebGL path keeps the camera story and draft link',await noGL.locator('#camera-chapter-title').isVisible()&&await noGL.locator('#intro-enter').count()===1);
  check('No unhandled application errors',report.errors.length===0);report.passed=true;
 }catch(error){report.passed=false;report.failure=error.stack;process.exitCode=1;}
 finally{fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();}
})();

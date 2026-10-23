const{chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(process.env.VISN_UI_OUT||'output/playwright/logo-convergence');fs.mkdirSync(out,{recursive:true});
(async()=>{const browser=await chromium.launch({channel:'chrome',args:['--enable-unsafe-swiftshader']});const context=await browser.newContext({viewport:{width:1280,height:900},recordVideo:{dir:path.join(out,'video'),size:{width:1280,height:900}}});const page=await context.newPage(),checks=[],errors=[];let passed=false;page.on('pageerror',e=>errors.push(e.message));const check=(name,ok)=>{assert.ok(ok,name);checks.push(name);};
 try{
  await page.goto(process.env.VISN_UI_URL||'http://127.0.0.1:8220');await page.locator('#logo-dialog').waitFor({state:'visible'});
  await page.evaluate(()=>{const intervals=[];let previous=0;window.__logoTiming=null;function tick(now){if(!document.querySelector('#logo-dialog').open){intervals.sort((a,b)=>a-b);window.__logoTiming={frames:intervals.length,medianMs:intervals[Math.floor(intervals.length*.5)],p95Ms:intervals[Math.floor(intervals.length*.95)],maxMs:intervals.at(-1)};return;}if(previous)intervals.push(now-previous);previous=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);});
  check('Initial visit starts the inward reveal',await page.locator('#logo-dialog').getAttribute('data-intro')==='true');
  check('GPU tunnel compiles',await page.locator('#logo-dialog').getAttribute('data-shader')==='ready');
  check('Street initialization is deferred during the intro',await page.locator('#town-intro').getAttribute('data-state')==='poster');
  await page.waitForFunction(()=>+document.querySelector('#logo-dialog').dataset.revealTime>.6);await page.screenshot({path:path.join(out,'01-inward.png')});
  await page.waitForFunction(()=>+document.querySelector('#logo-dialog').dataset.revealTime>2.6);await page.screenshot({path:path.join(out,'02-aperture.png')});
  check('Exactly six aperture blades assemble',await page.locator('#logo-exploration [data-blade]').count()===6);
  await page.waitForFunction(()=>+document.querySelector('#logo-dialog').dataset.revealTime>4.2);await page.screenshot({path:path.join(out,'03-outward-nodes.png')});
  await page.locator('#logo-dialog').waitFor({state:'hidden'});check('Initial reveal releases the landing page',true);
  await page.evaluate(()=>document.querySelector('#logo-replay').click());await page.waitForFunction(()=>document.querySelector('#logo-dialog').dataset.revealPhase==='complete');
  check('Replay holds the assembled logo for inspection',await page.locator('#logo-dialog').isVisible());
  const alignment=await page.evaluate(()=>{const a=document.querySelector('#logo-exploration').getBoundingClientRect(),b=document.querySelector('.logo-tunnel').getBoundingClientRect();return Math.hypot(a.x+a.width/2-b.x-b.width/2,a.y+a.height/2-b.y-b.height/2);});
  check('Tunnel and logo share one screen centre',alignment<1);
  check('Final nodes occupy the frozen outer radius',await page.locator('#logo-exploration [data-outer-node]').evaluateAll(nodes=>nodes.every(n=>{const xy=n.getAttribute('transform').match(/translate\(([-\d.e]+) ([-\d.e]+)\)/);return xy&&Math.abs(Math.hypot(+xy[1],+xy[2])-602)<.001;})));
  await page.screenshot({path:path.join(out,'04-complete.png')});await page.keyboard.press('Escape');check('Escape dismisses the reveal',!await page.locator('#logo-dialog').isVisible());
  const phone=await browser.newPage({viewport:{width:390,height:844}});await phone.goto('http://127.0.0.1:8220');await phone.locator('#logo-dialog').waitFor({state:'visible'});await phone.waitForFunction(()=>+document.querySelector('#logo-dialog').dataset.revealTime>4.8);await phone.screenshot({path:path.join(out,'phone-logo.png')});await phone.locator('#logo-close').click();check('Phone has a usable skip control',!await phone.locator('#logo-dialog').isVisible());await phone.close();
  const reduced=await browser.newPage({reducedMotion:'reduce'});await reduced.goto('http://127.0.0.1:8220');await reduced.waitForTimeout(150);check('Reduced-motion visits bypass the automatic tunnel',!await reduced.locator('#logo-dialog').isVisible());await reduced.evaluate(()=>document.querySelector('#logo-replay').click());check('Reduced-motion exploration presents the complete logo immediately',await reduced.locator('#logo-dialog').getAttribute('data-reveal-phase')==='complete');await reduced.close();
  const noGL=await browser.newPage();await noGL.addInitScript(()=>{const old=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return /webgl/.test(type)?null:old.call(this,type,...args);};});await noGL.goto('http://127.0.0.1:8220');await noGL.waitForTimeout(200);check('No-WebGL visits retain an accessible static landing',!await noGL.locator('#logo-dialog').isVisible());await noGL.close();check('No application errors',errors.length===0);passed=true;
 }finally{
  const timing=await page.evaluate(()=>window.__logoTiming).catch(()=>null);
  const video=page.video();await context.close();await video.saveAs(path.join(out,'logo-convergence.webm'));await browser.close();fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,passed,timing},null,2));console.log(JSON.stringify({checks,errors}));
 }
})().catch(e=>{console.error(e);process.exitCode=1;});

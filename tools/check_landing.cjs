// Run with Playwright installed externally; the application itself has no npm dependency.
const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const root=require('node:path').resolve(__dirname,'..');
const baseURL=process.env.VISN_PREVIEW_URL || 'http://127.0.0.1:8080/';
fs.mkdirSync(root+'/output/playwright',{recursive:true});
async function captureSceneFrame(page) {
  // Screenshot the visible region without scrolling a deliberately cropped canvas.
  const clip=await page.evaluate(()=>{
    const r=document.querySelector('#town-canvas').getBoundingClientRect();
    const x=Math.max(0,r.left),y=Math.max(0,r.top);
    return {x:x+scrollX,y:y+scrollY,width:Math.min(innerWidth,r.right)-x,height:Math.min(innerHeight,r.bottom)-y};
  });
  return (await page.screenshot({clip})).toString('base64');
}

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.VISN_BROWSER_PATH || (process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined),headless:true,args:['--no-sandbox']});
  const results=[], errors=[];
  const context=await browser.newContext({viewport:{width:1440,height:960},deviceScaleFactor:1});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.stack));
  await page.goto(baseURL);
  await page.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');
  await page.waitForTimeout(800);
  const start=await captureSceneFrame(page);
  await page.waitForTimeout(500);
  assert.ok((await captureSceneFrame(page))!==start,'Canvas must visibly change while playing');
  results.push('Animated WebGL frames change over time');
  await page.screenshot({path:root+'/output/playwright/desktop.png'});
  for(let i=0;i<3;i++) {
    await page.locator('.story-tab').nth(i).click();
    assert.equal(await page.locator('.story-tab').nth(i).getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#signal-state').textContent(),['CAPTURING','TRANSMITTING','DELIVERING'][i]);
  }
  results.push('All three story controls update the animation phase and explanation');
  await page.waitForFunction(()=>document.querySelector('#signal-state').textContent==='CAPTURING',null,{timeout:12000});
  results.push('The complete signal loop returns from delivery to capture');
  await page.locator('#intro-motion').click();
  await page.waitForTimeout(100);
  const paused=await captureSceneFrame(page);
  await page.waitForTimeout(450);
  assert.ok((await captureSceneFrame(page))===paused,'Pause freezes canvas');
  results.push('Pause freezes the rendered scene');
  await page.locator('#intro-motion').click();
  await page.locator('#intro-tour').click();
  assert.equal(await page.locator('#signal-state').textContent(),'CAPTURING');
  await page.locator('#intro-enter').click();
  assert.equal(await page.locator('#town-intro').isVisible(),false);
  assert.equal(await page.locator('.app-shell').evaluate(e=>e.inert),false);
  assert.equal(await page.locator('#job-form').isVisible(),true);
  await page.locator('.nav-item[data-view="memory"]').click();
  assert.equal(await page.locator('#memory-view').isVisible(),true);
  await page.locator('#replay-intro').click();
  assert.equal(await page.locator('#town-intro').isVisible(),true);
  assert.equal(await page.locator('.app-shell').evaluate(e=>e.inert),true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#town-intro').isVisible(),false);
  results.push('Entry, existing workspace navigation, replay, Escape, and focus isolation work');
  await page.locator('#replay-intro').click();
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(700);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No mobile horizontal overflow');
  await page.screenshot({path:root+'/output/playwright/mobile.png',fullPage:true});
  await page.locator('.story-tab').nth(1).click();
  assert.equal(await page.locator('#signal-state').textContent(),'TRANSMITTING');
  await page.locator('[data-enter]').first().click();
  assert.equal(await page.locator('#town-intro').isVisible(),false);
  results.push('390px mobile layout has no horizontal overflow; story and entry controls work');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.locator('#replay-intro').click();
  assert.equal(await page.locator('#intro-motion').getAttribute('aria-pressed'),'true');
  const still=await captureSceneFrame(page);
  await page.waitForTimeout(400);
  assert.ok((await captureSceneFrame(page))===still,'Reduced motion must freeze canvas');
  await page.screenshot({path:root+'/output/playwright/reduced-motion.png',fullPage:true});
  results.push('Reduced-motion preference produces a static scene');
  assert.deepEqual(errors,[],'No browser runtime exceptions');
  results.push('No browser runtime exceptions');
  // A fresh context verifies WebGL failure still allows access to the real app.
  const fallback=await browser.newPage();
  await fallback.addInitScript(()=>{const orig=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){if(type.startsWith('webgl'))return null;return orig.call(this,type,...args);};});
  await fallback.goto(baseURL);
  await fallback.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='error');
  await fallback.locator('#intro-enter').click();
  assert.equal(await fallback.locator('#job-form').isVisible(),true);
  results.push('WebGL-unavailable fallback preserves workspace entry');
  // Inspect the real exported renderer in a separate browser fixture. This keeps
  // production code free of debug globals and still exercises the served module.
  const geometryPage=await browser.newPage({viewport:{width:1440,height:960}});
  geometryPage.on('pageerror',e=>errors.push(e.stack));
  await geometryPage.route('**/intro.js',route=>route.fulfill({contentType:'application/javascript',body:`
    import {TownScene} from '/engine.js';
    import * as THREE from '/vendor/three.module.min.js';
    window.townForTest=new TownScene(document.querySelector('#town-canvas'),document.querySelector('#camera-labels'),()=>{});
    window.townTHREE=THREE;
    document.querySelector('#town-intro').dataset.state='ready';
  `}));
  await geometryPage.goto(baseURL);
  await geometryPage.waitForFunction(()=>!!window.townForTest);
  const sceneReport=await geometryPage.evaluate(()=>{
    const scene=window.townForTest,THREE=window.townTHREE;
    const corners=[[-4.6,.2,-4.6],[4.6,.2,-4.6],[-4.6,.2,4.6],[4.6,.2,4.6]];
    scene.time=2;scene.update(0);
    const projections=corners.map(p=>new THREE.Vector3(...p).project(scene.camera).toArray());
    const before=scene.vehicles[0].mesh.position.clone();
    let finite=true;
    for(const time of [4,12,24,36,48,64,90]) {
      scene.time=time;scene.update(0);
      scene.scene.traverse(object=>{if(!object.matrixWorld.elements.every(Number.isFinite))finite=false;});
    }
    scene.time=6;scene.update(0);
    const adults=[...scene.actors,...scene.vehicles.map(v=>v.driver),scene.handcart.porter];
    const anatomy=adults.every(actor=>{
      const height=actor.anatomy.height*actor.mesh.scale.y;
      const ratio=actor.anatomy.height/actor.anatomy.headHeight;
      return height>1.7&&height<2&&ratio>7&&ratio<8&&actor.ankles.length===2&&actor.hands.length===2;
    });
    let gripError=0;
    for(const actor of adults.filter(a=>a.role==='reader'||a.heldProp==='parasol'||a.grips)) {
      scene.fitHands(actor);scene.scene.updateMatrixWorld(true);
      const targets=actor.grips || (actor.role==='reader'?[[-.155,1.24,.37],[.155,1.24,.37]]:[null,[.35,1.2,.12]]);
      targets.forEach((target,side)=>{
        if(!target)return;
        const hand=actor.hands[side].getWorldPosition(new THREE.Vector3());
        const goal=actor.torso.localToWorld(new THREE.Vector3(...target));
        gripError=Math.max(gripError,hand.distanceTo(goal));
      });
    }
    const seatedFeet=[...scene.actors.filter(a=>a.seated),...scene.vehicles.map(v=>v.driver)].map(actor=>{
      const expected=actor.role==='reader'?.23:scene.vehicles.find(v=>v.driver===actor).mesh.position.y+.8;
      return Math.max(...actor.ankles.map(ankle=>Math.abs(ankle.localToWorld(new THREE.Vector3(0,-actor.anatomy.soleOffset,0)).y-expected)));
    });
    const report={anatomy,gripError,seatedFootError:Math.max(...seatedFeet),diagonal:Math.abs(scene.camera.position.x-scene.camera.position.z)<.001,cornersVisible:projections.every(([x,y,z])=>Math.abs(x)<1&&Math.abs(y)<1&&Math.abs(z)<1),projections,finite,people:scene.actors.length,carriages:scene.vehicles.length,trafficMoved:before.distanceTo(scene.vehicles[0].mesh.position)>1,drawCalls:scene.renderer.info.render.calls,triangles:scene.renderer.info.render.triangles,dynamicBatches:scene.dynamicBatches.length};
    return report;
  });
  assert.ok(sceneReport.diagonal && sceneReport.cornersVisible,'The corner viewpoint must show all four perpendicular pavement corners');
  assert.ok(sceneReport.finite,'Animated geometry remains finite across the full traffic cycle');
  assert.ok(sceneReport.people>=30 && sceneReport.carriages===2 && sceneReport.trafficMoved,'A populated town has actual moving carriage traffic');
  assert.ok(sceneReport.anatomy,'All pedestrians, drivers and porters use adult proportions and complete hand/ankle rigs');
  assert.ok(sceneReport.gripError<.001,'Readers, parasols, carriage reins and handcart grips fit the adult arm rigs');
  assert.ok(sceneReport.seatedFootError<.001,'Seated feet rest on pavement or the carriage footboard');
  assert.ok(sceneReport.dynamicBatches>0,'Detailed moving models use instancing');
  await geometryPage.screenshot({path:root+'/output/playwright/refined-street.png'});
  results.push('Adult proportions, prop grips and seated foot contact verified');
  results.push('Four square junction corners are in frame from a diagonal corner viewpoint');
  results.push('Detailed people and moving carriages remain valid over a 90-second scene sample');
  assert.deepEqual(errors,[],'No runtime errors in the larger animated town');
  fs.writeFileSync(root+'/output/playwright/verification.json',JSON.stringify({results,errors,sceneReport},null,2));
  console.log(JSON.stringify({results,errors,sceneReport},null,2));
  await browser.close();
})().catch(e=>{console.error(e.stack);process.exit(1)});

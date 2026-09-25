// npm is needed only for this external browser check, not by the application.
const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),out=root+'/output/playwright';fs.mkdirSync(out,{recursive:true});
const baseURL=process.env.VISN_PREVIEW_URL||'http://127.0.0.1:8080/';
async function frame(page){const clip=await page.locator('#town-canvas').evaluate(e=>{const r=e.getBoundingClientRect(),x=Math.max(r.left,0),y=Math.max(r.top,0);return{x,y,width:Math.min(innerWidth,r.right)-x,height:Math.min(innerHeight,r.bottom)-y};});return(await page.screenshot({clip})).toString('base64');}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.VISN_BROWSER_PATH||(process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined),headless:true});
 const results=[],errors=[];const page=await browser.newPage({viewport:{width:1440,height:960}});page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(baseURL);await page.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');
 const moving=await frame(page);await page.waitForTimeout(350);assert.ok((await frame(page))!==moving,'The scene advances while playing');
 await page.locator('#intro-motion').click();await page.waitForTimeout(150);const paused=await frame(page);await page.waitForTimeout(200);assert.ok((await frame(page))===paused,'Pause freezes the visible scene');
 for(let i=0;i<3;i++){await page.locator('.story-tab').nth(i).click();assert.equal(await page.locator('.story-tab').nth(i).getAttribute('aria-pressed'),'true');assert.equal(await page.locator('#signal-state').textContent(),['YOUR VIEW','KNOWLEDGE','SHARED VALUE'][i]);}
 await page.screenshot({path:out+'/contributor-landing.png'});results.push('Live 3D animation, pause and all contributor story chapters');
 await page.locator('[data-scroll="how-it-works"]').first().click();await page.waitForTimeout(500);await page.screenshot({path:out+'/contributor-story.png'});
 await page.goto(baseURL+'#/contribute');await page.locator('#contribution-next').click();assert.equal(await page.locator('#step-counter').textContent(),'01 / 04');
 await page.locator('#contribution-camera').fill('Corner shop entrance');await page.locator('#contribution-context').selectOption({label:'Shop or business entrance'});await page.locator('#contribution-area').fill('Bengaluru');await page.locator('#contribution-authority').check();
 await page.screenshot({path:out+'/onboarding.png'});await page.locator('#contribution-next').click();await page.locator('#contribution-next').click();assert.equal(await page.locator('#contribution-error').isVisible(),true);
 await page.locator('#contribution-file').setInputFiles({name:'not-video.txt',mimeType:'text/plain',buffer:Buffer.from('not a video')});assert.match(await page.locator('#contribution-error').textContent(),/Choose a non-empty/);
 await page.locator('#use-example').click();await page.locator('#contribution-next').click();await page.locator('#segment-start').fill('10');await page.locator('#segment-end').fill('5');await page.locator('#contribution-next').click();assert.equal(await page.locator('#step-counter').textContent(),'03 / 04');
 await page.locator('#segment-start').fill('2.5');await page.locator('#segment-end').fill('22.5');await page.locator('#contribution-reviewed').check();await page.locator('#contribution-next').click();assert.match(await page.locator('#contribution-review').textContent(),/2.5–22.5/);
 await page.screenshot({path:out+'/contribution-review.png'});await page.locator('#contribution-next').click();assert.equal(await page.locator('#step-counter').textContent(),'04 / 04');await page.locator('#contribution-draft-consent').check();await page.locator('#contribution-next').click();
 await page.waitForURL('**/#/home');await page.locator('#contributor-home').waitFor({state:'visible'});assert.equal(await page.locator('#contributor-draft-count').textContent(),'1');await page.reload();assert.equal(await page.locator('#contributor-draft-count').textContent(),'1');await page.locator('.draft-details summary').click();assert.match(await page.locator('.draft-details').textContent(),/2.5–22.5/);
 await page.screenshot({path:out+'/contributor-home.png'});results.push('Validated onboarding, explicit permissions, segment boundaries, review and persistent local draft');
 // A real browser-generated recording exercises file decoding, not a mocked file input.
 const recording=await page.evaluate(async()=>{const c=document.createElement('canvas');c.width=160;c.height=90;const ctx=c.getContext('2d'),stream=c.captureStream(10),chunks=[];const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});const done=new Promise(resolve=>recorder.onstop=async()=>resolve(Array.from(new Uint8Array(await new Blob(chunks,{type:'video/webm'}).arrayBuffer()))));recorder.ondataavailable=e=>chunks.push(e.data);recorder.start();for(let i=0;i<12;i++){ctx.fillStyle=i%2?'#d5c2a4':'#5c7759';ctx.fillRect(0,0,160,90);await new Promise(r=>setTimeout(r,100));}recorder.stop();stream.getTracks().forEach(t=>t.stop());return done;});
 await page.goto(baseURL+'#/contribute');await page.locator('#contribution-camera').fill('Local video test');await page.locator('#contribution-context').selectOption({index:1});await page.locator('#contribution-authority').check();await page.locator('#contribution-next').click();
 await page.locator('#contribution-file').setInputFiles({name:'local-preview.webm',mimeType:'video/webm',buffer:Buffer.from(recording)});await page.waitForFunction(()=>document.querySelector('#contribution-file-status').textContent.includes('MB'));await page.locator('#contribution-next').click();assert.equal(await page.locator('#contribution-preview').isVisible(),true);await page.locator('#preview-segment').click();await page.waitForTimeout(150);assert.equal(await page.locator('#contribution-preview').evaluate(v=>v.paused),false);results.push('Real local WebM decoding and segment playback');
 await page.goto(baseURL+'#/workspace');assert.equal(await page.locator('#job-form').isVisible(),true);assert.equal(await page.locator('.app-shell').evaluate(e=>e.inert),false);await page.screenshot({path:out+'/warm-workspace.png'});await page.locator('#replay-intro').click();await page.locator('#town-intro').waitFor({state:'visible'});assert.equal(await page.locator('#town-intro').isVisible(),true);results.push('Existing analysis workspace and return navigation remain usable');
 for(const width of [320,768,1024,1440]){
  await page.setViewportSize({width,height:900});
  for(const route of ['#/','#/contribute','#/home']){await page.goto(baseURL+route);await page.waitForTimeout(80);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Overflow at ${width} ${route}`);if(route==='#/')assert.equal(await page.locator('#town-intro').evaluate(e=>e.scrollWidth<=e.clientWidth),true,`Landing overflow ${width}`);}
  if(width===320){await page.screenshot({path:out+'/mobile-home.png',fullPage:true});await page.goto(baseURL+'#/contribute');await page.screenshot({path:out+'/mobile-onboarding.png',fullPage:true});await page.goto(baseURL+'#/');await page.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');await page.screenshot({path:out+'/mobile-landing.png'});}
 }
 results.push('Landing, onboarding and home fit 320, 768, 1024 and 1440 px');
 await page.emulateMedia({reducedMotion:'reduce'});await page.goto(baseURL);await page.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='ready');const still=await frame(page);await page.waitForTimeout(200);assert.ok((await frame(page))===still,'Reduced motion remains static');results.push('Reduced motion freezes the 3D scene');
 const fallback=await browser.newPage();await fallback.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type.startsWith('webgl')?null:get.call(this,type,...args);};});await fallback.goto(baseURL);await fallback.waitForFunction(()=>document.querySelector('#town-intro').dataset.state==='error');await fallback.locator('#intro-enter').click();await fallback.locator('#contribution-camera').waitFor({state:'visible'});assert.equal(await fallback.locator('#contribution-camera').isVisible(),true);await fallback.close();results.push('No-WebGL fallback still allows contributor onboarding');
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
        const goal=actor.worldGrips?actor.worldGrips[side].clone():actor.torso.localToWorld(new THREE.Vector3(...target));
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

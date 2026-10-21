// Continuous real-browser recording, not screenshot-based motion acceptance.
const {chromium}=require(process.env.VISN_PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const out=path.resolve(process.env.VISN_UI_OUT||'output/playwright/camera-remodel');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',args:['--enable-unsafe-swiftshader']});
 const context=await browser.newContext({viewport:{width:1440,height:1000},recordVideo:{dir:path.join(out,'video'),size:{width:1440,height:1000}}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.VISN_UI_URL||'http://127.0.0.1:8220/#/home');
 await page.setContent('<style>body{margin:0;background:#f5eddf}canvas{width:100vw;height:100vh;display:block}#labels{position:fixed;inset:0;pointer-events:none}#labels>*{display:none}</style><canvas id="playback"></canvas><div id="labels"></div>');
 const report=await page.evaluate(async()=>{
  const {TownScene}=await import('/engine.js');const T=await import('/vendor/three.module.min.js');
  const town=new TownScene(document.querySelector('canvas'),document.querySelector('#labels'),()=>{});
  const original=town.recording.pose.bind(town.recording);let close=false;
  town.recording.pose=(camera,...args)=>{original(camera,...args);if(close){camera.position.set(-.3,3.5,10.2);camera.lookAt(-4.8,1.2,6.9);camera.updateMatrixWorld();}};
  let priorTime=null,previous=null,maxJointSpeed=0;const intervals=[];
  const update=town.update.bind(town);town.update=delta=>{
   update(delta);if(delta<=0)return;
   const now=performance.now();if(priorTime)intervals.push(now-priorTime);priorTime=now;
   // A planted local remains in frame through gesture-cycle seams.
   const points=town.actors.filter(a=>a.partner).flatMap(actor=>[actor.head,...actor.hands]).map(o=>o.getWorldPosition(new T.Vector3()));
   if(previous)points.forEach((p,i)=>maxJointSpeed=Math.max(maxJointSpeed,p.distanceTo(previous[i])/delta));previous=points;
  };
  town.start();await new Promise(r=>setTimeout(r,14000));close=true;await new Promise(r=>setTimeout(r,14000));town.stop();
  intervals.sort((a,b)=>a-b);const result={frames:intervals.length,medianMs:intervals[Math.floor(intervals.length*.5)],p95Ms:intervals[Math.floor(intervals.length*.95)],maxMs:intervals.at(-1),over33ms:intervals.filter(x=>x>33.4).length,maxConversationJointSpeed:maxJointSpeed};town.dispose();return result;
 });
 const video=page.video();await context.close();await video.saveAs(path.join(out,'street-continuous.webm'));await browser.close();
 report.errors=errors;fs.writeFileSync(path.join(out,'motion-playback.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));assert.equal(errors.length,0);assert.ok(report.maxConversationJointSpeed<3,'Conversation joint jump');
})().catch(e=>{console.error(e);process.exitCode=1;});

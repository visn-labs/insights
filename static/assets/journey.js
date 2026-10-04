import * as THREE from '../vendor/three.module.min.js';

const clamp=THREE.MathUtils.clamp;
const smooth=t=>t*t*(3-2*t);
const mix=THREE.MathUtils.lerp;
const paper=0xf5eddf,coffee=0x493327,green=0x557560,brass=0xb39561;

// A small persistent stage: the same camera and frames carry through all chapters.
export class JourneyScene {
  constructor(canvas) {
    this.canvas=canvas;this.time=0;this.flowTime=0;this.progress=0;this.target=0;
    this.pointer=new THREE.Vector2();this.pointerSmooth=new THREE.Vector2();
    this.actions=[0,0,0,0];this.actionTargets=[0,0,0,0];
    this.paused=false;this.reduced=false;this.visible=false;this.running=false;
    this.scene=new THREE.Scene();this.scene.background=new THREE.Color(paper);
    this.camera=new THREE.PerspectiveCamera(36,1,.1,60);
    this.renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:false,powerPreference:'low-power'});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFSoftShadowMap;
    this.scene.add(new THREE.HemisphereLight(0xfff7e7,0x8a755c,1.8));
    const light=new THREE.DirectionalLight(0xfff0d7,2.2);light.position.set(-3,7,6);light.castShadow=true;
    light.shadow.mapSize.set(1024,1024);Object.assign(light.shadow.camera,{left:-6,right:6,top:5,bottom:-5});light.shadow.normalBias=.025;this.scene.add(light);
    const rim=new THREE.DirectionalLight(0xd8e3cc,1.7);rim.position.set(5,3,-5);this.scene.add(rim);
    this.materials=new Map();this.world=new THREE.Group();this.scene.add(this.world);
    this.cameraModel=this.makeCamera();this.world.add(this.cameraModel);
    this.frames=Array.from({length:5},(_,i)=>this.makeFrame(i));this.frames.forEach(frame=>this.world.add(frame));
    this.core=new THREE.Group();this.world.add(this.core);
    this.mesh(this.core,new THREE.IcosahedronGeometry(.62,1),green);
    const wire=new THREE.Mesh(new THREE.IcosahedronGeometry(.67,1),new THREE.MeshBasicMaterial({color:brass,wireframe:true,transparent:true,opacity:.5}));this.core.add(wire);
    this.coreRing=this.mesh(this.core,new THREE.TorusGeometry(.9,.018,8,64),brass);this.coreRing.rotation.x=.6;
    this.coin=this.makeCoin();this.world.add(this.coin);
    this.links=this.frames.map(()=>{
      const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3()]),new THREE.LineBasicMaterial({color:0x91a083,transparent:true,opacity:0}));this.world.add(line);return line;
    });
    this.loopCurve=new THREE.CatmullRomCurve3([new THREE.Vector3(-2.8,-.4,0),new THREE.Vector3(-1,1.8,-.8),new THREE.Vector3(1.6,.8,0),new THREE.Vector3(.4,-1.3,.7),new THREE.Vector3(-2.8,-.4,0)]);
    this.loop=new THREE.Line(new THREE.BufferGeometry().setFromPoints(this.loopCurve.getPoints(100)),new THREE.LineBasicMaterial({color:brass,transparent:true,opacity:0}));this.world.add(this.loop);
    this.packets=Array.from({length:24},()=>{const item=this.mesh(this.world,new THREE.OctahedronGeometry(.045),green);item.castShadow=false;return item;});
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(200,200),new THREE.ShadowMaterial({color:coffee,opacity:.16}));this.scene.add(floor);floor.rotation.x=-Math.PI/2;floor.position.y=-1.7;floor.receiveShadow=true;
    this.resize();
  }
  material(color,metalness=0) {
    const key=`${color}/${metalness}`;
    if(!this.materials.has(key))this.materials.set(key,new THREE.MeshStandardMaterial({color,metalness,roughness:metalness?.34:.65}));
    return this.materials.get(key);
  }
  mesh(parent,geometry,color,metalness=0) {
    const mesh=new THREE.Mesh(geometry,this.material(color,metalness));mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;
  }
  box(parent,x,y,z,w,h,d,color,metalness=0) {
    const geometry=new THREE.BoxGeometry(w,h,d,3,3,3),radius=Math.min(.055,w/5,h/5,d/5);
    const inner=new THREE.Vector3(w/2-radius,h/2-radius,d/2-radius),vertex=new THREE.Vector3(),nearest=new THREE.Vector3();
    const positions=geometry.attributes.position;
    for(let i=0;i<positions.count;i++){vertex.fromBufferAttribute(positions,i);nearest.copy(vertex).clamp(inner.clone().negate(),inner);vertex.sub(nearest).normalize().multiplyScalar(radius).add(nearest);positions.setXYZ(i,vertex.x,vertex.y,vertex.z);}
    geometry.computeVertexNormals();const mesh=this.mesh(parent,geometry,color,metalness);mesh.position.set(x,y,z);return mesh;
  }
  makeCamera() {
    const group=new THREE.Group();
    this.box(group,0,.3,0,1.18,.72,1.7,0xe0d2b8,.3);
    this.box(group,0,.71,.08,1.34,.08,1.99,0xf3e6ce,.2);
    this.box(group,0,.3,.865,1.08,.62,.07,coffee,.2);
    this.lens=new THREE.Group();this.lens.position.set(0,.31,.95);group.add(this.lens);
    for(const [r,z,color] of [[.29,0,brass],[.25,.035,0x252f2b],[.18,.065,0x607f75],[.105,.075,0x253e38]]){
      const ring=this.mesh(this.lens,new THREE.CylinderGeometry(r,r,.055,48),color,.6);ring.rotation.x=Math.PI/2;ring.position.z=z;
    }
    const glint=this.mesh(this.lens,new THREE.SphereGeometry(.04,12,8),0xd8edc8);glint.position.set(-.06,.065,.12);
    for(const side of [-1,1])for(let i=0;i<6;i++)this.box(group,side*.598,.23,-.55+i*.15,.018,.21,.035,0x9e8d75);
    for(const x of [-.45,.45])for(const y of [.06,.54]){const screw=this.mesh(group,new THREE.SphereGeometry(.027,8,6),brass,.8);screw.position.set(x,y,.91);}
    this.box(group,0,-.18,-.3,.23,.36,.26,0x72604a,.4);
    this.box(group,0,-.63,-.46,.16,.72,.18,0x72604a,.4);
    const base=this.mesh(group,new THREE.CylinderGeometry(.5,.56,.11,48),brass,.5);base.position.set(0,-1,-.46);
    const led=this.mesh(group,new THREE.SphereGeometry(.04,16,8),0x83ac73);led.position.set(.4,.13,.92);
    return group;
  }
  makeFrame(index) {
    const group=new THREE.Group();
    this.box(group,0,0,0,1.22,.9,.085,0xfff7e7);
    this.box(group,0,.02,.049,1.08,.69,.018,0xc0b499);
    this.box(group,0,-.37,.061,.96,.016,.016,brass);
    // A miniature street inside each tangible frame, with recessed windows.
    for(let n=0;n<3;n++){
      const x=(n-1)*.32,height=.26+((n+index)%3)*.075;
      this.box(group,x,-.2+height/2,.075,.26,height,.08,n%2?0x967d60:0xb39a76);
      this.box(group,x,-.19+height,.088,.29,.045,.12,coffee);
      for(const side of [-1,1])this.box(group,x+side*.068,-.09+height*.3,.12,.045,.07,.01,0xf0ddb6);
    }
    const box=new THREE.BoxGeometry(1.26,.94,.1),edges=new THREE.EdgesGeometry(box);box.dispose();
    const border=new THREE.LineSegments(edges,new THREE.LineBasicMaterial({color:green}));group.userData.border=border;group.add(border);
    return group;
  }
  makeCoin() {
    const group=new THREE.Group();
    const coin=this.mesh(group,new THREE.CylinderGeometry(.88,.88,.16,64),brass,.65);coin.rotation.x=Math.PI/2;
    for(const z of [-.085,.085]){const ring=this.mesh(group,new THREE.TorusGeometry(.78,.022,8,64),0xe3cca0,.65);ring.position.z=z;}
    for(let i=0;i<48;i++){const a=i*Math.PI*2/48;const ridge=this.box(group,Math.cos(a)*.873,Math.sin(a)*.873,0,.014,.035,.14,0x9b7b4d,.6);ridge.rotation.z=a;}
    const label=document.createElement('canvas');label.width=label.height=256;
    const ctx=label.getContext('2d');ctx.fillStyle='#493327';ctx.textAlign='center';ctx.textBaseline='middle';ctx.font='italic 150px Georgia';ctx.fillText('v.',128,124);ctx.font='14px sans-serif';ctx.fillText('SHARED VALUE',128,203);
    const texture=new THREE.CanvasTexture(label);texture.colorSpace=THREE.SRGBColorSpace;
    const mark=new THREE.Mesh(new THREE.PlaneGeometry(1.42,1.42),new THREE.MeshBasicMaterial({map:texture,transparent:true,depthWrite:false}));mark.position.z=.092;group.add(mark);
    return group;
  }
  resize(){const r=this.canvas.getBoundingClientRect();this.width=Math.max(1,r.width);this.height=Math.max(1,r.height);this.camera.aspect=this.width/this.height;this.camera.updateProjectionMatrix();this.renderer.setSize(this.width,this.height,false);this.draw(0);}
  setProgress(value){this.target=clamp(value,0,3);if(this.paused||this.reduced){this.progress=this.target;this.draw(0);}else this.start();}
  setAction(index,active){this.actionTargets[index]=Number(active);if(this.paused||this.reduced){this.actions[index]=Number(active);this.draw(0);}else this.start();}
  setMotion(paused,reduced){this.paused=paused;this.reduced=reduced;if(paused)this.stop();else this.start();this.progress=this.target;this.draw(0);}
  setVisible(visible){this.visible=visible;if(visible)this.start();else this.stop();}
  start(){if(this.running||this.paused||!this.visible||document.hidden)return;this.running=true;this.last=performance.now();const tick=now=>{if(!this.running)return;const dt=Math.max(0,Math.min(.05,(now-this.last)/1000));this.last=now;this.draw(dt);this.raf=requestAnimationFrame(tick);};this.raf=requestAnimationFrame(tick);}
  stop(){this.running=false;cancelAnimationFrame(this.raf);}
  pose(group,keys,p){const index=Math.min(2,Math.floor(p)),u=smooth(p-index),a=keys[index],b=keys[index+1];group.position.set(mix(a[0],b[0],u),mix(a[1],b[1],u),mix(a[2],b[2],u));group.scale.setScalar(Math.max(.001,mix(a[3],b[3],u)));group.rotation.y=mix(a[4],b[4],u);}
  draw(dt){
    this.time+=dt;const ease=1-Math.exp(-dt*8);
    if(dt){this.progress=mix(this.progress,this.target,ease);this.pointerSmooth.lerp(this.pointer,ease);this.actions=this.actions.map((v,i)=>mix(v,this.actionTargets[i],ease));}
    const p=this.progress,t=this.time,float=this.paused||this.reduced?0:Math.sin(t*.8)*.045;
    this.world.rotation.y=this.reduced?0:this.pointerSmooth.x*.13;
    this.world.rotation.x=this.reduced?0:this.pointerSmooth.y*.045;
    this.camera.position.set(mix(3.3,1.6,p/3),mix(2.1,1.2,p/3),8.7);this.camera.lookAt(0,-.05,0);
    // Preserve enough space around the whole constellation on narrow canvases.
    this.camera.position.multiplyScalar(Math.max(1,1.15/this.camera.aspect));this.camera.lookAt(0,-.05,0);
    this.pose(this.cameraModel,[[0,.15,0,1.4,-.45],[-2.5,.25,-.3,.62,-.1],[-2.6,-.6,0,.35,.15],[-2.7,-.45,0,.52,-.15]],p);
    this.cameraModel.rotation.y+=this.actions[0]*.9*Math.max(0,1-p);
    this.lens.position.z=.95+this.actions[0]*.42;
    this.cameraModel.position.y+=float;
    this.pose(this.core,[[.6,.3,0,.001,0],[.6,.3,0,.001,0],[.35,.35,0,1,0],[-.8,.85,-.3,.46,.4]],p);
    this.core.rotation.y+=t*.16;this.coreRing.rotation.z=t*.13;
    this.pose(this.coin,[[1.5,.2,0,.001,0],[1.5,.2,0,.001,0],[1.5,.2,0,.001,0],[1.4,.15,.4,1.32,-.1]],p);
    this.coin.rotation.y+=Math.sin(t*.6)*.09;this.coin.position.y+=float;
    for(let i=0;i<5;i++){
      const a=i*Math.PI*2/5+.3,spread=1+this.actions[2]*.22;
      const orbit=[.35+Math.cos(a)*1.85*spread,.35+Math.sin(a)*1.45*spread,Math.sin(a)*.25];
      this.pose(this.frames[i],[[.25,.3,.2,.001,0],[(i-2)*1.05+.3,(i%2)*.12,.3-i*.12,.8,-.15],[...orbit,.42,-.2],[-1.5+(i%2)*.48,-.65+Math.floor(i/2)*.4,-.2,.27,.15]],p);
      const selected=!this.actionTargets[1]||i===2;this.frames[i].userData.border.visible=selected&&p>.4&&p<1.7;
      if(p>.6&&p<1.6){this.frames[i].position.y+=this.actions[1]*(i===2?.35:-.12);this.frames[i].scale.multiplyScalar(1-this.actions[1]*(i===2?-.15:.25));}
      const positions=this.links[i].geometry.attributes.position;positions.setXYZ(0,...this.core.position.toArray());positions.setXYZ(1,...this.frames[i].position.toArray());positions.needsUpdate=true;this.links[i].geometry.computeBoundingSphere();
      this.links[i].material.opacity=Math.max(0,1-Math.abs(p-2))*(.28+this.actions[2]*.4);
    }
    this.loop.material.opacity=smooth(clamp(p-2,0,1))*(.25+this.actions[3]*.35);
    this.flowTime+=dt*(.11+this.actions[3]*.07);
    this.packets.forEach((packet,i)=>{
      const u=(this.flowTime+i/24)%1;
      const capture=this.cameraModel.position.clone().add(new THREE.Vector3(.2,.2,.8)).lerp(this.frames[2].position,u);capture.y+=Math.sin(u*Math.PI)*.35;
      const network=this.frames[i%5].position.clone().lerp(this.core.position,u);
      packet.position.copy(capture).lerp(network,smooth(clamp((p-1.15)/.65,0,1))).lerp(this.loopCurve.getPoint(u),smooth(clamp((p-2.2)/.65,0,1)));
      packet.visible=p>.15;
      packet.scale.setScalar(Math.sin(u*Math.PI)*(.6+this.actions[3]));
    });
    this.renderer.render(this.scene,this.camera);
  }
  dispose(){this.stop();const geometries=new Set(),materials=new Set();this.scene.traverse(o=>{if(o.geometry)geometries.add(o.geometry);if(o.material)materials.add(o.material);});geometries.forEach(g=>g.dispose());materials.forEach(m=>{m.map?.dispose();m.dispose();});this.renderer.dispose();}
}

export function mountJourney(root,reducedMotion) {
  const section=document.querySelector('#how-it-works'),layout=section.querySelector('.journey-layout'),visual=section.querySelector('.journey-visual'),canvas=document.querySelector('#journey-canvas');
  const steps=[...section.querySelectorAll('[data-journey-step]')],motion=document.querySelector('#journey-motion');
  const labels=['A PERSPECTIVE ONLY YOU HAVE','A MOMENT YOU CHOOSE','PERSPECTIVES BECOME KNOWLEDGE','VALUE COMES BACK TO YOU'];
  let scene=null,failed=false,inView=false,current=-1,scheduled=false,paused=reducedMotion.matches;
  function update(){
    scheduled=false;if(root.hidden)return;
    const bounds=visual.getBoundingClientRect();inView=bounds.bottom>0&&bounds.top<innerHeight&&layout.getBoundingClientRect().bottom>0;
    if(inView&&!scene&&!failed){try{scene=new JourneyScene(canvas);scene.setMotion(paused,reducedMotion.matches);visual.dataset.state='ready';}catch(error){failed=true;visual.dataset.state='error';visual.querySelector('.journey-fallback').hidden=false;motion.disabled=true;section.querySelectorAll('[data-journey-action]').forEach(button=>button.disabled=true);console.warn('Visn journey: 3D unavailable',error);}}
    const mobile=innerWidth<=760;
    const centers=steps.map(step=>step.getBoundingClientRect().top+(mobile?0:step.offsetHeight/2));
    const focus=mobile?0:innerHeight/2;
    let progress=0;
    for(let i=0;i<3;i++)if(centers[i]<=focus)progress=i+clamp((focus-centers[i])/(centers[i+1]-centers[i]),0,1);
    const index=Math.round(progress);
    if(index!==current){current=index;visual.dataset.chapter=String(index);steps.forEach((step,i)=>step.classList.toggle('is-current',i===index));section.querySelectorAll('.journey-nav button').forEach(button=>{if(Number(button.dataset.chapter)===index)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});document.querySelector('#journey-object-label').textContent=`0${index+1} / ${labels[index]}`;canvas.setAttribute('aria-label',`3D chapter ${index+1}: ${labels[index].toLowerCase()}.`);}
    section.style.setProperty('--journey-progress',String((progress+1)/4));
    if(scene&&!failed){scene.setVisible(inView&&!root.hidden);if(inView)scene.setProgress(progress);}
  }
  function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(update);}}
  function goTo(index,focusHeading=true){const target=steps[index];const offset=innerWidth<=760?0:(innerHeight-target.offsetHeight)/2;if(focusHeading){const heading=target.querySelector('h3');heading.tabIndex=-1;heading.focus({preventScroll:true});}root.scrollTo({top:root.scrollTop+target.getBoundingClientRect().top-root.getBoundingClientRect().top-offset,behavior:reducedMotion.matches?'instant':'smooth'});}
  section.querySelectorAll('[data-chapter]').forEach(button=>button.addEventListener('click',()=>goTo(Number(button.dataset.chapter),!button.closest('.journey-nav'))));
  section.querySelectorAll('[data-journey-action]').forEach(button=>button.addEventListener('click',()=>{const index=Number(button.dataset.journeyAction),active=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(active));scene?.setAction(index,active);}));
  function syncMotion(){motion.setAttribute('aria-pressed',String(paused));motion.textContent=paused?'Play motion ▶':'Pause motion Ⅱ';document.querySelector('#journey-interaction-hint').textContent=reducedMotion.matches?'Choose a chapter to explore':paused?'Motion paused · Scroll to explore':'Move your pointer to explore';scene?.setMotion(paused,reducedMotion.matches);}
  motion.addEventListener('click',()=>{paused=!paused;syncMotion();});
  canvas.addEventListener('pointermove',event=>{if(!scene||reducedMotion.matches)return;const r=canvas.getBoundingClientRect();scene.pointer.set((event.clientX-r.left)/r.width*2-1,(event.clientY-r.top)/r.height*2-1);});
  canvas.addEventListener('pointerleave',()=>scene?.pointer.set(0,0));
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();scene?.stop();failed=true;visual.dataset.state='error';visual.querySelector('.journey-fallback').hidden=false;});
  root.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('resize',()=>{scene?.resize();schedule();});
  window.addEventListener('hashchange',()=>{scene?.setVisible(!root.hidden&&inView);schedule();});
  document.addEventListener('visibilitychange',()=>scene?.setVisible(!document.hidden&&!root.hidden&&inView));
  reducedMotion.addEventListener('change',()=>{paused=reducedMotion.matches;syncMotion();schedule();});
  window.addEventListener('pagehide',()=>scene?.stop());window.addEventListener('pageshow',schedule);
  syncMotion();schedule();return {goTo};
}

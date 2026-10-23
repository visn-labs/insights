import {motion} from './motion.js';
import {transition} from './recording-state.js';
import {REVEAL_SECONDS,revealState,outwardNode} from './logo-reveal-state.js';
// Original shader implementation, informed by the reference's polar/inverse-
// radius construction. Positive elapsed time moves heads toward one fixed origin.
const fragment=`precision highp float;
uniform vec2 resolution;
uniform float time,energy,cream;
void main(){
 vec2 uv=(gl_FragCoord.xy-.5*resolution)/min(resolution.x,resolution.y);
 float radius=length(uv);
 // One inverse-depth coordinate shared by every particle. Subtracting time
 // increases the visible feature's depth and contracts its projected radius.
 vec2 tunnel=vec2(atan(uv.y,uv.x)*5./6.2831853,.075/max(tan(radius),.0001)-time*.55);
 vec3 light=vec3(0.);
 for(int i=0;i<18;i++){
  float id=float(i)+1.;
  float seed=fract(sin(id*73.13+2.7)*43758.5453);
  vec2 anchor=vec2(seed,fract(seed*37.71));
  vec2 delta=fract(tunnel-anchor+.5)-.5;
  float distanceSquared=dot(delta,delta);
  vec3 tint=mix(vec3(.17,.68,.43),vec3(.85,.67,.35),step(.77,seed));
  float core=.00024/(distanceSquared+.000025);
  float halo=.00050/(distanceSquared+.004);
  light+=tint*(core+halo);
 }
 vec3 beams=vec3(1.)-exp(-light*1.6);
 beams*=smoothstep(.003,.018,radius);
 vec3 dark=mix(vec3(.042,.047,.036),vec3(.075,.086,.064),exp(-radius*3.));
 vec3 paper=vec3(.961,.929,.875),warm=vec3(.28,.235,.175);
 vec3 colour=mix(mix(dark,warm,smoothstep(0.,.5,cream)),paper,smoothstep(.18,1.,cream));
 colour+=beams*energy*(1.-cream*.9);
 colour+=vec3(.08,.2,.12)*exp(-radius*radius*150.)*energy;
 gl_FragColor=vec4(colour,1.);
}`;

function createTunnel(canvas){
 const gl=canvas.getContext('webgl',{alpha:false,antialias:false,powerPreference:'high-performance'});
 if(!gl)return null;
 const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){const message=gl.getShaderInfoLog(shader);gl.deleteShader(shader);throw Error(message);}return shader;};
 const vertex=compile(gl.VERTEX_SHADER,'attribute vec2 position;void main(){gl_Position=vec4(position,0.,1.);}');
 const pixel=compile(gl.FRAGMENT_SHADER,fragment),program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,pixel);gl.linkProgram(program);
 if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
 const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
 gl.useProgram(program);const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
 const uniforms=Object.fromEntries(['resolution','time','energy','cream'].map(name=>[name,gl.getUniformLocation(program,name)]));
 return {draw(time,state){
   const ratio=Math.min(devicePixelRatio||1,1.5),width=Math.round(canvas.clientWidth*ratio),height=Math.round(canvas.clientHeight*ratio);
   if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;gl.viewport(0,0,width,height);}
   gl.uniform2f(uniforms.resolution,width,height);gl.uniform1f(uniforms.time,time);gl.uniform1f(uniforms.energy,state.gather);gl.uniform1f(uniforms.cream,state.cream);gl.drawArrays(gl.TRIANGLES,0,3);
  },dispose(){gl.deleteBuffer(buffer);gl.deleteProgram(program);gl.deleteShader(vertex);gl.deleteShader(pixel);}};
}

export function mountLogoReveal({dialog,svg,mark,poseLogo}){
 const canvas=document.createElement('canvas');canvas.className='logo-tunnel';canvas.setAttribute('aria-hidden','true');dialog.prepend(canvas);
 const ns='http://www.w3.org/2000/svg',defs=svg.querySelector('defs');
 const clip=document.createElementNS(ns,'clipPath');clip.id='reveal-path-growth';clip.setAttribute('clipPathUnits','userSpaceOnUse');
 const circle=document.createElementNS(ns,'circle');circle.setAttribute('r','0');clip.append(circle);defs.append(clip);
 const iris=mark.blades[0].parentElement,originalClip=iris.getAttribute('clip-path');
 const close=document.querySelector('#logo-close'),replay=document.querySelector('#logo-play');
 const captions=[...dialog.querySelectorAll(':scope > p,:scope > h2')];
 const eligible=()=> (!location.hash||location.hash==='#/'||location.hash==='#')&&scrollY<50&&motion.enabled;
 let initialPending=eligible();
 let frame=0,time=0,previous=0,renderer=null,intro=false,closing=false;
 const notify=()=>window.dispatchEvent(new CustomEvent('visn:logo-reveal',{detail:{active:dialog.open}}));
 function pose(t){
  const state=revealState(t);poseLogo(mark,state.blades,state.hex,1,Math.max(.001,state.core));
  iris.removeAttribute('clip-path');
  mark.blades.forEach((blade,i)=>{
   const travel=(1-state.blades)*1450;
   blade.setAttribute('transform',`rotate(${i*60}) translate(0 ${-travel}) rotate(${-64*(1-state.blades)} 58 -558)`);
   blade.style.opacity=String(transition(state.blades,0,.16));
  });
  circle.setAttribute('r',String(Math.max(182,outwardNode(state.nodes).radius)));
  mark.paths.forEach(path=>{path.setAttribute('clip-path','url(#reveal-path-growth)');path.style.opacity=String(transition(state.pathways,0,.12));});
  mark.outer.forEach((node,i)=>{
   const point=outwardNode(state.nodes,i);
   node.setAttribute('transform',`translate(${point.x} ${point.y}) scale(${.35+.65*state.nodes})`);node.style.opacity=String(transition(state.nodes,0,.13));
  });
  mark.hex.style.opacity=String(state.hex);
  // Inner network nodes also travel out from the same source, along their spokes.
  mark.hex.querySelectorAll('circle').forEach(node=>{const x=+node.getAttribute('cx'),y=+node.getAttribute('cy');node.setAttribute('transform',`translate(${(state.hex-1)*x} ${(state.hex-1)*y})`);});
  svg.style.opacity=String(transition(t,.65,1.2));
  captions.forEach(element=>{element.style.opacity=state.caption;});
  dialog.dataset.revealPhase=t<1.65?'gather':t<3.5?'aperture':t<5.1?'network':'complete';
  dialog.dataset.palette=state.cream>.5?'paper':'dark';dialog.dataset.revealTime=t.toFixed(3);
  renderer?.draw(t,state);
 }
 function restore(){
  iris.setAttribute('clip-path',originalClip);mark.paths.forEach(path=>path.removeAttribute('clip-path'));
  mark.hex.querySelectorAll('circle').forEach(node=>node.removeAttribute('transform'));
  poseLogo(mark,1,1,1);svg.style.opacity='1';captions.forEach(element=>{element.style.opacity='1';});
 }
 function stop(){cancelAnimationFrame(frame);frame=0;renderer?.dispose();renderer=null;restore();}
 function finish(){
  stop();dialog.dataset.revealPhase='complete';dialog.dataset.palette='paper';dialog.dataset.revealTime=String(REVEAL_SECONDS);
  if(intro&&dialog.open){closing=true;dialog.close();}notify();
 }
 function play({automatic=false}={}){
  stop();intro=automatic;closing=false;time=0;previous=0;
  dialog.classList.add('logo-reveal-dialog');dialog.dataset.intro=String(intro);
  close.textContent=intro?'Skip intro':'×';close.setAttribute('aria-label',intro?'Skip logo intro':'Close logo exploration');
  if(!dialog.open)dialog.showModal();notify();
  if(!motion.enabled){finish();return;}
  try{renderer=createTunnel(canvas);}catch{renderer=null;}
  dialog.dataset.shader=renderer?'ready':'fallback';
  if(!renderer){finish();return;}
  pose(0);
  function tick(now){
   if(!dialog.open)return;
   time+=previous?Math.min((now-previous)/1000,.05):0;previous=now;pose(time);
   if(time>=REVEAL_SECONDS){finish();return;}frame=requestAnimationFrame(tick);
  }
  frame=requestAnimationFrame(tick);
 }
 close.addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{stop();notify();});
 replay.addEventListener('click',()=>play());
 motion.subscribe(enabled=>{if(!enabled&&dialog.open&&!closing)finish();});
 document.querySelector('#logo-replay').addEventListener('click',()=>play());
 window.addEventListener('pagehide',stop);
 canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();finish();});
 // The original SVG remains the no-JS/reduced-motion result; this never blocks
 // contributor deep links. Scrolling into the story can dismiss an initial reveal.
 requestAnimationFrame(()=>{const automatic=initialPending&&eligible();initialPending=false;if(automatic)play({automatic:true});else notify();});
 return {play,get pending(){return initialPending;},finishIntro(){if(initialPending){initialPending=false;notify();}if(intro&&dialog.open)finish();}};
}

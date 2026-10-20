import {motion,settle} from './motion.js';
const NS='http://www.w3.org/2000/svg';
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const ease=v=>{v=clamp(v);return v*v*(3-2*v);};
const lerp=(a,b,t)=>a+(b-a)*t;
const phase=(t,start,end)=>ease((t-start)/(end-start));
const point=(angle,r)=>[Math.cos(angle)*r,Math.sin(angle)*r];
function element(tag,attributes={},parent){const node=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,value);parent?.append(node);return node;}

// Each shape is independently movable. The sixfold silhouette follows the supplied logo.
function logo(parent,id){
  const defs=element('defs',{},parent);
  const green=element('radialGradient',{id:`${id}-green`,cx:'30%',cy:'25%',r:'85%'},defs);
  [['0%','#94b99b'],['45%','#398b61'],['100%','#096544']].forEach(([offset,color])=>element('stop',{offset,'stop-color':color},green));
  const brown=element('linearGradient',{id:`${id}-brown`,x1:'0',y1:'0',x2:'1',y2:'1'},defs);
  [['0%','#846047'],['45%','#513523'],['100%','#6b4934']].forEach(([offset,color])=>element('stop',{offset,'stop-color':color},brown));
  const mint=element('linearGradient',{id:`${id}-mint`,x1:'0',y1:'1',x2:'0',y2:'0'},defs);
  [['0%','#167249'],['100%','#c2e0c7']].forEach(([offset,color])=>element('stop',{offset,'stop-color':color},mint));
  const rim=element('clipPath',{id:`${id}-rim`},defs);element('circle',{r:578},rim);
  const group=element('g',{'data-logo':'true'},parent),blades=[],paths=[],outer=[];
  const iris=element('g',{'clip-path':`url(#${id}-rim)`},group);
  for(let i=0;i<6;i++){
    const blade=element('g',{'data-blade':i},iris);
    element('path',{d:'M58 -558 C220 -545 380 -480 452 -316 C400 -200 285 -80 187 -52 C172 -250 136 -402 58 -558Z',fill:`url(#${id}-brown)`,stroke:'#fff7e5','stroke-width':2},blade);blades.push(blade);
    const route=element('g',{transform:`rotate(${i*60})`},group);
    const path=element('path',{d:'M75 -193 C115 -310 66 -469 14 -564 C89 -425 131 -280 102 -187Z',fill:`url(#${id}-mint)`, 'data-pathway':i},route);paths.push(path);
    const sphere=element('g',{'data-outer-node':i},group);element('circle',{r:33,fill:`url(#${id}-green)`},sphere);outer.push(sphere);
  }
  const hex=element('g',{'data-hexagon':'true',fill:`url(#${id}-brown)`,stroke:'#63432d','stroke-width':9,'stroke-linecap':'round'},group);
  const vertices=Array.from({length:6},(_,i)=>point((i*60-60)*Math.PI/180,184));
  const line=element('path',{d:vertices.map(([x,y],i)=>`${i?'L':'M'}${x} ${y}`).join(' ')+'Z '+vertices.map(([x,y])=>`M0 0L${x} ${y}`).join(' '),fill:'none',pathLength:1,'stroke-dasharray':1},hex);
  vertices.forEach(([cx,cy])=>element('circle',{cx,cy,r:29,stroke:'#fff6e6','stroke-width':5},hex));
  const core=element('g',{'data-core':'true'},group);
  [70,60,50,40,30,21].forEach((r,i)=>element('circle',{r,fill:['#94b99b','#7cae8a','#639f79','#468e69','#30865e','#15764f'][i]},core));
  return {group,blades,paths,outer,hex,line,core};
}
function poseLogo(mark,closure,hexGrowth,pathGrowth,coreScale=1){
  mark.blades.forEach((blade,i)=>{
    // All six leaves turn on fixed rim pivots, like a geared camera iris.
    // The inner tips sweep inward together; the outer housing never flies around.
    blade.setAttribute('transform',`rotate(${i*60}) rotate(${-42*(1-closure)} 58 -558)`);
    blade.style.opacity=String(phase(closure,0,.12));
  });
  mark.hex.style.opacity=String(hexGrowth);mark.line.style.strokeDashoffset=String(1-hexGrowth);
  mark.paths.forEach(path=>{path.style.opacity=String(pathGrowth);path.setAttribute('transform',`translate(92 -159) scale(${Math.max(.001,pathGrowth)}) translate(-92 159)`);});
  mark.outer.forEach((sphere,i)=>{const [x,y]=point((i*60-90)*Math.PI/180,602+(1-pathGrowth)*800);sphere.setAttribute('transform',`translate(${x} ${y})`);sphere.style.opacity=String(pathGrowth);});
  mark.core.setAttribute('transform',`scale(${Math.max(.001,coreScale)})`);
}

export function mountJourney(){
  const experience=document.querySelector('#camera-film-experience');
  let progress=0;
  function update(){
    const top=experience.getBoundingClientRect().top+scrollY;
    progress=clamp((scrollY-top)/innerHeight,0,4);
    const zoom=phase(progress,.08,.95),reel=phase(progress,2.05,2.95);
    experience.style.setProperty('--camera-zoom',zoom);
    experience.style.setProperty('--reel-zoom',reel);
    experience.dataset.phase=reel>.99?'reel':zoom>.99?'camera':'street';
    document.querySelectorAll('[data-recording-chapter]').forEach(button=>{const active=+button.dataset.recordingChapter===(reel>.5?1:0);if(active)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
    window.dispatchEvent(new CustomEvent('visn:recording-progress',{detail:progress}));
  }
  function goTo(index){const top=experience.getBoundingClientRect().top+scrollY;window.scrollTo({top:top+innerHeight*(index===0?1.02:2.98),behavior:motion.enabled?'smooth':'instant'});}
  document.querySelectorAll('[data-recording-chapter]').forEach(button=>button.addEventListener('click',()=>goTo(+button.dataset.recordingChapter)));
  window.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update);
  document.querySelectorAll('.landing-brand,.mini-mark').forEach((brand,i)=>{const svg=element('svg',{viewBox:'-700 -700 1400 1400','aria-hidden':'true',class:'visn-brand-icon'});poseLogo(logo(svg,`signature-${i}`),1,1,1);brand.prepend(svg);});
  const dialog=document.querySelector('#logo-dialog'),svg=document.querySelector('#logo-exploration');poseLogo(logo(svg,'exploration'),1,1,1);
  document.querySelector('#logo-replay').addEventListener('click',()=>dialog.showModal());document.querySelector('#logo-close').addEventListener('click',()=>dialog.close());document.querySelector('#logo-play').addEventListener('click',()=>{if(motion.enabled)svg.animate([{transform:'rotate(-30deg) scale(.8)'},{transform:'rotate(0) scale(1)'}],{duration:1000,easing:'cubic-bezier(.2,.8,.2,1)'});});
  update();return {goTo,get progress(){return progress;}};
}

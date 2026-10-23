import {motion,settle} from './motion.js';
import {recordingState} from './recording-state.js';
import {mountLogoReveal} from './logo-reveal.js';
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
  const viewport=experience.querySelector('.cinema-sticky');
  const nav=experience.querySelector('.recording-nav');
  const wash=experience.querySelector('.hero-wash');
  const buttons=[...experience.querySelectorAll('[data-recording-chapter]')];
  const copies=[...experience.querySelectorAll('.camera-story-copy')];
  const navButtons=[...nav.querySelectorAll('button')];
  const indicator=document.createElement('span');indicator.className='recording-position';indicator.setAttribute('aria-hidden','true');nav.prepend(indicator);
  const track=document.createElement('span');track.className='recording-track';track.setAttribute('aria-hidden','true');nav.append(track);
  const fill=document.createElement('span');track.append(fill);
  let progress=0,scheduled=0,top=0,height=innerHeight,lastChapter=-1,reveal;
  function measure(){
    if(!viewport.clientHeight)return;
    top=experience.getBoundingClientRect().top+scrollY;
    height=viewport.clientHeight;
  }
  function update(){
    scheduled=0;
    progress=clamp((scrollY-top)/height,0,4);
    const state=recordingState(progress);
    if(progress>.08)reveal?.finishIntro();
    wash.style.opacity=1-state.zoom;
    experience.dataset.phase=state.phase;
    fill.style.transform=`scaleX(${state.journey})`;
    if(lastChapter!==state.chapter){
      lastChapter=state.chapter;
      indicator.style.transform=`translateX(${state.chapter*100}%)`;
      navButtons.forEach(button=>{if(+button.dataset.recordingChapter===state.chapter)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
    }
    copies.forEach((copy,index)=>{
      const visibility=motion.enabled?(index?state.reelCopy:state.cameraCopy):1;
      copy.style.opacity=visibility;
      copy.style.transform=`translateY(${(1-visibility)*14}px)`;
      // Hidden chapter actions cannot receive an invisible keyboard focus.
      copy.inert=visibility<.02;
    });
    window.dispatchEvent(new CustomEvent('visn:recording-progress',{detail:progress}));
  }
  function schedule(){if(!scheduled)scheduled=requestAnimationFrame(update);}
  function goTo(index,{keyboard=false}={}){
    measure();
    window.scrollTo({top:top+height*(index===0?1.02:2.98),behavior:motion.enabled&&!keyboard?'smooth':'instant'});
    if(keyboard){update();const heading=experience.querySelector(index===0?'#camera-chapter-title':'#reel-chapter-title');heading?.setAttribute('tabindex','-1');heading?.focus({preventScroll:true});}
  }
  buttons.forEach(button=>button.addEventListener('click',event=>goTo(+button.dataset.recordingChapter,{keyboard:event.detail===0})));
  window.addEventListener('scroll',schedule,{passive:true});
  window.addEventListener('resize',()=>{measure();schedule();});
  document.fonts?.ready.then(()=>{measure();schedule();});
  const resize=new ResizeObserver(()=>{measure();schedule();});resize.observe(viewport);
  motion.subscribe(schedule);
  measure();
  document.querySelectorAll('.landing-brand,.mini-mark').forEach((brand,i)=>{const svg=element('svg',{viewBox:'-700 -700 1400 1400','aria-hidden':'true',class:'visn-brand-icon'});poseLogo(logo(svg,`signature-${i}`),1,1,1);brand.prepend(svg);});
  const dialog=document.querySelector('#logo-dialog'),svg=document.querySelector('#logo-exploration');
  const mark=logo(svg,'exploration');poseLogo(mark,1,1,1);
  reveal=mountLogoReveal({dialog,svg,mark,poseLogo});
  update();return {goTo,get progress(){return progress;},get introPending(){return reveal?.pending||false;}};
}

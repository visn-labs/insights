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
  const group=element('g',{'data-logo':'true'},parent),blades=[],paths=[],outer=[];
  for(let i=0;i<6;i++){
    const blade=element('g',{'data-blade':i},group);
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
    const angle=(i*60-60)*Math.PI/180,distance=(1-closure)*1050;
    blade.setAttribute('transform',`translate(${Math.cos(angle)*distance} ${Math.sin(angle)*distance}) rotate(${i*60+(1-closure)*115})`);
    blade.style.opacity=String(phase(closure,0,.3));
  });
  mark.hex.style.opacity=String(hexGrowth);mark.line.style.strokeDashoffset=String(1-hexGrowth);
  mark.paths.forEach(path=>{path.style.opacity=String(pathGrowth);path.setAttribute('transform',`translate(92 -159) scale(${Math.max(.001,pathGrowth)}) translate(-92 159)`);});
  mark.outer.forEach((sphere,i)=>{const [x,y]=point((i*60-90)*Math.PI/180,602+(1-pathGrowth)*800);sphere.setAttribute('transform',`translate(${x} ${y})`);sphere.style.opacity=String(pathGrowth);});
  mark.core.setAttribute('transform',`scale(${Math.max(.001,coreScale)})`);
}

export function mountJourney(root,reducedMotion){
  const section=document.querySelector('#how-it-works'),steps=[...section.querySelectorAll('[data-journey-step]')];
  const visual=section.querySelector('.journey-visual'),motion=document.querySelector('#journey-motion');
  const background=document.createElement('div');background.className='logo-universe';background.setAttribute('aria-hidden','true');root.prepend(background);
  const svg=element('svg',{preserveAspectRatio:'xMidYMid meet'},background);
  // Stable seeded positions and edge order make reverse scrolling retrace the same story.
  let seed=712;const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
  const network=element('g',{},svg),edges=element('g',{stroke:'#398562','stroke-width':1},network);
  const mark=logo(svg,'visn-story'),nodesLayer=element('g',{},network);
  const nodes=Array.from({length:32},(_,i)=>({x:random()-.5,y:random()-.5,z:random(),phase:random()*Math.PI*2,el:element('circle',{r:8+i%4,fill:'url(#visn-story-green)'},nodesLayer)}));
  const connections=[];for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++)connections.push({a:i,b:j,order:random(),el:element('line',{},edges)});
  connections.sort((a,b)=>a.order-b.order);
  document.querySelectorAll('.landing-brand').forEach((brand,i)=>{const icon=element('svg',{viewBox:'-700 -700 1400 1400','aria-hidden':'true',class:'visn-brand-icon'});const small=logo(icon,`visn-brand-${i}`);poseLogo(small,1,1,1);brand.prepend(icon);});
  const controls=document.createElement('div');controls.className='logo-opening-controls';controls.innerHTML='<span>VISN / MANY PERSPECTIVES. ONE PICTURE.</span><button type="button" id="logo-skip">Skip introduction ↗</button>';root.append(controls);
  const replay=document.createElement('button');replay.type='button';replay.className='logo-replay';replay.textContent='Replay logo ↻';document.querySelector('.intro-actions').after(replay);
  let width=innerWidth,height=innerHeight,time=0,introTime=0,opening=!reducedMotion.matches&&!root.hidden,paused=reducedMotion.matches,progress=0,target=0,raf=0,last=0,active=-1,inStory=false;
  let pointer={x:0,y:0,active:false},pulse=0;
  function resize(){width=innerWidth;height=innerHeight;svg.setAttribute('viewBox',`${-width/2} ${-height/2} ${width} ${height}`);update();draw();}
  function finish(){opening=false;introTime=9;root.classList.remove('logo-opening');controls.hidden=true;draw();}
  function replayOpening(){root.scrollTo({top:0,behavior:'instant'});introTime=0;opening=!reducedMotion.matches;paused=reducedMotion.matches;syncMotion();draw();start();}
  function update(){
    if(root.hidden){stop();return;}
    const centers=steps.map(step=>step.getBoundingClientRect().top+Math.min(step.offsetHeight,height)/2);
    target=0;for(let i=0;i<3;i++)if(centers[i]<=height/2)target=i+clamp((height/2-centers[i])/(centers[i+1]-centers[i]));
    const index=Math.round(target);visual.dataset.chapter=String(index);
    if(active!==index){active=index;steps.forEach((step,i)=>step.classList.toggle('is-current',i===index));section.querySelectorAll('.journey-nav button').forEach(button=>{if(+button.dataset.chapter===index)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});document.querySelector('#journey-object-label').textContent=['01 / EVERY PERSPECTIVE BEGINS SOMEWHERE','02 / YOUR MOMENT JOINS THE PICTURE','03 / CONNECTIONS BECOME KNOWLEDGE','04 / MANY PERSPECTIVES. SHARED VALUE.'][index];}
    section.style.setProperty('--journey-progress',String((target+1)/4));
    if(paused||reducedMotion.matches)progress=target;
    draw();start();
  }
  function draw(){
    root.classList.toggle('logo-opening',opening);controls.hidden=!opening;
    const bounds=section.getBoundingClientRect();inStory=bounds.top<height&&bounds.bottom>0;
    background.style.opacity=opening?'1':inStory?'.32':'0';
    const scale=Math.min(width*.7,height*.72)/1400;
    const tilt=pointer.active&&!paused&&!reducedMotion.matches?pointer.x/width*4:0;
    mark.group.setAttribute('transform',`rotate(${tilt}) scale(${scale*(1+pulse*.035)})`);
    let spread=1,density=0,closure=0,hexGrowth=0,pathGrowth=0,coreScale=0,networkOpacity=1;
    if(opening){
      const t=introTime,burst=phase(t,.45,2.25),collapse=phase(t,3.1,4.6);
      spread=burst*(1-collapse)*.83;density=phase(t,.5,1.6);closure=phase(t,2.85,4.6);
      hexGrowth=phase(t,4.8,6.2);pathGrowth=phase(t,6.15,7.7);coreScale=1-burst+collapse;
      networkOpacity=burst*(1-phase(t,4.15,4.65));
      background.dataset.phase=t<.45?'core':t<2.85?'explode':t<4.8?'collapse':t<6.15?'hexagon':t<7.7?'pathways':'assembled';
      if(t>8.2)background.style.opacity=String(1-phase(t,8.2,9));
    }else{
      const p=progress/3;
      closure=phase(p,.76,.94);hexGrowth=phase(p,.9,.97);pathGrowth=phase(p,.94,1);
      spread=lerp(1,.23,phase(p,0,.76))*(1-phase(p,.8,.95));density=phase(p,.04,.7);
      networkOpacity=1-phase(p,.85,.95);coreScale=phase(p,.82,.95);
      background.dataset.phase=p<.25?'scatter':p<.76?'connect':p<.95?'compress':'assembled';
      if(reducedMotion.matches){closure=hexGrowth=pathGrowth=coreScale=1;networkOpacity=0;}
    }
    poseLogo(mark,closure,hexGrowth,pathGrowth,coreScale);
    network.style.opacity=String(networkOpacity);background.dataset.edges=String(Math.ceil(density*connections.length));background.dataset.spread=spread.toFixed(3);background.dataset.progress=progress.toFixed(3);
    if(networkOpacity<=0)return;
    const positions=nodes.map((node,i)=>{
      const drift=paused||reducedMotion.matches?0:Math.sin(time*.45+node.phase)*16;
      let x=node.x*width*1.7*spread+drift*spread,y=node.y*height*1.6*spread+Math.cos(time*.35+node.phase)*12*spread;
      if(pointer.active&&!paused&&!reducedMotion.matches){const dx=x-pointer.x,dy=y-pointer.y,d=Math.hypot(dx,dy)||1,force=Math.max(0,1-d/180)*38*spread;x+=dx/d*force;y+=dy/d*force;}
      x+=Math.cos(node.phase)*pulse*30*spread;y+=Math.sin(node.phase)*pulse*30*spread;
      node.el.setAttribute('cx',x);node.el.setAttribute('cy',y);node.el.setAttribute('r',(8+i%4)*lerp(.55,1,node.z));return [x,y];
    });
    connections.forEach((edge,i)=>{const opacity=clamp(density*connections.length-i,0,1)*.24;edge.el.style.opacity=String(opacity);if(!opacity)return;const a=positions[edge.a],b=positions[edge.b];edge.el.setAttribute('x1',a[0]);edge.el.setAttribute('y1',a[1]);edge.el.setAttribute('x2',b[0]);edge.el.setAttribute('y2',b[1]);});
  }
  function tick(now){raf=0;const dt=Math.max(0,Math.min(.05,(now-last)/1000));last=now;
    if(!paused){time+=dt;if(opening)introTime+=dt;progress=lerp(progress,target,1-Math.exp(-dt*9));pulse*=Math.exp(-dt*3);}
    if(introTime>=9&&opening)finish();draw();if(canAnimate())raf=requestAnimationFrame(tick);
  }
  function canAnimate(){return !root.hidden&&!document.hidden&&!paused&&(opening||inStory);}
  function start(){if(!raf&&canAnimate()){last=performance.now();raf=requestAnimationFrame(tick);}}
  function stop(){cancelAnimationFrame(raf);raf=0;}
  function syncMotion(){motion.setAttribute('aria-pressed',String(paused));motion.textContent=paused?'Play motion ▶':'Pause motion Ⅱ';document.querySelector('#journey-interaction-hint').textContent=paused?'Still frame · scroll to explore':'Move among the perspectives';if(paused)stop();else start();}
  function goTo(index,focusHeading=true){finish();const step=steps[index];if(focusHeading){const h=step.querySelector('h3');h.tabIndex=-1;h.focus({preventScroll:true});}root.scrollTo({top:root.scrollTop+step.getBoundingClientRect().top-root.getBoundingClientRect().top,behavior:reducedMotion.matches?'instant':'smooth'});}
  section.querySelectorAll('[data-chapter]').forEach(button=>button.addEventListener('click',()=>goTo(+button.dataset.chapter,!button.closest('.journey-nav'))));
  section.querySelectorAll('[data-journey-action]').forEach(button=>button.addEventListener('click',()=>{pulse=1;pointer.active=false;draw();}));
  motion.addEventListener('click',()=>{paused=!paused;syncMotion();draw();});
  controls.querySelector('button').addEventListener('click',finish);replay.addEventListener('click',replayOpening);
  root.addEventListener('pointermove',event=>{pointer={x:event.clientX-width/2,y:event.clientY-height/2,active:true};},{passive:true});
  root.addEventListener('pointerleave',()=>{pointer.active=false;});
  root.addEventListener('scroll',()=>{if(opening&&root.scrollTop>30)finish();update();},{passive:true});
  root.addEventListener('keydown',event=>{if((event.key==='Escape'||event.key==='Tab')&&opening)finish();});
  window.addEventListener('resize',resize);window.addEventListener('hashchange',()=>{finish();update();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else update();});
  reducedMotion.addEventListener('change',()=>{paused=reducedMotion.matches;finish();syncMotion();update();});
  window.addEventListener('pagehide',stop);window.addEventListener('pageshow',update);
  visual.dataset.state='ready';resize();syncMotion();start();return {goTo};
}

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

export function mountJourney(root,reducedMotion){
  const section=document.querySelector('#how-it-works'),steps=[...section.querySelectorAll('[data-journey-step]')];
  const visual=section.querySelector('.journey-visual'),motion=document.querySelector('#journey-motion');
  const background=document.createElement('div');background.className='logo-universe';background.setAttribute('aria-hidden','true');root.prepend(background);
  const svg=element('svg',{preserveAspectRatio:'xMidYMid meet'},background);
  // Stable seeded positions and edge order make reverse scrolling retrace the same story.
  let seed=712;const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
  const network=element('g',{'data-network':'true'},svg),edges=element('g',{stroke:'#398562','stroke-width':.8,'stroke-linecap':'round'},network);
  const mark=logo(svg,'visn-story'),nodesLayer=element('g',{},network);
  // Jittered cells fill the viewport evenly without clumps or a visible grid.
  const nodes=Array.from({length:84},(_,i)=>({
    x:((i%12)+.05+random()*.9)/12-.5,y:(Math.floor(i/12)+.05+random()*.9)/7-.5,
    z:random(),phase:random()*Math.PI*2,offsetX:0,offsetY:0,
    el:element('circle',{'data-floating-node':i,fill:'url(#visn-story-green)'},nodesLayer)
  }));
  // Map the same neighbors into a disk as they gather; topology stays readable.
  nodes.forEach(node=>{const x=node.x*2,y=node.y*2,dx=x*Math.sqrt(1-y*y/2),dy=y*Math.sqrt(1-x*x/2);node.orbit=Math.atan2(dy,dx);node.orbitRadius=Math.hypot(dx,dy);});
  // A connected local backbone, then a few short branches: never an all-to-all web.
  const connections=[],degree=nodes.map(()=>0),connected=new Set([0]);
  const distance=(a,b)=>Math.hypot((nodes[a].x-nodes[b].x)*1.5,nodes[a].y-nodes[b].y);
  function connect(a,b){degree[a]++;degree[b]++;connections.push({a,b,order:(Math.hypot(nodes[a].x,nodes[a].y)+Math.hypot(nodes[b].x,nodes[b].y))/2+random()*.045,el:element('path',{'data-connection':'true',fill:'none',pathLength:1,'stroke-dasharray':1},edges)});}
  while(connected.size<nodes.length){
    let best=null;
    for(const a of connected)for(let b=0;b<nodes.length;b++)if(!connected.has(b)&&degree[a]<3){const d=distance(a,b);if(!best||d<best.d)best={a,b,d};}
    connect(best.a,best.b);connected.add(best.b);
  }
  const candidates=[];
  for(let a=0;a<nodes.length;a++)for(let b=a+1;b<nodes.length;b++)if(distance(a,b)<.18)candidates.push({a,b,d:distance(a,b)});
  candidates.sort((a,b)=>a.d-b.d);
  for(const {a,b} of candidates)if(degree[a]<3&&degree[b]<3&&!connections.some(e=>(e.a===a&&e.b===b)||(e.a===b&&e.b===a)))connect(a,b);
  connections.sort((a,b)=>a.order-b.order);
  background.dataset.nodes=String(nodes.length);background.dataset.maxDegree=String(Math.max(...degree));
  document.querySelectorAll('.landing-brand').forEach((brand,i)=>{const icon=element('svg',{viewBox:'-700 -700 1400 1400','aria-hidden':'true',class:'visn-brand-icon'});const small=logo(icon,`visn-brand-${i}`);poseLogo(small,1,1,1);brand.prepend(icon);});
  const controls=document.createElement('div');controls.className='logo-opening-controls';controls.innerHTML='<span>VISN / MANY PERSPECTIVES. ONE PICTURE.</span><button type="button" id="logo-skip">Skip introduction ↗</button>';root.append(controls);
  const replay=document.createElement('button');replay.type='button';replay.className='logo-replay';replay.textContent='Replay logo ↻';document.querySelector('.intro-actions').after(replay);
  let width=innerWidth,height=innerHeight,time=0,introTime=0,opening=!reducedMotion.matches&&!root.hidden,paused=reducedMotion.matches,progress=0,target=0,raf=0,last=0,active=-1,inStory=false,entry=0,entryTarget=0;
  let pointer={x:0,y:0,active:false},smoothPointer={x:0,y:0,strength:0},pulse=0,pulseAge=10;
  function resize(){width=innerWidth;height=innerHeight;svg.setAttribute('viewBox',`${-width/2} ${-height/2} ${width} ${height}`);update();draw();}
  function finish(){opening=false;introTime=10.2;root.classList.remove('logo-opening');controls.hidden=true;draw();}
  function replayOpening(){root.scrollTo({top:0,behavior:'instant'});introTime=0;entry=entryTarget=0;opening=!reducedMotion.matches;paused=reducedMotion.matches;syncMotion();draw();start();}
  function update(){
    if(root.hidden){stop();return;}
    const centers=steps.map(step=>step.getBoundingClientRect().top+Math.min(step.offsetHeight,height)/2);
    target=0;for(let i=0;i<3;i++)if(centers[i]<=height/2)target=i+clamp((height/2-centers[i])/(centers[i+1]-centers[i]));
    entryTarget=clamp(root.scrollTop/Math.max(1,root.scrollTop+steps[0].getBoundingClientRect().top));
    const index=Math.round(target);visual.dataset.chapter=String(index);
    if(active!==index){active=index;steps.forEach((step,i)=>step.classList.toggle('is-current',i===index));section.querySelectorAll('.journey-nav button').forEach(button=>{if(+button.dataset.chapter===index)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});document.querySelector('#journey-object-label').textContent=['01 / EVERY PERSPECTIVE BEGINS SOMEWHERE','02 / YOUR MOMENT JOINS THE PICTURE','03 / CONNECTIONS BECOME KNOWLEDGE','04 / MANY PERSPECTIVES. SHARED VALUE.'][index];}
    section.style.setProperty('--journey-progress',String((target+1)/4));
    if(paused||reducedMotion.matches){progress=target;entry=entryTarget;}
    draw();start();
  }
  function draw(dt=0){
    root.classList.toggle('logo-opening',opening);controls.hidden=!opening;
    const bounds=section.getBoundingClientRect();inStory=bounds.top<height&&bounds.bottom>0;
    const reveal=opening?phase(introTime,8.1,10.2):1;
    const bloom=phase(entry,.12,.96);
    const exit=1-phase(-bounds.bottom,0,height*.6);
    background.style.opacity=String(lerp(1,lerp(.16,.34,bloom)*exit,reveal));
    background.style.backgroundColor=`rgba(248,242,230,${1-reveal})`;
    background.dataset.entry=entry.toFixed(3);
    root.style.setProperty('--story-entry',String(bloom));
    const scale=Math.min(width*.7,height*.72)/1400;
    const tilt=reducedMotion.matches?0:smoothPointer.x/width*3*smoothPointer.strength;
    mark.group.setAttribute('transform',`rotate(${tilt}) scale(${scale*(1+pulse*.035)})`);
    const gatheredSpread=520*scale/Math.hypot(width*.52,height*.52)*.86;
    let spread=1,density=0,closure=0,hexGrowth=0,pathGrowth=0,coreScale=0,networkOpacity=1,gather=0;
    if(opening){
      gather=phase(introTime,2.1,2.85);
      const t=introTime,burst=phase(t,.45,2.25),collapse=phase(t,3.1,4.6);
      spread=burst*lerp(.96,gatheredSpread,phase(t,2.1,2.85))*lerp(1,.12,collapse);density=phase(t,.5,1.6);closure=phase(t,2.85,4.6);
      hexGrowth=phase(t,4.8,6.2);pathGrowth=phase(t,6.15,7.7);coreScale=1-burst+collapse;
      networkOpacity=burst*(1-phase(t,4.15,4.65));
      background.dataset.phase=t<.45?'core':t<2.85?'explode':t<4.8?'collapse':t<6.15?'hexagon':t<7.7?'pathways':'assembled';

    }else{
      const p=progress/3;gather=phase(p,.45,.76);
      closure=phase(p,.76,.94);hexGrowth=phase(p,.9,.97);pathGrowth=phase(p,.94,1);
      spread=lerp(1,gatheredSpread,phase(p,.25,.76))*lerp(1,.12,phase(p,.76,.95));density=phase(p,.04,.7);
      networkOpacity=1-phase(p,.85,.95);coreScale=phase(p,.82,.95);
      background.dataset.phase=p<.25?'scatter':p<.76?'connect':p<.95?'compress':'assembled';
      // Keep the assembled opening mark, then release its six endpoints into the story.
      closure=lerp(1,closure,bloom);hexGrowth=lerp(1,hexGrowth,bloom);pathGrowth=lerp(1,pathGrowth,bloom);coreScale=lerp(1,coreScale,bloom);
      networkOpacity*=phase(bloom,.06,.72);
      if(reducedMotion.matches){closure=hexGrowth=pathGrowth=coreScale=1;networkOpacity=0;}
    }
    poseLogo(mark,closure,hexGrowth,pathGrowth,coreScale);
    if(!opening&&bloom<1)mark.outer.forEach((sphere,i)=>{const [x,y]=point((i*60-90)*Math.PI/180,602);sphere.setAttribute('transform',`translate(${x} ${y})`);sphere.style.opacity=String(1-bloom);});
    network.style.opacity=String(networkOpacity);background.dataset.edges=String(Math.ceil(density*connections.length));background.dataset.spread=spread.toFixed(3);background.dataset.progress=progress.toFixed(3);
    if(networkOpacity<=0)return;
    // Derive the shrinking opening from the same pivot and inner-tip geometry.
    const angle=-42*(1-closure)*Math.PI/180;
    const aperture=Math.hypot(58+129*Math.cos(angle)-506*Math.sin(angle),-558+129*Math.sin(angle)+506*Math.cos(angle));
    const apertureRadius=Math.max(1,(aperture-28)*scale);
    background.dataset.apertureRadius=apertureRadius.toFixed(2);
    const positions=nodes.map((node,i)=>{
      const depth=lerp(.65,1,node.z),drift=(Math.sin(time*.22+node.y*3)*18+Math.sin(time*.3+node.phase)*3)*depth;
      let x=(node.x*width*1.04+drift)*spread,y=(node.y*height*1.04+(Math.cos(time*.18+node.x*3)*14+Math.cos(time*.27+node.phase)*3)*depth)*spread;
      const orbitRadius=node.orbitRadius*Math.hypot(width*.52,height*.52)*spread;
      x=lerp(x,Math.cos(node.orbit+time*.035)*orbitRadius+drift*spread,gather);
      y=lerp(y,Math.sin(node.orbit+time*.035)*orbitRadius+Math.cos(time*.19+node.phase)*8*spread,gather);
      if(!opening&&bloom<1){
        const [sourceX,sourceY]=point((i%6*60-90)*Math.PI/180,602*scale);
        const release=phase(bloom,(i%7)*.015,.83+(i%7)*.025);
        x=lerp(sourceX,x,release);y=lerp(sourceY,y,release);
      }
      const dx=x-smoothPointer.x,dy=y-smoothPointer.y,d=Math.hypot(dx,dy)||1;
      const force=ease(1-d/170)*26*smoothPointer.strength*spread*(1-closure);
      if(dt){const damping=1-Math.exp(-dt*5);node.offsetX=lerp(node.offsetX,dx/d*force,damping);node.offsetY=lerp(node.offsetY,dy/d*force,damping);}
      const waveAge=pulseAge-Math.hypot(x,y)/650;
      const wave=waveAge>0?Math.sin(Math.min(waveAge,1)*Math.PI)*Math.exp(-waveAge*2)*12*spread:0;
      const direction=Math.atan2(y,x);
      x+=node.offsetX+Math.cos(direction)*wave;y+=node.offsetY+Math.sin(direction)*wave;
      const radius=(4.5+node.z*4)*lerp(1,.35,closure);
      // Leaves bound the gathering: every sphere stays inside the closing opening.
      if(closure>0&&(opening||bloom>.999)){const limit=Math.max(1,apertureRadius-radius-3),length=Math.hypot(x,y);if(length>limit){x*=limit/length;y*=limit/length;}}
      node.el.setAttribute('cx',x);node.el.setAttribute('cy',y);node.el.setAttribute('r',radius);node.el.style.opacity=String(lerp(.55,1,node.z));return [x,y];
    });
    connections.forEach((edge,i)=>{
      const growth=phase(density,i/connections.length*.82,i/connections.length*.82+.18);
      const opacity=growth*.22*lerp(1,.45,closure);
      edge.el.style.opacity=String(opacity);if(!opacity)return;
      edge.el.style.strokeDashoffset=String(1-growth);
      const a=positions[edge.a],b=positions[edge.b],dx=b[0]-a[0],dy=b[1]-a[1];
      const bend=Math.sin(time*.22+(nodes[edge.a].phase+nodes[edge.b].phase)*.3)*.045;
      edge.el.setAttribute('d',`M${a[0]} ${a[1]} Q${(a[0]+b[0])/2-dy*bend} ${(a[1]+b[1])/2+dx*bend} ${b[0]} ${b[1]}`);
    });
  }
  function tick(now){raf=0;const dt=Math.max(0,Math.min(.05,(now-last)/1000));last=now;
    if(!paused){const damping=1-Math.exp(-dt*7);smoothPointer.x=lerp(smoothPointer.x,pointer.x,damping);smoothPointer.y=lerp(smoothPointer.y,pointer.y,damping);smoothPointer.strength=lerp(smoothPointer.strength,pointer.active?1:0,damping);time+=dt;if(opening)introTime+=dt;progress=lerp(progress,target,1-Math.exp(-dt*6));entry=lerp(entry,entryTarget,1-Math.exp(-dt*6));pulseAge+=dt;pulse=Math.sin(Math.min(1,pulseAge/.45)*Math.PI/2)*Math.exp(-pulseAge*2);}
    if(introTime>=10.2&&opening)finish();draw(dt);if(canAnimate())raf=requestAnimationFrame(tick);
  }
  function canAnimate(){return !root.hidden&&!document.hidden&&!paused&&(opening||inStory||entry<.999||Math.abs(entry-entryTarget)>.001);}
  function start(){if(!raf&&canAnimate()){last=performance.now();raf=requestAnimationFrame(tick);}}
  function stop(){cancelAnimationFrame(raf);raf=0;}
  function syncMotion(){motion.setAttribute('aria-pressed',String(paused));motion.textContent=paused?'Play motion ▶':'Pause motion Ⅱ';document.querySelector('#journey-interaction-hint').textContent=paused?'Still frame · scroll to explore':'Move among the perspectives';if(paused)stop();else start();}
  function goTo(index,focusHeading=true){finish();const step=steps[index];if(focusHeading){const h=step.querySelector('h3');h.tabIndex=-1;h.focus({preventScroll:true});}root.scrollTo({top:root.scrollTop+step.getBoundingClientRect().top-root.getBoundingClientRect().top,behavior:reducedMotion.matches?'instant':'smooth'});}
  section.querySelectorAll('[data-chapter]').forEach(button=>button.addEventListener('click',()=>goTo(+button.dataset.chapter,!button.closest('.journey-nav'))));
  section.querySelectorAll('[data-journey-action]').forEach(button=>button.addEventListener('click',()=>{pulseAge=0;pointer.active=false;start();}));
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

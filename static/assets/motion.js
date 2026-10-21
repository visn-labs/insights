// One site preference, shared by the optional street and productive transitions.
const system=matchMedia('(prefers-reduced-motion: reduce)');
let preference='on',keyboard=false;
document.addEventListener('keydown',event=>{if(!event.metaKey&&!event.ctrlKey&&!event.altKey)keyboard=true;},{capture:true});
document.addEventListener('pointerdown',()=>{keyboard=false;},{capture:true,passive:true});
try{preference=localStorage.getItem('visn.motion')||'on';}catch{}
const subscribers=new Set(),effects=new WeakMap(),active=new Set();
export const motion={get keyboard(){return keyboard;},get enabled(){return preference!=='off'&&!system.matches&&!document.hidden;},get reduced(){return !this.enabled;},subscribe(fn){subscribers.add(fn);fn(this.enabled);return()=>subscribers.delete(fn);}};
function publish(){
  if(!motion.enabled){active.forEach(effect=>effect.cancel());active.clear();}
  document.documentElement.dataset.motion=motion.enabled?'on':'off';
  document.querySelectorAll('#intro-motion,[data-motion-toggle]').forEach(button=>{
    button.textContent=system.matches?'Reduced motion':preference==='off'?'Motion off':'Motion on';
    button.setAttribute('aria-pressed',String(motion.enabled));button.disabled=system.matches;
    button.setAttribute('aria-label',system.matches?'System reduced motion is enabled':preference==='off'?'Turn on animation':'Turn off animation');
  });
  subscribers.forEach(fn=>fn(motion.enabled));
}
document.querySelectorAll('#intro-motion,[data-motion-toggle]').forEach(button=>button.addEventListener('click',()=>{
  preference=preference==='off'?'on':'off';try{localStorage.setItem('visn.motion',preference);}catch{}publish();
}));
export function settle(element,frames=[{opacity:.65,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],duration=180){
  const previous=effects.get(element);
  // Retarget an unfinished reveal from its visible state instead of flashing
  // back to the initial keyframe when someone reverses an onboarding step.
  if(previous?.playState==='running'){
    const current=getComputedStyle(element);
    frames=[{opacity:current.opacity,transform:current.transform},frames.at(-1)];
  }
  previous?.cancel();
  if(!motion.enabled||motion.keyboard||!element.animate)return;
  const effect=element.animate(frames,{duration,easing:'cubic-bezier(.23,1,.32,1)'});effects.set(element,effect);active.add(effect);effect.finished.then(()=>active.delete(effect),()=>active.delete(effect));
}
system.addEventListener('change',publish);document.addEventListener('visibilitychange',publish);
window.addEventListener('storage',event=>{if(event.key==='visn.motion'){preference=event.newValue||'on';publish();}});
publish();

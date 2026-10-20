// One site preference, shared by the optional street and productive transitions.
const system=matchMedia('(prefers-reduced-motion: reduce)');
let preference='on';
try{preference=localStorage.getItem('visn.motion')||'on';}catch{}
const subscribers=new Set(),effects=new WeakMap(),active=new Set();
export const motion={get enabled(){return preference!=='off'&&!system.matches&&!document.hidden;},get reduced(){return !this.enabled;},subscribe(fn){subscribers.add(fn);fn(this.enabled);return()=>subscribers.delete(fn);}};
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
export function settle(element,frames=[{opacity:.5,translate:'0 10px'},{opacity:1,translate:'0 0'}],duration=220){
  effects.get(element)?.cancel();
  if(!motion.enabled||!element.animate)return;
  const effect=element.animate(frames,{duration,easing:'cubic-bezier(.22,.8,.25,1)'});effects.set(element,effect);active.add(effect);effect.finished.then(()=>active.delete(effect),()=>active.delete(effect));
}
system.addEventListener('change',publish);document.addEventListener('visibilitychange',publish);
window.addEventListener('storage',event=>{if(event.key==='visn.motion'){preference=event.newValue||'on';publish();}});
publish();

import {renderContributor} from './assets/contributor.js';
import {mountJourney} from './assets/journey.js';
import {motion} from './assets/motion.js';
const root=document.querySelector('#town-intro'),canvas=document.querySelector('#town-canvas'),app=document.querySelector('.app-shell');
const shell=document.querySelector('#contributor-shell'),status=document.querySelector('#intro-status');
const journey=mountJourney();
let scene,pending,landing=true,heroVisible=true,lastRoute,streetRequested=false;
const phone=matchMedia('(max-width:760px)'),streetPlay=document.querySelector('#street-play');
streetPlay.addEventListener('click',()=>{streetRequested=true;syncScene();});
function syncScene(){
  streetPlay.hidden=!phone.matches||streetRequested||!motion.enabled;
  const play=landing&&heroVisible&&motion.enabled&&!journey.introPending&&!document.querySelector('#logo-dialog').open&&(!phone.matches||streetRequested||journey.progress>.05);root.dataset.townActive=String(play);
  if(!scene){if(play)initialize();return;}
  scene.reduced=!motion.enabled;scene.paused=!play;if(play)scene.start();else scene.stop();
}
async function initialize(){
  if(scene||pending||!motion.enabled)return;
  pending=(async()=>{
    try{
      const {TownScene}=await import('./engine.js');
      if(!landing||!motion.enabled||(phone.matches&&!streetRequested&&journey.progress<=.05))return;
      scene=new TownScene(canvas,document.querySelector('#camera-labels'),()=>{});
      scene.setRecordingProgress(journey.progress);scene.resize();scene.update(0);root.dataset.state='ready';syncScene();
    }catch(error){root.dataset.state='poster';status.textContent='A still illustration. The whole contribution story is available.';}
    finally{pending=null;}
  })();return pending;
}
function route(force=false){
  const raw=location.hash.replace(/^#\/?/,''),[path]=raw.split('?');
  if(force!==true&&lastRoute===raw)return;const initial=lastRoute===undefined;lastRoute=raw;
  document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
  const contributor=['home','contribute','rewards'].includes(path),workspace=path==='workspace',known=contributor||workspace||path==='';
  landing=path==='';root.hidden=!landing;shell.hidden=landing||workspace;app.hidden=!workspace;app.inert=!workspace;
  document.body.classList.toggle('landing-open',landing);document.querySelector('#contribution-preview').pause();
  if(contributor||!known)renderContributor(known?path:'not-found',new URLSearchParams(raw.split('?')[1]||''));
  const titles={home:'Your local drafts',contribute:'Prepare a local draft',rewards:'Programme status',workspace:'Analysis workspace','not-found':'Outside the frame'};
  document.title=landing?'Visn | Your corner. A clearer picture.':`Visn | ${titles[known?path:'not-found']}`;
  document.querySelector('meta[name="description"]').content=landing?'Follow a sample contribution, then prepare a local draft. Your recording stays on your device.':workspace?'Visn local video evidence and analysis workspace.':path==='home'?'Review, edit and delete local draft metadata. No video is stored with your drafts.':path==='rewards'?'What is available now and what is planned for the Visn contributor programme.':'Prepare camera context, choose a recording, select an interval and save local draft details.';
  window.scrollTo({top:0,behavior:'instant'});syncScene();
  const target=landing?document.querySelector('#intro-title'):workspace?document.querySelector('#page-title'):document.querySelector({home:'#contributor-home-title',contribute:'#onboarding-title',rewards:'#rewards-title'}[path]||'#not-found-title');
  if(!initial){target.tabIndex=-1;target.focus({preventScroll:true});}
}
function navigate(path,transition=false){
  const update=()=>{history.pushState(null,'','#'+path);route(true);};
  if(transition&&motion.enabled&&!motion.keyboard&&document.startViewTransition){const view=document.startViewTransition(update);view.finished.catch(()=>{});}else update();
}
window.addEventListener('visn:navigate',event=>navigate(event.detail.path,event.detail.transition));
window.addEventListener('hashchange',()=>route());window.addEventListener('popstate',()=>route());
document.querySelector('#replay-intro').addEventListener('click',()=>navigate('/'));
document.querySelector('#intro-tour').addEventListener('click',event=>journey.goTo(0,{keyboard:event.detail===0}));
document.querySelectorAll('[data-scroll]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();journey.goTo(0,{keyboard:event.detail===0});}));
document.querySelector('.skip-link').addEventListener('click',event=>{event.preventDefault();const target=landing?document.querySelector('#main-content'):workspace?app.querySelector('main'):shell.querySelector('main:not([hidden])');target.tabIndex=-1;target.focus();target.scrollIntoView({behavior:'instant'});});
root.addEventListener('pointermove',event=>{if(scene&&motion.enabled&&landing&&heroVisible)scene.pointer.set((event.clientX/innerWidth-.5)*.5,(event.clientY/innerHeight-.5)*.5);},{passive:true});
root.addEventListener('pointerleave',()=>scene?.pointer.set(0,0));
new IntersectionObserver(entries=>{heroVisible=entries[0].isIntersecting;syncScene();},{threshold:0}).observe(document.querySelector('#camera-film-experience'));
window.addEventListener('resize',()=>{if(landing)scene?.resize();syncScene();});
window.addEventListener('pagehide',()=>scene?.stop());window.addEventListener('pageshow',syncScene);
canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();scene?.stop();root.dataset.state='poster';status.textContent='A still illustration. Your choices and the story are still available.';});
canvas.addEventListener('webglcontextrestored',()=>{if(!scene)return;scene.resize();scene.update(0);root.dataset.state='ready';syncScene();});
window.addEventListener('visn:logo-reveal',syncScene);
window.addEventListener('visn:recording-progress',event=>{scene?.setRecordingProgress(event.detail);syncScene();});
route();motion.subscribe(syncScene);

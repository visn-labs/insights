import {renderContributor} from './assets/contributor.js';
const root = document.querySelector('#town-intro');
const canvas = document.querySelector('#town-canvas');
const app = document.querySelector('.app-shell');
const replay = document.querySelector('#replay-intro');
const motion = document.querySelector('#intro-motion');
const status = document.querySelector('#intro-status');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const stories = [
  ['It starts with your perspective.', 'Your camera sees a small part of the world. You choose the moment to contribute.', 'YOUR VIEW'],
  ['A shared understanding.', 'Contributed moments can help build a useful, searchable knowledge base.', 'KNOWLEDGE'],
  ['Value comes back to you.', 'A contributor programme designed around rewards. Explore the preview to see how sharing could work.', 'SHARED VALUE'],
];
let scene, dismissed = false, userPaused = reducedMotion.matches;
let initPromise;

function showStory(index) {
  document.querySelectorAll('.story-tab').forEach(button => {
    const active = Number(button.dataset.story) === index;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  document.querySelector('#signal-title').textContent = stories[index][0];
  document.querySelector('#signal-description').textContent = stories[index][1];
  document.querySelector('#signal-state').textContent = stories[index][2];
  document.querySelector('#signal-source').textContent = index === 0 ? 'CAM 01–04' : 'CONTEXT';
  document.querySelector('#signal-destination').textContent = index === 2 ? 'INSIGHT' : 'VISN';
}
function motionLabel() {
  motion.setAttribute('aria-pressed', String(userPaused));
  motion.setAttribute('aria-label', userPaused ? 'Play scene animation' : 'Pause scene animation');
  motion.innerHTML = userPaused ? 'Play motion <span aria-hidden="true">▶</span>' : 'Pause motion <span aria-hidden="true">Ⅱ</span>';
  if (scene) status.textContent = userPaused ? 'A quiet moment · Animation paused' : 'A living illustration · Move your pointer to explore';
}
function enter(){location.hash='/workspace';}
function route() {
  const page=location.hash.replace(/^#\/?/,'').split('?')[0];
  const contributor=page==='home'||page==='contribute',workspace=page==='workspace';
  dismissed=contributor||workspace;
  document.querySelector('#contribution-preview').pause();
  root.hidden=dismissed;app.hidden=!workspace;app.inert=!workspace;
  document.querySelector('#contributor-shell').hidden=!contributor;
  document.body.classList.toggle('landing-open',!dismissed);
  if(dismissed)scene?.stop();else if(scene){scene.resize();if(heroVisible)scene.start();}else initialize();
  if(contributor)renderContributor(page);
  document.title=contributor?(page==='home'?'Visn — Contributor home':'Visn — Your first contribution'):workspace?'Visn — Analysis workspace':'Visn — Your view. Shared value.';
  window.scrollTo(0,0);
  const target=contributor?document.querySelector(page==='home'?'#contributor-home-title':'#onboarding-title'):workspace?document.querySelector('#page-title'):document.querySelector('#intro-enter');
  if(target.tagName==='H1')target.setAttribute('tabindex','-1');target.focus({preventScroll:true});
}
let heroVisible=true;
async function initialize() {
  if (scene || initPromise) return initPromise;
  initPromise = (async () => {
    try {
      const { TownScene } = await import('./engine.js');
      scene = new TownScene(canvas, document.querySelector('#camera-labels'), showStory);
      scene.reduced = reducedMotion.matches;
      showStory(scene.stage);
      root.dataset.state = 'ready';
      scene.paused = userPaused;
      if (!dismissed) scene.start();
      motionLabel();
    } catch (error) {
      console.error('VISN town scene:', error);
      root.dataset.state = 'error';
      status.textContent = '3D scene unavailable. Your workspace is ready.';
      motion.disabled = true;
      document.querySelector('#intro-tour').disabled = true;
    }
  })();
  return initPromise;
}
document.querySelectorAll('[data-enter]').forEach(button => button.addEventListener('click', enter));
replay.addEventListener('click',()=>{location.hash='/';});
document.querySelector('.landing-brand').addEventListener('click', event => { event.preventDefault(); root.scrollTo({top:0,behavior:reducedMotion.matches?'auto':'smooth'});scene?.setStage(0); });
document.querySelectorAll('[data-story]').forEach(button => button.addEventListener('click', () => {
  const index = Number(button.dataset.story); showStory(index); scene?.setStage(index);
}));
document.querySelector('#intro-tour').addEventListener('click', () => {
  document.querySelector('#how-it-works').scrollIntoView({behavior:reducedMotion.matches?'auto':'smooth'});
  scene?.setStage(0);
  if (!reducedMotion.matches) { userPaused = false; scene?.setPaused(false); motionLabel(); }
  else { showStory(1); scene?.setStage(1); }
});
motion.addEventListener('click', () => { userPaused = !userPaused; scene?.setPaused(userPaused); motionLabel(); });
root.addEventListener('pointermove', event => {
  if (!scene || reducedMotion.matches) return;
  scene.pointer.set((event.clientX / innerWidth - .5) * 2, (event.clientY / innerHeight - .5) * 2);
}, { passive: true });
root.addEventListener('pointerleave', () => scene?.pointer.set(0, 0));
window.addEventListener('hashchange',route);
document.querySelectorAll('[data-scroll]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();document.getElementById(link.dataset.scroll).scrollIntoView({behavior:reducedMotion.matches?'auto':'smooth'});}));
window.addEventListener('resize', () => { if (!dismissed) scene?.resize(); });
// Recompute label offsets when a short mobile viewport scrolls the landing.
root.addEventListener('scroll', () => { if (!dismissed && scene) { scene.rect = canvas.getBoundingClientRect(); if (userPaused) scene.update(0); } }, { passive: true });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) scene?.stop(); else if (!dismissed&&heroVisible) scene?.start();
});
reducedMotion.addEventListener('change', event => {
  userPaused = event.matches;
  if (scene) { scene.reduced = event.matches; scene.setPaused(userPaused); scene.update(0); if (dismissed) scene.stop(); }
  motionLabel();
});
canvas.addEventListener('webglcontextlost', event => {
  event.preventDefault(); scene?.stop(); root.dataset.state = 'error';
  status.textContent = 'Scene interrupted. Reload to restore it, or open your workspace.';
});
window.addEventListener('pagehide', () => scene?.stop());
window.addEventListener('pageshow', () => { if (!dismissed&&heroVisible) scene?.start(); });
new IntersectionObserver(entries=>{heroVisible=entries[0].isIntersecting;if(scene){if(heroVisible&&!dismissed)scene.start();else scene.stop();}},{threshold:.05}).observe(document.querySelector('.landing-hero'));
motionLabel();
route();

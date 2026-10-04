import {renderContributor} from './assets/contributor.js';
import {mountJourney} from './assets/journey.js';
const root = document.querySelector('#town-intro');
const canvas = document.querySelector('#town-canvas');
const app = document.querySelector('.app-shell');
const replay = document.querySelector('#replay-intro');
const motion = document.querySelector('#intro-motion');
const status = document.querySelector('#intro-status');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const stories = [
  ['It starts with your perspective.', 'Your camera sees a small part of the world. You choose the moment to contribute.', 'YOUR VIEW'],
  ['Everyday moments. Useful answers.', 'Your footage can help answer questions about activity, arrivals and how places are used.', 'KNOWLEDGE'],
  ['Knowledge creates shared value.', 'People pay for insights. Our aim is to reward the contributors who help make those answers possible.', 'SHARED VALUE'],
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
  const contributor=['home','contribute','rewards'].includes(page),workspace=page==='workspace';
  dismissed=contributor||workspace;
  document.querySelector('#contribution-preview').pause();
  root.hidden=dismissed;app.hidden=!workspace;app.inert=!workspace;
  if(!dismissed)root.scrollTo({top:0,behavior:'instant'});
  document.querySelector('#contributor-shell').hidden=!contributor;
  document.body.classList.toggle('landing-open',!dismissed);
  if(dismissed)scene?.stop();else if(scene){scene.resize();if(heroVisible)scene.start();}else initialize();
  if(contributor)renderContributor(page);
  document.title=contributor?({home:'Visn — Contributor home',contribute:'Visn — Your first contribution',rewards:'Visn — How shared value works'}[page]):workspace?'Visn — Analysis workspace':'Visn — Your view. Shared value.';
  window.scrollTo(0,0);
  const target=contributor?document.querySelector({home:'#contributor-home-title',contribute:'#onboarding-title',rewards:'#rewards-title'}[page]):workspace?document.querySelector('#page-title'):document.querySelector('#intro-enter');
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
  journey.goTo(0);
});
motion.addEventListener('click', () => { userPaused = !userPaused; scene?.setPaused(userPaused); motionLabel(); });
root.addEventListener('pointermove', event => {
  if (!scene || reducedMotion.matches) return;
  scene.pointer.set((event.clientX / innerWidth - .5) * 2, (event.clientY / innerHeight - .5) * 2);
}, { passive: true });
root.addEventListener('pointerleave', () => scene?.pointer.set(0, 0));
window.addEventListener('hashchange',route);
document.querySelectorAll('[data-scroll]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();if(link.dataset.scroll==='how-it-works')journey.goTo(0);else document.getElementById(link.dataset.scroll).scrollIntoView({behavior:reducedMotion.matches?'instant':'smooth'});}));
const perspectives = {
  shop: ['A SHOP ENTRANCE', 'When does this corner come to life?', 'Patterns of arrivals could help a local business understand its busiest hours.', [20,28,42,36,55,74,93,85,62,73,44,26]],
  loading: ['A LOADING BAY', 'When do deliveries slow things down?', 'Patterns of vehicle arrivals could help an operator plan deliveries around quieter periods.', [28,75,94,66,32,25,47,83,72,39,26,18]],
  forecourt: ['A FORECOURT', 'How does this space change through the day?', 'Patterns of occupancy could help a business understand when its forecourt is in greatest demand.', [15,24,55,88,73,58,42,63,92,76,48,22]],
};
function showPerspective(key) {
  const [source,question,answer,bars]=perspectives[key];
  const buttons=[...document.querySelectorAll('[data-perspective]')];
  buttons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.perspective===key)));
  document.querySelector('#perspective-source').textContent=source;
  document.querySelector('#perspective-question').textContent=question;
  document.querySelector('#perspective-answer').textContent=answer;
  document.querySelector('.example-heading>span:last-child').textContent=`0${Object.keys(perspectives).indexOf(key)+1} / 03`;
  document.querySelectorAll('.chart-bars i').forEach((bar,i)=>bar.style.setProperty('--bar-height',bars[i]+'%'));
}
document.querySelectorAll('[data-perspective]').forEach(button=>button.addEventListener('click',()=>showPerspective(button.dataset.perspective)));
showPerspective('shop');
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
const journey=mountJourney(root,reducedMotion);
route();

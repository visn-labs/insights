const root = document.querySelector('#town-intro');
const canvas = document.querySelector('#town-canvas');
const app = document.querySelector('.app-shell');
const replay = document.querySelector('#replay-intro');
const motion = document.querySelector('#intro-motion');
const status = document.querySelector('#intro-status');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const stories = [
  ['Small moments. Clear signals.', 'Four cameras follow movement across the junction.', 'CAPTURING'],
  ['Every moment, connected.', 'Captured moments travel together as a stream of useful context.', 'TRANSMITTING'],
  ['The street becomes a story.', 'Signals rise beyond the rooftops. Find the moments that matter in your workspace.', 'DELIVERING'],
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
function enter() {
  dismissed = true;
  scene?.stop();
  root.hidden = true;
  app.inert = false;
  document.body.classList.remove('landing-open');
  window.scrollTo(0, 0);
  document.querySelector('#page-title').setAttribute('tabindex', '-1');
  document.querySelector('#page-title').focus({ preventScroll: true });
}
async function initialize() {
  if (scene || initPromise) return initPromise;
  initPromise = (async () => {
    try {
      const { TownScene } = await import('./engine.js');
      scene = new TownScene(canvas, document.querySelector('#camera-labels'), showStory);
      scene.reduced = reducedMotion.matches;
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
function show() {
  dismissed = false; root.hidden = false; app.inert = true;
  document.body.classList.add('landing-open');
  window.scrollTo(0, 0);
  if (scene) { scene.resize(); scene.start(); } else initialize();
}

document.querySelectorAll('[data-enter]').forEach(button => button.addEventListener('click', enter));
replay.addEventListener('click', () => { show(); document.querySelector('#intro-enter').focus({ preventScroll: true }); });
document.querySelector('.landing-brand').addEventListener('click', event => { event.preventDefault(); scene?.setStage(0); });
document.querySelectorAll('[data-story]').forEach(button => button.addEventListener('click', () => {
  const index = Number(button.dataset.story); showStory(index); scene?.setStage(index);
}));
document.querySelector('#intro-tour').addEventListener('click', () => {
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
window.addEventListener('keydown', event => { if (event.key === 'Escape' && !dismissed) enter(); });
window.addEventListener('resize', () => { if (!dismissed) scene?.resize(); });
// Recompute label offsets when a short mobile viewport scrolls the landing.
window.addEventListener('scroll', () => { if (!dismissed && scene) { scene.rect = canvas.getBoundingClientRect(); if (userPaused) scene.update(0); } }, { passive: true });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) scene?.stop(); else if (!dismissed) scene?.start();
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
window.addEventListener('pageshow', () => { if (!dismissed) scene?.start(); });
motionLabel();
show();

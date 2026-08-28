const root = document.querySelector('#town-intro');
const canvas = document.querySelector('#town-canvas');
const enterButton = document.querySelector('#intro-enter');
const skipButton = document.querySelector('#intro-skip');
const replayButton = document.querySelector('#replay-intro');
const status = document.querySelector('#intro-status');

if (!root || !canvas) throw new Error('Town intro markup is missing');

const context = canvas.getContext('2d', { alpha: true, desynchronized: true });
if (!context) throw new Error('2D canvas is unavailable');

const WORLD = { width: 1600, height: 900 };
const STEP_MS = 1000 / 16;
const STEP_SECONDS = 1 / 16;
const FRAME_SIZE = 160;
const FRAME_COLS = 4;
const TOTAL_FRAMES = 16;
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const COLORS = {
  paper: '#f2dfbd',
  paperLight: '#fff3d5',
  paperShade: '#d4b587',
  ink: '#744a33',
  inkDark: '#4b3024',
  inkSoft: '#9a7051',
  street: '#caa879',
  streetDark: '#a77d58',
  camera: '#5f9362',
  cameraDark: '#315c43',
  phone: '#3fa8a2',
  phoneGlow: '#77d7cb',
};

// Speeds are world pixels per second. Every source drawing faces right; a
// negative direction mirrors the vector sheet at draw time. The cadence is
// intentionally role-specific instead of forcing every character to 16 FPS.
const ACTOR_BLUEPRINTS = [
  { name: 'apple_seller', x: 1090, y: 685, size: 174, mode: 'idle', speed: 0, direction: 1, frameFps: 6, phase: 0, phone: [0.59, 0.43] },
  { name: 'aristocrat', x: 1470, y: 590, size: 132, mode: 'wrap', speed: 31, direction: -1, frameFps: 9, phase: 2, phone: [0.62, 0.42] },
  { name: 'beggar_child', x: 1190, y: 760, size: 132, mode: 'idle', speed: 0, direction: 1, frameFps: 6, phase: 7, phone: [0.68, 0.42] },
  { name: 'blacksmith', x: 1360, y: 700, size: 164, mode: 'patrol', speed: 18, direction: -1, frameFps: 8, phase: 5, minX: 1230, maxX: 1460 },
  { name: 'blind_beggar', x: 520, y: 628, size: 142, mode: 'wrap', speed: 14, direction: 1, frameFps: 7, phase: 9 },
  { name: 'chimney_sweep', x: 930, y: 735, size: 142, mode: 'wrap', speed: 39, direction: -1, frameFps: 11, phase: 4, phone: [0.55, 0.47] },
  { name: 'constable', x: 720, y: 655, size: 154, mode: 'wrap', speed: 29, direction: 1, frameFps: 9, phase: 1, phone: [0.65, 0.48] },
  { name: 'drunkard', x: 1350, y: 615, size: 143, mode: 'wrap', speed: 12, direction: -1, frameFps: 7, phase: 8, sway: 0.045 },
  { name: 'flower_girl', x: 825, y: 570, size: 125, mode: 'wrap', speed: 18, direction: 1, frameFps: 7, phase: 11, phone: [0.63, 0.41] },
  { name: 'gentleman', x: 540, y: 745, size: 180, mode: 'wrap', speed: 28, direction: 1, frameFps: 9, phase: 3, phone: [0.68, 0.43] },
  { name: 'hansom_cab', x: -180, y: 610, size: 300, mode: 'wrap', speed: 88, direction: 1, frameFps: 16, phase: 0, margin: 330 },
  { name: 'high_society_lady', x: 1510, y: 750, size: 188, mode: 'wrap', speed: 17, direction: -1, frameFps: 7, phase: 12, phone: [0.68, 0.40] },
  { name: 'lamplighter', x: 420, y: 575, size: 138, mode: 'wrap', speed: 23, direction: 1, frameFps: 8, phase: 6 },
  { name: 'maid', x: 1280, y: 790, size: 178, mode: 'wrap', speed: 27, direction: -1, frameFps: 9, phase: 10, phone: [0.67, 0.44] },
  { name: 'newsboy', x: 650, y: 555, size: 126, mode: 'wrap', speed: 53, direction: 1, frameFps: 13, phase: 13, phone: [0.70, 0.38] },
  { name: 'pickpocket', x: 1460, y: 665, size: 154, mode: 'wrap', speed: 22, direction: -1, frameFps: 10, phase: 5, bob: 1 },
  { name: 'postman', x: 250, y: 710, size: 152, mode: 'wrap', speed: 42, direction: 1, frameFps: 11, phase: 14, phone: [0.65, 0.44] },
  { name: 'priest', x: 1160, y: 585, size: 132, mode: 'wrap', speed: 15, direction: -1, frameFps: 7, phase: 3, phone: [0.65, 0.43] },
  { name: 'rat_catcher', x: 350, y: 790, size: 188, mode: 'wrap', speed: 24, direction: 1, frameFps: 8, phase: 9 },
  { name: 'sailor', x: 1060, y: 800, size: 184, mode: 'wrap', speed: 31, direction: -1, frameFps: 9, phase: 6, phone: [0.64, 0.43] },
  { name: 'seamstress', x: 780, y: 800, size: 181, mode: 'wrap', speed: 21, direction: 1, frameFps: 8, phase: 1, phone: [0.66, 0.43] },
  { name: 'street_musician', x: 990, y: 625, size: 158, mode: 'idle', speed: 0, direction: 1, frameFps: 8, phase: 4 },
  { name: 'urchin', x: 140, y: 620, size: 138, mode: 'wrap', speed: 69, direction: 1, frameFps: 16, phase: 8, phone: [0.69, 0.43] },
  { name: 'wealthy_couple', x: 1450, y: 820, size: 212, mode: 'wrap', speed: 18, direction: -1, frameFps: 7, phase: 15, phone: [0.68, 0.40] },
];

const CAMERAS = [
  { x: 825, y: 302, scale: 1.06, phase: 0.1, label: '01' },
  { x: 1105, y: 228, scale: 1.18, phase: 1.5, label: '02' },
  { x: 1428, y: 335, scale: 1.1, phase: 3.2, label: '03' },
  { x: 1010, y: 520, scale: 1.0, phase: 4.3, label: '04', pole: true },
  { x: 1500, y: 555, scale: 1.12, phase: 5.4, label: '05', pole: true },
];

const state = {
  actors: [],
  images: new Map(),
  running: false,
  initialized: false,
  raf: null,
  lastTime: 0,
  accumulator: 0,
  elapsed: 0,
  pointer: { x: 0, y: 0 },
  targetPointer: { x: 0, y: 0 },
  viewport: { width: 0, height: 0, dpr: 1, scale: 1, offsetX: 0, offsetY: 0 },
  backdrop: document.createElement('canvas'),
};

function vectorUrl(name) {
  return `/assets/vector_${name}.svg`;
}

function loadImage(name) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve([name, image]);
    image.onerror = () => reject(new Error(`Could not load ${vectorUrl(name)}`));
    image.src = vectorUrl(name);
  });
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function path(ctx, points, close = true) {
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  points.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  if (close) ctx.closePath();
}

function drawCloud(ctx, x, y, scale, alpha) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = COLORS.paperLight;
  ctx.strokeStyle = COLORS.inkSoft;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(-88, 18);
  ctx.bezierCurveTo(-76, -11, -48, -18, -28, 1);
  ctx.bezierCurveTo(-11, -33, 34, -34, 47, 0);
  ctx.bezierCurveTo(72, -7, 94, 8, 91, 28);
  ctx.bezierCurveTo(45, 38, -45, 39, -88, 18);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawHouse(ctx, config) {
  const { x, y, width, height, roof, stories = 2, lean = 0, shop = false } = config;
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = COLORS.inkDark;
  ctx.lineWidth = 3;
  ctx.lineJoin = 'round';
  ctx.fillStyle = config.fill || '#d9bc8d';
  ctx.beginPath();
  ctx.moveTo(lean, 0);
  ctx.bezierCurveTo(width * 0.3, -6, width * 0.7, 5, width + lean, 0);
  ctx.lineTo(width, height);
  ctx.lineTo(0, height);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = config.roofFill || COLORS.inkDark;
  ctx.beginPath();
  ctx.moveTo(-18, 4);
  ctx.quadraticCurveTo(width * 0.5, -roof - 18, width + 22, 4);
  ctx.quadraticCurveTo(width * 0.5, -roof + 1, -18, 4);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  if (config.chimney) {
    ctx.fillStyle = COLORS.ink;
    roundedRect(ctx, width * 0.72, -roof * 0.78, 18, roof * 0.62, 3);
    ctx.fill();
    ctx.stroke();
  }

  for (let row = 0; row < stories; row += 1) {
    const windowY = 46 + row * ((height - 70) / Math.max(stories, 1));
    [0.24, 0.68].forEach((ratio, index) => {
      const windowWidth = Math.max(18, width * 0.16);
      const windowHeight = Math.max(28, height * 0.16);
      const windowX = width * ratio - windowWidth / 2;
      roundedRect(ctx, windowX, windowY, windowWidth, windowHeight, 7);
      ctx.fillStyle = index === row % 2 ? '#efd68f' : '#82aaa0';
      ctx.fill();
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(windowX + windowWidth / 2, windowY + 2);
      ctx.lineTo(windowX + windowWidth / 2, windowY + windowHeight - 2);
      ctx.stroke();
    });
  }

  if (shop) {
    ctx.fillStyle = '#8d4d38';
    ctx.beginPath();
    ctx.moveTo(-7, height * 0.67);
    ctx.quadraticCurveTo(width / 2, height * 0.58, width + 7, height * 0.67);
    ctx.lineTo(width - 3, height * 0.76);
    ctx.quadraticCurveTo(width / 2, height * 0.69, 3, height * 0.76);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function drawFountain(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = COLORS.ink;
  ctx.lineWidth = 2.5;
  ctx.fillStyle = '#b99368';
  ctx.beginPath();
  ctx.ellipse(0, 26, 92, 25, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#8db5ad';
  ctx.beginPath();
  ctx.ellipse(0, 19, 77, 16, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#b99368';
  roundedRect(ctx, -10, -54, 20, 73, 7);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -52);
  ctx.bezierCurveTo(-8, -100, -35, -92, -47, -57);
  ctx.moveTo(0, -52);
  ctx.bezierCurveTo(8, -100, 35, -92, 47, -57);
  ctx.strokeStyle = COLORS.phone;
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.restore();
}

function drawLamp(ctx, x, y, scale = 1) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.strokeStyle = COLORS.inkDark;
  ctx.fillStyle = COLORS.inkDark;
  ctx.lineWidth = 3;
  roundedRect(ctx, -4, -125, 8, 132, 4);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(0, 8, 19, 7, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(-18, -120);
  ctx.quadraticCurveTo(0, -145, 18, -120);
  ctx.lineTo(12, -83);
  ctx.lineTo(-12, -83);
  ctx.closePath();
  ctx.fillStyle = '#d9a943';
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawMarket(ctx, x, y) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = COLORS.inkDark;
  ctx.lineWidth = 3;
  ctx.fillStyle = '#815237';
  roundedRect(ctx, -85, -35, 170, 67, 9);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(-105, -40);
  ctx.quadraticCurveTo(0, -105, 105, -40);
  ctx.quadraticCurveTo(0, -68, -105, -40);
  ctx.closePath();
  ctx.fillStyle = '#9d4e3c';
  ctx.fill();
  ctx.stroke();
  [-72, 72].forEach((poleX) => {
    roundedRect(ctx, poleX - 3, -42, 6, 93, 3);
    ctx.fillStyle = COLORS.ink;
    ctx.fill();
  });
  ['#b56a44', '#d3a947', '#6d9466', '#9f4436'].forEach((color, index) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.ellipse(-55 + index * 36, -9 + (index % 2) * 4, 13, 8, -0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });
  ctx.restore();
}

function drawStaticTown(ctx) {
  const sky = ctx.createLinearGradient(0, 0, 0, WORLD.height);
  sky.addColorStop(0, '#fff1ce');
  sky.addColorStop(0.58, '#e8cea3');
  sky.addColorStop(1, '#cba474');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, WORLD.width, WORLD.height);

  ctx.fillStyle = 'rgba(216,163,66,.68)';
  ctx.beginPath();
  ctx.arc(1310, 132, 54, 0, Math.PI * 2);
  ctx.fill();
  drawCloud(ctx, 945, 145, 1.05, 0.58);
  drawCloud(ctx, 1390, 230, 0.72, 0.42);
  drawCloud(ctx, 590, 215, 0.65, 0.32);

  // Distant skyline: softened contours establish depth without hard polygons.
  ctx.fillStyle = 'rgba(101,65,47,.25)';
  ctx.beginPath();
  ctx.moveTo(0, 430);
  ctx.bezierCurveTo(150, 380, 225, 405, 350, 350);
  ctx.bezierCurveTo(480, 280, 610, 390, 735, 318);
  ctx.bezierCurveTo(880, 248, 1020, 370, 1170, 295);
  ctx.bezierCurveTo(1335, 230, 1470, 340, 1600, 274);
  ctx.lineTo(1600, 560);
  ctx.lineTo(0, 560);
  ctx.closePath();
  ctx.fill();

  drawHouse(ctx, { x: 690, y: 205, width: 210, height: 310, roof: 82, stories: 3, fill: '#d6b485', chimney: true });
  drawHouse(ctx, { x: 910, y: 165, width: 250, height: 350, roof: 106, stories: 3, fill: '#c9a170', shop: true });
  drawHouse(ctx, { x: 1172, y: 244, width: 194, height: 274, roof: 76, stories: 2, fill: '#dfc092', chimney: true });
  drawHouse(ctx, { x: 1372, y: 214, width: 215, height: 305, roof: 92, stories: 3, fill: '#c49a6b', shop: true });

  // Perspective road and curved cobblestone seams.
  ctx.fillStyle = COLORS.street;
  ctx.beginPath();
  ctx.moveTo(0, 470);
  ctx.quadraticCurveTo(800, 515, 1600, 458);
  ctx.lineTo(1600, 900);
  ctx.lineTo(0, 900);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(92,58,42,.24)';
  ctx.lineWidth = 2;
  for (let row = 0; row < 9; row += 1) {
    const y = 525 + row * 48;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.bezierCurveTo(440, y - 14, 1050, y + 17, 1600, y - 7);
    ctx.stroke();
    const spacing = 112 + row * 12;
    for (let x = (row % 2) * spacing * 0.5; x < WORLD.width; x += spacing) {
      ctx.beginPath();
      ctx.moveTo(x, y - 22);
      ctx.quadraticCurveTo(x - 8, y, x - 2, y + 25);
      ctx.stroke();
    }
  }

  drawMarket(ctx, 1185, 575);
  drawFountain(ctx, 865, 670);
  drawLamp(ctx, 735, 575, 0.9);
  drawLamp(ctx, 1328, 620, 1.02);

  // Foreground border gives the scene its ink-illustration finish.
  ctx.strokeStyle = 'rgba(75,48,36,.42)';
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(0, 868);
  ctx.bezierCurveTo(340, 834, 650, 888, 990, 857);
  ctx.bezierCurveTo(1250, 833, 1420, 880, 1600, 851);
  ctx.stroke();
}

function rebuildBackdrop() {
  const { width, height, dpr, scale, offsetX, offsetY } = state.viewport;
  const backdrop = state.backdrop;
  backdrop.width = Math.max(1, Math.round(width * dpr));
  backdrop.height = Math.max(1, Math.round(height * dpr));
  const ctx = backdrop.getContext('2d');
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * offsetX, dpr * offsetY);
  drawStaticTown(ctx);
}

function resize() {
  const width = root.clientWidth || window.innerWidth;
  const height = root.clientHeight || window.innerHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.6);
  const scale = Math.max(width / WORLD.width, height / WORLD.height);
  const offsetX = (width - WORLD.width * scale) / 2;
  const offsetY = (height - WORLD.height * scale) / 2;
  state.viewport = { width, height, dpr, scale, offsetX, offsetY };
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  rebuildBackdrop();
  render();
}

function update(delta) {
  state.elapsed += delta;
  state.pointer.x += (state.targetPointer.x - state.pointer.x) * 0.11;
  state.pointer.y += (state.targetPointer.y - state.pointer.y) * 0.11;
  for (const actor of state.actors) {
    actor.frameClock += delta * actor.frameFps;
    if (actor.mode === 'idle') continue;
    actor.x += actor.direction * actor.speed * delta;
    if (actor.mode === 'patrol') {
      if (actor.x <= actor.minX) {
        actor.x = actor.minX;
        actor.direction = 1;
      } else if (actor.x >= actor.maxX) {
        actor.x = actor.maxX;
        actor.direction = -1;
      }
      continue;
    }
    const margin = actor.margin || actor.size * 0.72;
    if (actor.direction > 0 && actor.x > WORLD.width + margin) actor.x = -margin;
    if (actor.direction < 0 && actor.x < -margin) actor.x = WORLD.width + margin;
  }
}

function drawCamera(ctx, camera) {
  const scan = Math.sin(state.elapsed * 0.82 + camera.phase);
  const pulse = (Math.sin(state.elapsed * 2.35 + camera.phase) + 1) / 2;
  ctx.save();
  ctx.translate(camera.x, camera.y);
  ctx.scale(camera.scale, camera.scale);
  if (camera.pole) {
    ctx.fillStyle = COLORS.inkDark;
    roundedRect(ctx, -4, -6, 8, 126, 4);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(0, 121, 19, 7, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.rotate(scan * 0.12);
  const cone = ctx.createLinearGradient(20, 8, 178, 85);
  cone.addColorStop(0, `rgba(95,147,98,${0.17 + pulse * 0.08})`);
  cone.addColorStop(1, 'rgba(95,147,98,0)');
  ctx.fillStyle = cone;
  ctx.beginPath();
  ctx.moveTo(24, 5);
  ctx.lineTo(190, 38 + scan * 19);
  ctx.lineTo(176, 111 + scan * 25);
  ctx.closePath();
  ctx.fill();

  ctx.shadowColor = COLORS.phoneGlow;
  ctx.shadowBlur = 14 + pulse * 10;
  ctx.fillStyle = COLORS.camera;
  ctx.strokeStyle = COLORS.cameraDark;
  ctx.lineWidth = 3;
  roundedRect(ctx, -29, -17, 65, 34, 10);
  ctx.fill();
  ctx.stroke();
  path(ctx, [[-42, -9], [-28, -14], [-28, 13], [-42, 18]]);
  ctx.fillStyle = COLORS.cameraDark;
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = COLORS.phoneGlow;
  ctx.beginPath();
  ctx.arc(22, 0, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = 'rgba(49,92,67,.94)';
  roundedRect(ctx, -18, 23, 47, 18, 8);
  ctx.fill();
  ctx.fillStyle = COLORS.paperLight;
  ctx.font = '700 10px ui-monospace, monospace';
  ctx.textAlign = 'center';
  ctx.fillText(`CAM ${camera.label}`, 5, 36);
  ctx.restore();
}

function drawPhone(ctx, actor, left, top, size) {
  if (!actor.phone) return;
  const [u, v] = actor.phone;
  const localX = actor.direction > 0 ? u : 1 - u;
  const x = left + localX * size;
  const y = top + v * size;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(actor.direction * -0.16);
  ctx.shadowColor = COLORS.phoneGlow;
  ctx.shadowBlur = 10;
  ctx.fillStyle = COLORS.phone;
  ctx.strokeStyle = COLORS.cameraDark;
  ctx.lineWidth = 1.6;
  roundedRect(ctx, -4.5, -8, 9, 16, 2.2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = COLORS.phoneGlow;
  roundedRect(ctx, -2.6, -5.8, 5.2, 9.4, 1);
  ctx.fill();
  ctx.restore();
}

function drawActor(ctx, actor) {
  const image = state.images.get(actor.name);
  if (!image) return;
  const frame = prefersReducedMotion ? actor.phase % TOTAL_FRAMES : Math.floor(actor.frameClock + actor.phase) % TOTAL_FRAMES;
  const sourceX = (frame % FRAME_COLS) * FRAME_SIZE;
  const sourceY = Math.floor(frame / FRAME_COLS) * FRAME_SIZE;
  const bobAmount = actor.bob === 1 ? 1.4 : actor.speed > 45 ? 2.4 : actor.speed > 0 ? 1.2 : 0.35;
  const bob = prefersReducedMotion ? 0 : Math.abs(Math.sin(state.elapsed * Math.PI * actor.frameFps / 4 + actor.phase)) * bobAmount;
  const sway = actor.sway ? Math.sin(state.elapsed * 2.2 + actor.phase) * actor.sway : 0;
  const left = actor.x - actor.size / 2;
  const top = actor.y - actor.size + bob;

  ctx.save();
  ctx.translate(actor.x, actor.y);
  ctx.rotate(sway);
  ctx.scale(actor.direction, 1);
  ctx.drawImage(
    image,
    sourceX,
    sourceY,
    FRAME_SIZE,
    FRAME_SIZE,
    -actor.size / 2,
    -actor.size + bob,
    actor.size,
    actor.size,
  );
  ctx.restore();
  drawPhone(ctx, actor, left, top, actor.size);
}

function render() {
  if (!canvas.width || !canvas.height) return;
  const { dpr, scale, offsetX, offsetY } = state.viewport;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(state.backdrop, 0, 0);
  context.setTransform(
    dpr * scale,
    0,
    0,
    dpr * scale,
    dpr * (offsetX + state.pointer.x * 9),
    dpr * (offsetY + state.pointer.y * 4),
  );
  CAMERAS.forEach((camera) => drawCamera(context, camera));
  [...state.actors].sort((a, b) => a.y - b.y).forEach((actor) => drawActor(context, actor));
}

function tick(timestamp) {
  if (!state.running) return;
  if (!state.lastTime) state.lastTime = timestamp;
  const elapsedMs = Math.min(timestamp - state.lastTime, 250);
  state.lastTime = timestamp;
  state.accumulator += elapsedMs;
  let updated = false;
  let catchUpSteps = 0;
  while (state.accumulator >= STEP_MS && catchUpSteps < 4) {
    update(STEP_SECONDS);
    state.accumulator -= STEP_MS;
    catchUpSteps += 1;
    updated = true;
  }
  if (updated) render();
  state.raf = requestAnimationFrame(tick);
}

function revealCopy() {
  root.querySelectorAll('.intro-reveal').forEach((element, index) => {
    window.setTimeout(() => element.classList.add('revealed'), prefersReducedMotion ? 0 : 90 + index * 70);
  });
}

async function initialize() {
  if (state.initialized) return;
  state.initialized = true;
  resize();
  const names = [...new Set(ACTOR_BLUEPRINTS.map(({ name }) => name))];
  const results = await Promise.allSettled(names.map(loadImage));
  results.forEach((result) => {
    if (result.status === 'fulfilled') state.images.set(result.value[0], result.value[1]);
    else console.warn(result.reason);
  });
  state.actors = ACTOR_BLUEPRINTS.map((blueprint) => ({ ...blueprint, frameClock: 0 }));
  root.dataset.state = 'ready';
  enterButton.disabled = false;
  status.textContent = prefersReducedMotion
    ? `Vector town ready · ${state.images.size} actors · reduced motion`
    : `Vector town ready · ${state.images.size} actors · fixed 16 FPS`;
  revealCopy();
  render();
}

async function start() {
  await initialize();
  if (
    state.running
    || prefersReducedMotion
    || root.classList.contains('intro-dismissed')
    || root.classList.contains('intro-leaving')
  ) return;
  state.running = true;
  state.lastTime = 0;
  state.accumulator = 0;
  state.raf = requestAnimationFrame(tick);
}

function stop() {
  state.running = false;
  if (state.raf) cancelAnimationFrame(state.raf);
  state.raf = null;
  state.lastTime = 0;
}

function dismissIntro() {
  if (root.classList.contains('intro-dismissed')) return;
  root.classList.add('intro-leaving');
  window.sessionStorage.setItem('visn-town-intro-seen', '1');
  window.setTimeout(() => {
    root.classList.add('intro-dismissed');
    root.classList.remove('intro-leaving');
    root.setAttribute('aria-hidden', 'true');
    stop();
    replayButton?.focus({ preventScroll: true });
  }, prefersReducedMotion ? 0 : 720);
}

function replayIntro() {
  root.classList.remove('intro-dismissed', 'intro-leaving');
  root.removeAttribute('aria-hidden');
  start();
  enterButton?.focus({ preventScroll: true });
}

root.addEventListener('pointermove', (event) => {
  state.targetPointer.x = (event.clientX / window.innerWidth - 0.5) * 2;
  state.targetPointer.y = (event.clientY / window.innerHeight - 0.5) * 2;
});
root.addEventListener('pointerleave', () => {
  state.targetPointer.x = 0;
  state.targetPointer.y = 0;
});
enterButton?.addEventListener('click', dismissIntro);
skipButton?.addEventListener('click', dismissIntro);
replayButton?.addEventListener('click', replayIntro);
window.addEventListener('resize', resize);
window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !root.classList.contains('intro-dismissed')) dismissIntro();
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (!root.classList.contains('intro-dismissed')) start();
});

const forceReplay = new URLSearchParams(window.location.search).get('intro') === '1';
const alreadySeen = window.sessionStorage.getItem('visn-town-intro-seen') === '1';
if (alreadySeen && !forceReplay) {
  root.classList.add('intro-dismissed');
  root.setAttribute('aria-hidden', 'true');
} else {
  start();
}

// Preserve the existing local-data interstitial between the town and dashboard.
const dataStream = document.getElementById('data-stream-container');
const btnDashboard = document.getElementById('btn-enter-dashboard');
const fakeDataMessages = [
  '[INFO] Initializing VISN neural bridge...',
  '[OK]   Connection secured on port 8080.',
  '[DATA] Camera 01 | Pedestrian traffic normal.',
  '[DATA] Camera 02 | Detected 3 active tracks.',
  '[WARN] Node latency spike detected. Auto-resolving...',
  '[OK]   Stream aligned. Buffer healthy.',
  '[INFO] Backend synchronization complete.',
  '[DATA] YOLO26 local runner active.',
  '[DATA] GEMMA fallback ready.',
];

window.setInterval(() => {
  if (!dataStream) return;
  const denominator = Math.max(1, document.body.scrollHeight - window.innerHeight);
  if (window.scrollY / denominator <= 0.4) return;
  const line = document.createElement('div');
  line.className = 'data-line';
  line.innerText = `> ${new Date().toISOString()} ${fakeDataMessages[Math.floor(Math.random() * fakeDataMessages.length)]}`;
  dataStream.appendChild(line);
  if (dataStream.childNodes.length > 20) dataStream.removeChild(dataStream.firstChild);
  dataStream.scrollTop = dataStream.scrollHeight;
}, 300);

btnDashboard?.addEventListener('click', () => {
  root.classList.add('intro-leaving');
  window.setTimeout(() => {
    root.classList.add('intro-dismissed');
    document.body.style.overflow = 'auto';
    const feedPage = document.querySelector('.data-feed-page');
    const runway = document.querySelector('.intro-scroll-runway');
    if (feedPage) feedPage.style.display = 'none';
    if (runway) runway.style.display = 'none';
    stop();
  }, prefersReducedMotion ? 0 : 800);
});

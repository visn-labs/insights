import * as THREE from './vendor/three.module.min.js';

const root = document.querySelector('#town-intro');
const canvas = document.querySelector('#town-canvas');
const enterButton = document.querySelector('#intro-enter');
const skipButton = document.querySelector('#intro-skip');
const replayButton = document.querySelector('#replay-intro');
const status = document.querySelector('#intro-status');

if (!root || !canvas) {
  throw new Error('Town intro markup is missing');
}

const PALETTE = {
  paper: 0xf2dfbd,
  paperLight: 0xfff4d8,
  paperShade: 0xd8bd90,
  street: 0xcdb087,
  ink: 0x744a33,
  inkDark: 0x4b3025,
  inkSoft: 0xa47b5b,
  camera: 0x5f9362,
  cameraDark: 0x315c43,
  phone: 0x3fa8a2,
  phoneGlow: 0x77d7cb,
};

const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const state = {
  renderer: null,
  scene: null,
  camera: null,
  clock: new THREE.Clock(),
  frame: null,
  running: false,
  initialized: false,
  walkers: [],
  townspeople: [],
  scanPivots: [],
  cameraAssemblies: [],
  clockHands: [],
  ambientActors: [],
  targetPointer: new THREE.Vector2(),
  pointer: new THREE.Vector2(),
  cameraBase: new THREE.Vector3(18, 16.5, 22),
  sceneRoot: null,
};

const inkLine = new THREE.LineBasicMaterial({ color: PALETTE.ink, transparent: true, opacity: 0.94 });
const darkLine = new THREE.LineBasicMaterial({ color: PALETTE.inkDark, transparent: true, opacity: 0.95 });

function flatMaterial(color, opacity = 0.28, side = THREE.FrontSide) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 1,
    metalness: 0,
    transparent: opacity < 1,
    opacity,
    side,
    depthWrite: opacity > 0.55,
  });
}

function outlinedMesh(geometry, fill, edge = PALETTE.ink, opacity = 0.2, threshold = 20) {
  const group = new THREE.Group();
  const mesh = new THREE.Mesh(geometry, flatMaterial(fill, opacity, THREE.DoubleSide));
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geometry, threshold),
    new THREE.LineBasicMaterial({ color: edge, transparent: true, opacity: 0.92 }),
  );
  group.add(mesh, edges);
  return group;
}

function lineFrom(points, material = inkLine, closed = false) {
  const vectors = points.map(([x, y, z = 0]) => new THREE.Vector3(x, y, z));
  if (closed && vectors.length) vectors.push(vectors[0].clone());
  return new THREE.Line(new THREE.BufferGeometry().setFromPoints(vectors), material);
}

function curvedStroke(points, color = PALETTE.ink, radius = 0.026, opacity = 0.94, closed = false) {
  const vectors = points.map(([x, y, z = 0]) => new THREE.Vector3(x, y, z));
  const curve = new THREE.CatmullRomCurve3(vectors, closed, 'centripetal', 0.45);
  const geometry = new THREE.TubeGeometry(curve, Math.max(16, vectors.length * 8), radius, 7, closed);
  return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
    color,
    roughness: 0.92,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity > 0.45,
  }));
}

function ovalMesh(rx, ry, depth, fill, edge = PALETTE.ink, opacity = 0.28) {
  const group = outlinedMesh(new THREE.SphereGeometry(1, 24, 16), fill, edge, opacity, 28);
  group.scale.set(rx, ry, depth);
  return group;
}

function archedPanel(width, height, fill = PALETTE.paperLight, opacity = 0.42) {
  const shape = new THREE.Shape();
  shape.moveTo(-width / 2, -height / 2);
  shape.lineTo(-width / 2, height * 0.16);
  shape.bezierCurveTo(-width / 2, height * 0.48, width / 2, height * 0.48, width / 2, height * 0.16);
  shape.lineTo(width / 2, -height / 2);
  shape.closePath();
  const panel = outlinedMesh(new THREE.ShapeGeometry(shape, 20), fill, PALETTE.ink, opacity, 28);
  const arch = curvedStroke([
    [-width / 2, height * 0.13, 0.015],
    [-width * 0.34, height * 0.37, 0.015],
    [0, height * 0.47, 0.015],
    [width * 0.34, height * 0.37, 0.015],
    [width / 2, height * 0.13, 0.015],
  ], PALETTE.ink, 0.018, 0.72);
  panel.add(arch);
  return panel;
}

function ellipseLine(rx, ry, material = inkLine, segments = 28) {
  const points = [];
  for (let index = 0; index <= segments; index += 1) {
    const angle = (index / segments) * Math.PI * 2;
    points.push([Math.cos(angle) * rx, Math.sin(angle) * ry, 0]);
  }
  return lineFrom(points, material);
}

function addSurfaceLines(scene) {
  const ground = outlinedMesh(new THREE.PlaneGeometry(44, 28), PALETTE.paper, PALETTE.inkSoft, 0.94);
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.03;
  scene.add(ground);

  const road = outlinedMesh(new THREE.PlaneGeometry(43, 7.8), PALETTE.street, PALETTE.ink, 0.36);
  road.rotation.x = -Math.PI / 2;
  road.position.y = 0.005;
  scene.add(road);

  for (let x = -20; x <= 20; x += 2.3) {
    const seam = curvedStroke([
      [x, 0.018, -3.65],
      [x + 0.24, 0.018, -1.3],
      [x + 0.68, 0.018, 1.2],
      [x + 0.9, 0.018, 3.65],
    ], PALETTE.ink, 0.01, 0.13);
    scene.add(seam);
  }
  [-4.35, 4.35].forEach((z) => {
    const curb = outlinedMesh(new THREE.BoxGeometry(44, 0.22, 0.44), PALETTE.paperShade, PALETTE.ink, 0.36);
    curb.position.set(0, 0.08, z);
    scene.add(curb);
  });
}

function gableGeometry(width, depth, baseY, roofHeight) {
  const x = width / 2;
  const z = depth / 2;
  const vertices = new Float32Array([
    -x, baseY, -z, x, baseY, -z, 0, baseY + roofHeight, -z,
    -x, baseY, z, x, baseY, z, 0, baseY + roofHeight, z,
  ]);
  const indices = [0, 1, 2, 5, 4, 3, 0, 3, 4, 0, 4, 1, 2, 1, 4, 2, 4, 5, 0, 2, 5, 0, 5, 3];
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function addBuilding(scene, config) {
  const { x, z, width, height, depth, floors = 2, facing = 1, shop = false, clock = false } = config;
  const group = new THREE.Group();
  group.position.set(x, 0, z);

  const body = outlinedMesh(new THREE.BoxGeometry(width, height, depth), PALETTE.paperLight, PALETTE.ink, 0.78);
  body.position.y = height / 2;
  group.add(body);

  const roof = outlinedMesh(gableGeometry(width + 0.3, depth + 0.3, height, 1.25), PALETTE.paperShade, PALETTE.inkDark, 0.68);
  group.add(roof);

  const frontZ = facing * (depth / 2 + 0.012);
  const frontRotation = facing > 0 ? 0 : Math.PI;
  const door = archedPanel(width * 0.22, 1.72, PALETTE.paperShade, 0.54);
  door.position.set(width * 0.2, 0.83, frontZ);
  door.rotation.y = frontRotation;
  group.add(door);

  for (let floor = 0; floor < floors; floor += 1) {
    const y = 1.15 + floor * 1.65;
    [-0.28, 0.28].forEach((fraction) => {
      const windowPanel = archedPanel(width * 0.19, 0.88, PALETTE.phone, 0.15);
      windowPanel.children[0].material.opacity = 0.055;
      windowPanel.position.set(width * fraction, y, frontZ + facing * 0.006);
      windowPanel.rotation.y = frontRotation;
      group.add(windowPanel);
      const cross = lineFrom([
        [-width * 0.09, 0, 0], [width * 0.09, 0, 0],
        [0, 0, 0], [0, 0.39, 0], [0, -0.39, 0],
      ], inkLine);
      cross.position.set(width * fraction, y, frontZ + facing * 0.02);
      cross.rotation.y = frontRotation;
      group.add(cross);
    });
  }

  if (shop) {
    const awning = curvedStroke([
      [-width * 0.42, 2.18, frontZ],
      [-width * 0.18, 2.24, frontZ + facing * 0.14],
      [width * 0.18, 2.24, frontZ + facing * 0.14],
      [width * 0.42, 2.18, frontZ],
      [width * 0.34, 1.88, frontZ + facing * 0.48],
      [0, 1.8, frontZ + facing * 0.52],
      [-width * 0.34, 1.88, frontZ + facing * 0.48],
      [-width * 0.42, 2.18, frontZ],
    ], PALETTE.inkDark, 0.027, 0.9, true);
    group.add(awning);

    for (let x = -width * 0.28; x <= width * 0.28; x += width * 0.14) {
      const scallop = curvedStroke([
        [x - width * 0.07, 1.88, frontZ + facing * 0.49],
        [x, 1.8, frontZ + facing * 0.54],
        [x + width * 0.07, 1.88, frontZ + facing * 0.49],
      ], PALETTE.ink, 0.018, 0.72);
      group.add(scallop);
    }
  }

  if (clock) {
    const face = ellipseLine(0.62, 0.62, darkLine, 36);
    face.position.set(0, height - 0.85, frontZ + facing * 0.03);
    face.rotation.y = frontRotation;
    group.add(face);
    const hands = new THREE.Group();
    hands.add(curvedStroke([[0, 0, 0], [0.02, 0.18, 0], [0, 0.38, 0]], PALETTE.inkDark, 0.022));
    hands.add(curvedStroke([[0, 0, 0], [0.13, -0.03, 0], [0.28, -0.12, 0]], PALETTE.inkDark, 0.022));
    hands.position.copy(face.position);
    hands.rotation.y = frontRotation;
    group.add(hands);
    state.clockHands.push(hands);
  }

  const roofFlourish = curvedStroke([
    [-width * 0.34, height + 0.58, 0],
    [-width * 0.17, height + 1.02, 0],
    [0, height + 1.26, 0],
    [width * 0.17, height + 1.02, 0],
    [width * 0.34, height + 0.58, 0],
  ], PALETTE.inkDark, 0.02, 0.56);
  roofFlourish.position.z = frontZ + facing * 0.03;
  roofFlourish.rotation.y = frontRotation;
  group.add(roofFlourish);

  scene.add(group);
  return group;
}

function addTownBuildings(scene) {
  [
    { x: -15, z: -7.2, width: 5.1, height: 4.6, depth: 4.2, facing: 1, shop: true },
    { x: -9.4, z: -7.4, width: 4.8, height: 5.5, depth: 4.5, facing: 1 },
    { x: -3.7, z: -7.2, width: 5.3, height: 4.3, depth: 4.1, facing: 1, shop: true },
    { x: 2.8, z: -7.8, width: 5.2, height: 7.1, depth: 4.8, facing: 1, floors: 3, clock: true },
    { x: 9.4, z: -7.1, width: 5.5, height: 4.9, depth: 4.1, facing: 1, shop: true },
    { x: 15.5, z: -7.4, width: 4.8, height: 5.8, depth: 4.5, facing: 1 },
    { x: -14.5, z: 7.1, width: 5.7, height: 5.2, depth: 4.2, facing: -1 },
    { x: -8.2, z: 7.4, width: 5.2, height: 4.4, depth: 4.6, facing: -1, shop: true },
    { x: -1.9, z: 7.2, width: 5.6, height: 5.8, depth: 4.2, facing: -1 },
    { x: 4.6, z: 7.5, width: 5.1, height: 4.7, depth: 4.7, facing: -1, shop: true },
    { x: 10.6, z: 7.2, width: 5.3, height: 5.5, depth: 4.2, facing: -1 },
    { x: 16.1, z: 7.6, width: 4.5, height: 4.3, depth: 4.8, facing: -1, shop: true },
  ].forEach((config) => addBuilding(scene, config));
}

function makePhone() {
  const phone = outlinedMesh(new THREE.BoxGeometry(0.24, 0.4, 0.045), PALETTE.phone, PALETTE.phoneGlow, 0.9);
  phone.position.z = 0.035;
  return phone;
}

function illustratedGarment(style) {
  const shape = new THREE.Shape();
  if (style === 'lady') {
    shape.moveTo(-0.22, 1.98);
    shape.bezierCurveTo(-0.36, 1.77, -0.32, 1.42, -0.42, 1.08);
    shape.bezierCurveTo(-0.5, 0.8, -0.62, 0.58, -0.53, 0.48);
    shape.bezierCurveTo(-0.26, 0.39, 0.27, 0.39, 0.54, 0.5);
    shape.bezierCurveTo(0.62, 0.62, 0.49, 0.83, 0.42, 1.1);
    shape.bezierCurveTo(0.33, 1.45, 0.36, 1.77, 0.22, 1.98);
  } else {
    shape.moveTo(-0.26, 1.98);
    shape.bezierCurveTo(-0.38, 1.72, -0.35, 1.22, -0.34, 0.83);
    shape.quadraticCurveTo(-0.16, 0.68, 0, 0.63);
    shape.quadraticCurveTo(0.18, 0.7, 0.35, 0.85);
    shape.bezierCurveTo(0.37, 1.25, 0.39, 1.7, 0.26, 1.98);
  }
  shape.quadraticCurveTo(0, 2.1, -0.26, 1.98);
  shape.closePath();
  return outlinedMesh(new THREE.ShapeGeometry(shape, 28), PALETTE.inkSoft, PALETTE.ink, style === 'lady' ? 0.2 : 0.16, 34);
}

function makeArm(side) {
  const arm = new THREE.Group();
  const stroke = curvedStroke([
    [0, 0, 0],
    [side * 0.09, -0.24, 0.012],
    [side * 0.07, -0.55, 0.018],
    [side * 0.02, -0.78, 0.028],
  ], PALETTE.inkDark, 0.043, 0.95);
  const hand = ovalMesh(0.075, 0.095, 0.06, PALETTE.paperShade, PALETTE.ink, 0.62);
  hand.position.set(side * 0.02, -0.81, 0.03);
  arm.add(stroke, hand);
  return arm;
}

function makeLeg(side) {
  const leg = new THREE.Group();
  leg.add(curvedStroke([
    [0, 0, 0],
    [side * 0.04, -0.25, 0],
    [side * 0.02, -0.56, 0.015],
    [side * 0.15, -0.72, 0.035],
  ], PALETTE.inkDark, 0.05, 0.96));
  const shoe = ovalMesh(0.16, 0.055, 0.075, PALETTE.inkDark, PALETTE.inkDark, 0.88);
  shoe.position.set(side * 0.19, -0.75, 0.04);
  shoe.rotation.z = side * -0.08;
  leg.add(shoe);
  return leg;
}

function makePerson(style = 'gentleman', phone = true) {
  const person = new THREE.Group();
  const torso = new THREE.Group();
  person.add(torso);

  const head = ovalMesh(0.23, 0.28, 0.14, PALETTE.paperShade, PALETTE.inkDark, 0.72);
  head.position.y = 2.32;
  torso.add(head);
  const faceContour = curvedStroke([
    [-0.11, 2.44, 0.15],
    [-0.17, 2.33, 0.17],
    [-0.11, 2.19, 0.15],
    [0.03, 2.12, 0.15],
    [0.14, 2.24, 0.15],
    [0.13, 2.4, 0.15],
  ], PALETTE.inkDark, 0.016, 0.8);
  torso.add(faceContour);

  if (style === 'lady') {
    torso.add(illustratedGarment(style));
    const bonnet = curvedStroke([
      [-0.3, 2.45, 0.08],
      [-0.22, 2.66, 0.08],
      [0.02, 2.72, 0.08],
      [0.26, 2.58, 0.08],
      [0.28, 2.45, 0.08],
      [0.05, 2.48, 0.11],
      [-0.3, 2.45, 0.08],
    ], PALETTE.inkDark, 0.038, 0.96, true);
    torso.add(bonnet);
    torso.add(curvedStroke([[-0.23, 1.44, 0.05], [0, 1.32, 0.05], [0.25, 1.45, 0.05]], PALETTE.ink, 0.018, 0.58));
  } else {
    torso.add(illustratedGarment(style));
    torso.add(curvedStroke([[-0.2, 1.89, 0.05], [0, 1.68, 0.06], [0.2, 1.89, 0.05]], PALETTE.inkDark, 0.021, 0.76));
    if (style === 'worker') {
      const cap = curvedStroke([
        [-0.28, 2.54, 0.08],
        [-0.13, 2.68, 0.08],
        [0.15, 2.65, 0.08],
        [0.31, 2.5, 0.08],
        [0.06, 2.48, 0.1],
        [-0.28, 2.54, 0.08],
      ], PALETTE.inkDark, 0.034, 0.96, true);
      torso.add(cap);
      torso.add(curvedStroke([[-0.19, 1.77, 0.055], [-0.04, 1.46, 0.06], [0.17, 0.98, 0.055], [0.28, 1.72, 0.055]], PALETTE.ink, 0.018, 0.74));
    } else {
      const brim = ovalMesh(0.39, 0.065, 0.12, PALETTE.inkDark, PALETTE.inkDark, 0.94);
      brim.position.y = 2.58;
      const crown = ovalMesh(0.22, 0.36, 0.14, PALETTE.inkDark, PALETTE.inkDark, 0.92);
      crown.position.y = 2.87;
      torso.add(brim, crown);
    }
  }

  const leftArm = makeArm(-1);
  leftArm.position.set(-0.25, 1.88, 0);
  const rightArm = makeArm(1);
  rightArm.position.set(0.25, 1.88, 0);
  torso.add(leftArm, rightArm);

  const leftLeg = makeLeg(-1);
  leftLeg.position.set(-0.13, 0.78, 0);
  const rightLeg = makeLeg(1);
  rightLeg.position.set(0.13, 0.78, 0);
  person.add(leftLeg, rightLeg);

  if (phone) {
    const device = makePhone();
    device.scale.set(1.08, 1.08, 1.08);
    device.position.set(0.12, -0.74, 0.11);
    rightArm.add(device);
    rightArm.rotation.z = -0.42;
  }

  person.userData = { leftArm, rightArm, leftLeg, rightLeg, style };
  person.scale.setScalar(style === 'lady' ? 1.0 : 1.04);
  return person;
}

function addWalker(scene, style, start, end, speed, offset, phone = true) {
  const person = makePerson(style, phone);
  person.position.set(start[0], 0.08, start[1]);
  scene.add(person);
  state.walkers.push({ person, start: new THREE.Vector2(...start), end: new THREE.Vector2(...end), speed, offset });
  state.townspeople.push(person);
  return person;
}

function addStaticPerson(scene, style, x, z, activity = 'phone') {
  const person = makePerson(style, true);
  person.position.set(x, 0.08, z);
  scene.add(person);
  state.townspeople.push(person);
  state.ambientActors.push({ person, activity, offset: Math.random() * Math.PI * 2 });
  return person;
}

function addMarketCart(scene) {
  const cart = new THREE.Group();
  const counter = outlinedMesh(new THREE.BoxGeometry(2.5, 0.85, 1.25), PALETTE.paperShade, PALETTE.ink, 0.62);
  counter.position.y = 0.72;
  cart.add(counter);
  const roof = lineFrom([[-1.35, 2.35, -0.7], [-1.35, 2.35, 0.7], [1.35, 2.35, 0.7], [1.35, 2.35, -0.7], [-1.35, 2.35, -0.7]], darkLine);
  cart.add(roof);
  [-1.18, 1.18].forEach((x) => {
    cart.add(lineFrom([[x, 0.9, -0.62], [x, 2.35, -0.7], [x, 0.9, 0.62], [x, 2.35, 0.7]], inkLine));
  });
  for (let x = -0.9; x <= 0.9; x += 0.45) {
    const bottle = ellipseLine(0.09, 0.16, inkLine, 12);
    bottle.position.set(x, 1.25, 0.67);
    cart.add(bottle);
  }
  cart.position.set(-5.3, 0, 3.05);
  scene.add(cart);
  addStaticPerson(scene, 'worker', -3.45, 3.05, 'wave');
  addStaticPerson(scene, 'lady', -6.9, 2.75, 'phone');
}

function addFountain(scene) {
  const fountain = new THREE.Group();
  const basin = outlinedMesh(new THREE.CylinderGeometry(1.7, 1.9, 0.42, 28), PALETTE.paperShade, PALETTE.ink, 0.66);
  basin.position.y = 0.24;
  fountain.add(basin);
  const stem = outlinedMesh(new THREE.CylinderGeometry(0.18, 0.28, 1.8, 12), PALETTE.paperShade, PALETTE.ink, 0.66);
  stem.position.y = 1.18;
  fountain.add(stem);
  const bowl = outlinedMesh(new THREE.CylinderGeometry(0.75, 0.42, 0.32, 20), PALETTE.paperShade, PALETTE.ink, 0.66);
  bowl.position.y = 2.02;
  fountain.add(bowl);
  const water = new THREE.Group();
  [-0.36, -0.12, 0.12, 0.36].forEach((x, index) => {
    water.add(curvedStroke([
      [0, 2.58, 0],
      [x * 0.42, 2.74 + (index % 2) * 0.08, 0],
      [x, 2.22, 0],
      [x * 1.2, 1.88, 0],
    ], PALETTE.phone, 0.018, 0.58));
  });
  fountain.add(water);
  fountain.position.set(7.5, 0, 0);
  scene.add(fountain);
  state.ambientActors.push({ person: water, activity: 'water', offset: 0.8 });
}

function addTree(scene, x, z, scale = 1) {
  const tree = new THREE.Group();
  const trunk = curvedStroke([
    [0, 0, 0],
    [-0.08, 0.7, 0],
    [0.05, 1.5, 0],
    [-0.04, 2.25, 0],
  ], PALETTE.inkDark, 0.09, 0.86);
  tree.add(trunk);
  const crown = new THREE.Group();
  [
    [-0.55, 2.35, 0, 0.78, 0.7],
    [0.08, 2.58, 0.03, 0.9, 0.82],
    [0.65, 2.3, -0.02, 0.72, 0.64],
    [-0.05, 2.02, 0.06, 1.02, 0.68],
  ].forEach(([cx, cy, cz, rx, ry]) => {
    const leaf = ovalMesh(rx, ry, 0.34, PALETTE.camera, PALETTE.ink, 0.13);
    leaf.position.set(cx, cy, cz);
    crown.add(leaf);
  });
  tree.add(crown);
  tree.position.set(x, 0, z);
  tree.scale.setScalar(scale);
  scene.add(tree);
  state.ambientActors.push({ person: crown, activity: 'tree', offset: Math.random() * Math.PI * 2 });
}

function makeCameraLabel(text) {
  const labelCanvas = document.createElement('canvas');
  labelCanvas.width = 256;
  labelCanvas.height = 72;
  const context = labelCanvas.getContext('2d');
  context.fillStyle = 'rgba(37, 74, 52, 0.92)';
  context.beginPath();
  context.roundRect(3, 3, 250, 66, 22);
  context.fill();
  context.fillStyle = '#dff4d5';
  context.font = '600 24px ui-monospace, monospace';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText(text, 128, 37);
  const texture = new THREE.CanvasTexture(labelCanvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.scale.set(2.2, 0.62, 1);
  return sprite;
}

function addCctv(scene, position, rotationY = 0, pole = false, label = 'CAM') {
  const assembly = new THREE.Group();
  assembly.position.set(...position);
  assembly.rotation.y = rotationY;
  if (pole) {
    const post = outlinedMesh(new THREE.CylinderGeometry(0.11, 0.16, 5.2, 18), PALETTE.inkSoft, PALETTE.inkDark, 0.45);
    post.position.y = -2.5;
    assembly.add(post);
    const lampArm = curvedStroke([[0, -0.22, 0], [0.28, 0.08, 0], [0.64, 0.17, 0], [0.96, 0.08, 0]], PALETTE.inkDark, 0.045, 0.92);
    assembly.add(lampArm);
    const lamp = ovalMesh(0.25, 0.2, 0.24, PALETTE.paperLight, PALETTE.ink, 0.34);
    lamp.position.set(0.92, 0.05, 0);
    assembly.add(lamp);
  } else {
    assembly.add(curvedStroke([[0.12, -0.16, 0], [-0.25, 0.04, 0], [-0.58, 0.27, 0], [-0.82, -0.06, 0]], PALETTE.cameraDark, 0.052, 0.96));
  }

  const cameraHead = new THREE.Group();
  const cameraBody = ovalMesh(0.7, 0.28, 0.32, PALETTE.camera, PALETTE.cameraDark, 0.98);
  cameraBody.position.set(0.22, -0.18, 0);
  cameraHead.add(cameraBody);
  const hood = ovalMesh(0.76, 0.11, 0.39, PALETTE.cameraDark, PALETTE.cameraDark, 0.96);
  hood.position.set(0.26, -0.01, 0);
  hood.rotation.z = -0.04;
  cameraHead.add(hood);
  const barrel = outlinedMesh(new THREE.CylinderGeometry(0.19, 0.25, 0.4, 28), PALETTE.camera, PALETTE.cameraDark, 0.98, 28);
  barrel.rotation.z = Math.PI / 2;
  barrel.position.set(0.8, -0.18, 0);
  cameraHead.add(barrel);
  const lensMaterial = new THREE.MeshStandardMaterial({
    color: PALETTE.phoneGlow,
    emissive: PALETTE.phone,
    emissiveIntensity: 2.8,
    roughness: 0.25,
  });
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.145, 0.145, 0.055, 32), lensMaterial);
  lens.rotation.z = Math.PI / 2;
  lens.position.set(1.02, -0.18, 0);
  cameraHead.add(lens);
  const lensRing = new THREE.Mesh(
    new THREE.TorusGeometry(0.2, 0.035, 10, 32),
    new THREE.MeshBasicMaterial({ color: PALETTE.phoneGlow, transparent: true, opacity: 0.9, depthWrite: false }),
  );
  lensRing.rotation.y = Math.PI / 2;
  lensRing.position.copy(lens.position);
  lensRing.position.x += 0.045;
  cameraHead.add(lensRing);

  const pulseRing = lensRing.clone();
  pulseRing.material = lensRing.material.clone();
  pulseRing.material.opacity = 0.42;
  pulseRing.position.x += 0.035;
  cameraHead.add(pulseRing);
  assembly.add(cameraHead);

  const scanPivot = new THREE.Group();
  scanPivot.position.set(1.06, -0.2, 0);
  scanPivot.rotation.z = -1.03;
  const cone = new THREE.Mesh(
    new THREE.ConeGeometry(1.75, 6.2, 48, 1, true),
    new THREE.MeshBasicMaterial({ color: PALETTE.camera, transparent: true, opacity: 0.105, side: THREE.DoubleSide, depthWrite: false }),
  );
  cone.position.y = -3.06;
  scanPivot.add(cone);
  cameraHead.add(scanPivot);
  const badge = makeCameraLabel(`${label} · ACTIVE`);
  badge.position.set(0.18, 0.62, 0);
  assembly.add(badge);
  state.scanPivots.push({ pivot: scanPivot, base: rotationY, offset: Math.random() * Math.PI * 2 });
  state.cameraAssemblies.push({ assembly, head: cameraHead, pulseRing, lensMaterial, badge, offset: Math.random() * Math.PI * 2 });
  assembly.scale.setScalar(pole ? 1.28 : 1.34);
  scene.add(assembly);
}

function addSurveillance(scene) {
  addCctv(scene, [-12, 5.35, -4.45], 0.2, true, 'CAM 01');
  addCctv(scene, [0.1, 5.35, 4.45], Math.PI, true, 'CAM 02');
  addCctv(scene, [13.2, 5.35, -4.45], -0.1, true, 'CAM 03');
  addCctv(scene, [-6.4, 4.3, -5.0], 0.15, false, 'CAM 04');
  addCctv(scene, [7.2, 4.65, 5.05], Math.PI - 0.2, false, 'CAM 05');
}

function addCarriage(scene) {
  const carriage = new THREE.Group();
  const cabin = outlinedMesh(new THREE.BoxGeometry(2.2, 1.4, 1.25), PALETTE.paperShade, PALETTE.inkDark, 0.65);
  cabin.position.y = 1.15;
  carriage.add(cabin);
  const wheels = [];
  [-0.82, 0.82].forEach((x) => [-0.7, 0.7].forEach((z) => {
    const wheel = outlinedMesh(new THREE.TorusGeometry(0.48, 0.045, 8, 24), PALETTE.ink, PALETTE.inkDark, 0.4);
    wheel.position.set(x, 0.52, z);
    wheel.rotation.x = Math.PI / 2;
    carriage.add(wheel);
    wheels.push(wheel);
  }));
  const horse = new THREE.Group();
  const body = outlinedMesh(new THREE.SphereGeometry(0.7, 16, 10), PALETTE.paperShade, PALETTE.ink, 0.13);
  body.scale.set(1.45, 0.75, 0.75);
  body.position.y = 1.1;
  horse.add(body);
  const neck = curvedStroke([[0.42, 1.24, 0], [0.63, 1.46, 0], [0.82, 1.78, 0], [1.22, 1.88, 0]], PALETTE.inkDark, 0.065, 0.94);
  horse.add(neck);
  const head = ovalMesh(0.39, 0.25, 0.24, PALETTE.paperShade, PALETTE.inkDark, 0.7);
  head.position.set(1.35, 1.86, 0);
  head.rotation.z = -0.15;
  horse.add(head);
  const horseLegs = [];
  [-0.42, 0.35].forEach((x, index) => {
    const leg = curvedStroke([[x, 0.82, -0.25], [x + (index ? 0.08 : -0.06), 0.53, -0.25], [x - 0.08, 0.13, -0.25], [x + 0.22, 0.05, -0.25]], PALETTE.inkDark, 0.045, 0.95);
    horse.add(leg);
    horseLegs.push(leg);
  });
  horse.add(curvedStroke([[1.0, 2.03, 0], [1.14, 2.32, 0], [1.25, 2.09, 0]], PALETTE.inkDark, 0.035, 0.94));
  horse.position.x = 2.7;
  carriage.add(horse);
  carriage.userData = { wheels, horse, horseLegs };
  carriage.position.set(-20, 0, -1.55);
  scene.add(carriage);
  state.ambientActors.push({ person: carriage, activity: 'carriage', offset: 0 });
}

function addAmbientDetails(scene) {
  [
    [-11.2, 7.0, -8.0, 0.9],
    [3.7, 8.7, -8.2, 1.8],
    [12.8, 7.1, 7.6, 2.6],
  ].forEach(([x, y, z, offset]) => {
    const smoke = new THREE.Group();
    [0, 0.45, 0.9].forEach((rise, index) => {
      const puff = ovalMesh(0.2 + index * 0.1, 0.26 + index * 0.08, 0.12, PALETTE.paperLight, PALETTE.inkSoft, 0.12);
      puff.position.set(index * 0.12, rise, 0);
      smoke.add(puff);
    });
    smoke.position.set(x, y, z);
    scene.add(smoke);
    state.ambientActors.push({ person: smoke, activity: 'smoke', offset, base: smoke.position.clone() });
  });

  [
    [-4, 8.8, -4.5],
    [1.2, 9.6, -6.2],
    [8.2, 8.5, -3.2],
  ].forEach(([x, y, z], index) => {
    const bird = new THREE.Group();
    bird.add(curvedStroke([[-0.42, 0, 0], [-0.2, 0.15, 0], [0, 0.02, 0]], PALETTE.ink, 0.018, 0.58));
    bird.add(curvedStroke([[0, 0.02, 0], [0.2, 0.15, 0], [0.42, 0, 0]], PALETTE.ink, 0.018, 0.58));
    bird.position.set(x, y, z);
    scene.add(bird);
    state.ambientActors.push({ person: bird, activity: 'bird', offset: index * 1.7, base: bird.position.clone() });
  });
}

function addDust(scene) {
  const positions = [];
  for (let index = 0; index < 180; index += 1) {
    positions.push((Math.random() - 0.5) * 42, Math.random() * 8 + 0.2, (Math.random() - 0.5) * 22);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const points = new THREE.Points(geometry, new THREE.PointsMaterial({ color: PALETTE.inkSoft, size: 0.025, transparent: true, opacity: 0.3 }));
  scene.add(points);
  state.ambientActors.push({ person: points, activity: 'dust', offset: 0 });
}

function populateTown(scene) {
  addSurfaceLines(scene);
  addTownBuildings(scene);
  addMarketCart(scene);
  addFountain(scene);
  addTree(scene, -17.4, 4.1, 1.15);
  addTree(scene, 15.5, -4.1, 0.96);
  addTree(scene, 10.4, 4.25, 0.78);
  addSurveillance(scene);
  addCarriage(scene);
  addAmbientDetails(scene);
  addDust(scene);

  addWalker(scene, 'gentleman', [-17, -2.6], [16, -2.2], 0.035, 0.1, true);
  addWalker(scene, 'lady', [13, 2.2], [-15, 2.6], 0.027, 0.7, true);
  addWalker(scene, 'worker', [-10, 0.2], [11, 0.6], 0.042, 1.3, true);
  addWalker(scene, 'gentleman', [17, 3.0], [-17, 3.25], 0.022, 0.25, false);
  addWalker(scene, 'lady', [-2, -3.15], [10, -3.0], 0.03, 1.6, true);
  addStaticPerson(scene, 'gentleman', 6.0, 1.4, 'conversation');
  addStaticPerson(scene, 'lady', 5.15, 1.2, 'conversation');
  addStaticPerson(scene, 'worker', 11.8, -3.1, 'phone');
}

function initScene() {
  if (state.initialized) return;
  state.initialized = true;
  try {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.65));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    renderer.shadowMap.enabled = false;
    state.renderer = renderer;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(PALETTE.paper, 0.022);
    state.scene = scene;
    const camera = new THREE.OrthographicCamera(-18, 18, 11, -11, 0.1, 120);
    camera.position.copy(state.cameraBase);
    camera.lookAt(0, 1.8, 0);
    state.camera = camera;

    scene.add(new THREE.HemisphereLight(PALETTE.paperLight, PALETTE.paperShade, 2.6));
    const sun = new THREE.DirectionalLight(0xfff0cf, 2.1);
    sun.position.set(-12, 22, 10);
    scene.add(sun);
    const greenGlow = new THREE.PointLight(PALETTE.camera, 0.55, 22);
    greenGlow.position.set(0, 6, 0);
    scene.add(greenGlow);

    const sceneRoot = new THREE.Group();
    sceneRoot.position.y = prefersReducedMotion ? 0 : -0.9;
    sceneRoot.scale.setScalar(prefersReducedMotion ? 1 : 0.94);
    scene.add(sceneRoot);
    state.sceneRoot = sceneRoot;
    populateTown(sceneRoot);
    resize();
    const pointerGuidance = window.matchMedia('(pointer: coarse)').matches ? 'touch and drag to look around' : 'move your pointer to look around';
    status.textContent = prefersReducedMotion ? 'Town tableau ready · reduced motion' : `Town memory ready · ${pointerGuidance}`;
    root.dataset.state = 'ready';
    enterButton.disabled = false;
    revealCopy();
  } catch (error) {
    console.warn('Could not initialize the 3D town intro', error);
    root.classList.add('intro-fallback');
    root.dataset.state = 'ready';
    status.textContent = 'Illustrated fallback ready';
    enterButton.disabled = false;
    revealCopy();
  }
}

function revealCopy() {
  root.querySelectorAll('.intro-masthead, .intro-observer-card, .intro-look-cue, .intro-footer').forEach((element) => element.classList.add('revealed'));
  const elements = root.querySelectorAll('.intro-copy .intro-reveal');
  const anime = window.anime;
  if (!prefersReducedMotion && anime?.animate && anime?.stagger) {
    anime.animate(elements, {
      opacity: 1,
      y: { from: 24 },
      duration: 950,
      delay: anime.stagger(105, { start: 180 }),
      ease: 'outExpo',
    });
  } else {
    elements.forEach((element) => element.classList.add('revealed'));
  }
}

function resize() {
  if (!state.renderer || !state.camera) return;
  const width = root.clientWidth || window.innerWidth;
  const height = root.clientHeight || window.innerHeight;
  const aspect = width / Math.max(height, 1);
  const viewHeight = aspect < 0.85 ? 24 : 20;
  state.camera.left = -(viewHeight * aspect) / 2;
  state.camera.right = (viewHeight * aspect) / 2;
  state.camera.top = viewHeight / 2;
  state.camera.bottom = -viewHeight / 2;
  state.camera.updateProjectionMatrix();
  state.renderer.setSize(width, height, false);
  state.cameraAssemblies.forEach(({ badge }) => {
    badge.scale.set(aspect < 0.85 ? 1.62 : 2.2, aspect < 0.85 ? 0.47 : 0.62, 1);
  });
}

function updateTown(elapsed) {
  const revealProgress = THREE.MathUtils.clamp(elapsed / 2.25, 0, 1);
  const revealEase = 1 - ((1 - revealProgress) ** 3);
  state.pointer.lerp(state.targetPointer, 0.035);
  state.camera.position.x = state.cameraBase.x + state.pointer.x * 1.45;
  state.camera.position.y = state.cameraBase.y + state.pointer.y * 0.65;
  state.camera.lookAt(state.pointer.x * 0.7, 1.8 + state.pointer.y * 0.2, 0);
  state.camera.zoom = THREE.MathUtils.lerp(0.84, 1, revealEase);
  state.camera.updateProjectionMatrix();
  if (state.sceneRoot) {
    state.sceneRoot.position.y = THREE.MathUtils.lerp(-0.9, 0, revealEase);
    state.sceneRoot.scale.setScalar(THREE.MathUtils.lerp(0.94, 1, revealEase));
    state.sceneRoot.rotation.y = THREE.MathUtils.lerp(-0.045, 0, revealEase);
  }

  state.walkers.forEach((walker) => {
    const cycle = (elapsed * walker.speed + walker.offset) % 2;
    const progress = cycle <= 1 ? cycle : 2 - cycle;
    const direction = cycle <= 1 ? 1 : -1;
    const x = THREE.MathUtils.lerp(walker.start.x, walker.end.x, progress);
    const z = THREE.MathUtils.lerp(walker.start.y, walker.end.y, progress);
    const cadence = elapsed * 5.2 + walker.offset * 4;
    walker.person.position.set(x, 0.08 + Math.abs(Math.sin(cadence)) * 0.035, z);
    walker.person.scale.x = Math.abs(walker.person.scale.x) * direction;
    const { leftArm, rightArm, leftLeg, rightLeg } = walker.person.userData;
    leftLeg.rotation.z = Math.sin(cadence) * 0.34;
    rightLeg.rotation.z = -Math.sin(cadence) * 0.34;
    leftArm.rotation.z = -Math.sin(cadence) * 0.2;
    rightArm.rotation.z = -0.34 + Math.sin(cadence) * 0.08;
  });

  state.ambientActors.forEach((actor) => {
    const rhythm = elapsed * 1.8 + actor.offset;
    if (actor.activity === 'wave') actor.person.userData.rightArm.rotation.z = -1.0 + Math.sin(rhythm * 1.8) * 0.35;
    if (actor.activity === 'phone') actor.person.userData.rightArm.rotation.z = -0.85 + Math.sin(rhythm) * 0.04;
    if (actor.activity === 'conversation') actor.person.userData.leftArm.rotation.z = Math.sin(rhythm) * 0.18;
    if (actor.activity === 'carriage') {
      actor.person.position.x = ((elapsed * 1.1 + 20) % 44) - 22;
      actor.person.position.y = Math.abs(Math.sin(elapsed * 3.4)) * 0.025;
      actor.person.userData.wheels?.forEach((wheel) => { wheel.rotation.z = -elapsed * 2.25; });
      if (actor.person.userData.horse) actor.person.userData.horse.position.y = Math.abs(Math.sin(elapsed * 4.2)) * 0.04;
    }
    if (actor.activity === 'dust') actor.person.rotation.y = elapsed * 0.012;
    if (actor.activity === 'water') {
      actor.person.scale.y = 0.96 + Math.sin(rhythm * 1.8) * 0.04;
      actor.person.rotation.y = Math.sin(rhythm * 0.35) * 0.08;
    }
    if (actor.activity === 'tree') actor.person.rotation.z = Math.sin(rhythm * 0.38) * 0.025;
    if (actor.activity === 'smoke') {
      actor.person.position.y = actor.base.y + Math.sin(rhythm * 0.45) * 0.12;
      actor.person.position.x = actor.base.x + Math.sin(rhythm * 0.3) * 0.08;
      actor.person.scale.setScalar(1 + Math.sin(rhythm * 0.35) * 0.05);
    }
    if (actor.activity === 'bird') {
      actor.person.children[0].rotation.z = Math.sin(rhythm * 2.2) * 0.22;
      actor.person.children[1].rotation.z = -Math.sin(rhythm * 2.2) * 0.22;
      actor.person.position.x = actor.base.x + Math.sin(rhythm * 0.22) * 1.1;
      actor.person.position.y = actor.base.y + Math.sin(rhythm * 0.41) * 0.18;
    }
  });

  state.scanPivots.forEach(({ pivot, offset }, index) => {
    pivot.rotation.y = Math.sin(elapsed * 0.55 + offset) * (index % 2 ? 0.38 : 0.52);
  });

  state.cameraAssemblies.forEach(({ head, pulseRing, lensMaterial, badge, offset }, index) => {
    const pulse = (Math.sin(elapsed * 2.1 + offset) + 1) / 2;
    head.rotation.y = Math.sin(elapsed * 0.42 + offset) * (index % 2 ? 0.11 : 0.16);
    pulseRing.scale.setScalar(1 + pulse * 0.72);
    pulseRing.material.opacity = 0.5 * (1 - pulse);
    lensMaterial.emissiveIntensity = 2.25 + pulse * 2.3;
    badge.position.y = 0.62 + Math.sin(elapsed * 1.1 + offset) * 0.025;
  });

  state.clockHands.forEach((hands) => { hands.rotation.z = -elapsed * 0.018; });

  state.townspeople.forEach((person) => {
    const dx = state.camera.position.x - person.position.x;
    const dz = state.camera.position.z - person.position.z;
    const sign = Math.sign(person.scale.x) || 1;
    person.rotation.y = Math.atan2(dx, dz);
    person.scale.x = Math.abs(person.scale.x) * sign;
  });
}

function renderFrame() {
  if (!state.running || !state.renderer || !state.scene || !state.camera) return;
  const elapsed = state.clock.getElapsedTime();
  if (!prefersReducedMotion) updateTown(elapsed);
  state.renderer.render(state.scene, state.camera);
  if (!prefersReducedMotion) state.frame = requestAnimationFrame(renderFrame);
}

function start() {
  initScene();
  if (!state.renderer || state.running) return;
  state.running = true;
  state.clock.start();
  renderFrame();
}

function stop() {
  state.running = false;
  if (state.frame) cancelAnimationFrame(state.frame);
  state.frame = null;
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
  state.targetPointer.set((event.clientX / window.innerWidth - 0.5) * 2, -(event.clientY / window.innerHeight - 0.5) * 2);
});
root.addEventListener('pointerleave', () => state.targetPointer.set(0, 0));
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

import * as THREE from './vendor/three.module.min.js';
import {walkingRoute,walkingTravel,poseWalking} from './assets/walking.js';

// One locally rendered world. Distances are metres; motion uses elapsed seconds.
const C = { paper: 0xf5eddf, plaster: 0xe3caa1, light: 0xf7e5bf, timber: 0x654732, roof: 0x75523f, brick: 0x9c7054, dark: 0x3e332c, glass: 0x71887d, stone: 0xc2a889, brass: 0xac8853, green: 0x567566, signal: 0xd7f4bf };
const UP = new THREE.Vector3(0, 1, 0);
const TAU = Math.PI * 2;
const seeded = (seed = 887) => () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };

export class TownScene {
  constructor(canvas, labels, onStage) {
    this.canvas = canvas;
    this.labels = labels;
    this.onStage = onStage;
    this.time = 1.5;
    this.stage = 0;
    this.storyOffset = 0;
    this.running = false;
    this.paused = false;
    this.reduced = false;
    this.pointer = new THREE.Vector2();
    this.smoothPointer = new THREE.Vector2();
    this.random = seeded();
    this.materials = new Map();
    this.actors = [];
    this.cameras = [];
    this.smoke = [];
    this.leaves = [];
    this.vehicles = [];
    this.birds = [];
    this.dynamicBatches = [];
    this.geometryCache = new Map();
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(C.paper, 34, 76);
    this.camera = new THREE.PerspectiveCamera(39, 1, .1, 120);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.65));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(C.paper, 0);
    this.scene.add(new THREE.HemisphereLight(0xfff4e0, 0x96836b, 2.2));
    const sunlight = new THREE.DirectionalLight(0xffefd5, 2.2);
    sunlight.position.set(-16, 28, 12);
    sunlight.castShadow = true;
    sunlight.shadow.mapSize.set(2048, 2048);
    Object.assign(sunlight.shadow.camera, { left: -26, right: 26, top: 26, bottom: -26, near: .5, far: 80 });
    sunlight.shadow.bias = -.0008;
    sunlight.shadow.normalBias = .035;
    this.scene.add(sunlight);
    this.sun = sunlight;
    const ramp = new THREE.DataTexture(new Uint8Array([100, 175, 225, 255]), 4, 1, THREE.RedFormat);
    ramp.needsUpdate = true;
    ramp.magFilter = ramp.minFilter = THREE.NearestFilter;
    this.ramp = ramp;
    this.boxGeometry = new THREE.BoxGeometry(1, 1, 1);
    this.sphereGeometry = new THREE.SphereGeometry(1, 16, 12);
    this.buildStreet();
    this.buildTown();
    this.batchArchitecture();
    this.buildLife();
    this.batchAnimatedMeshes();
    this.buildNetwork();
    this.buildAtmosphere();
    this.resize();
    this.update(0);
  }

  material(color, unlit = false) {
    const key = `${color}:${unlit}`;
    if (!this.materials.has(key)) this.materials.set(key, unlit
      ? new THREE.MeshBasicMaterial({ color })
      : new THREE.MeshToonMaterial({ color, gradientMap: this.ramp }));
    return this.materials.get(key);
  }
  box(parent, x, y, z, w, h, d, color, edges = false) {
    const mesh = new THREE.Mesh(this.boxGeometry, this.material(color));
    mesh.position.set(x, y, z); mesh.scale.set(w, h, d);
    mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh);
    if (edges) {
      const line = new THREE.LineSegments(new THREE.EdgesGeometry(this.boxGeometry), new THREE.LineBasicMaterial({ color: C.timber, transparent: true, opacity: .2 }));
      mesh.add(line);
    }
    return mesh;
  }
  cylinder(parent, x, y, z, r1, r2, height, color, segments = 8) {
    const key=`cylinder/${r1}/${r2}/${height}/${segments}`;
    if(!this.geometryCache.has(key))this.geometryCache.set(key,new THREE.CylinderGeometry(r1,r2,height,segments));
    const mesh = new THREE.Mesh(this.geometryCache.get(key), this.material(color));
    mesh.position.set(x, y, z); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }
  orb(parent, x, y, z, radius, color) {
    const mesh = new THREE.Mesh(this.sphereGeometry, this.material(color));
    mesh.position.set(x, y, z); mesh.scale.setScalar(radius); mesh.castShadow = true; parent.add(mesh); return mesh;
  }
  beam(parent, a, b, width, color) {
    const from = new THREE.Vector3(...a), to = new THREE.Vector3(...b);
    const mesh = this.box(parent, 0, 0, 0, width, from.distanceTo(to), width, color);
    mesh.position.copy(from).add(to).multiplyScalar(.5);
    mesh.quaternion.setFromUnitVectors(UP, to.sub(from).normalize()); return mesh;
  }
  roundedGeometry(w, h, d, radius = .04) {
    const key = `${w}/${h}/${d}/${radius}`;
    if (this.geometryCache.has(key)) return this.geometryCache.get(key);
    const r = Math.min(radius, w / 3, h / 3, d / 3);
    const geometry = new THREE.BoxGeometry(w, h, d, 4, 4, 4);
    const p = geometry.attributes.position, n = geometry.attributes.normal;
    const inner = new THREE.Vector3(w / 2 - r, h / 2 - r, d / 2 - r);
    const vertex = new THREE.Vector3(), nearest = new THREE.Vector3(), normal = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      vertex.fromBufferAttribute(p, i);
      nearest.copy(vertex).clamp(inner.clone().negate(), inner);
      normal.copy(vertex).sub(nearest).normalize();
      vertex.copy(nearest).addScaledVector(normal, r);
      p.setXYZ(i, vertex.x, vertex.y, vertex.z); n.setXYZ(i, normal.x, normal.y, normal.z);
    }
    this.geometryCache.set(key, geometry); return geometry;
  }
  rounded(parent, x, y, z, w, h, d, color, radius = .04) {
    const mesh = new THREE.Mesh(this.roundedGeometry(w, h, d, radius), this.material(color));
    mesh.position.set(x, y, z); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }
  ellipsoid(parent, x, y, z, w, h, d, color) {
    const mesh = this.orb(parent, x, y, z, 1, color); mesh.scale.set(w, h, d); return mesh;
  }
  tube(parent, a, b, radius, color, topRadius = radius) {
    const from = new THREE.Vector3(...a), to = new THREE.Vector3(...b);
    const mesh = this.cylinder(parent, 0, 0, 0, topRadius, radius, from.distanceTo(to), color, 12);
    mesh.position.copy(from).add(to).multiplyScalar(.5);
    mesh.quaternion.setFromUnitVectors(UP, to.sub(from).normalize()); return mesh;
  }
  torus(parent, x, y, z, radius, thickness, color) {
    const key=`torus/${radius}/${thickness}`;
    if(!this.geometryCache.has(key))this.geometryCache.set(key,new THREE.TorusGeometry(radius,thickness,6,32));
    const mesh = new THREE.Mesh(this.geometryCache.get(key), this.material(color));
    mesh.position.set(x, y, z); mesh.castShadow = true; parent.add(mesh); return mesh;
  }
  lathe(parent, profile, color, segments = 20) {
    const key=`lathe/${profile.flat().join('/')}/${segments}`;
    if(!this.geometryCache.has(key))this.geometryCache.set(key,new THREE.LatheGeometry(profile.map(([r,y])=>new THREE.Vector2(r,y)),segments));
    const mesh = new THREE.Mesh(this.geometryCache.get(key), this.material(color));
    mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }
  // Lettering is a native texture on a physical sign, not a scene billboard.
  sign(parent, text, x, y, z, width, height, bg = '#493b2e', fg = '#f3dfb7') {
    const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 128;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, 512, 128);
    ctx.strokeStyle = fg; ctx.lineWidth = 2; ctx.strokeRect(9, 9, 494, 110);
    ctx.fillStyle = fg; ctx.font = '36px Georgia'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, 256, 65, 460);
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({ map: texture }));
    mesh.position.set(x, y, z); parent.add(mesh); return mesh;
  }

  buildStreet() {
    this.box(this.scene, 0, -.17, 0, 130, .25, 130, 0xd6bea0);
    // Instanced cobbles retain real shadows/depth without thousands of draw calls.
    const tiles = [];
    this.cobbleHeights=new Map();
    for (let x = -32; x < 32; x += .64) for (let z = -32; z < 32; z += .43) {
      if (Math.abs(x) > 4.35 && Math.abs(z) > 4.35) continue;
      tiles.push([x + (Math.round(z / .43) % 2) * .3, z]);
    }
    const stones = new THREE.InstancedMesh(this.roundedGeometry(1, 1, 1, .08), this.material(C.stone), tiles.length);
    const dummy = new THREE.Object3D(), color = new THREE.Color();
    tiles.forEach(([x, z], i) => {
      dummy.position.set(x, -.035 + this.random() * .023, z);
      dummy.scale.set(.57 + this.random() * .035, .09, .36 + this.random() * .025);
      const row=Math.round((z+32)/.43),shift=(Math.round(z/.43)%2)*.3;
      this.cobbleHeights.set(`${Math.round((x+32-shift)/.64)}/${row}`,{x,z,top:dummy.position.y+.045});
      dummy.rotation.y = (this.random() - .5) * .05; dummy.updateMatrix(); stones.setMatrixAt(i, dummy.matrix);
      color.setHex([0xcab597, 0xbfa589, 0xd0b99b, 0xb7a18a, 0xc4af94][i % 5]); stones.setColorAt(i, color);
    });
    stones.receiveShadow = true; this.scene.add(stones);
    for (const x of [-1, 1]) for (const z of [-1, 1]) {
      this.box(this.scene, x * 15.4, .045, z * 15.4, 21.5, .25, 21.5, 0xe2cfaf);
      for (let k = 4.7; k < 28; k += 1.05) {
        this.box(this.scene, x * 4.6, .15, z * k, .25, .32, 1, 0xcab699);
        this.box(this.scene, x * k, .15, z * 4.6, 1, .32, .25, 0xcab699);
      }
    }
    // Four perpendicular approaches, four square pavement corners; never a fork.
    for (const side of [-1, 1]) for (let i = -3; i <= 3; i++) {
      this.rounded(this.scene, i * .85, .035, side * 3.65, .62, .035, .48, 0xe1caaa, .015);
      this.rounded(this.scene, side * 3.65, .035, i * .85, .48, .035, .62, 0xe1caaa, .015);
    }
    const flags = new THREE.InstancedMesh(this.roundedGeometry(1, .07, 1, .02), this.material(0xe0ccaa), 4 * 18 * 18);
    let flagIndex = 0;
    for (const sx of [-1,1]) for (const sz of [-1,1]) for (let x=0;x<18;x++) for(let z=0;z<18;z++) {
      dummy.position.set(sx*(5.18+x*1.06),.202,sz*(5.18+z*1.06)); dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();flags.setMatrixAt(flagIndex,dummy.matrix);
      color.setHex([0xe0ccaa,0xdcc7a7,0xe2d0b4][flagIndex%3]);flags.setColorAt(flagIndex++,color);
    }
    flags.receiveShadow=true;this.scene.add(flags);
    for(const sx of [-1,1]) for(const sz of [-1,1]) {
      this.rounded(this.scene,sx*4.18,.035,sz*5.5,.42,.055,.72,C.dark,.02);
      for(let i=0;i<7;i++)this.box(this.scene,sx*4.18,.07,sz*5.5+(i-3)*.09,.34,.02,.035,0x8c8778);
    }
  }

  window(parent, x, y, z, w = .82, h = 1.3, shutters = true) {
    this.box(parent, x, y, z, w + .2, h + .2, .12, C.timber);
    this.box(parent, x, y, z + .075, w, h, .055, C.glass);
    // Recessed frames, an inner reveal and a quiet reflection in the upper pane.
    for (const side of [-1,1]) this.box(parent,x+side*(w/2-.055),y,z+.13,.065,h,.045,0xd6bc92);
    this.box(parent,x-w*.2,y+h*.24,z+.112,w*.28,.035,.015,0xb9cab3).rotation.z=.28;
    this.box(parent, x, y, z + .115, .065, h, .06, C.light);
    this.box(parent, x, y + .05, z + .12, w, .065, .06, C.light);
    this.box(parent, x, y - h / 2 - .12, z + .11, w + .38, .15, .32, C.light);
    if (shutters) for (const side of [-1, 1]) {
      this.box(parent, x + side * (w / 2 + .24), y, z + .04, .28, h, .075, C.green);
      for (let j = 0; j < 7; j++) this.box(parent, x + side * (w / 2 + .24), y - h * .4 + j * h / 8, z + .09, .24, .035, .035, 0x7a8970);
    }
  }
  building(x, z, w, d, h, color, name, rotation = 0, awning = false) {
    const g = new THREE.Group(); g.position.set(x, .22, z); g.rotation.y = rotation; this.scene.add(g);
    this.box(g, 0, h / 2, 0, w, h, d, color, true);
    this.box(g, 0, .25, 0, w + .14, .5, d + .14, 0xbaa184);
    for (const y of [2.65, h - .12]) this.box(g, 0, y, 0, w + .25, .16, d + .25, C.light);
    for (const dx of [-w / 2 + .12, w / 2 - .12]) {
      this.box(g, dx, h / 2, d / 2 + .045, .17, h, .12, C.timber);
      for (let j = 0; j < h; j += .45) this.box(g, dx, j + .2, d / 2 + .08, .34, .16, .16, 0xd9c09d);
    }
    // Two pitched planes, overhanging eaves, ridge, individual horizontal tile courses.
    const rise = w * .32, slope = Math.atan2(rise, w / 2 + .3), length = Math.hypot(w / 2 + .3, rise);
    for (const side of [-1, 1]) {
      const roof = this.box(g, side * (w / 4 + .15), h + rise / 2, 0, length, .14, d + .75, C.roof);
      roof.rotation.z = -side * slope;
      for (let i = 0; i < 14; i++) {
        const u = (i + .5) / 14;
        const course = this.box(g, side * u * (w / 2 + .3), h + rise * (1 - u) + .1, 0, .035, .035, d + .76, 0x9c7253);
        course.rotation.z = -side * slope;
        if (name) for(let tz=-d/2-.3+(i%2)*.22;tz<d/2+.3;tz+=.44) {
          const joint=this.box(g,side*u*(w/2+.3),h+rise*(1-u)+.08,tz,length/14,.025,.018,0x674d3d);
          joint.rotation.z=-side*slope;
        }
      }
      const gutter=this.cylinder(g,side*(w/2+.24),h-.06,0,.045,.045,d+.8,0x6e6452,12);gutter.rotation.x=Math.PI/2;
    }
    const triangle = new THREE.Shape(); triangle.moveTo(-w / 2, 0); triangle.lineTo(w / 2, 0); triangle.lineTo(0, rise); triangle.closePath();
    const gable = new THREE.Mesh(new THREE.ShapeGeometry(triangle), this.material(color));
    gable.position.set(0, h, d / 2 + .015); g.add(gable);
    this.beam(g, [-w / 2, h, d / 2 + .05], [0, h + rise, d / 2 + .05], .12, C.timber);
    this.beam(g, [0, h + rise, d / 2 + .05], [w / 2, h, d / 2 + .05], .12, C.timber);
    this.box(g, 0, h + rise * .46, d / 2 + .06, .1, rise * .85, .1, C.timber);
    this.box(g, 0, h + rise + .06, 0, .22, .18, d + .8, C.timber);
    for(let dz=-d/2;dz<d/2;dz+=.32) {
      const ridge=this.cylinder(g,0,h+rise+.08,dz,.13,.13,.3,0x8b674c,12);ridge.rotation.x=Math.PI/2;
    }
    if (name === 'THE CORNER HOUSE' || name === 'THE CLOCKMAKER') {
      const dial = new THREE.Group(); dial.position.set(0, h + rise * .4, d / 2 + .13); g.add(dial);
      const rim = this.cylinder(dial, 0, 0, 0, .43, .43, .07, C.brass, 32); rim.rotation.x = Math.PI / 2;
      const face = this.cylinder(dial, 0, 0, .05, .37, .37, .045, C.light, 32); face.rotation.x = Math.PI / 2;
      for (let i = 0; i < 12; i++) {
        const angle = i * TAU / 12;
        const tick = this.box(dial, Math.sin(angle) * .3, Math.cos(angle) * .3, .085, .022, .057, .015, C.timber);
        tick.rotation.z = -angle;
      }
      this.beam(dial, [0,0,.1], [-.16,.09,.1], .025, C.dark);
      this.beam(dial, [0,0,.1], [.06,.24,.1], .018, C.dark);
    }
    const chimneyX = -w * .28, chimneyY = h + rise * .8;
    this.box(g, chimneyX, chimneyY, -.7, .65, 1.8, .65, C.brick, true);
    this.box(g, chimneyX, chimneyY + .85, -.7, .82, .2, .8, C.light);
    for (let i = 0; i < 4; i++) this.box(g, chimneyX, chimneyY - .5 + i * .36, -.355, .66, .04, .025, C.light);
    const source = new THREE.Vector3(chimneyX, chimneyY + 1, -.7); g.localToWorld(source);
    for (let i = 0; i < 5; i++) {
      const m = new THREE.Mesh(this.sphereGeometry, new THREE.MeshBasicMaterial({ color: 0xfff3dd, transparent: true, opacity: .15, depthWrite: false }));
      this.scene.add(m); this.smoke.push({ mesh: m, source, offset: i * .2 });
    }
    for (let y = 3.6; y < h - .3; y += 2.0) for (let wx = -w / 2 + 1; wx < w / 2 - .5; wx += 1.7) this.window(g, wx, y, d / 2 + .06, .72, 1.15);
    // Side facade gives the junction convincing depth at the diagonal viewpoint.
    const side = new THREE.Group(); side.position.set(w / 2 + .03, 0, 0); side.rotation.y = Math.PI / 2; g.add(side);
    for (let y = 3.6; y < h - .3; y += 2) for (let sx = -d / 2 + 1; sx < d / 2 - .5; sx += 1.6) this.window(side, sx, y, .01, .68, 1.12, false);
    // Recessed door and glazed shop window.
    this.box(g, -w * .29, 1.25, d / 2 + .07, 1.05, 2.1, .15, C.timber);
    this.box(g, -w * .29, 1.58, d / 2 + .16, .76, 1.15, .045, C.glass);
    this.orb(g, -w * .29 + .3, 1.05, d / 2 + .22, .055, C.brass);
    this.window(g, w * .18, 1.38, d / 2 + .1, w * .39, 1.65, false);
    this.sign(g, name, 0, 2.48, d / 2 + .23, w - .35, .43);
    if(name) {
      this.facadeDetails(g,w,d,h);
      // The second street-facing wall gets a shop window and its own fascia.
      this.window(side,0,1.35,.05,Math.min(2.4,d*.48),1.75,false);
      this.sign(side,name,0,2.48,.18,d-.4,.42);
      const arch=new THREE.Mesh(new THREE.TorusGeometry(.51,.075,8,24,Math.PI),this.material(C.light));
      arch.position.set(-w*.29,1.85,d/2+.25);g.add(arch);
      this.box(g,-w*.29,1.32,d/2+.21,.04,1.05,.025,C.light);
      this.box(g,-w*.29,1.85,d/2+.23,.93,.05,.025,C.light);
    }
    if (awning) {
      const aw = new THREE.Group(); aw.position.set(0, 2.25, d / 2 + .63); aw.rotation.x = .17; g.add(aw);
      for (let i = 0; i < 12; i++) {
        this.box(aw, -w / 2 + w * (i + .5) / 12, 0, 0, w / 12, .08, 1.5, i % 2 ? 0xf0dfbb : 0x8c7456);
        this.box(aw, -w / 2 + w * (i + .5) / 12, -.12, .72, w / 12, .28, .08, i % 2 ? 0xf0dfbb : 0x8c7456);
        const scallop=this.cylinder(aw,-w/2+w*(i+.5)/12,-.26,.73,w/24,w/24,.06,i%2?0xf0dfbb:0x8c7456,16);scallop.rotation.x=Math.PI/2;
      }
    }
    return g;
  }

  facadeDetails(g,w,d,h) {
    // Masonry joints remain shallow, so plaster still carries the warm palette.
    for(let y=.35;y<2.4;y+=.3)for(let x=-w/2+.2;x<w/2-.2;x+=.7) {
      const bx=x+((Math.round(y/.3)%2)*.32);
      if(bx<w/2-.15)this.box(g,bx,y,d/2+.012,.58,.014,.015,0xc9b08b);
    }
    for(const x of [-w/2+.25,w/2-.25]) {
      this.cylinder(g,x,h/2,d/2+.16,.034,.034,h,0x797563,12);
      for(let y=.5;y<h;y+=1.3)this.torus(g,x,y,d/2+.16,.05,.016,C.brass).rotation.x=Math.PI/2;
    }
    if(h>5) {
      const bw=Math.min(w-1.3,3.5), by=3.01, bz=d/2+.48;
      this.rounded(g,0,by,bz,bw+.2,.14,.85,0xc2ac89,.045);
      for(let x=-bw/2;x<=bw/2;x+=.22) {
        this.cylinder(g,x,by+.43,bz+.42,.018,.018,.8,C.dark,8);
        this.torus(g,x,by+.43,bz+.43,.075,.012,C.dark);
      }
      this.tube(g,[-bw/2,by+.85,bz+.42],[bw/2,by+.85,bz+.42],.025,C.dark);
      for(const x of [-bw/2,bw/2])this.tube(g,[x,by+.85,d/2],[x,by+.85,bz+.42],.025,C.dark);
      this.rounded(g,w*.22,by+.21,bz,.8,.25,.25,C.timber,.025);
      for(let i=0;i<9;i++) {
        const x=w*.22+(i-4)*.08;
        this.ellipsoid(g,x,by+.4,bz,.085,.17,.1,0x788564);
        this.orb(g,x,by+.53+(i%2)*.06,bz+.015,.055,[0xc49b75,0xe6cbb3,0xb38869][i%3]);
      }
    }
    const bracketX=w*.4;
    this.tube(g,[bracketX,2.9,d/2],[bracketX,2.9,d/2+1],.025,C.dark);
    this.torus(g,bracketX,2.75,d/2+.55,.18,.018,C.dark).rotation.y=Math.PI/2;
    const hanging=this.sign(g,'EST. 1887',bracketX,2.5,d/2+.85,.72,.43);hanging.rotation.y=Math.PI/2;
  }

  buildTown() {
    this.building(-8.8, -9, 6.3, 5.8, 6.2, C.plaster, 'THE CORNER HOUSE', 0, true);
    this.building(-14.0, -8.6, 5.1, 6.8, 7.8, 0xd8c3a2, 'BOOKS & LETTERS');
    this.building(-19.0, -8.2, 4.7, 6, 6.4, 0xdcc29b, 'MERCER & SONS', 0, true);
    this.building(8.8, -10.5, 6.1, 6.5, 6.6, 0xefdcba, 'MAISON DU CAFÉ', 0, true);
    this.building(14, -8.1, 5.2, 6.1, 7.6, 0xc6af91, 'THE CLOCKMAKER');
    this.building(19, -8.7, 4.5, 6.5, 6.4, 0xe9d5b1, 'ATELIER', 0, true);
    this.building(-9.7, 9.8, 7.4, 7.8, 5.7, 0xc8af8e, 'BOTANICAL & CO.', Math.PI / 2, true);
    this.building(-9, 17.1, 7.4, 5.6, 6.8, C.plaster, 'THE PRINTING OFFICE', Math.PI / 2);
    // Low foreground market keeps the central four-way crossing readable.
    this.building(14.5, 14.5, 7, 5.6, 3.1, 0xdfc7a2, 'FLOWERS & FINDS', -Math.PI / 2, true);
    this.building(24.1, 13.4, 5.4, 7.4, 6.4, 0xcab291, 'THE TEA ROOMS', -Math.PI / 2);
    // Receding silhouettes and smaller houses make the streets continue beyond the frame.
    for (let i = 0; i < 5; i++) {
      this.building(-8, -17 - i * 7, 5.7, 5.8, 5.8 + i % 3, 0xd8c8ad, '');
      this.building(8, -17 - i * 7, 5.7, 5.8, 6.7 - i % 2, 0xe2d1b2, '');
    }
    for (const [x,z] of [[-4.8,-4.9],[4.9,-4.8],[-4.9,5],[5,5.1],[-4.8,-15],[4.9,17]]) this.lamp(x,z);
    for (const [x,z,s] of [[-15,4.9,1.1],[6.1,-13,1.1],[11.5,7.8,.72],[16,5.2,1.0],[-6.3,13,.9],[-19,-4.2,.7]]) this.tree(x,z,s);
    // Timber bench and raised flower beds soften the near corner.
    for (let i = 0; i < 4; i++) this.box(this.scene, 6.4 + i * .14, .7, 8.6, .11, .1, 1.8, C.timber);
    for (const z of [8, 9.2]) {
      this.box(this.scene, 6.65, .4, z, .08, .6, .08, C.dark);
      this.box(this.scene, 6.2, 1.05, z, .07, .85, .08, C.dark);
    }
    for (const y of [1, 1.2]) this.box(this.scene, 6.18, y, 8.6, .1, .13, 1.8, C.timber);
    for (const [x,z] of [[-5.6,-5.8],[6.0,-5.8],[8,-4.8],[-5.5,9.5],[7.4,6.1]]) {
      this.cylinder(this.scene, x,.43,z,.32,.23,.65,0x9e7654);
      for (let i = 0; i < 6; i++) this.orb(this.scene,x+(this.random()-.5)*.5,.86+this.random()*.25,z+(this.random()-.5)*.5,.22,[0x859064,0xb5a677,0xc4a778][i%3]);
    }
    this.cart = new THREE.Group(); this.cart.position.set(-7,.25,-5.35); this.cart.rotation.y=Math.PI/2; this.scene.add(this.cart);
    this.box(this.cart,0,.65,0,1.2,.6,1.8,0x956f4c,true);
    for (const x of [-.68,.68]) for (const z of [-.6,.6]) {
      const wheel = this.cylinder(this.cart,x,.32,z,.34,.34,.12,C.dark,12);wheel.rotation.z=Math.PI/2;
    }
    for(let i=0;i<24;i++) this.orb(this.cart,(i%4-.5)*.22-.25,1+(i%3)*.045,(Math.floor(i/4)-2.5)*.23,.13,i%3?0xcaa267:0x996e48);
    for (const x of [-3.8, 3.8]) for (const z of [-8, 9, 16]) {
      this.cylinder(this.scene,x,.36,z,.09,.13,.7,C.dark);
      this.orb(this.scene,x,.73,z,.11,C.brass);
    }
    this.marketProps();
  }
  marketProps() {
    for(const [x,z] of [[7.3,-6.2],[9.6,-6.2]]) {
      this.cylinder(this.scene,x,.91,z,.43,.43,.07,0x8d7252,32);
      this.cylinder(this.scene,x,.5,z,.035,.065,.8,C.dark,12);
      for(let i=0;i<3;i++) {
        const a=i*TAU/3;
        this.tube(this.scene,[x,.25,z],[x+Math.cos(a)*.33,.22,z+Math.sin(a)*.33],.025,C.dark);
      }
      for(const side of [-1,1]) {
        const chair=new THREE.Group();chair.position.set(x+side*.65,.22,z);chair.rotation.y=side*Math.PI/2;this.scene.add(chair);
        this.cylinder(chair,0,.43,0,.2,.2,.045,0x9b805c,24);
        const back=this.torus(chair,0,.75,-.16,.18,.02,C.dark);back.scale.y=1.25;
        for(const dx of [-.13,.13])for(const dz of [-.13,.13])this.tube(chair,[dx,.02,dz],[dx,.43,dz],.018,C.dark);
        for(const dx of [-.15,.15])this.tube(chair,[dx,.43,-.14],[dx,.79,-.14],.018,C.dark);
      }
      for(const side of [-1,1]) {
        this.cylinder(this.scene,x+side*.2,.968,z,.07,.07,.015,C.light,20);
        this.cylinder(this.scene,x+side*.2,1.01,z,.043,.033,.073,C.light,16);
        this.cylinder(this.scene,x+side*.2,1.05,z,.033,.033,.006,C.timber,16);
        this.torus(this.scene,x+side*.2+.049,1.015,z,.023,.008,C.light);
      }
    }
    for(const [x,z,scale] of [[-9.7,-5.4,.7],[-10.4,-5.4,.55],[6.25,-7.25,.6]]) {
      const g=new THREE.Group();g.position.set(x,.24,z);g.scale.setScalar(scale);this.scene.add(g);
      this.lathe(g,[[.37,0],[.41,.1],[.46,.5],[.41,.93],[.37,1],[0,1]],0x98744d,24);
      for(const y of [.14,.82])this.torus(g,0,y,0,.43,.028,C.dark).rotation.x=Math.PI/2;
      for(let i=0;i<14;i++) {
        const a=i*TAU/14;this.tube(g,[Math.sin(a)*.42,.1,Math.cos(a)*.42],[Math.sin(a)*.42,.92,Math.cos(a)*.42],.008,0x654d36);
      }
      for(let i=0;i<5;i++)this.box(g,(i-2)*.14,1.005,0,.125,.024,.65,0xb18e61);
    }
    for(let i=0;i<3;i++) {
      const g=new THREE.Group();g.position.set(-8.6-i*.53,.26+(i===1?.36:0),-5.45);this.scene.add(g);
      for(const side of [-1,1])for(let k=0;k<3;k++) {
        this.box(g,side*.24,.1+k*.095,0,.035,.07,.48,0xb09265);
        this.box(g,0,.1+k*.095,side*.24,.48,.07,.035,0xb09265);
      }
      for(let j=0;j<9;j++)this.ellipsoid(g,(j%3-1)*.13,.35,Math.floor(j/3)*.12-.12,.066,.06,.075,[0xc49c63,0x9a9a69,0xb99059][j%3]);
    }
  }
  lamp(x,z) {
    const g=new THREE.Group();g.position.set(x,.2,z);this.scene.add(g);
    this.cylinder(g,0,.14,0,.19,.26,.28,C.dark);
    this.cylinder(g,0,1.7,0,.055,.09,3.25,C.dark);
    this.box(g,0,3.48,0,.4,.58,.4,0xffdeb0);
    for(const a of [-1,1]) for(const b of [-1,1]) this.box(g,a*.2,3.48,b*.2,.045,.65,.045,C.dark);
    this.cylinder(g,0,3.84,0,0,.4,.25,C.dark,4).rotation.y=Math.PI/4;
    this.cylinder(g,0,3.14,0,.29,.1,.2,C.dark,4).rotation.y=Math.PI/4;
    this.orb(g,0,4,0,.07,C.brass);
  }
  tree(x,z,s) {
    const g=new THREE.Group();g.position.set(x,.2,z);g.scale.setScalar(s);this.scene.add(g);
    this.cylinder(g,0,1.3,0,.1,.23,2.6,C.timber,14);
    this.rounded(g,0,.08,0,1.55,.15,1.55,0xa99a80,.06);
    const leafGeometry=new THREE.IcosahedronGeometry(1,0);
    const foliage=new THREE.InstancedMesh(leafGeometry,this.material(0x859068),420);
    const dummy=new THREE.Object3D(),color=new THREE.Color();
    for(let branch=0;branch<7;branch++) {
      const a=branch*2.4, bx=Math.cos(a)*.85,bz=Math.sin(a)*.85,by=2.5+(branch%3)*.4;
      this.tube(g,[0,1.5,0],[bx,by,bz],.07,C.timber,.023);
      for(let i=0;i<60;i++) {
        const theta=this.random()*TAU, phi=Math.acos(2*this.random()-1),r=Math.cbrt(this.random())*.9;
        dummy.position.set(bx+Math.sin(phi)*Math.cos(theta)*r,by+Math.cos(phi)*r*.75,bz+Math.sin(phi)*Math.sin(theta)*r);
        dummy.rotation.set(this.random()*3,this.random()*6,this.random()*3);
        const size=.15+this.random()*.12;dummy.scale.set(size,size*.4,size*.8);dummy.updateMatrix();foliage.setMatrixAt(branch*60+i,dummy.matrix);
        color.setHex([0x899365,0xa0a478,0xb2b28a,0x6e8058,0x97976a][i%5]);foliage.setColorAt(branch*60+i,color);
      }
    }
    foliage.castShadow=true;foliage.receiveShadow=true;g.add(foliage);
    this.leaves.push({mesh:foliage,base:foliage.position.clone(),phase:x+z});
  }
  batchArchitecture() {
    // Bake static world transforms, then batch by material. Thousands of masonry
    // details become a few dozen draw calls; animated foliage/smoke stay separate.
    const dynamic = new Set([...this.leaves, ...this.smoke].map(item => item.mesh));
    const batches = new Map(), lines = [], originals = [];
    this.scene.updateMatrixWorld(true);
    this.scene.traverse(object => {
      if (object.isLineSegments) {
        const line = new THREE.LineSegments(object.geometry.clone().applyMatrix4(object.matrixWorld), object.material);
        lines.push(line);
      }
      if (!object.isMesh || object.isInstancedMesh || dynamic.has(object)) return;
      let geometry = object.geometry.clone().applyMatrix4(object.matrixWorld);
      if (geometry.index) { const indexed = geometry; geometry = indexed.toNonIndexed(); indexed.dispose(); }
      if (!batches.has(object.material)) batches.set(object.material, []);
      batches.get(object.material).push(geometry);
      originals.push(object);
    });
    for (const [material, parts] of batches) {
      const geometry = new THREE.BufferGeometry();
      for (const attribute of ['position', 'normal', 'uv']) {
        const size = attribute === 'uv' ? 2 : 3;
        const length = parts.reduce((sum, part) => sum + part.attributes.position.count * size, 0);
        const array = new Float32Array(length);
        let offset = 0;
        for (const part of parts) {
          if (part.attributes[attribute]) array.set(part.attributes[attribute].array, offset);
          offset += part.attributes.position.count * size;
        }
        geometry.setAttribute(attribute, new THREE.BufferAttribute(array, size));
      }
      const mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = true; mesh.receiveShadow = true;
      this.scene.add(mesh);
      parts.forEach(part => part.dispose());
    }
    originals.forEach(object => object.removeFromParent());
    lines.forEach(line => this.scene.add(line));
  }
  person(index, role='walker') {
    // Adult landmarks in metres: sole 0, ankle .096, knee .516, hip .956,
    // shoulder 1.435, chin 1.585, crown 1.835. Hats do not count as head height.
    const g=new THREE.Group(),female=index%3===0;
    const coat=[0x655444,0x9b7857,0xb2a58c,0x6d7b6b,0x765647,0xd9c7a7][index%6];
    const skin=[0xdfb992,0xb88965,0xd4a57d,0xe5c3a1][index%4];
    const hairColor=[0x392e27,0x584433,0x725a43,0x827366][index%4];
    const torso=new THREE.Group();g.add(torso);
    const body=this.lathe(torso,[[.15,.89],[.18,.98],[.155,1.1],[.175,1.28],[female?.195:.215,1.39],[female?.185:.20,1.44],[.135,1.49],[.07,1.51]],coat);
    body.scale.z=.74;
    let skirt,coatHem;
    if(female) {
      skirt=this.lathe(g,[[.29,.14],[.305,.18],[.285,.37],[.245,.65],[.19,.9],[.15,1.1]],coat,28);skirt.scale.z=.8;
      for(let i=0;i<14;i++) {
        const a=i*TAU/14;
        const fold=this.tube(g,[Math.sin(a)*.294,.18,Math.cos(a)*.235],[Math.sin(a)*.187,.88,Math.cos(a)*.15],.007,coat,.002);
        fold.castShadow=false;
      }
      this.rounded(torso,0,1.1,.105,.28,.032,.035,C.timber,.01);
    } else {
      coatHem=this.lathe(torso,[[.205,.76],[.207,.82],[.178,1.05],[.156,1.15]],coat);coatHem.scale.z=.72;
      this.rounded(torso,0,1.27,.13,.17,.34,.028,0xc8bda5,.018);
      this.rounded(torso,0,1.42,.15,.05,.15,.027,C.timber,.008);
    }
    this.cylinder(torso,0,1.555,0,.042,.051,.1,skin,14);
    for(const side of [-1,1]) {
      this.rounded(torso,side*.065,1.466,.093,.088,.056,.032,0xe7d4b2,.013).rotation.z=side*.28;
      this.rounded(torso,side*.10,1.355,.15,.058,.22,.025,coat,.012).rotation.z=side*.32;
      this.rounded(torso,side*.132,1.027,.132,.095,.016,.028,0x6a5541,.004);
    }
    for(let i=0;i<4;i++)this.orb(torso,.043,1.095+i*.078,.123+i*.008,.012,C.brass);
    if(role==='vendor')this.rounded(torso,0,1.0,.139,.3,.5,.022,0xd9cbb0,.024);
    if(role==='porter')this.rounded(torso,0,1.27,.14,.16,.18,.02,0x785641,.02);
    const head=new THREE.Group();head.position.y=1.71;torso.add(head);
    // A single tapered head surface avoids overlapping primitive seams at the jaw.
    if(!this.geometryCache.has('adult-head')) {
      const geometry=new THREE.SphereGeometry(1,24,18),position=geometry.attributes.position;
      for(let i=0;i<position.count;i++) {
        const y=position.getY(i),jaw=Math.max(0,-y);
        position.setXYZ(i,position.getX(i)*.09*(1-jaw*.24),y*.125,position.getZ(i)*.097*(1-jaw*.12)+jaw*.012);
      }
      geometry.computeVertexNormals();this.geometryCache.set('adult-head',geometry);
    }
    const face=new THREE.Mesh(this.geometryCache.get('adult-head'),this.material(skin));face.castShadow=true;head.add(face);
    this.ellipsoid(head,0,-.013,.094,.014,.03,.022,skin);
    this.ellipsoid(head,0,-.049,.088,.022,.0028,.007,0x956953);
    for(const side of [-1,1]) {
      this.ellipsoid(head,side*.088,-.004,-.007,.015,.03,.019,skin);
      this.ellipsoid(head,side*.034,.014,.09,.015,.006,.006,0xe8dec9);
      this.orb(head,side*.034,.014,.095,.0045,0x392f29);
      this.tube(head,[side*.019,.029,.095],[side*.05,.03,.082],.003,hairColor);
    }
    if(!female && index%4===0)this.ellipsoid(head,0,-.039,.092,.027,.005,.009,hairColor);
    if(!this.geometryCache.has('adult-hair'))this.geometryCache.set('adult-hair',new THREE.SphereGeometry(1,16,10,0,TAU,0,Math.PI*.54));
    const hair=new THREE.Mesh(this.geometryCache.get('adult-hair'),this.material(hairColor));
    hair.scale.set(.092,.13,.102);hair.position.set(0,.002,-.014);head.add(hair);
    for(let i=0;i<6;i++)this.ellipsoid(head,(i-2.5)*.025,.074,-.012+Math.sqrt(Math.max(0,.009-Math.pow((i-2.5)*.025,2))),.018,.034,.018,hairColor);
    if(female) {
      this.ellipsoid(head,0,-.005,-.106,.066,.057,.051,hairColor);
      this.cylinder(head,0,.115,0,.205,.21,.022,0xb9a17a,28);
      this.ellipsoid(head,0,.156,-.005,.112,.067,.105,0xcbb58e);
      this.orb(head,.125,.143,.025,.025,0xe0c4a1);
      this.tube(head,[-.087,.08,0],[-.052,-.117,.034],.009,0xb79b73);
      this.tube(head,[.087,.08,0],[.052,-.117,.034],.009,0xb79b73);
    } else if(role==='porter'||index%4===1) {
      this.ellipsoid(head,0,.111,0,.11,.041,.12,coat);
      this.ellipsoid(head,0,.089,.088,.104,.012,.079,coat);
    } else {
      this.cylinder(head,0,.115,0,.16,.165,.021,C.timber,28);
      this.lathe(head,[[.093,.122],[.096,.155],[.09,.28],[.1,.294],[0,.294]],coat,24);
      this.cylinder(head,0,.165,0,.098,.1,.035,0x3e342b,24);
    }
    const limbs=[],knees=[],elbows=[],ankles=[],hands=[],fingers=[];
    for(const side of [-1,1]) {
      const leg=new THREE.Group();leg.position.set(side*.097,.956,0);g.add(leg);
      const thigh=this.lathe(leg,[[.058,-.43],[.072,-.35],[.084,-.12],[.081,0]],0x4b4035,16);thigh.scale.z=.89;
      const knee=new THREE.Group();knee.position.y=-.44;leg.add(knee);
      this.ellipsoid(knee,0,0,.003,.06,.068,.063,0x4b4035);
      this.lathe(knee,[[.041,-.42],[.043,-.34],[.06,-.15],[.053,0]],0x4b4035,16);
      this.rounded(knee,0,-.395,0,.09,.039,.095,0x574b3d,.012);
      const ankle=new THREE.Group();ankle.position.y=-.42;knee.add(ankle);
      this.rounded(ankle,0,-.037,.062,.115,.105,.26,0x453329,.037);
      this.rounded(ankle,0,-.087,.062,.121,.018,.265,0x2d2823,.008);
      this.rounded(ankle,0,-.062,-.025,.105,.061,.079,0x2d2823,.01);
      for(let lace=0;lace<3;lace++)this.tube(ankle,[-.028,.006,.04+lace*.025],[.028,.006,.04+lace*.025],.003,C.timber);
      const arm=new THREE.Group();arm.position.set(side*(female?.193:.213),1.435,0);arm.rotation.z=side*.045;torso.add(arm);
      this.lathe(arm,[[.047,-.31],[.052,-.23],[.062,-.08],[.057,-.01],[.035,.035],[0,.048]],coat,20);
      const elbow=new THREE.Group();elbow.position.y=-.31;elbow.rotation.x=-.07;arm.add(elbow);
      this.orb(elbow,0,0,0,.049,coat);
      this.lathe(elbow,[[.033,-.265],[.04,-.22],[.046,-.1],[.05,0]],coat,16);
      this.cylinder(elbow,0,-.265,0,.036,.034,.026,0xe0cfb0,14);
      const hand=new THREE.Group();hand.position.y=-.285;elbow.add(hand);
      this.ellipsoid(hand,0,-.02,0,.034,.05,.023,skin);
      const fingerJoints=[];
      for(let finger=0;finger<4;finger++) {
        const joint=new THREE.Group();joint.position.set((finger-1.5)*.014,-.059,0);hand.add(joint);
        const length=finger===0||finger===3?.024:.03;
        this.cylinder(joint,0,-length/2,0,.0068,.006,length,skin,6);
        const tip=new THREE.Group();tip.position.y=-length;joint.add(tip);
        this.orb(tip,0,0,0,.007,skin);
        this.cylinder(tip,0,-.012,0,.006,.0048,.024,skin,6);
        fingerJoints.push({joint,tip});
      }
      fingers.push(fingerJoints);
      this.tube(hand,[side*.024,0,.006],[side*.045,-.036,.018],.01,skin,.008);
      limbs.push(leg,arm);knees.push(knee);elbows.push(elbow);ankles.push(ankle);hands.push(hand);
    }
    let heldProp=null;
    if(index%5===1 && role==='walker') {
      this.rounded(elbows[1],0,-.56,0,.25,.27,.12,C.timber,.029);
      this.torus(elbows[1],0,-.389,0,.077,.01,C.brass);heldProp='bag';
    }
    if(role==='reader') {
      this.rounded(torso,0,1.24,.37,.37,.012,.26,0xe9ddbf,.004).rotation.x=.32;
      this.box(torso,0,1.247,.37,.007,.014,.255,0x9b8667).rotation.x=.32;
    }
    if(index%11===3 && role==='walker') {
      heldProp='parasol';
      const parasol=new THREE.Group();parasol.position.set(.35,0,.12);torso.add(parasol);
      this.cylinder(parasol,0,1.74,0,.012,.012,1.36,C.timber,10);
      for(let i=0;i<10;i++) {
        const panel=new THREE.Mesh(new THREE.SphereGeometry(.63,6,6,i*TAU/10,TAU/10,0,Math.PI/2),this.material(i%2?0xd8c49f:0x9d8765));
        panel.position.y=2.24;panel.scale.y=.32;parasol.add(panel);
        const a=i*TAU/10;
        this.tube(parasol,[0,2.44,0],[Math.sin(a)*.61,2.25,Math.cos(a)*.61],.006,C.timber);
      }
      this.orb(parasol,0,2.465,0,.026,C.brass);
    }
    // Shared rough materials soften facial contours and tailored fabric at close range.
    g.traverse(mesh=>{
      if(!mesh.isMesh)return;
      if(mesh.geometry===this.sphereGeometry&&Math.max(mesh.scale.x,mesh.scale.y,mesh.scale.z)<.08) {
        if(!this.geometryCache.has('small-detail'))this.geometryCache.set('small-detail',new THREE.SphereGeometry(1,10,8));
        mesh.geometry=this.geometryCache.get('small-detail');
      }
      const color=mesh.material.color.getHex(),key=`person/${color}`;
      if(!this.materials.has(key))this.materials.set(key,new THREE.MeshStandardMaterial({color,roughness:.92}));
      mesh.material=this.materials.get(key);
    });
    const pelvis=new THREE.Group();pelvis.position.y=.956;
    for(const child of [...g.children]){child.position.y-=.956;pelvis.add(child);}g.add(pelvis);
    const spine=new THREE.Group();spine.position.y=.18;pelvis.add(spine);
    torso.position.y-=.18;spine.add(torso);
    g.scale.setScalar(.96+(index%6)*.015);
    this.scene.add(g);
    return {mesh:g,pelvis,spine,torso,head,limbs,knees,elbows,ankles,hands,fingers,skirt,coatHem,heldProp,role,phase:index*1.73,
      anatomy:{height:1.835,headHeight:.25,hip:.956,upperLeg:.44,lowerLeg:.42,soleOffset:.096,shoulder:1.435,upperArm:.31,lowerArm:.285}};
  }
  fitHands(actor) {
    // Fit the adult arm chain to its prop; walk timing is handled separately.
    const reach=(side,x,y,z)=>{
      const arm=actor.limbs[side*2+1],elbow=actor.elbows[side];
      const dx=x-arm.position.x,dz=z-arm.position.z,down=arm.position.y-y;
      const horizontal=Math.hypot(dx,dz),a=actor.anatomy.upperArm,b=actor.anatomy.lowerArm;
      const distance=THREE.MathUtils.clamp(Math.hypot(horizontal,down),Math.abs(a-b)+.001,a+b-.001);
      arm.rotation.order='YXZ';arm.rotation.set(
        Math.atan2(-horizontal,down)+Math.acos(THREE.MathUtils.clamp((a*a+distance*distance-b*b)/(2*a*distance),-1,1)),
        Math.atan2(dx,dz),0,'YXZ');
      elbow.rotation.x=-Math.PI+Math.acos(THREE.MathUtils.clamp((a*a+b*b-distance*distance)/(2*a*b),-1,1));
    };
    if(actor.role==='reader')for(const side of [0,1])reach(side,(side?1:-1)*.155,1.24,.37);
    if(actor.heldProp==='parasol') {
      reach(1,.35,1.2,.12);
      const arm=actor.limbs[3];
      actor.hands[1].rotation.set(-arm.rotation.x-actor.elbows[1].rotation.x,-arm.rotation.y,0);
    }
    if(actor.heldProp==='bag') {
      actor.limbs[3].rotation.x=0;actor.elbows[1].rotation.x=-.04;
    }
    if(actor.worldGrips) {
      actor.mesh.updateWorldMatrix(true,true);
      actor.worldGrips.forEach((point,side)=>{const p=actor.torso.worldToLocal(point.clone());reach(side,p.x,p.y,p.z);});
    } else if(actor.grips)actor.grips.forEach(([x,y,z],side)=>reach(side,x,y,z));
    for(const side of [0,1]) {
      const holding=actor.grips||(side===1&&actor.heldProp);
      actor.fingers[side].forEach(({joint,tip})=>{joint.rotation.x=holding?-.85:-.1;tip.rotation.x=holding?-.95:-.15;});
    }
  }
  seatPerson(actor,height,footrest=.23) {
    // The pelvis rests just above the seat; thighs extend forward, shins hang down.
    actor.seated=true;
    actor.mesh.position.y=height-(actor.anatomy.hip-.075)*actor.mesh.scale.y;
    const scale=actor.mesh.scale.y;
    const drop=(height+.075*scale-footrest)/scale-actor.anatomy.lowerLeg-actor.anatomy.soleOffset;
    const thigh=-Math.acos(THREE.MathUtils.clamp(drop/actor.anatomy.upperLeg,-1,1));
    for(let side=0;side<2;side++) {
      actor.limbs[side*2].rotation.x=thigh;
      actor.knees[side].rotation.x=-thigh;
    }
    // Fold the long coat above the seat instead of sending its hem through the wood.
    if(actor.coatHem){actor.coatHem.scale.y=.5;actor.coatHem.position.y=.55;}
  }
  groundHeight(x,z) {
    const ax=Math.abs(x),az=Math.abs(z);
    if(ax>4.725&&az>4.725)return .237;
    if((Math.abs(ax-4.6)<.126&&az>4.2)||(Math.abs(az-4.6)<.126&&ax>4.2))return .31;
    if(ax>4.65&&az>4.65)return .17;
    let height=-.045;
    const row=Math.round((z+32)/.43);
    for(let r=row-1;r<=row+1;r++) {
      const rz=-32+r*.43,shift=(Math.round(rz/.43)%2)*.3;
      const col=Math.round((x+32-shift)/.64);
      for(let c=col-1;c<=col+1;c++) {
        const stone=this.cobbleHeights.get(`${c}/${r}`);
        if(stone&&Math.abs(stone.x-x)<.31&&Math.abs(stone.z-z)<.205)height=Math.max(height,stone.top);
      }
    }
    for(const side of [-1,1])for(let i=-3;i<=3;i++) {
      if((Math.abs(x-i*.85)<.31&&Math.abs(z-side*3.65)<.24)||(Math.abs(z-i*.85)<.31&&Math.abs(x-side*3.65)<.24))height=Math.max(height,.0525);
    }
    return height;
  }
  route(points) {return walkingRoute(points);}
  walkPerson(actor,path,distance,speed,stride,turn=null) {
    poseWalking(actor,{path,distance,speed,stride,turn,ground:(x,z)=>this.groundHeight(x,z)});
    this.fitHands(actor);
  }
  buildLife() {
    // Sidewalk promenades round the four right-angle corners, plus crossing flows.
    let index=0;
    for(const sx of [-1,1]) for(const sz of [-1,1]) {
      for(let i=0;i<6;i++) {
        const actor=this.person(index++);
        const lane=5.02+(i%2)*.35;
        const points=[[sx*lane,sz*26],[sx*lane,sz*lane],[sx*26,sz*lane]];
        if(i%2)points.reverse();
        actor.route=this.route(points);actor.speed=i===4?1.05:.5+(i%3)*.1;actor.offset=10+i*4.4;
        actor.stride=(.86+actor.speed*.21)*actor.mesh.scale.y;
        this.actors.push(actor);
      }
    }
    for(let i=0;i<8;i++) {
      const actor=this.person(index++);const axis=i%2,side=i%4<2?1:-1;
      actor.route=this.route(axis?[[side*3.35,-8],[side*3.35,8]]:[[-8,side*3.35],[8,side*3.35]]);
      actor.crossing=axis;actor.offset=(i%4)*1.1;actor.speed=.7;
      actor.stride=actor.route.length/Math.round(actor.route.length/actor.mesh.scale.y);this.actors.push(actor);
    }
    const locals=[[-7.2,-5.85,0,'vendor'],[-8.5,-4.96,Math.PI,'reader'],[7,-5.35,Math.PI,'vendor'],[8.15,-5.1,-1.8,'walker'],[-5.2,7.2,1.3,'walker'],[-5.35,8.05,2.6,'walker'],[6.62,8.55,Math.PI/2,'reader'],[5.65,6.3,-2.4,'walker']];
    locals.forEach(([x,z,rotation,role],i)=>{
      const actor=this.person(index++,role);actor.mesh.position.set(x,.23,z);actor.mesh.rotation.y=rotation;actor.idle=true;actor.seated=i===6;
      if(actor.seated)this.seatPerson(actor,.75);
      this.actors.push(actor);
    });
    this.buildCarriage(0);this.buildCarriage(1);this.buildHandcart();
    for(let i=0;i<10;i++) {
      const g=new THREE.Group();this.scene.add(g);
      this.ellipsoid(g,0,.13,0,.075,.1,.14,0x8d897c);
      this.orb(g,0,.25,.1,.062,0x6f786d);
      this.ellipsoid(g,0,.23,.164,.025,.019,.05,0xbfa373);
      for(const side of [-1,1])this.tube(g,[side*.035,0,0],[side*.035,.09,.025],.009,C.timber);
      const wings=[];for(const side of [-1,1]){const wing=this.ellipsoid(g,side*.075,.16,-.015,.037,.07,.12,0xa4a092);wings.push(wing);}
      this.birds.push({mesh:g,wings,x:Math.cos(i*2.4)*2,z:Math.sin(i*2.4)*2,phase:i});
    }
  }
  wheel(parent,x,y,z,r) {
    const wheel=new THREE.Group();wheel.position.set(x,y,z);wheel.rotation.y=Math.PI/2;parent.add(wheel);
    this.torus(wheel,0,0,0,r,.045,0x463b30);
    this.torus(wheel,0,0,0,r-.055,.03,0x947651);
    for(let i=0;i<12;i++) {
      const a=i*TAU/12;this.tube(wheel,[0,0,0],[Math.sin(a)*(r-.055),Math.cos(a)*(r-.055),0],.017,0x8b6d49);
    }
    const hub=this.cylinder(wheel,0,0,0,.075,.075,.18,C.brass,14);hub.rotation.x=Math.PI/2;
    return wheel;
  }
  horse(parent,x,z,color,driver) {
    const g=new THREE.Group();g.position.set(x,0,z);parent.add(g);
    this.ellipsoid(g,0,1.14,0,.33,.43,.73,color);
    this.ellipsoid(g,0,1.25,.48,.24,.37,.28,color);
    const neck=this.ellipsoid(g,0,1.65,.63,.18,.48,.22,color);neck.rotation.x=.38;
    const head=this.ellipsoid(g,0,1.97,.88,.16,.24,.31,color);head.rotation.x=-.4;
    this.ellipsoid(g,0,1.85,1.12,.14,.12,.16,0x83694f);
    for(const side of [-1,1]) {
      this.ellipsoid(g,side*.1,2.22,.76,.045,.14,.065,color).rotation.z=side*.2;
      this.orb(g,side*.143,2.015,.96,.021,0x241f1b);
    }
    for(let i=0;i<10;i++)this.ellipsoid(g,0,1.3+i*.08,.31+i*.03,.11,.09,.08,0x42392e);
    const legs=[],knees=[];
    for(const side of [-1,1])for(const end of [-1,1]) {
      const leg=new THREE.Group();leg.position.set(side*.22,1.02,end*.47);g.add(leg);
      this.cylinder(leg,0,-.21,0,.073,.049,.42,color,12);
      const knee=new THREE.Group();knee.position.y=-.42;leg.add(knee);
      this.orb(knee,0,0,0,.065,color);this.cylinder(knee,0,-.2,0,.048,.034,.4,color,10);
      this.rounded(knee,0,-.43,.025,.11,.11,.15,0x3c332b,.025);legs.push(leg);knees.push(knee);
    }
    const tail=this.ellipsoid(g,0,.94,-.78,.1,.42,.12,0x42392e);tail.rotation.x=-.32;
    // Breast collar, saddle strap, reins and shafts connect the horse to the carriage.
    this.torus(g,0,1.18,.43,.34,.025,C.dark).scale.y=1.1;
    this.box(g,0,1.55,-.05,.58,.06,.17,C.dark);
    for(const side of [-1,1]) {
      const grip=parent.worldToLocal(driver.hands[side===-1?0:1].getWorldPosition(new THREE.Vector3()));
      this.tube(g,[side*.18,1.89,1.03],[grip.x-x,grip.y,grip.z-z],.011,C.dark);
      this.tube(g,[side*.44,.89,.05],[side*.6,.72,-1.75],.035,C.timber);
    }
    return {mesh:g,legs,knees,tail};
  }
  buildCarriage(index) {
    const g=new THREE.Group();this.scene.add(g);
    const color=index?0x88765a:0x536a5b;
    this.rounded(g,0,.81,-.45,1.45,.26,2.45,C.timber,.08);
    this.rounded(g,0,1.45,-.75,1.3,1.17,1.65,color,.11);
    this.rounded(g,0,2.1,-.75,1.5,.15,1.92,C.dark,.07);
    for(const side of [-1,1]) {
      this.rounded(g,side*.665,1.66,-.74,.055,.58,1.18,C.glass,.055);
      this.box(g,side*.71,1.68,-.74,.035,.61,.065,C.light);
      this.box(g,side*.7,1.66,-.2,.045,.64,.065,C.light);
      this.box(g,side*.7,1.66,-1.28,.045,.64,.065,C.light);
      this.box(g,side*.7,1.35,-.74,.05,.06,1.22,C.brass);
      this.box(g,side*.7,1.98,-.74,.05,.06,1.22,C.brass);
      this.box(g,side*.79,.61,-.6,.32,.065,.7,C.dark);
    }
    this.rounded(g,0,1.14,.5,1.12,.25,.5,C.dark,.07);
    this.rounded(g,0,.77,1,.98,.06,.42,C.timber,.02);
    this.rounded(g,0,1.43,.22,1.16,.38,.14,C.dark,.055);
    const wheels=[];
    for(const side of [-1,1])for(const end of [-1,1])wheels.push(this.wheel(g,side*.85,.6,end===1?.44:-1.4,end===1?.48:.59));
    for(const z of [.44,-1.4])this.tube(g,[-.85,.6,z],[.85,.6,z],.045,C.dark);
    const driver=this.person(46+index,'porter');driver.mesh.removeFromParent();g.add(driver.mesh);driver.mesh.position.set(0,0,.5);driver.mesh.scale.setScalar(.98);
    this.seatPerson(driver,1.265,.8);driver.grips=[[-.18,1.19,.39],[.18,1.19,.39]];this.fitHands(driver);
    g.updateMatrixWorld(true);
    const horse=this.horse(g,0,2.3,index?0x9c7b58:0x6b5140,driver);
    this.vehicles.push({mesh:g,wheels,horse,axis:index,driver});
  }
  buildHandcart() {
    const g=new THREE.Group();this.scene.add(g);
    this.rounded(g,0,.66,0,.9,.22,1.25,C.timber,.035);
    for(const x of [-.46,.46])for(let y=.76;y<1.07;y+=.11)this.box(g,x,y,0,.05,.08,1.3,0xa2835b);
    for(const z of [-.64,.64])for(let y=.76;y<1.07;y+=.11)this.box(g,0,y,z,.96,.08,.05,0xa2835b);
    for(const x of [-.4,.4])this.tube(g,[x,.63,-.2],[x,1.04,-1.3],.025,C.timber);
    const wheels=[this.wheel(g,-.57,.41,0,.39),this.wheel(g,.57,.41,0,.39)];
    for(let i=0;i<5;i++)this.rounded(g,(i%2-.5)*.38,1.1+Math.floor(i/2)*.18,((i%3)-1)*.3,.34,.25,.32,0xc1a47a,.025);
    const porter=this.person(51,'porter');porter.mesh.removeFromParent();g.add(porter.mesh);porter.mesh.position.set(0,0,-1.55);porter.grips=[-1,1].map(side=>[side*.4/porter.mesh.scale.x,1.04/porter.mesh.scale.y,.25/porter.mesh.scale.z]);this.fitHands(porter);
    this.handcart={mesh:g,wheels,porter};
    porter.stride=32/Math.round(32/porter.mesh.scale.y);
    porter.route=this.route([[-17.55,-2.3],[14.45,-2.3]]);
    porter.worldGrips=[new THREE.Vector3(),new THREE.Vector3()];
  }

  batchAnimatedMeshes() {
    const groups=new Map();
    const roots=[...this.actors,...this.vehicles,...this.birds,this.handcart].map(item=>item.mesh);
    roots.forEach(root=>root.traverse(mesh=>{
      if(!mesh.isMesh || mesh.isInstancedMesh)return;
      const key=`${mesh.geometry.uuid}/${mesh.material.uuid}`;
      if(!groups.has(key))groups.set(key,[]);groups.get(key).push(mesh);
    }));
    for(const sources of groups.values()) {
      if(sources.length<2)continue;
      const mesh=new THREE.InstancedMesh(sources[0].geometry,sources[0].material,sources.length);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow=true;mesh.receiveShadow=true;mesh.frustumCulled=false;
      sources.forEach(source=>source.visible=false);
      this.scene.add(mesh);this.dynamicBatches.push({mesh,sources});
    }
  }

  buildNetwork() {
    const sites=[[-4.5,3.3,-4.3],[4.6,3.6,-4.3],[-4.45,3.4,4.9],[5.1,3.2,5.0]];
    sites.forEach(([x,y,z],i)=>{
      const base=new THREE.Group();base.position.set(x,0,z);this.scene.add(base);
      this.cylinder(base,0,y/2,0,.055,.08,y,C.dark);
      this.beam(base,[0,y-.3,0],[0,y,.5],.08,C.dark);
      const head=new THREE.Group();head.position.set(0,y,.48);base.add(head);
      this.rounded(head,0,0,0,.32,.25,.57,C.light,.045);
      this.rounded(head,0,.145,.04,.39,.035,.68,0xf2e5c9,.013);
      this.rounded(head,0,0,.29,.27,.2,.035,C.dark,.04);
      const lens=this.cylinder(head,0,0,.328,.077,.077,.045,C.glass,24);lens.rotation.x=Math.PI/2;
      this.torus(head,0,0,.354,.078,.013,0x54615c);
      this.orb(head,-.018,.02,.36,.015,0xdce5d4);
      for(const side of [-1,1])for(const y of [-.075,.075])this.orb(head,side*.106,y,.318,.008,C.brass);
      const led=this.orb(head,.108,-.065,.32,.015,C.signal);led.material=this.material(C.signal,true);
      const origin=new THREE.Vector3(x,y,z+.48);
      const destination=new THREE.Vector3(1.5,9.6,-5);
      const curve=new THREE.CubicBezierCurve3(origin,new THREE.Vector3(x*.7,y+3.2,z*.7),new THREE.Vector3(3.5,8.7,-1),destination);
      const path=new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)),new THREE.LineBasicMaterial({color:0x91b18a,transparent:true,opacity:.25,depthWrite:false}));
      this.scene.add(path);
      const packets=[];
      for(let j=0;j<10;j++) {
        const packet=new THREE.Mesh(new THREE.OctahedronGeometry(.105,0),new THREE.MeshBasicMaterial({color:C.signal,transparent:true,opacity:1,depthWrite:false}));
        this.scene.add(packet);packets.push(packet);
      }
      const coneGeometry=new THREE.BufferGeometry();
      coneGeometry.setAttribute('position',new THREE.Float32BufferAttribute(new Float32Array(18),3));
      coneGeometry.setIndex([0,1,2,0,2,3,0,3,4,0,4,1]);
      const cone=new THREE.Mesh(coneGeometry,new THREE.MeshBasicMaterial({color:0xb8d59c,transparent:true,opacity:.075,side:THREE.DoubleSide,depthWrite:false}));this.scene.add(cone);
      const ring=new THREE.Mesh(new THREE.RingGeometry(.5,.535,40),new THREE.MeshBasicMaterial({color:0x678e6b,transparent:true,opacity:.5,side:THREE.DoubleSide,depthWrite:false}));ring.rotation.x=-Math.PI/2;this.scene.add(ring);
      const label=document.createElement('span');label.className='camera-marker';label.textContent=`CAM 0${i+1}`;this.labels.appendChild(label);
      const targetBox=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(.85,1.95,.75)),new THREE.LineBasicMaterial({color:0x729c70,transparent:true,opacity:.6}));this.scene.add(targetBox);
      const capture=new THREE.Line(new THREE.BufferGeometry().setFromPoints([origin,origin]),new THREE.LineBasicMaterial({color:0xabc891,transparent:true,opacity:.4}));this.scene.add(capture);
      this.cameras.push({head,origin,curve,path,packets,cone,ring,label,targetBox,capture});
    });
  }
  buildAtmosphere() {
    const positions=new Float32Array(160*3);
    for(let i=0;i<160;i++){positions[i*3]=(this.random()-.5)*35;positions[i*3+1]=1+this.random()*15;positions[i*3+2]=(this.random()-.5)*30;}
    const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(positions,3));
    this.dust=new THREE.Points(geo,new THREE.PointsMaterial({color:0xfff3c9,size:.045,transparent:true,opacity:.6,depthWrite:false}));this.scene.add(this.dust);
  }

  resize() {
    const rect=this.canvas.getBoundingClientRect();
    this.width=Math.max(1,rect.width);this.height=Math.max(1,rect.height);
    this.camera.aspect=this.width/this.height;
    this.camera.fov=this.width < 700 ? 65 : 58;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.width,this.height,false);
    this.rect=rect;
    if(!this.running || this.paused) this.update(0);
  }
  setStage(stage) {
    this.storyOffset=stage*6+.4-this.time%18;
    this.stage=stage;this.onStage(stage);this.update(0);
  }
  setPaused(paused) { this.paused=paused;if(paused)this.stop();else this.start(); }
  start() {
    if(this.running || this.paused || document.hidden)return;
    this.running=true;this.last=performance.now();
    const tick=(now)=>{
      if(!this.running)return;
      const delta=Math.max(0,Math.min((now-this.last)/1000,.05));this.last=now;
      this.update(delta);this.raf=requestAnimationFrame(tick);
    };this.raf=requestAnimationFrame(tick);
  }
  stop() { this.running=false;cancelAnimationFrame(this.raf); }
  update(delta) {
    this.time+=delta;
    const t=this.time,signalTime=t+this.storyOffset,stage=Math.floor(((signalTime%18+18)%18)/6);
    if(stage!==this.stage){this.stage=stage;this.onStage(stage);}
    if (this.reduced) this.smoothPointer.set(0, 0);
    else if (delta > 0) this.smoothPointer.lerp(this.pointer, 1 - Math.exp(-delta * 3));
    // Equal X/Z offsets put the eye on the southeast corner's 45-degree bisector.
    // The aim stays on the intersection; the four square kerb returns remain visible.
    this.camera.position.set(12+this.smoothPointer.x*.28,7.8+this.smoothPointer.y*.15,12-this.smoothPointer.x*.28);
    this.camera.lookAt(0,1.65,0);
    this.camera.updateMatrixWorld();
    for(const actor of this.actors) {
      if(actor.route) {
        let path=actor.route,distance,speed,turn=null;
        if(actor.crossing!==undefined) {
          const clock=t+10+(actor.crossing?32:0),cycle=clock%64;
          const reverse=Math.floor(clock/64)%2;
          const journey=walkingTravel(cycle-5-actor.offset,actor.route.length,18);
          distance=journey.distance;speed=journey.speed;
          const backward={length:actor.route.length,sample:d=>{const p=actor.route.sample(actor.route.length-d);p.yaw+=Math.PI;return p;}};
          if(reverse)path=backward;
          if(cycle<2.5){turn={from:reverse?actor.route:backward,progress:cycle/2.5};speed=.23*Math.sin(Math.PI*turn.progress);}
        } else {distance=(t*actor.speed+actor.offset)%actor.route.length;speed=actor.speed;}
        this.walkPerson(actor,path,distance,speed,actor.stride,turn);
      } else {
        actor.head.rotation.y=Math.sin(t*.65+actor.phase)*.13;
        if(actor.idle) {
          actor.limbs[1].rotation.x=actor.role==='reader'?-.82:-.4+Math.sin(t*1.6+actor.phase)*.18;
          actor.limbs[3].rotation.x=actor.role==='vendor'?-.7+Math.sin(t*1.9+actor.phase)*.22:actor.role==='reader'?-.82:-.15;
          actor.elbows.forEach(e=>e.rotation.x=-.25);
        }
        this.fitHands(actor);
      }
    }
    for(const vehicle of this.vehicles) {
      const phase=(t+10+(vehicle.axis?32:0))%64;
      const progress=THREE.MathUtils.clamp(phase/27,0,1);
      const travel=-30+progress*60;
      vehicle.mesh.position.set(vehicle.axis?-1.15:travel,.14,vehicle.axis?travel:1.15);
      vehicle.mesh.rotation.y=vehicle.axis?0:Math.PI/2;
      vehicle.wheels.forEach(wheel=>wheel.rotation.z=-travel/.54);
      vehicle.horse.legs.forEach((leg,i)=>{
        const gait=t*6+(i===0||i===3?0:Math.PI);leg.rotation.x=Math.sin(gait)*.36;
        vehicle.horse.knees[i].rotation.x=Math.max(0,-Math.sin(gait))*.7;
      });
      vehicle.horse.mesh.position.y=Math.abs(Math.sin(t*6))*.035;
      vehicle.horse.tail.rotation.z=Math.sin(t*2)*.1;
    }
    const cartMotion=walkingTravel((t+10)%64,32,29);
    const cartTravel=-16+cartMotion.distance;
    this.handcart.mesh.position.set(cartTravel,.12,-2.3);this.handcart.mesh.rotation.y=Math.PI/2;
    this.handcart.wheels.forEach(wheel=>wheel.rotation.z=-cartTravel/.39);
    const porter=this.handcart.porter;
    this.handcart.mesh.updateWorldMatrix(true,true);
    porter.worldGrips.forEach((p,side)=>p.set(side?.4:-.4,1.04,-1.3).applyMatrix4(this.handcart.mesh.matrixWorld));
    this.walkPerson(porter,porter.route,cartMotion.distance,cartMotion.speed,porter.stride);
    this.birds.forEach(({mesh,wings,x,z,phase})=>{
      const flutter=(t+phase*1.3)%21, fly=flutter>16;
      mesh.position.set(x+Math.sin(t*.45+phase)*.35,.1+(fly?Math.sin((flutter-16)/5*Math.PI)*1.6:0),z+Math.cos(t*.4+phase)*.3);
      mesh.rotation.y=t*.25+phase;
      wings.forEach((wing,j)=>wing.rotation.z=fly?Math.sin(t*18)*(j?1:-1):0);
    });
    const point=new THREE.Vector3();
    this.cameras.forEach((cam,i)=>{
      const actor=this.actors[24+i];
      const target=actor.mesh.position.clone();target.y+=.9;
      // Each head and frustum follow the same actor used by its track box.
      const vector=target.clone().sub(cam.origin);cam.head.rotation.y=Math.atan2(vector.x,vector.z);cam.head.rotation.x=-Math.atan2(vector.y,Math.hypot(vector.x,vector.z));
      const center=target.clone();center.y=.12;
      const p=cam.cone.geometry.attributes.position;
      p.setXYZ(0,cam.origin.x,cam.origin.y,cam.origin.z);
      [[-1,-1],[1,-1],[1,1],[-1,1]].forEach(([x,z],j)=>p.setXYZ(j+1,center.x+x*.9,.12,center.z+z*1.1));
      p.needsUpdate=true;cam.cone.geometry.computeBoundingSphere();
      cam.cone.material.opacity=(stage===0?.065:.035)+Math.sin(t*2+i)*.008;
      cam.ring.position.copy(center);cam.ring.scale.setScalar(1+(t*.65+i)%1.2);cam.ring.material.opacity=.3*(1-((t*.65+i)%1.2)/1.2);
      cam.targetBox.position.copy(target);cam.targetBox.material.opacity=stage===0?.45:.18;
      const cp=cam.capture.geometry.attributes.position;cp.setXYZ(0,cam.origin.x,cam.origin.y,cam.origin.z);cp.setXYZ(1,target.x,target.y,target.z);cp.needsUpdate=true;cam.capture.geometry.computeBoundingSphere();cam.capture.material.opacity=stage===0?.3:.1;
      cam.path.material.opacity=stage===0?.12:.33;
      cam.packets.forEach((packet,j)=>{
        const u=(t*.115+j/10+i*.067)%1;
        cam.curve.getPoint(u,point);packet.position.copy(point);
        packet.material.opacity=Math.min(1,u*12)*(1-Math.pow(u,4))*(stage===0?.65:1);
        packet.scale.setScalar((1-u*.55)*(j%3===0?1.6:1));packet.rotation.y=t*1.2+j;
      });
      point.copy(cam.origin).add(new THREE.Vector3(0,.5,0)).project(this.camera);
      const canvasRect=this.rect, stageRect=this.labels.getBoundingClientRect();
      const px=(point.x*.5+.5)*this.width+canvasRect.left-stageRect.left;
      const py=(-point.y*.5+.5)*this.height+canvasRect.top-stageRect.top;
      cam.label.style.transform=`translate(${px.toFixed(1)}px,${py.toFixed(1)}px) translate(-50%,-100%)`;
      cam.label.hidden=point.z>1||point.z< -1||px<0||px>stageRect.width||py<100||py>stageRect.height-65;
      cam.label.classList.toggle('active',i===Math.floor(t/4)%4);
    });
    this.smoke.forEach(({mesh,source,offset})=>{
      const p=(t*.1+offset)%1;
      mesh.position.copy(source).add(new THREE.Vector3(p*1.7, p*3,Math.sin(p*5+source.x)*.17));
      mesh.scale.setScalar(.14+p*.7);mesh.material.opacity=(1-p)*.19;
    });
    this.leaves.forEach(({mesh,base,phase})=>{mesh.position.x=base.x+Math.sin(t*.7+phase)*.025;});
    this.dust.rotation.y=t*.007;
    this.scene.updateMatrixWorld(true);
    for(const batch of this.dynamicBatches) {
      batch.sources.forEach((source,i)=>batch.mesh.setMatrixAt(i,source.matrixWorld));
      batch.mesh.instanceMatrix.needsUpdate=true;
    }
    this.renderer.render(this.scene,this.camera);
  }
  dispose() {
    this.stop();
    const geometries=new Set(),materials=new Set(),textures=new Set();
    this.scene.traverse(obj=>{if(obj.geometry)geometries.add(obj.geometry);if(obj.material)for(const mat of (Array.isArray(obj.material)?obj.material:[obj.material]))materials.add(mat);});
    materials.forEach(mat=>{if(mat.map)textures.add(mat.map);mat.dispose();});textures.forEach(tex=>tex.dispose());geometries.forEach(geo=>geo.dispose());this.ramp.dispose();this.renderer.dispose();this.labels.replaceChildren();
  }
}

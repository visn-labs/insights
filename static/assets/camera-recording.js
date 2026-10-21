import * as T from '../vendor/three.module.min.js';
import {recordingState,followPointer} from './recording-state.js';

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const ease = x => { x = clamp(x); return x * x * (3 - 2 * x); };
const blend = (x, a, b) => ease((x - a) / (b - a));

export function filmTransportPoint(distance, halfSpan = .72, radius = .30) {
  const straight = halfSpan * 2, arc = Math.PI * radius;
  const perimeter = straight * 2 + arc * 2;
  distance = ((distance % perimeter) + perimeter) % perimeter;
  if (distance < straight) return [-halfSpan + distance, radius];
  distance -= straight;
  if (distance < arc) {
    const angle = Math.PI / 2 - distance / radius;
    return [halfSpan + Math.cos(angle) * radius, Math.sin(angle) * radius];
  }
  distance -= arc;
  if (distance < straight) return [halfSpan - distance, -radius];
  const angle = -Math.PI / 2 - (distance - straight) / radius;
  return [-halfSpan + Math.cos(angle) * radius, Math.sin(angle) * radius];
}

// One physical recording attachment on CAM 04, diagonally opposite CAM 01.
// The two visible film runs are different portions of the same closed belt.
export class CameraRecording {
  constructor(town) {
    this.town = town;
    this.cameraSite = town.cameras[3];
    this.head = this.cameraSite.head;
    this.head.children.forEach(child => child.visible = false);
    this.model = new T.Group();
    this.model.scale.setScalar(.4);
    this.head.add(this.model);
    this.pointerX={value:0,velocity:0};this.pointerY={value:0,velocity:0};
    this.previousTime=0;this.finePointer=typeof matchMedia==='function'&&matchMedia('(hover:hover) and (pointer:fine)').matches;
    this.approach=new T.CubicBezierCurve3();this.macro=new T.CubicBezierCurve3();
    this.progress = 0;
    this.zoom = 0;
    this.reel = 0;
    this.scale = .4;
    this.halfSpan = .72;
    this.radius = .30;
    this.speed = .31;
    this.deckCenter = new T.Vector3(0, .015, -.27);
    this.transportScale = .26;
    this.materials = {
      board: new T.MeshStandardMaterial({color:'#344c3c',roughness:.74,metalness:.12}),
      paint: new T.MeshStandardMaterial({color: '#c9c3ae', roughness: .47, metalness: .22}),
      graphite: new T.MeshStandardMaterial({color: '#302f2a', roughness: .44, metalness: .48}),
      metal: new T.MeshStandardMaterial({color: '#9a9b8b', roughness: .29, metalness: .83}),
      brass: new T.MeshStandardMaterial({color: '#99784d', roughness: .38, metalness: .72}),
      rubber: new T.MeshStandardMaterial({color: '#242925', roughness: .85}),
      glass: new T.MeshPhysicalMaterial({color: '#233936', roughness: .065, metalness: .15, clearcoat: 1}),
      light: new T.MeshBasicMaterial({color: '#bed7a5'}),
    };
    this.makeSurfaceMaps();
    this.makeEnvironment();
    this.shellMaterial = this.materials.paint.clone();
    this.trimMaterial = this.materials.graphite.clone();
    this.buildHousing();
    this.buildTransport();
    this.head.updateWorldMatrix(true,true);
    const inverse=this.model.matrixWorld.clone().invert(),bounds=new T.Box3();
    this.transport.traverse(object=>{if(!object.isMesh)return;object.geometry.computeBoundingBox();bounds.union(object.geometry.boundingBox.clone().applyMatrix4(new T.Matrix4().multiplyMatrices(inverse,object.matrixWorld)));});
    this.cassetteFits=bounds.min.x>-.31&&bounds.max.x<.31&&bounds.min.y>-.18&&bounds.max.y<.18&&bounds.min.z>-.70&&bounds.max.z<.45;
    this.buildPackets();
    this.buildFog();
    this.model.traverse(object => object.layers.set(1));
    town.scene.traverse(object => { if (object.isLight) object.layers.enable(1); });
    town.sun.layers.disable(1);
    this.detailLight=new T.DirectionalLight('#fff0d8',1.8);this.detailLight.layers.set(1);this.detailLight.castShadow=true;
    this.detailLight.shadow.mapSize.set(town.canvas.clientWidth<760?512:1024,town.canvas.clientWidth<760?512:1024);
    this.detailLight.shadow.bias=-.00003;this.detailLight.shadow.normalBias=.0004;this.detailLight.shadow.radius=2;
    Object.assign(this.detailLight.shadow.camera,{near:.01,far:8});town.scene.add(this.detailLight,this.detailLight.target);
    this.captureTarget = new T.WebGLRenderTarget(512, 384, {depthBuffer: true});
    this.captureTarget.texture.colorSpace = T.SRGBColorSpace;
    this.captureCamera = new T.PerspectiveCamera(58, 4 / 3, .1, 80);
    this.captureBytes = new Uint8Array(512 * 384 * 4);
    this.captureCanvas = document.createElement('canvas');
    this.captureCanvas.width = 512;
    this.captureCanvas.height = 384;
    this.lastCapture = -10;
    this.captureIndex = 0;
    this.tick = 0;
  }

  mesh(geometry, material, parent = this.model) {
    const mesh = new T.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  box(w, h, d, material, parent = this.model, radius = .025) {
    const r = Math.min(radius, w / 8, h / 8, d / 8);
    const shape = new T.Shape();
    shape.moveTo(-w / 2 + r, -h / 2);
    shape.lineTo(w / 2 - r, -h / 2);
    shape.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
    shape.lineTo(w / 2, h / 2 - r);
    shape.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
    shape.lineTo(-w / 2 + r, h / 2);
    shape.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
    shape.lineTo(-w / 2, -h / 2 + r);
    shape.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
    const geometry = new T.ExtrudeGeometry(shape, {depth: d - r * 2, bevelEnabled: true, bevelSize: r, bevelThickness: r, bevelSegments: 3, steps: 1});
    geometry.translate(0, 0, -d / 2 + r);
    return this.mesh(geometry, material, parent);
  }

  cylinder(r, depth, material, parent = this.model) {
    const mesh = this.mesh(new T.CylinderGeometry(r, r, depth, 64), material, parent);
    mesh.rotation.x = Math.PI / 2;
    return mesh;
  }

  ring(r, width, material, parent = this.model) {
    return this.mesh(new T.TorusGeometry(r, width, 10, 96), material, parent);
  }

  makeSurfaceMaps(){
    // Deterministic, mipmapped microstructure. Surface scale stays far below
    // the fasteners; this is finish, not a substitute for geometry.
    this.surfaceTextures=[];
    for(const [name,material,brushed] of [['paint',this.materials.paint,false],['metal',this.materials.metal,true]]){
      const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
      const ctx=canvas.getContext('2d'),pixels=ctx.createImageData(256,256);let seed=641;
      for(let y=0;y<256;y++)for(let x=0;x<256;x++){
        seed=(Math.imul(seed,1664525)+1013904223)>>>0;
        const grain=brushed?Math.sin(y*2.17)*9+(seed/4294967296-.5)*5:(seed/4294967296-.5)*24;
        const value=216+grain,index=(y*256+x)*4;
        pixels.data[index]=pixels.data[index+1]=pixels.data[index+2]=value;pixels.data[index+3]=255;
      }
      ctx.putImageData(pixels,0,0);const texture=new T.CanvasTexture(canvas);
      texture.wrapS=texture.wrapT=T.RepeatWrapping;texture.repeat.set(3,3);
      texture.anisotropy=Math.min(4,this.town.renderer.capabilities.getMaxAnisotropy());
      material.roughnessMap=texture;material.bumpMap=texture;material.bumpScale=brushed?.00012:.0005;
      if(name==='paint')material.roughness=.64;
      this.surfaceTextures.push(texture);
    }
  }

  makeEnvironment() {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 256;
    const ctx = canvas.getContext('2d');
    const light = ctx.createLinearGradient(0, 0, 0, 256);
    light.addColorStop(0, '#777a6c');
    light.addColorStop(.5, '#d6d1bd');
    light.addColorStop(1, '#53473a');
    ctx.fillStyle = light;
    ctx.fillRect(0, 0, 512, 256);
    // Broad window and narrow edge strip shape the enamel/metal reflections.
    for(const [x,y,w,h,tint] of [[62,28,128,145,'255,248,232'],[332,52,28,132,'207,220,210']]){
      const panel=ctx.createLinearGradient(x,0,x+w,0);
      panel.addColorStop(0,`rgba(${tint},0)`);panel.addColorStop(.15,`rgba(${tint},.95)`);panel.addColorStop(.85,`rgba(${tint},.95)`);panel.addColorStop(1,`rgba(${tint},0)`);
      ctx.fillStyle=panel;ctx.fillRect(x,y,w,h);
    }
    const source = new T.CanvasTexture(canvas);
    source.colorSpace = T.SRGBColorSpace;
    source.mapping = T.EquirectangularReflectionMapping;
    const generator = new T.PMREMGenerator(this.town.renderer);
    this.environment = generator.fromEquirectangular(source);
    for (const material of Object.values(this.materials)) {
      if ('envMap' in material) {
        material.envMap = this.environment.texture;
        material.envMapIntensity = .6;
      }
    }
    source.dispose();
    generator.dispose();
  }

  buildHousing() {
    const m = this.materials;
    const shell = this.shellMaterial, trim = this.trimMaterial;
    // Continuous radiused aluminium extrusion, with a real hollow interior.
    const outline=(w,h,r)=>{const p=new T.Shape();p.moveTo(-w/2+r,-h/2);p.lineTo(w/2-r,-h/2);p.quadraticCurveTo(w/2,-h/2,w/2,-h/2+r);p.lineTo(w/2,h/2-r);p.quadraticCurveTo(w/2,h/2,w/2-r,h/2);p.lineTo(-w/2+r,h/2);p.quadraticCurveTo(-w/2,h/2,-w/2,h/2-r);p.lineTo(-w/2,-h/2+r);p.quadraticCurveTo(-w/2,-h/2,-w/2+r,-h/2);return p;};
    const profile=outline(.73,.46,.065),cavity=outline(.665,.395,.044);
    profile.holes.push(new T.Path(cavity.getPoints(16).reverse()));
    const enclosure=new T.ExtrudeGeometry(profile,{depth:1.25,bevelEnabled:false,curveSegments:12});
    enclosure.translate(0,0,-.745);this.mesh(enclosure,shell);
    this.box(.69,.415,.028,shell).position.set(0,0,-.746);
    this.box(.67,.395,.012,trim).position.set(0,0,.512);
    this.buildElectronics();
    this.box(.83,.024,1.48,shell).position.set(0,.265,-.03);
    for(const side of [-1,1])this.box(.018,.045,1.38,shell).position.set(side*.406,.242,-.07);
    this.box(.68, .37, .05, m.graphite).position.z = .53;
    for (let i = 0; i < 4; i++) {
      const barrel = this.cylinder(.24 - i * .017, .10, i % 2 ? m.rubber : m.graphite);
      barrel.position.z = .57 + i * .075;
      this.ring(.235 - i * .017, .003, m.metal).position.z = .63 + i * .075;
    }
    const grip = new T.InstancedMesh(new T.BoxGeometry(.009, .025, .07), m.graphite, 64);
    const tooth = new T.Object3D();
    for (let i = 0; i < 64; i++) {
      const angle = i / 64 * Math.PI * 2;
      tooth.position.set(Math.cos(angle) * .225, Math.sin(angle) * .225, .70);
      tooth.rotation.z = angle - Math.PI / 2;
      tooth.updateMatrix();
      grip.setMatrixAt(i, tooth.matrix);
    }
    grip.castShadow = true;
    this.model.add(grip);
    this.opticalGlass=m.glass.clone();Object.assign(this.opticalGlass,{transparent:true,opacity:.52,roughness:.035,metalness:.12,envMapIntensity:1.2});
    this.cylinder(.164,.018,m.graphite).position.z=.861;
    this.cylinder(.073,.012,m.rubber).position.z=.884;
    this.lens = this.mesh(new T.SphereGeometry(.17, 48, 24), this.opticalGlass);
    this.lens.scale.z = .12;
    this.lens.position.z = .88;
    this.ring(.158,.003,m.graphite).position.z=.889;
    for (let i = 0; i < 18; i++) {
      const a = i / 18 * Math.PI * 2;
      const diode = this.mesh(new T.SphereGeometry(.012, 10, 8), m.glass);
      diode.position.set(Math.cos(a) * .265, Math.sin(a) * .16, .56);
    }
    // Sealed end-cap gaskets and captive screws, rather than painted-on vents.
    for(const z of [-.716,.485]){
      for(const side of [-1,1])this.box(.003,.33,.009,trim).position.set(side*.367,0,z);
      this.box(.61,.003,.009,trim).position.set(0,-.231,z);
    }
    for(const side of [-1,1]){
      for(const z of [-.65,.42])for(const y of [-.14,.14]){
        const fastener=this.cylinder(.013,.008,m.metal);fastener.rotation.y=Math.PI/2;fastener.position.set(side*.37,y,z);
        this.box(.002,.012,.002,m.graphite).position.set(side*.376,y,z);
      }
      // Shallow cooling ribs are cast below the sealed chamber.
      for(let i=0;i<9;i++)this.box(.022,.025,.018,shell).position.set(side*.347,-.224,-.46+i*.085);
    }
    const plate=this.box(.004,.073,.34,m.metal);plate.position.set(.368,.025,-.22);
    const label=document.createElement('canvas');label.width=512;label.height=128;
    const ink=label.getContext('2d');ink.fillStyle='#b7b6ac';ink.fillRect(0,0,512,128);ink.fillStyle='#313732';ink.font='500 28px monospace';ink.fillText('VISN  /  CAM 04',20,42);ink.font='18px monospace';ink.fillText('OPTICAL UNIT  •  IP67',20,76);ink.fillText('24V DC   |   S/N 004-1868',20,105);
    const decal=new T.CanvasTexture(label);decal.colorSpace=T.SRGBColorSpace;
    const labelMesh=this.mesh(new T.PlaneGeometry(.30,.065),new T.MeshStandardMaterial({map:decal,roughness:.68,metalness:.35}));labelMesh.rotation.y=Math.PI/2;labelMesh.position.set(.372,.025,-.22);
    for(const side of [-1,1])for(const y of [-.155,.155]){
      const screw=this.cylinder(.017,.016,m.metal);screw.position.set(side*.29,y,.565);
      this.box(.017,.003,.002,m.graphite).position.copy(screw.position).add(new T.Vector3(0,0,.01));
    }
    this.box(.20, .055, .01, m.graphite).position.set(.2, -.13, -.76);
    this.recordLED = this.mesh(new T.SphereGeometry(.018, 12, 8), m.light);
    this.recordLED.position.set(.30, .13, .57);
    // Cast yoke, tilt spindle and locking washers support the weatherproof case.
    for(const side of [-1,1]){
      this.box(.035,.22,.22,m.metal).position.set(side*.17,-.32,-.28);
      const pivot=this.cylinder(.047,.41,m.graphite);pivot.rotation.y=Math.PI/2;pivot.position.set(0,-.28,-.28);
      const washer=this.cylinder(.055,.012,m.metal);washer.rotation.y=Math.PI/2;washer.position.set(side*.205,-.28,-.28);
    }
    this.box(.40,.035,.31,m.metal).position.set(0,-.44,-.28);
    for(const x of [-.14,.14])this.cylinder(.018,.018,m.graphite).position.set(x,-.45,-.28);
    for(let i=0;i<5;i++){const gland=this.ring(.027,.004,m.rubber);gland.position.set(.2,-.12,-.77-i*.012);}

    const cable = new T.CatmullRomCurve3([
      new T.Vector3(.2, -.12, -.76), new T.Vector3(.44, -.38, -.88),
      new T.Vector3(.30, -.65, -.5), new T.Vector3(0, -.55, -.25),
    ]);
    this.mesh(new T.TubeGeometry(cable, 40, .014, 8, false), m.rubber);

  }

  buildElectronics() {
    const m=this.materials;
    const board=this.box(.59,.018,.93,m.board);board.position.set(0,-.155,-.17);
    // Routed board traces, solder pads, passives and standoffs have physical depth.
    for(let i=0;i<16;i++){
      const x=-.26+i*.034;
      this.box(.002,.0015,.20+(i%4)*.07,m.brass).position.set(x,-.144,-.40);
      this.box(.018,.006,.028,i%3?m.rubber:m.metal).position.set(x,-.135,.20);
      for(const z of [-.59,.25])this.box(.009,.003,.014,m.metal).position.set(x,-.142,z);
    }
    for(const x of [-.25,.25])for(const z of [-.57,.24]){
      const post=this.cylinder(.018,.055,m.brass);post.rotation.x=0;post.position.set(x,-.18,z);
      const screw=this.cylinder(.023,.009,m.metal);screw.rotation.x=0;screw.position.set(x,-.139,z);
      this.box(.025,.002,.004,m.graphite).position.set(x,-.133,z);
    }
    this.box(.18,.028,.18,m.graphite).position.set(0,-.123,.22);
    for(let i=0;i<9;i++)this.box(.014,.038,.15,m.metal).position.set(-.072+i*.018,-.09,.22);
    for(let i=0;i<4;i++){
      const cap=this.cylinder(.022,.055,m.graphite);cap.rotation.x=0;cap.position.set(-.23+i*.055,-.11,-.58);
      const top=this.cylinder(.021,.002,m.metal);top.rotation.x=0;top.position.set(-.23+i*.055,-.081,-.58);
      this.box(.023,.002,.002,m.graphite).position.set(-.23+i*.055,-.079,-.58);
    }
    // Sensor carrier and flex circuit sit immediately behind the optical barrel.
    this.box(.27,.25,.018,m.board).position.set(0,0,.44);
    this.box(.105,.09,.02,m.metal).position.set(0,0,.455);
    this.box(.07,.055,.002,m.glass).position.set(0,0,.467);
    const ribbon=new T.CatmullRomCurve3([new T.Vector3(.16,.025,.43),new T.Vector3(.20,.07,.32),new T.Vector3(.24,-.06,.20),new T.Vector3(.22,-.13,.08)]);
    const positions=[],indices=[];
    for(let i=0;i<=40;i++){const p=ribbon.getPointAt(i/40);positions.push(p.x-.022,p.y,p.z,p.x+.022,p.y,p.z);if(i<40){const n=i*2;indices.push(n,n+1,n+2,n+1,n+3,n+2);}}
    const flex=new T.BufferGeometry();flex.setAttribute('position',new T.Float32BufferAttribute(positions,3));flex.setIndex(indices);flex.computeVertexNormals();
    const flexMaterial=m.brass.clone();flexMaterial.side=T.DoubleSide;this.mesh(flex,flexMaterial);
    for(const offset of [-.014,-.007,0,.007,.014]){const points=ribbon.getSpacedPoints(40).map(p=>p.add(new T.Vector3(offset,.001,0)));this.mesh(new T.TubeGeometry(new T.CatmullRomCurve3(points),40,.0008,4,false),m.metal);}
    for(const x of [-.20,.20]){
      this.box(.08,.08,.07,m.metal).position.set(x,-.08,-.68);
      this.box(.055,.045,.008,m.rubber).position.set(x,-.08,-.721);
    }
  }

  flangeGeometry() {
    const face = new T.Shape();
    face.absarc(0, 0, this.radius, 0, Math.PI * 2, false);
    const hub = new T.Path();
    hub.absarc(0, 0, .047, 0, Math.PI * 2, true);
    face.holes.push(hub);
    for (let i = 0; i < 6; i++) {
      const a = i * Math.PI / 3;
      const cutout = new T.Path();
      cutout.absarc(Math.cos(a) * .185, Math.sin(a) * .185, .070, 0, Math.PI * 2, true);
      face.holes.push(cutout);
    }
    const geometry = new T.ExtrudeGeometry(face, {depth: .012, bevelEnabled: true, bevelSize: .003, bevelThickness: .003, bevelSegments: 2, steps: 1, curveSegments: 16});
    geometry.translate(0, 0, -.006);
    return geometry;
  }

  buildTransport() {
    const m = this.materials;
    this.transport = new T.Group();
    this.transport.position.copy(this.deckCenter);
    this.transport.scale.setScalar(this.transportScale);
    this.transport.rotation.y=Math.PI/2;
    this.model.add(this.transport);
    this.reels = [];
    const flange = this.flangeGeometry();
    for (const x of [-this.halfSpan, this.halfSpan]) {
      const spool = new T.Group();
      spool.position.x = x;
      this.transport.add(spool);
      this.cylinder(.275, .27, m.rubber, spool);
      for (const z of [-.16, .16]) {
        this.mesh(flange, m.metal, spool).position.z = z;
        this.ring(.295, .005, m.brass, spool).position.z = z + .014;
      }
      for (let i = 0; i < 7; i++) this.ring(.255 + i * .005, .0016, m.brass, spool).position.z = .142;
      this.cylinder(.064, .38, m.graphite, spool);
      this.cylinder(.033, .405, m.brass, spool);
      this.reels.push(spool);
      const motor=this.cylinder(.12,.15,m.metal,this.transport);motor.position.set(x,0,.30);
      this.cylinder(.075,.025,m.graphite,this.transport).position.set(x,0,.39);
      for(let i=0;i<8;i++){const a=i*Math.PI/4;this.box(.013,.035,.012,m.graphite,this.transport).position.set(x+Math.cos(a)*.09,Math.sin(a)*.09,.38);}

    }
    this.filmCanvas = document.createElement('canvas');
    this.filmCanvas.width = 4096;
    this.filmCanvas.height = 512;
    this.filmContext = this.filmCanvas.getContext('2d');
    this.filmTexture = new T.CanvasTexture(this.filmCanvas);
    this.filmTexture.colorSpace = T.SRGBColorSpace;
    this.filmTexture.wrapS = T.RepeatWrapping;
    this.filmTexture.anisotropy = Math.min(8, this.town.renderer.capabilities.getMaxAnisotropy());
    this.filmMaterial = new T.MeshStandardMaterial({map: this.filmTexture, color: '#e8d8b3', roughness: .34, metalness: .12, side: T.DoubleSide, transparent: true, alphaTest: .5});
    const positions = [], uv = [], indices = [];
    const circumference = this.halfSpan * 4 + Math.PI * 2 * this.radius;
    for (let i = 0; i <= 192; i++) {
      const [x, y] = filmTransportPoint(i / 192 * circumference, this.halfSpan, this.radius);
      positions.push(x, y, -.21, x, y, .21);
      uv.push(i / 192, 0, i / 192, 1);
      if (i < 192) {const n = i * 2; indices.push(n, n + 1, n + 2, n + 1, n + 3, n + 2);}
    }
    const geometry = new T.BufferGeometry();
    geometry.setAttribute('position', new T.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    this.mesh(geometry, this.filmMaterial, this.transport);
    this.beltLength = circumference;
    this.frames = Array.from({length: 8}, () => null);
    this.paintFilm();
  }

  paintFilm() {
    const ctx = this.filmContext;
    ctx.setTransform(2,0,0,2,0,0);
    ctx.clearRect(0, 0, 2048, 256);
    ctx.fillStyle = '#282a22';
    ctx.fillRect(0, 0, 2048, 256);
    for (let i = 0; i < 8; i++) {
      const x = i * 256;
      ctx.fillStyle = '#b4ac88';
      ctx.fillRect(x + 11, 39, 234, 177);
      if (this.frames[i]) ctx.drawImage(this.frames[i], x + 16, 44, 224, 167);
      else {
        ctx.fillStyle = '#53624d';
        ctx.fillRect(x + 16, 44, 224, 167);
        ctx.strokeStyle = '#b8c4a4';
        ctx.strokeRect(x + 90, 77, 50, 88);
      }
      ctx.fillStyle = '#d3c59e';
      ctx.font = '11px monospace';
      ctx.fillText(String(i + 1).padStart(3, '0'), x + 15, 235);
      for (let h = 0; h < 6; h++) for (const y of [9, 231]) ctx.clearRect(x + h * 42 + 8, y, 21, 15);
    }
    this.filmTexture.needsUpdate = true;
  }

  buildPackets() {
    this.packetCurve = new T.CubicBezierCurve3(new T.Vector3(5.5, 3.8, 5.9), new T.Vector3(6, 5.7, 4.5), new T.Vector3(3, 7.4, 1.5), new T.Vector3(1.5, 9.3, -2));
    this.packets = [];
    for (let i = 0; i < 22; i++) {
      const mesh = this.mesh(new T.BoxGeometry(.035, .025, .06), new T.MeshBasicMaterial({color: i % 3 ? '#a6b795' : '#e0d4b5', transparent: true, opacity: .2, depthWrite: false}), this.town.scene);
      mesh.castShadow = false;
      mesh.layers.set(2);
      this.packets.push(mesh);
    }
  }

  buildFog() {
    this.fogScene = new T.Scene();
    this.fogCamera = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.fogMaterial = new T.ShaderMaterial({
      uniforms: {uZoom: {value: 0}, uReel: {value: 0}, uTime: {value: 0}, uAspect: {value: 1}, uBackdrop: {value: 0}},
      vertexShader: 'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',
      fragmentShader: `precision highp float;varying vec2 vUv;uniform float uZoom,uReel,uTime,uAspect,uBackdrop;
      float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.,1.)),f.x),f.y);}
      float fbm(vec2 p){float n=0.,a=.5;for(int i=0;i<4;i++){n+=noise(p)*a;p=p*2.03+7.1;a*=.5;}return n;}
      void main(){vec2 center=mix(vec2(.41,.56),vec2(.5,.57),uReel);vec2 p=(vUv-center)*vec2(uAspect,1.);float cloud=fbm(p*4.+vec2(uTime*.013,-uTime*.009));float radial=length(p*vec2(.82,1.12));float fog=smoothstep(.16,.78,radial+(cloud-.5)*.15);float textFog=(1.-smoothstep(.30,.57,vUv.y))*mix(smoothstep(.55,.88,vUv.x),1.-smoothstep(.33,.58,vUv.x),uReel);if(uAspect<1.)textFog=1.-smoothstep(.38,.58,vUv.y);float density=max(fog*.985,textFog*.995);density=mix(density,.80+fog*.18,uBackdrop)*uZoom;vec3 cream=mix(vec3(.938,.902,.835),vec3(.961,.929,.875),cloud);gl_FragColor=vec4(cream,density);}`,
      transparent: true, depthWrite: false, depthTest: false,
    });
    this.fogScene.add(new T.Mesh(new T.PlaneGeometry(2, 2), this.fogMaterial));
  }

  setProgress(progress) { this.progress = progress; }

  pose(camera, width, height, time) {
    const {zoom,reel,cutaway}=recordingState(this.progress);
    this.zoom = zoom;
    this.reel = reel;
    const transparent=cutaway>0;
    if(this.shellMaterial.transparent!==transparent){this.shellMaterial.needsUpdate=true;this.trimMaterial.needsUpdate=true;}
    if(this.lastTransparent!==transparent){this.model.traverse(object=>{if(object.isMesh&&(object.material===this.shellMaterial||object.material===this.trimMaterial))object.castShadow=!transparent;});this.lastTransparent=transparent;}
    this.shellMaterial.transparent=cutaway>0;
    this.shellMaterial.depthWrite=cutaway===0;
    this.shellMaterial.opacity=1-cutaway*.985;
    this.trimMaterial.transparent=cutaway>0;this.trimMaterial.depthWrite=cutaway===0;this.trimMaterial.opacity=1-cutaway*.985;
    this.town.canvas.dataset.internalCassette=String(this.cassetteFits);
    this.town.canvas.dataset.cassetteScale=this.transportScale.toFixed(2);
    this.town.canvas.dataset.cutaway=cutaway.toFixed(3);
    this.head.lookAt(new T.Vector3(0, .8, 0));
    this.head.updateWorldMatrix(true, true);
    this.detailLight.visible=zoom>.015;
    this.detailLight.position.copy(this.model.localToWorld(new T.Vector3(2,3,3)));
    this.detailLight.target.position.copy(this.model.localToWorld(new T.Vector3(0,0,-.2)));
    const extent=T.MathUtils.lerp(.9,.14,reel);
    if(this.shadowExtent!==extent){this.shadowExtent=extent;Object.assign(this.detailLight.shadow.camera,{left:-extent,right:extent,top:extent,bottom:-extent});this.detailLight.shadow.camera.updateProjectionMatrix();}
    const phone = width < 760;
    const base = new T.Vector3(12, 7.8, 12);
    const baseTarget = new T.Vector3(0, 1.65, 0);
    const close = new T.Vector3(phone ? 1.9 : 1.5, phone ? 1.05 : .75, phone ? 2.8 : 2.1);
    const closeTarget = new T.Vector3(0, .02, -.06);
    const reelView = this.transport.localToWorld(new T.Vector3(0, phone ? 1.65 : .95, phone ? 2.5 : 1.3));
    const world = p => this.model.localToWorld(p);
    world(close); world(closeTarget);
    const filmTarget = this.transport.localToWorld(new T.Vector3(0, phone ? .07 : .23, 0));
    // The handoff is a physical dolly into the same enclosure, not two
    // independently interpolated shots. The final authored framing is retained.
    const approach=this.approach,macro=this.macro;
    approach.v0.copy(base);approach.v1.copy(base).lerp(close,.30).add(new T.Vector3(0,.8,0));approach.v2.copy(close).lerp(base,.12);approach.v3.copy(close);
    macro.v0.copy(close);macro.v1.copy(close).lerp(reelView,.42);macro.v2.copy(reelView).lerp(close,.12);macro.v3.copy(reelView);
    const target=baseTarget.lerp(closeTarget,zoom).lerp(filmTarget,reel);
    camera.position.copy(zoom<1?approach.getPoint(zoom):macro.getPoint(reel));
    camera.up.set(0,1,0).lerp(new T.Vector3(0,1,0).transformDirection(this.transport.matrixWorld),reel).normalize();
    const dt=Math.max(0,Math.min(time-this.previousTime,.05));this.previousTime=time;
    const pointerActive=this.finePointer&&!this.town.reduced;
    const px=pointerActive?this.town.pointer.x:0,py=pointerActive?this.town.pointer.y:0;
    followPointer(this.pointerX,px,dt);followPointer(this.pointerY,py,dt);
    const inspection=zoom*(1-reel)*.045;
    camera.position.add(new T.Vector3(this.pointerX.value*inspection,-this.pointerY.value*inspection*.65,0).applyQuaternion(this.model.getWorldQuaternion(new T.Quaternion())));
    camera.lookAt(target);
    camera.rotateZ(reel * Math.PI / 6);
    const fov=T.MathUtils.lerp(T.MathUtils.lerp(phone?65:58,phone?52:38,zoom),phone?65:33,reel),near=T.MathUtils.lerp(.1,.015,reel);
    const offsetX=width*T.MathUtils.lerp(.08,0,reel)*zoom,offsetY=height*(phone?.18:T.MathUtils.lerp(.05,0,reel))*zoom;
    if(camera.fov!==fov||camera.near!==near||camera.view?.fullWidth!==width||camera.view?.fullHeight!==height||camera.view?.offsetX!==offsetX||camera.view?.offsetY!==offsetY){
      camera.fov=fov;camera.near=near;
      camera.setViewOffset(width,height,offsetX,offsetY,width,height);
    }
    camera.layers.enable(1);
    camera.updateMatrixWorld();
    const travelDistance = time * this.speed;
    this.reels.forEach(spool => spool.rotation.z = -travelDistance / this.radius);
    this.filmTexture.offset.x = -(travelDistance / this.beltLength) % 1;
    this.recordLED.material.color.set('#bed7a5');
    this.packetCurve.v0.copy(world(new T.Vector3(0,.18,-.7)));
    this.packets.forEach((packet, i) => {
      const t = (time * .14 + i / this.packets.length) % 1;
      packet.position.copy(this.packetCurve.getPoint(t));
      packet.material.opacity = zoom * (1 - reel) * .48 * Math.sin(Math.PI * t) * (1 - T.MathUtils.smoothstep(t, .65, 1));
      packet.rotation.set(t * 2, t * 3, .3);
    });
    this.fogMaterial.uniforms.uZoom.value = zoom;
    this.fogMaterial.uniforms.uReel.value = reel;
    this.fogMaterial.uniforms.uTime.value = time;
    this.fogMaterial.uniforms.uAspect.value = width / height;
    this.tick = time;
    const left = this.transport.localToWorld(new T.Vector3(-this.halfSpan, this.radius, 0)).project(camera);
    const right = this.transport.localToWorld(new T.Vector3(this.halfSpan, this.radius, 0)).project(camera);
    this.town.canvas.dataset.projectedFilmAngle = (Math.atan2(-(right.y-left.y)*height,(right.x-left.x)*width)*180/Math.PI).toFixed(2);
    this.town.canvas.dataset.selectedCamera = '4';
    this.town.canvas.dataset.reelRoll = (reel * 30).toFixed(2);
    this.town.canvas.dataset.recordingView = recordingState(this.progress).phase;
    this.town.canvas.dataset.reelAngle = this.reels[0].rotation.z.toFixed(4);
    this.town.canvas.dataset.filmOffset = this.filmTexture.offset.x.toFixed(4);
  }

  capture(time) {
    if (time - this.lastCapture < 2 || this.zoom < .2) return;
    this.lastCapture = time;
    const renderer = this.town.renderer;
    this.model.updateWorldMatrix(true, true);
    this.captureCamera.position.copy(this.model.localToWorld(new T.Vector3(0, 0, .95)));
    this.captureCamera.lookAt(0, .8, 0);
    const previousTarget = renderer.getRenderTarget();
    const visible = this.model.visible;
    this.model.visible = false;
    const shadowUpdate = renderer.shadowMap.autoUpdate;
    renderer.shadowMap.autoUpdate = false;
    try {
      renderer.setRenderTarget(this.captureTarget);
      renderer.render(this.town.scene, this.captureCamera);
      renderer.readRenderTargetPixels(this.captureTarget, 0, 0, 512, 384, this.captureBytes);
      const pixels = new Uint8ClampedArray(this.captureBytes.length);
      for (let y = 0; y < 384; y++) pixels.set(this.captureBytes.subarray((383 - y) * 2048, (384 - y) * 2048), y * 2048);
      this.captureCanvas.getContext('2d').putImageData(new ImageData(pixels, 512, 384), 0, 0);
      const frame = document.createElement('canvas');
      frame.width = 512; frame.height = 384;
      frame.getContext('2d').drawImage(this.captureCanvas, 0, 0);
      if (this.captureIndex === 0) this.frames.fill(frame);
      this.frames[this.captureIndex++ % 8] = frame;
      this.paintFilm();
    } finally {
      this.model.visible = visible;
      renderer.setRenderTarget(previousTarget);
      renderer.shadowMap.autoUpdate = shadowUpdate;
    }
  }

  render(renderer, scene, camera) {
    if (this.zoom <= .015) {
      camera.layers.enableAll();
      renderer.render(scene, camera);
      return;
    }
    // The town remains the background. Optical separation keeps its nearby lamp
    // from occluding the camera during the macro shot, without moving that lamp.
    camera.layers.set(0);
    renderer.render(scene, camera);
    renderer.autoClear = false;
    this.fogMaterial.uniforms.uBackdrop.value = 1;
    renderer.clearDepth();
    renderer.render(this.fogScene, this.fogCamera);
    renderer.clearDepth();
    camera.layers.set(2);
    renderer.render(scene, camera);
    renderer.clearDepth();
    camera.layers.set(1);
    const shadowUpdate = renderer.shadowMap.autoUpdate;
    renderer.shadowMap.autoUpdate = true;
    renderer.render(scene, camera);
    renderer.shadowMap.autoUpdate = shadowUpdate;
    this.fogMaterial.uniforms.uBackdrop.value = 0;
    renderer.clearDepth();
    renderer.render(this.fogScene, this.fogCamera);
    renderer.autoClear = true;
    camera.layers.enableAll();
  }

  dispose() {
    this.surfaceTextures.forEach(texture=>texture.dispose());
    this.captureTarget.dispose();
    this.detailLight.shadow.map?.dispose();
    this.environment.dispose();
    this.shellMaterial.dispose();
    this.trimMaterial.dispose();
    this.opticalGlass.dispose();
    this.filmTexture.dispose();
    this.fogScene.children[0].geometry.dispose();
    this.fogMaterial.dispose();
  }
}

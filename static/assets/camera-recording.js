import * as T from '../vendor/three.module.min.js';

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
      glass: new T.MeshPhysicalMaterial({color: '#1a4f43', roughness: .08, metalness: .42, clearcoat: 1}),
      light: new T.MeshBasicMaterial({color: '#bed7a5'}),
    };
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
    ctx.fillStyle = '#fff8e8';
    ctx.fillRect(75, 45, 85, 90);
    ctx.fillStyle = '#c5d2c7';
    ctx.fillRect(340, 70, 95, 50);
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
    // Separate walls make a genuine empty enclosure instead of a solid block.
    const shell = this.shellMaterial, trim = this.trimMaterial;
    this.box(.73, .035, 1.25, shell).position.set(0, -.215, -.12);
    this.box(.035, .43, 1.25, shell).position.set(-.35, 0, -.12);
    this.box(.035, .43, 1.25, shell).position.set(.35, 0, -.12);
    this.box(.73, .43, .035, shell).position.set(0, 0, -.735);
    this.box(.68, .035, 1.20, shell).position.set(0, .215, -.12);
    const board = this.box(.59, .018, .93, m.board);
    board.position.set(0,-.155,-.17);
    for(let i=0;i<7;i++) this.box(.035,.018,.12,m.brass).position.set(-.23+i*.075,-.135,.20);
    this.box(.19,.06,.21,m.graphite).position.set(0,-.1,.23);
    for(const x of [-.23,.23]) for(const z of [-.56,.25]) {
      const standoff=this.cylinder(.019,.10,m.brass);standoff.rotation.x=0;standoff.position.set(x,-.1,z);
    }
    this.box(.83, .045, 1.48, shell).position.set(0, .265, -.03);
    this.box(.68, .37, .05, m.graphite).position.z = .53;
    for (let i = 0; i < 4; i++) {
      const barrel = this.cylinder(.24 - i * .017, .10, i % 2 ? m.graphite : m.metal);
      barrel.position.z = .57 + i * .075;
      this.ring(.235 - i * .017, .008, m.brass).position.z = .63 + i * .075;
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
    for (const r of [.105,.156]) this.ring(r,.002,m.glass).position.z=.885;
    for (let i = 0; i < 18; i++) {
      const a = i / 18 * Math.PI * 2;
      const diode = this.mesh(new T.SphereGeometry(.012, 10, 8), m.glass);
      diode.position.set(Math.cos(a) * .265, Math.sin(a) * .16, .56);
    }
    for (const side of [-1, 1]) {
      for (let i = 0; i < 12; i++) {
        this.box(.008, .09, .025, trim).position.set(side * .373, -.04, -.59 + i * .064);
      }
      for (const y of [-.16, .16]) {
        const screw = this.cylinder(.017, .016, m.metal);
        screw.position.set(side * .29, y, .565);
        this.box(.017, .003, .002, m.graphite).position.copy(screw.position).add(new T.Vector3(0, 0, .01));
      }
    }
    for (const side of [-1, 1]) {
      for (const y of [-.155, .155]) this.box(.006, .004, .79, trim).position.set(side * .372, y, -.16);
      for (const z of [-.555, .235]) this.box(.006, .31, .004, trim).position.set(side * .372, 0, z);
    }
    this.box(.20, .055, .01, m.graphite).position.set(.2, -.13, -.76);
    this.recordLED = this.mesh(new T.SphereGeometry(.018, 12, 8), m.light);
    this.recordLED.position.set(.30, .13, .57);
    this.box(.20, .18, .32, m.graphite).position.set(0, -.31, -.28);
    const cable = new T.CatmullRomCurve3([
      new T.Vector3(.2, -.12, -.76), new T.Vector3(.44, -.38, -.88),
      new T.Vector3(.30, -.65, -.5), new T.Vector3(0, -.55, -.25),
    ]);
    this.mesh(new T.TubeGeometry(cable, 40, .014, 8, false), m.rubber);

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
    const zoom = blend(this.progress, .08, .95);
    const reel = blend(this.progress, 2.05, 2.95);
    this.zoom = zoom;
    this.reel = reel;
    const cutaway=blend(reel,.04,.62);
    const transparent=cutaway>0;
    if(this.shellMaterial.transparent!==transparent){this.shellMaterial.needsUpdate=true;this.trimMaterial.needsUpdate=true;}
    this.model.traverse(object=>{if(object.isMesh&&(object.material===this.shellMaterial||object.material===this.trimMaterial))object.castShadow=!transparent;});
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
    Object.assign(this.detailLight.shadow.camera,{left:-extent,right:extent,top:extent,bottom:-extent});this.detailLight.shadow.camera.updateProjectionMatrix();
    const phone = width < 760;
    const base = new T.Vector3(12, 7.8, 12);
    const baseTarget = new T.Vector3(0, 1.65, 0);
    const close = new T.Vector3(phone ? 1.9 : 1.5, phone ? 1.05 : .75, phone ? 2.8 : 2.1);
    const closeTarget = new T.Vector3(0, .02, -.06);
    const reelView = this.transport.localToWorld(new T.Vector3(0, phone ? 1.65 : .95, phone ? 2.5 : 1.3));
    const world = p => this.model.localToWorld(p);
    world(close); world(closeTarget);
    const filmTarget = this.transport.localToWorld(new T.Vector3(0, phone ? .07 : .23, 0));
    const travel = base.clone().lerp(close, zoom);
    travel.y += Math.sin(zoom * Math.PI) * 1.0;
    const target = baseTarget.lerp(closeTarget, zoom);
    camera.position.copy(travel.lerp(reelView, reel));
    camera.up.set(0, 1, 0).lerp(new T.Vector3(0, 1, 0).transformDirection(this.transport.matrixWorld), reel).normalize();
    camera.lookAt(target.lerp(filmTarget, reel));
    camera.rotateZ(reel * Math.PI / 6);
    camera.fov = T.MathUtils.lerp(T.MathUtils.lerp(phone ? 65 : 58, phone ? 52 : 38, zoom), phone ? 65 : 33, reel);
    camera.setViewOffset(width, height, width * T.MathUtils.lerp(.08, 0, reel) * zoom, height * (phone ? .18 : T.MathUtils.lerp(.05, 0, reel)) * zoom, width, height);
    camera.near=T.MathUtils.lerp(.1,.015,reel);
    camera.updateProjectionMatrix();
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
    this.town.canvas.dataset.recordingView = reel > .99 ? 'reel' : zoom > .99 ? 'camera' : 'street';
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

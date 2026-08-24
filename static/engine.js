import * as THREE from 'three';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

class VictorianEnvironment3D {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x0a0c10);
        this.scene.fog = new THREE.FogExp2(0x0a0c10, 0.015);

        // Perspective Camera simulating a 50mm lens
        this.camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 1000);
        this.camera.position.set(0, 10, 80);

        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.container.appendChild(this.renderer.domElement);

        this.clock = new THREE.Clock();
        this.entities = [];
        this.backgrounds = [];

        // 16 FPS Lock Configuration
        this.targetFPS = 16;
        this.frameDuration = 1.0 / this.targetFPS;
        this.timeAccumulator = 0.0;

        this.initLighting();
        this.initParallaxEnvironment();
        this.bindScrollTriggers();

        window.addEventListener('resize', this.onWindowResize.bind(this));
        this.renderLoop();
    }

    initLighting() {
        const ambientLight = new THREE.AmbientLight(0x2a2c35, 1.2);
        this.scene.add(ambientLight);

        // Simulated Gas Lamp illumination affecting planes
        const gasLight = new THREE.PointLight(0xd4af37, 2.5, 150);
        gasLight.position.set(-20, 15, 10);
        this.scene.add(gasLight);
    }

    initParallaxEnvironment() {
        // Load static background SVGs as textures onto massive planes
        this.addBackgroundPlane('/assets/layer_0_sky.svg', 500, 150, -100, 0.05);
        this.addBackgroundPlane('/assets/layer_1_buildings.svg', 600, 100, -50, 0.2);
        this.addBackgroundPlane('/assets/layer_2_street.svg', 600, 40, -15, 0.5);
    }

    addBackgroundPlane(textureUrl, width, height, zDepth, parallaxFactor) {
        const textureLoader = new THREE.TextureLoader();
        textureLoader.load(textureUrl, (texture) => {
            texture.wrapS = THREE.RepeatWrapping;
            texture.wrapT = THREE.RepeatWrapping;

            const geometry = new THREE.PlaneGeometry(width, height);
            const material = new THREE.MeshBasicMaterial({
                map: texture,
                transparent: true,
                depthWrite: false
            });
            const plane = new THREE.Mesh(geometry, material);
            plane.position.set(0, height / 2 - 10, zDepth);
            plane.userData = { parallaxFactor: parallaxFactor, texture: texture, offset: 0 };

            this.backgrounds.push(plane);
            this.scene.add(plane);
        });
    }

    spawnEntity(config) {
        // Creates a 2D Sprite mapped onto a 3D Plane
        const textureLoader = new THREE.TextureLoader();
        textureLoader.load(config.spriteSheetUrl, (texture) => {
            texture.magFilter = THREE.NearestFilter; // Preserve hard vector edges
            texture.minFilter = THREE.NearestFilter;

            // Configure Sprite Sheet dimensions
            const tilesHorizontal = 16;
            const tilesVertical = 1;
            texture.repeat.set(1 / tilesHorizontal, 1 / tilesVertical);

            const geometry = new THREE.PlaneGeometry(config.width, config.height);
            const material = new THREE.MeshBasicMaterial({
                map: texture,
                transparent: true,
                alphaTest: 0.1,
                side: THREE.DoubleSide
            });

            const mesh = new THREE.Mesh(geometry, material);
            mesh.position.set(config.startX, config.yOffset, config.zDepth);

            if (config.direction === -1) {
                mesh.scale.x = -1; // Flip horizontally
            }

            const entity = {
                mesh: mesh,
                texture: texture,
                id: config.id,
                type: config.type,
                speed: config.speed * config.direction,
                currentFrame: 0,
                totalFrames: tilesHorizontal,
                state: 'WALK', // States: WALK, IDLE, RUN, FLEE
                baseSpeed: config.speed * config.direction,
                radius: config.radius || 15
            };

            this.entities.push(entity);
            this.scene.add(mesh);
        });
    }

    bindScrollTriggers() {
        // Camera pushes slightly forward and pans right on scroll
        gsap.to(this.camera.position, {
            x: 60,
            z: 60,
            ease: "none",
            scrollTrigger: {
                trigger: "body",
                start: "top top",
                end: "bottom bottom",
                scrub: 1.5
            }
        });
    }

    calculateInteractions() {
        for (let i = 0; i < this.entities.length; i++) {
            let entA = this.entities[i];

            for (let j = i + 1; j < this.entities.length; j++) {
                let entB = this.entities[j];

                // Calculate Euclidean distance in XZ plane
                let dx = entA.mesh.position.x - entB.mesh.position.x;
                let dz = entA.mesh.position.z - entB.mesh.position.z;
                let distance = Math.sqrt(dx * dx + dz * dz);

                if (distance < (entA.radius + entB.radius)) {
                    this.triggerInteraction(entA, entB);
                }
            }
        }
    }

    triggerInteraction(entA, entB) {
        // Interaction Logic Rule 1: Police Override
        if (entA.type === 'CONSTABLE' && entB.type === 'URCHIN') {
            entB.state = 'FLEE';
            entB.speed = entB.baseSpeed * 3.0; // Urchin runs away
        } else if (entB.type === 'CONSTABLE' && entA.type === 'URCHIN') {
            entA.state = 'FLEE';
            entA.speed = entA.baseSpeed * 3.0;
        }

        // Interaction Logic Rule 2: Vehicle Threat
        const isVehicle = (type) => ['HANSOM_CAB', 'OMNIBUS'].includes(type);
        if (isVehicle(entA.type) && !isVehicle(entB.type)) {
            entB.speed = entB.baseSpeed * 2.0; // Pedestrian rushes
        } else if (isVehicle(entB.type) && !isVehicle(entA.type)) {
            entA.speed = entA.baseSpeed * 2.0;
        }
    }

    renderLoop() {
        requestAnimationFrame(this.renderLoop.bind(this));

        const delta = this.clock.getDelta();
        this.timeAccumulator += delta;

        // Continuous Background Parallax updates every frame
        for (let bg of this.backgrounds) {
            bg.userData.offset += (0.1 * bg.userData.parallaxFactor) * delta;
            bg.userData.texture.offset.x = bg.userData.offset;
        }

        // Fixed 16 FPS Animation and Logic Tick
        if (this.timeAccumulator >= this.frameDuration) {

            this.calculateInteractions();

            for (let entity of this.entities) {
                // Update spatial X coordinate
                if (entity.state !== 'IDLE') {
                    entity.mesh.position.x += entity.speed;
                }

                // Screen wrapping to create infinite crowd
                if (entity.mesh.position.x > 150) entity.mesh.position.x = -150;
                if (entity.mesh.position.x < -150) entity.mesh.position.x = 150;

                // Advance sprite frame
                entity.currentFrame = (entity.currentFrame + 1) % entity.totalFrames;

                // Update texture offset based on current frame
                const offset = entity.currentFrame / entity.totalFrames;
                entity.texture.offset.x = offset;

                // Reset temporary states
                if (entity.state === 'FLEE') {
                    // Gradual speed normalization
                    entity.speed += (entity.baseSpeed - entity.speed) * 0.1;
                    if (Math.abs(entity.speed - entity.baseSpeed) < 0.1) {
                        entity.state = 'WALK';
                    }
                }
            }

            this.timeAccumulator -= this.frameDuration;
        }

        this.renderer.render(this.scene, this.camera);
    }

    onWindowResize() {
        this.camera.aspect = window.innerWidth / window.innerHeight;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(window.innerWidth, window.innerHeight);
    }
}

// Initialization Configuration
document.addEventListener('DOMContentLoaded', () => {
    // Determine the target container
    const engine = new VictorianEnvironment3D('canvas-container');

    // Instantiate the Cast of Characters
    engine.spawnEntity({
        id: 'gentleman_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/gentleman.png',
        width: 16, height: 26, startX: -50, yOffset: 13, zDepth: 5, direction: 1, speed: 0.5, radius: 10
    });

    engine.spawnEntity({
        id: 'urchin_1', type: 'URCHIN', spriteSheetUrl: '/assets/urchin.png',
        width: 12, height: 20, startX: 30, yOffset: 10, zDepth: 8, direction: -1, speed: 0.8, radius: 10
    });

    engine.spawnEntity({
        id: 'constable_1', type: 'CONSTABLE', spriteSheetUrl: '/assets/constable.png',
        width: 17, height: 28, startX: 80, yOffset: 14, zDepth: 2, direction: -1, speed: 0.4, radius: 25
    });

    engine.spawnEntity({
        id: 'hansom_1', type: 'HANSOM_CAB', spriteSheetUrl: '/assets/hansom_cab.png',
        width: 45, height: 35, startX: -100, yOffset: 17.5, zDepth: -10, direction: 1, speed: 1.5, radius: 30
    });

    // newly generated characters
    engine.spawnEntity({ id: 'seamstress_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/seamstress.png', width: 15, height: 25, startX: 60, yOffset: 12.5, zDepth: 6, direction: -1, speed: 0.45, radius: 10 });
    engine.spawnEntity({ id: 'flower_girl_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/flower_girl.png', width: 14, height: 22, startX: -40, yOffset: 11, zDepth: 4, direction: 1, speed: 0.4, radius: 10 });
    engine.spawnEntity({ id: 'chimney_sweep_1', type: 'URCHIN', spriteSheetUrl: '/assets/chimney_sweep.png', width: 13, height: 21, startX: -70, yOffset: 10.5, zDepth: 7, direction: 1, speed: 0.6, radius: 10 });
    engine.spawnEntity({ id: 'lamplighter_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/lamplighter.png', width: 18, height: 27, startX: 110, yOffset: 13.5, zDepth: 1, direction: -1, speed: 0.4, radius: 15 });
    engine.spawnEntity({ id: 'blind_beggar_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/blind_beggar.png', width: 15, height: 24, startX: -20, yOffset: 12, zDepth: 4, direction: 1, speed: 0.2, radius: 10 });
    engine.spawnEntity({ id: 'high_society_lady_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/high_society_lady.png', width: 16, height: 26, startX: 90, yOffset: 13, zDepth: -3, direction: -1, speed: 0.4, radius: 15 });
    engine.spawnEntity({ id: 'newsboy_1', type: 'URCHIN', spriteSheetUrl: '/assets/newsboy.png', width: 13, height: 20, startX: -10, yOffset: 10, zDepth: 9, direction: 1, speed: 0.8, radius: 10 });
    engine.spawnEntity({ id: 'aristocrat_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/aristocrat.png', width: 17, height: 28, startX: -90, yOffset: 14, zDepth: 5, direction: 1, speed: 0.5, radius: 15 });
    engine.spawnEntity({ id: 'street_musician_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/street_musician.png', width: 16, height: 25, startX: -45, yOffset: 12.5, zDepth: 3, direction: -1, speed: 0, radius: 15 });

    // batch 2 characters
    engine.spawnEntity({ id: 'apple_seller_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/apple_seller.png', width: 15, height: 24, startX: 20, yOffset: 12, zDepth: 2, direction: 1, speed: 0, radius: 10 });
    engine.spawnEntity({ id: 'pickpocket_1', type: 'URCHIN', spriteSheetUrl: '/assets/pickpocket.png', width: 14, height: 23, startX: 120, yOffset: 11.5, zDepth: 8, direction: -1, speed: 0.7, radius: 10 });
    engine.spawnEntity({ id: 'rat_catcher_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/rat_catcher.png', width: 17, height: 26, startX: -130, yOffset: 13, zDepth: 4, direction: 1, speed: 0.5, radius: 15 });
    engine.spawnEntity({ id: 'beggar_child_1', type: 'URCHIN', spriteSheetUrl: '/assets/beggar_child.png', width: 12, height: 18, startX: 40, yOffset: 9, zDepth: 5, direction: -1, speed: 0, radius: 10 });
    engine.spawnEntity({ id: 'wealthy_couple_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/wealthy_couple.png', width: 22, height: 27, startX: -80, yOffset: 13.5, zDepth: -5, direction: 1, speed: 0.4, radius: 25 });
    engine.spawnEntity({ id: 'drunkard_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/drunkard.png', width: 16, height: 25, startX: 140, yOffset: 12.5, zDepth: 1, direction: -1, speed: 0.3, radius: 15 });
    engine.spawnEntity({ id: 'priest_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/priest.png', width: 16, height: 26, startX: -150, yOffset: 13, zDepth: 0, direction: 1, speed: 0.3, radius: 12 });
    engine.spawnEntity({ id: 'sailor_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/sailor.png', width: 17, height: 27, startX: 160, yOffset: 13.5, zDepth: 6, direction: -1, speed: 0.5, radius: 15 });
    engine.spawnEntity({ id: 'blacksmith_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/blacksmith.png', width: 18, height: 28, startX: -110, yOffset: 14, zDepth: -2, direction: 1, speed: 0.45, radius: 15 });
    engine.spawnEntity({ id: 'maid_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/maid.png', width: 15, height: 24, startX: -15, yOffset: 12, zDepth: 3, direction: 1, speed: 0.5, radius: 10 });
    engine.spawnEntity({ id: 'postman_1', type: 'CIVILIAN', spriteSheetUrl: '/assets/postman.png', width: 16, height: 26, startX: 130, yOffset: 13, zDepth: 7, direction: -1, speed: 0.6, radius: 12 });
});

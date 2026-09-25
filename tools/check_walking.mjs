import assert from 'node:assert/strict';
import * as THREE from '../static/vendor/three.module.min.js';
import {TownScene} from '../static/engine.js';
import {walkingRoute,walkingTravel,poseWalking} from '../static/assets/walking.js';

// Exercise the actual production model and solver, without a GPU or npm package.
const town=Object.assign(Object.create(TownScene.prototype),{
  scene:new THREE.Scene(),materials:new Map(),geometryCache:new Map(),ramp:null,
  boxGeometry:new THREE.BoxGeometry(1,1,1),sphereGeometry:new THREE.SphereGeometry(1,16,12)
});
const flat=()=>0;
const path=walkingRoute([[0,-6],[0,0],[6,0]]);
let maxContactError=0,maxSlide=0,minClearance=Infinity,maxLift=0,maxKnee=0,samples=0;
for(const index of [0,2,5]) {
  const actor=town.person(index),scale=actor.mesh.scale.y;
  for(const speed of [.5,.85,1.1]) {
    const stride=(.86+speed*.21)*scale;
    const previous=[null,null];
    for(let step=0;step<=720;step++) {
      const distance=2+step/90;
      poseWalking(actor,{path,distance,speed,stride,ground:flat});
      for(let side=0;side<2;side++) {
        const foot=actor.walking.feet[side],ankle=actor.ankles[side];
        const pivot=foot.pitch<0?-.0705:.1945;
        const contact=ankle.localToWorld(new THREE.Vector3(0,-.096,pivot));
        if(foot.stance)maxContactError=Math.max(maxContactError,Math.abs(contact.y));
        const flatContact=ankle.localToWorld(new THREE.Vector3(0,-.096,0));
        if(foot.stance&&foot.phase>.12&&foot.phase<.42) {
          if(previous[side])maxSlide=Math.max(maxSlide,flatContact.distanceTo(previous[side]));
          previous[side]=flatContact;
        } else previous[side]=null;
        // Inspect real boot vertices, not only the requested IK target.
        ankle.traverse(mesh=>{
          if(!mesh.isMesh)return;
          const p=mesh.geometry.attributes.position;
          for(let v=0;v<p.count;v++)minClearance=Math.min(minClearance,new THREE.Vector3().fromBufferAttribute(p,v).applyMatrix4(mesh.matrixWorld).y);
        });
        maxLift=Math.max(maxLift,foot.lift);
        maxKnee=Math.max(maxKnee,actor.knees[side].quaternion.angleTo(new THREE.Quaternion()));
        assert.ok(actor.mesh.matrixWorld.elements.every(Number.isFinite));samples++;
      }
    }
  }
}
assert.ok(maxContactError<1e-5,`Foot contact error ${maxContactError}`);
assert.ok(maxSlide<1e-5,`Planted foot slid ${maxSlide}`);
assert.ok(minClearance>-.002,`Boot penetrated ground ${minClearance}`);
assert.ok(maxLift>.09,'Swing foot must clear the floor');
assert.ok(maxKnee>.5&&maxKnee<2.1,'Knee flexion stays anatomical');
const actor=town.person(2),route=walkingRoute([[0,0],[0,16]]),stride=1;
for(const time of [-1,0,.25,1,8,17,17.75,18,20]) {
  const motion=walkingTravel(time,16,18);
  poseWalking(actor,{path:route,...motion,stride,ground:flat});
  if(time<=0||time>=18) {
    assert.equal(motion.speed,0);
    assert.ok(actor.walking.feet.every(f=>f.stance&&f.lift===0));
  }
}
const reverse={length:route.length,sample:d=>{const p=route.sample(route.length-d);p.yaw+=Math.PI;return p;}};
let lastTurnFeet;
for(let frame=0;frame<=150;frame++) {
  const progress=frame/150;
  poseWalking(actor,{path:reverse,distance:0,speed:.23*Math.sin(Math.PI*progress),stride,ground:flat,turn:{from:route,progress}});
  assert.ok(actor.walking.feet.some(f=>f.stance),'A turn retains a support foot');
  const feet=actor.ankles.map(a=>a.localToWorld(new THREE.Vector3(0,-.096,0)));
  if(lastTurnFeet)for(let side=0;side<2;side++)assert.ok(feet[side].distanceTo(lastTurnFeet[side])<.03,'Turning feet cannot teleport');
  lastTurnFeet=feet;
}
const clock={time:7.9,storyOffset:0,onStage(){},update(){}};
TownScene.prototype.setStage.call(clock,2);
assert.equal(clock.time,7.9,'Story selection must not rewind pedestrian motion');
console.log(JSON.stringify({samples,maxContactError,maxSlide,minClearance,maxLift,maxKnee,startStop:'passed',storyClock:'passed'},null,2));

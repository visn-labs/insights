import assert from 'node:assert/strict';
import * as T from '../static/vendor/three.module.min.js';
import {TownScene} from '../static/engine.js';
import {poseStreetActivity,posePersonDetails} from '../static/assets/street-activity.js';
const town=Object.assign(Object.create(TownScene.prototype),{scene:new T.Scene(),materials:new Map(),geometryCache:new Map(),ramp:null,boxGeometry:new T.BoxGeometry(1,1,1),sphereGeometry:new T.SphereGeometry(1,16,12)});
let maxGripError=0,maxStandingContactError=0;
for(const role of ['reader','vendor']){
 const actor=town.person(role==='reader'?38:32,role);actor.mesh.position.set(0,.23,0);if(role==='vendor')town.buildVendorActivity(actor);
 for(let i=0;i<=500;i++){
  poseStreetActivity(actor,i/20);town.fitHands(actor);posePersonDetails(actor,i/20);actor.mesh.updateWorldMatrix(true,true);
  actor.worldGrips.forEach((target,side)=>{const error=actor.hands[side].getWorldPosition(new T.Vector3()).distanceTo(target);maxGripError=Math.max(maxGripError,error);});
  actor.ankles.forEach(ankle=>maxStandingContactError=Math.max(maxStandingContactError,Math.abs(ankle.localToWorld(new T.Vector3(0,-.096,0)).y-.23)));
  actor.eyes.forEach(eye=>assert.ok(eye.scale.y>=.05&&eye.scale.y<=1));
 }
}
assert.ok(maxStandingContactError<1e-10,'Standing weight shifts must not move foot contact');
assert.ok(maxGripError<.035,`Hand contact error ${maxGripError}`);
console.log(JSON.stringify({actionSamples:1002,maxGripError,maxStandingContactError,reader:'page-linked hand targets',vendor:'produce-linked hand targets'}));

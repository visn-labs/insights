import assert from 'node:assert/strict';
import * as T from '../static/vendor/three.module.min.js';
import {TownScene} from '../static/engine.js';
import {walkingRoute,poseWalking} from '../static/assets/walking.js';
import {poseStreetActivity} from '../static/assets/street-activity.js';
const town=Object.assign(Object.create(TownScene.prototype),{scene:new T.Scene(),materials:new Map(),geometryCache:new Map(),ramp:null,boxGeometry:new T.BoxGeometry(1,1,1),sphereGeometry:new T.SphereGeometry(1,16,12)});
const path=walkingRoute([[0,-8],[0,0],[8,0]]),dt=1/120,speed=.8;
let distanceError=0,maxJointSpeed=0,maxHeadAcceleration=0,previous=null,velocity=null;
const actor=town.person(2);
for(let i=0;i<2200;i++){
 const distance=.2+i*dt*speed;
 if(distance>path.length-.2)break;
 poseWalking(actor,{path,distance,speed,stride:1.02,ground:()=>0});
 const points=[actor.head,...actor.knees,...actor.ankles,...actor.hands].map(o=>o.getWorldPosition(new T.Vector3()));
 if(previous){const v=points.map((p,j)=>p.clone().sub(previous[j]).divideScalar(dt));maxJointSpeed=Math.max(maxJointSpeed,...v.map(p=>p.length()));if(velocity){const acc=v[0].distanceTo(velocity[0])/dt;if(acc>maxHeadAcceleration){maxHeadAcceleration=acc;}}velocity=v;}
 previous=points;
 const a=path.sample(distance).point,b=path.sample(distance+.001).point;
 distanceError=Math.max(distanceError,Math.abs(a.distanceTo(b)/.001-1));
}
assert.ok(distanceError<.005,`Uneven path speed: ${distanceError}`);
assert.ok(maxJointSpeed<5,`Joint jump: ${maxJointSpeed} m/s`);
assert.ok(maxHeadAcceleration<40,`Head acceleration spike: ${maxHeadAcceleration}`);
// Cycle seams must converge as the sample interval shrinks, not snap.
let maxSeamRotation=0;
for(const role of ['reader','vendor','conversation']){
 const person=town.person(38,role==='conversation'?'walker':role);
 if(role==='vendor')town.buildVendorActivity(person);
 if(role==='conversation'){person.partner={};person.conversationPhase=0;}
 const period=role==='reader'?13:role==='vendor'?12:11;
 const seam=period*3-(role==='conversation'?0:person.phase);
 poseStreetActivity(person,seam-1e-5);const before=person.head.quaternion.clone();
 poseStreetActivity(person,seam+1e-5);maxSeamRotation=Math.max(maxSeamRotation,before.angleTo(person.head.quaternion));
}
assert.ok(maxSeamRotation<1e-4,`Action cycle snap: ${maxSeamRotation}`);
console.log(JSON.stringify({rateHz:120,distanceError,maxJointSpeed,maxHeadAcceleration,maxSeamRotation}));

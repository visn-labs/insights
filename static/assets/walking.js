import * as THREE from '../vendor/three.module.min.js';

const TAU=Math.PI*2;
const clamp=THREE.MathUtils.clamp;
const ease=x=>{x=clamp(x,0,1);return x*x*x*(10+x*(-15+x*6));};
const DOWN=new THREE.Vector3(0,-1,0);
const UP=new THREE.Vector3(0,1,0);

// Rounded pavement corners, sampled by metres rather than by curve parameter.
export function walkingRoute(points) {
  const vertices=points.map(([x,z])=>new THREE.Vector3(x,0,z));
  const curve=new THREE.CurvePath();let start=vertices[0];
  for(let i=1;i<vertices.length-1;i++) {
    const v=vertices[i],before=vertices[i-1],after=vertices[i+1];
    const radius=Math.min(.85,v.distanceTo(before)*.2,v.distanceTo(after)*.2);
    const entry=v.clone().add(before.clone().sub(v).normalize().multiplyScalar(radius));
    const exit=v.clone().add(after.clone().sub(v).normalize().multiplyScalar(radius));
    curve.add(new THREE.LineCurve3(start,entry));
    curve.add(new THREE.QuadraticBezierCurve3(entry,v,exit));start=exit;
  }
  curve.add(new THREE.LineCurve3(start,vertices.at(-1)));
  const length=curve.getLength();
  return {length,sample(distance){
    const d=clamp(distance,0,length),u=d/length;
    const point=curve.getPoint(u),direction=curve.getTangent(u).normalize();
    point.addScaledVector(direction,distance-d);
    return {point,yaw:Math.atan2(direction.x,direction.z)};
  }};
}

// Integral of a cosine speed ramp: acceleration and stopping do not teleport feet.
export function walkingTravel(time,length,duration,ramp=1) {
  const t=clamp(time,0,duration),v=length/(duration-ramp);
  if(t<ramp)return {distance:v*(t/2-ramp*Math.sin(Math.PI*t/ramp)/(2*Math.PI)),speed:v*(1-Math.cos(Math.PI*t/ramp))/2};
  if(t>duration-ramp) {
    const left=duration-t;
    return {distance:length-v*(left/2-ramp*Math.sin(Math.PI*left/ramp)/(2*Math.PI)),speed:v*(1-Math.cos(Math.PI*left/ramp))/2};
  }
  return {distance:v*(t-ramp/2),speed:v};
}

function footPlacement(path,distance,stride,side,ground,scale,activity) {
  const shifted=distance/stride+side*.5,cycle=Math.floor(shifted),phase=shifted-cycle;
  const stance=.62,plant=(cycle-side*.5+stance/2)*stride;
  const sample=d=>{
    const {point,yaw}=path.sample(d);
    point.x+=Math.cos(yaw)*(side?1:-1)*.097*scale;
    point.z-=Math.sin(yaw)*(side?1:-1)*.097*scale;
    // Highest support under heel and toe; a flat sole spans the uneven cobbles.
    const floor=Math.max(...[-.065,.065,.19].map(z=>ground(point.x+Math.sin(yaw)*z*scale,point.z+Math.cos(yaw)*z*scale)));
    point.y=floor;return {point,yaw};
  };
  const from=sample(plant);
  let origin=from.point.clone(),yaw=from.yaw,pitch,lift=0;
  if(phase<stance) {
    // Heel landing, flat support, then a push off around the toe.
    pitch=phase<.11?-.20*(1-ease(phase/.11)):.40*ease((phase-.43)/(stance-.43));
  } else {
    const u=(phase-stance)/(1-stance),to=sample(plant+stride);
    origin.lerp(to.point,ease(u));
    yaw+=Math.atan2(Math.sin(to.yaw-yaw),Math.cos(to.yaw-yaw))*ease(u);
    lift=Math.sin(Math.PI*u)**2*.115*scale;
    pitch=.40+(-.60)*ease(u);
  }
  pitch*=activity;lift*=activity;
  const pivot=pitch<0?-.0705:.1945;
  // Keep the supporting heel/toe fixed while rolling the ankle over it.
  const q=new THREE.Quaternion().setFromAxisAngle(UP,yaw);
  const roll=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),pitch);
  const offset=new THREE.Vector3(0,-.096,pivot).applyQuaternion(roll).negate();
  offset.z+=pivot;offset.multiplyScalar(scale).applyQuaternion(q);
  const ankle=origin.clone().add(offset);ankle.y+=lift;
  q.multiply(roll);
  return {origin,ankle,quaternion:q,phase,stance:phase<stance,pitch,lift};
}

function turningFeet(actor,path,turn,stride,ground) {
  const scale=actor.mesh.scale.y,center=path.sample(0).point;
  return [0,1].map(side=>{
    const from=footPlacement(turn.from,turn.from.length,stride,side,ground,scale,0);
    const to=footPlacement(path,0,stride,side,ground,scale,0);
    const u=clamp(turn.progress*2-side,0,1),blend=ease(u);
    const rotation=new THREE.Quaternion().setFromAxisAngle(UP,Math.PI*blend);
    const origin=from.origin.clone().sub(center).applyQuaternion(rotation).add(center);
    origin.y=THREE.MathUtils.lerp(from.origin.y,to.origin.y,blend);
    const lift=Math.sin(Math.PI*u)**2*.10*scale;
    const ankle=origin.clone();ankle.y+=.096*scale+lift;
    return {origin,ankle,quaternion:rotation.multiply(from.quaternion),phase:u,stance:u===0||u===1,pitch:0,lift};
  });
}

// All foot targets live in world space. IK bends the legs to reach them; it never
// rotates a planted foot with the pelvis or uses an unrelated animation clock.
export function poseWalking(actor,{path,distance,speed,stride,ground,turn=null}) {
  const scale=actor.mesh.scale.y,activity=clamp(speed/.38,0,1);
  const phase=turn?turn.progress*TAU+Math.PI:distance/stride*TAU;
  const root=path.sample(distance);
  root.point.y=ground(root.point.x,root.point.z);
  if(turn)root.yaw=turn.from.sample(turn.from.length).yaw+Math.PI*ease(turn.progress);
  actor.mesh.parent.updateWorldMatrix(true,false);
  actor.mesh.position.copy(actor.mesh.parent.worldToLocal(root.point.clone()));
  const facing=new THREE.Quaternion().setFromAxisAngle(UP,root.yaw);
  const parentRotation=actor.mesh.parent.getWorldQuaternion(new THREE.Quaternion());
  actor.mesh.quaternion.copy(parentRotation.invert().multiply(facing));
  const feet=turn?turningFeet(actor,path,turn,stride,ground):[0,1].map(side=>footPlacement(path,distance,stride,side,ground,scale,activity));
  const pelvis=actor.pelvis;
  pelvis.rotation.set(0,Math.cos(phase)*.045*activity,Math.sin(phase)*.022*activity);
  pelvis.position.x=-Math.sin(phase)*.018*activity;
  pelvis.position.z=0;
  const floor=(feet[0].origin.y+feet[1].origin.y)/2;
  const rootHeight=actor.mesh.getWorldPosition(new THREE.Vector3()).y;
  pelvis.position.y=(floor-rootHeight)/scale+actor.anatomy.hip-.022*activity+Math.sin(phase)**2*.012*activity;
  actor.spine.rotation.set(.035*activity,-Math.cos(phase)*.06*activity,-Math.sin(phase)*.016*activity);
  actor.torso.rotation.set(0,0,0);
  actor.head.rotation.set(-.025*activity,Math.cos(phase)*.018*activity,Math.sin(phase)*.012*activity);
  actor.mesh.updateWorldMatrix(true,false);
  // Lower the pelvis just enough to respect bone lengths during double support.
  for(let iteration=0;iteration<2;iteration++) {
    let lower=0;
    for(let side=0;side<2;side++) {
      const hip=actor.limbs[side*2].getWorldPosition(new THREE.Vector3());
      const target=feet[side].ankle;
      const horizontal=Math.hypot(hip.x-target.x,hip.z-target.z);
      const reach=(actor.anatomy.upperLeg+actor.anatomy.lowerLeg-.003)*scale;
      const maxY=target.y+Math.sqrt(Math.max(.001,reach*reach-horizontal*horizontal));
      lower=Math.max(lower,(hip.y-maxY)/scale);
    }
    pelvis.position.y-=lower;actor.mesh.updateWorldMatrix(true,false);
  }
  const inversePelvis=pelvis.getWorldQuaternion(new THREE.Quaternion()).invert();
  for(let side=0;side<2;side++) {
    const leg=actor.limbs[side*2],knee=actor.knees[side],ankle=actor.ankles[side];
    const target=pelvis.worldToLocal(feet[side].ankle.clone()).sub(leg.position);
    const length=target.length(),a=actor.anatomy.upperLeg,b=actor.anatomy.lowerLeg;
    const direction=target.clone().normalize();
    const pole=new THREE.Vector3(0,0,1).applyQuaternion(facing).applyQuaternion(inversePelvis);
    pole.addScaledVector(direction,-pole.dot(direction)).normalize();
    const along=clamp((a*a+length*length-b*b)/(2*length),-a,a);
    const kneePoint=direction.clone().multiplyScalar(along).addScaledVector(pole,Math.sqrt(Math.max(0,a*a-along*along)));
    leg.quaternion.setFromUnitVectors(DOWN,kneePoint.clone().normalize());
    const lower=new THREE.Quaternion().setFromUnitVectors(DOWN,target.clone().sub(kneePoint).normalize());
    knee.quaternion.copy(leg.quaternion).invert().multiply(lower);
    ankle.quaternion.copy(lower).invert().multiply(inversePelvis).multiply(feet[side].quaternion);
    const arm=actor.limbs[side*2+1];
    // The arm opposite the advancing leg swings forward, with a relaxed elbow.
    arm.rotation.set(Math.cos(phase+side*Math.PI)*.27*activity,0,(side?1:-1)*.045,'YXZ');
    actor.elbows[side].rotation.x=-.13-.12*activity*(.5+.5*Math.sin(phase+side*Math.PI));
  }
  actor.walking={distance,speed,stride,feet};
  actor.mesh.updateWorldMatrix(true,true);
}

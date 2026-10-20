import * as T from '../vendor/three.module.min.js';
const smooth=x=>{x=T.MathUtils.clamp(x,0,1);return x*x*(3-2*x);};
const window=(t,a,b)=>smooth((t-a)/(b-a));

const DOWN=new T.Vector3(0,-1,0);

function settleStanding(actor,time){
 if(actor.seated)return;
 const pelvis=actor.pelvis,a=actor.anatomy.upperLeg,b=actor.anatomy.lowerLeg;
 pelvis.position.set(Math.sin(time*.34+actor.phase)*.009,actor.anatomy.hip-.005,0);
 pelvis.rotation.set(0,0,0);
 for(let side=0;side<2;side++){
  const leg=actor.limbs[side*2],target=new T.Vector3((side?1:-1)*.097,.096,0).sub(pelvis.position).sub(leg.position);
  const length=target.length(),direction=target.clone().normalize();
  const along=(a*a+length*length-b*b)/(2*length);
  const joint=direction.multiplyScalar(along).add(new T.Vector3(0,0,Math.sqrt(Math.max(0,a*a-along*along))));
  leg.quaternion.setFromUnitVectors(DOWN,joint.clone().normalize());
  const lower=new T.Quaternion().setFromUnitVectors(DOWN,target.sub(joint).normalize());
  actor.knees[side].quaternion.copy(leg.quaternion).invert().multiply(lower);
  actor.ankles[side].quaternion.copy(lower).invert();
 }
}

// Every action has a contact target. Hands follow the same object that moves.
export function poseStreetActivity(actor,time){
 settleStanding(actor,time);
 const breath=Math.sin(time*1.5+actor.phase);
 actor.spine.rotation.x=breath*.006;
 actor.head.rotation.set(breath*.012,Math.sin(time*.24+actor.phase)*.055,0);
 for(let side=0;side<2;side++){
  actor.limbs[side*2+1].rotation.set(-.045,0,(side?1:-1)*.035,'YXZ');
  actor.elbows[side].rotation.x=-.14;
 }
 if(actor.book){
  const t=(time+actor.phase)%13;
  const flip=window(t,8,8.8)*(1-window(t,10.1,11));
  actor.page.rotation.z=flip*Math.PI*.88;
  actor.head.rotation.x=.24+flip*.035+breath*.008;
  actor.mesh.updateWorldMatrix(true,true);
  actor.worldGrips=[actor.book.localToWorld(new T.Vector3(-.155,.015,.04)),actor.page.localToWorld(new T.Vector3(.155,.015,.04))];
 }else if(actor.produce){
  const t=(time+actor.phase)%12;
  const lift=window(t,1.2,3)*(1-window(t,7.5,9.5));
  actor.produce.position.set(.15-.09*lift,1.02+.21*lift,.39-.10*lift);
  actor.head.rotation.x=.13+lift*.07;
  actor.head.rotation.y=lift*.08;
  actor.mesh.updateWorldMatrix(true,true);
  actor.worldGrips=[actor.mesh.localToWorld(new T.Vector3(-.16,1.02,.36)),actor.produce.getWorldPosition(new T.Vector3()).add(new T.Vector3(0,.035,0))];
 }else if(actor.partner){
  const t=(time+actor.conversationPhase)%11;
  const speaking=window(t,.4,1.3)*(1-window(t,4.2,5.4));
  actor.limbs[3].rotation.x=-.08-speaking*.23;
  actor.elbows[1].rotation.x=-.16-speaking*.55;
  actor.hands[1].rotation.z=-speaking*.24;
  actor.head.rotation.x=breath*.02+(speaking?0:Math.sin(t*2)*.025);
 }
}

export function posePersonDetails(actor,time){
 const period=3.4+(actor.phase%2.7),phase=(time+actor.phase)%period;
 const blink=phase<.16?Math.sin(phase/.16*Math.PI):0;
 actor.eyes?.forEach(eye=>eye.scale.y=1-blink*.94);
 if(actor.bag){const p=actor.walking?actor.walking.distance/actor.walking.stride*Math.PI*2:0;actor.bag.rotation.z=Math.sin(p-.4)*.035;}
}

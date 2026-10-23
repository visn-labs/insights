import {transition} from './recording-state.js';
export const REVEAL_SECONDS=5.6;
export function revealState(time){
  return {gather:1-transition(time,1.8,3),cream:transition(time,1.9,3.4),
    blades:transition(time,1.65,3.5),hex:transition(time,3.05,4.1),
    pathways:transition(time,3.5,5.1),nodes:transition(time,3.5,5.1),
    core:transition(time,.8,2.3),caption:transition(time,4.65,5.35)};
}
// Outgoing perspective rays have decreasing depth; these rays increase depth.
// Used both by the shader's projection and the directional regression check.
export function inwardRadius(depth){return Math.atan(.075/depth);}


// Curved outward travel follows the green gaps rather than cutting straight
// across the aperture leaves; its end position is the frozen logo's radius.
export function outwardNode(progress,index=0){
  const x=400*progress*(1-progress),y=-602*progress,angle=index*Math.PI/3;
  return {x:x*Math.cos(angle)-y*Math.sin(angle),y:x*Math.sin(angle)+y*Math.cos(angle),radius:Math.hypot(x,y)};
}

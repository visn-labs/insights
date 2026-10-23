// One scroll score for the renderer, copy and chapter navigation. No delayed scroll.
const clamp=x=>Math.max(0,Math.min(1,x));
export const transition=(value,start,end)=>{
  const t=clamp((value-start)/(end-start));
  return t*t*t*(10+t*(-15+t*6));
};
export function recordingState(progress){
  const zoom=transition(progress,.08,.95),reel=transition(progress,2.05,2.95);
  return {zoom,reel,cutaway:transition(reel,.04,.62),
    chapter:reel>.5?1:0,phase:reel>.99?'reel':zoom>.99?'camera':'street',
    cameraCopy:transition(progress,.48,.92)*(1-transition(progress,1.85,2.35)),
    reelCopy:transition(progress,2.32,2.88),
    journey:clamp((progress-.08)/2.87)};
}

// Exact critically damped response: pointer reversals preserve velocity, and
// equal elapsed time gives the same result at 30, 60 or 120 Hz. Scroll stays exact.
export function followPointer(state,target,dt,frequency=11){
  if(dt<=0)return state;
  const offset=state.value-target,decay=Math.exp(-frequency*dt);
  const impulse=state.velocity+frequency*offset;
  state.value=target+(offset+impulse*dt)*decay;
  state.velocity=(state.velocity-frequency*impulse*dt)*decay;
  return state;
}

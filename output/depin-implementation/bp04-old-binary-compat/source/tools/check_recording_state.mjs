import assert from 'node:assert/strict';
import {recordingState,transition,followPointer} from '../static/assets/recording-state.js';
for(const progress of [-1,0,.08,.48,.95,1.02,1.85,2.05,2.5,2.95,2.98,4,5]){
 const state=recordingState(progress);
 for(const key of ['zoom','reel','cutaway','cameraCopy','reelCopy','journey'])assert.ok(state[key]>=0&&state[key]<=1,key);
}
assert.equal(recordingState(1.02).cameraCopy,1);assert.equal(recordingState(2.98).reelCopy,1);
assert.equal(recordingState(1.02).cutaway,0);assert.equal(recordingState(2.98).cutaway,1);
assert.equal(recordingState(2.5).chapter,0);assert.equal(recordingState(2.98).chapter,1);
// Camera travel reaches each hold with zero velocity and zero acceleration.
const h=1e-4;
for(const edge of [.08,.95]){
 const before=transition(edge-h,.08,.95),at=transition(edge,.08,.95),after=transition(edge+h,.08,.95);
 assert.ok(Math.abs(after-before)/(2*h)<1e-5);
 assert.ok(Math.abs(after-2*at+before)/h**2<.01);
}
const outcomes=[30,60,120].map(hz=>{const state={value:0,velocity:0};for(let i=0;i<hz;i++)followPointer(state,1,1/hz);return state.value;});
assert.ok(Math.max(...outcomes)-Math.min(...outcomes)<1e-12);
const pointer={value:0,velocity:0};for(let i=0;i<12;i++)followPointer(pointer,1,1/60);
const previous=pointer.value;followPointer(pointer,-1,1/120);
assert.ok(Math.abs(pointer.value-previous)<.04,'Pointer reversals must retain a continuous trajectory');
assert.ok(Number.isFinite(pointer.velocity));
console.log(JSON.stringify({sharedScore:'passed',endpointContinuity:'passed',pointerRateIndependence:outcomes,pointerReversal:'passed'}));

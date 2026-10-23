import assert from 'node:assert/strict';
import {filmTransportPoint} from '../static/assets/camera-recording.js';
const radius=.30,half=.72,length=half*4+2*Math.PI*radius;
for(let i=0;i<2000;i++){
 const d=i/2000*length,a=filmTransportPoint(d),b=filmTransportPoint(d+1e-5);
 assert.ok(Math.hypot(b[0]-a[0],b[1]-a[1])<=1.0001e-5,'No teleport at a straight/arc seam');
 const closed=filmTransportPoint(d+length);assert.ok(Math.hypot(closed[0]-a[0],closed[1]-a[1])<1e-10,'Closed film loop');
}
const upper=filmTransportPoint(.5),upperNext=filmTransportPoint(.51),lower=filmTransportPoint(half*2+Math.PI*radius+.5),lowerNext=filmTransportPoint(half*2+Math.PI*radius+.51);
assert.ok(upperNext[0]>upper[0]&&lowerNext[0]<lower[0],'Upper and return film move in opposite directions');
assert.ok(Math.abs(Math.hypot(...upper.map((v,i)=>v-upperNext[i]))-Math.hypot(...lower.map((v,i)=>v-lowerNext[i])))<1e-10,'Both film runs have identical physical speed');
console.log('Recording transport: 2,000 loop/seam samples; opposing lanes and equal speed passed.');

import assert from 'node:assert/strict';
import {revealState,inwardRadius,outwardNode} from '../static/assets/logo-reveal-state.js';
let lastRadius=Infinity,lastBlade=-1,lastNode=-1,lastOutwardRadius=-1;
for(let sample=0;sample<=1200;sample++){
 const time=sample/200,state=revealState(time),radius=inwardRadius(.1+time*.55);
 assert.ok(radius<=lastRadius,'Perspective motion must converge');
 assert.ok(state.blades>=lastBlade,'Blades assemble inward without reversing');
 assert.ok(state.nodes>=lastNode,'Nodes grow outward without reversing');
 assert.equal(state.pathways,state.nodes,'Pathway fronts must follow the same outward travel');
 const node=outwardNode(state.nodes);assert.ok(node.radius>=lastOutwardRadius,'Green nodes must keep moving outward');lastOutwardRadius=node.radius;
 lastRadius=radius;lastBlade=state.blades;lastNode=state.nodes;
}
for(const key of ['blades','hex','pathways','nodes','core','caption'])assert.equal(revealState(5.6)[key],1);
assert.equal(revealState(5.6).gather,0);
console.log('1,201 timeline samples: inward flow, inward blade assembly, outward nodes/pathways, final hold passed.');

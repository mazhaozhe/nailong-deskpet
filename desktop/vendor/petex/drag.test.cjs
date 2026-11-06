const {test}=require('node:test');
const assert=require('node:assert/strict');
const {DragTracker}=require('../app/drag.cjs');
const sector=state=>Math.round(((Math.atan2(state.gaze.x,-state.gaze.y)+Math.PI*2)%(Math.PI*2))/(Math.PI/8))%16;
const tracker=()=>new DragTracker({x:100,y:100},{x:40,y:30},0);
test('dragging ignores press jitter and preserves the initial grab offset',()=>{
  const drag=tracker();assert.equal(drag.update({x:103,y:102},16).dragging,false);
  const state=drag.update({x:110,y:100},32);assert.equal(state.dragging,true);assert.deepEqual(state.position,{x:50,y:30});
  assert.deepEqual(drag.update({x:145,y:128},48).position,{x:85,y:58});
});
test('stationary samples retain left facing rather than flickering back to right',()=>{
  const drag=tracker();const first=drag.update({x:80,y:100},16);assert.equal(first.direction,'running-left');assert.equal(sector(first),12);
  for(let time=32;time<1000;time+=16){const state=drag.update({x:80,y:100},time);assert.equal(state.direction,'running-left');assert.equal(sector(state),12);}
});
test('movement selects all sixteen v2 directions including vertical and diagonals',()=>{
  for(let i=0;i<16;i++){
    const angle=i*Math.PI/8;const drag=tracker();const state=drag.update({x:100+Math.sin(angle)*20,y:100-Math.cos(angle)*20},16);
    assert.equal(sector(state),i);
  }
});
test('direction filtering rejects boundary noise but follows a deliberate reversal',()=>{
  const drag=tracker();let point={x:100,y:80};drag.update(point,16);
  for(let i=2;i<15;i++){
    const angle=(i%2?10:13)*Math.PI/180;point={x:point.x+Math.sin(angle)*8,y:point.y-Math.cos(angle)*8};
    assert.equal(sector(drag.update(point,i*16)),0);
  }
  for(let i=15;i<35;i++){point={x:point.x,y:point.y+10};drag.update(point,i*16);}
  assert.equal(sector(drag.update(point,560)),8);
});
test('a drag remains active when returning to the start or holding for over 15 seconds',()=>{
  const drag=tracker();drag.update({x:120,y:100},16);
  assert.equal(drag.update({x:100,y:100},32).dragging,true);
  assert.equal(drag.update({x:100,y:100},60000).dragging,true);
});
test('a slow turn after a fast move is not dominated by the previous speed',()=>{
  const drag=tracker();drag.update({x:20,y:100},16);
  drag.update({x:20,y:100},600);
  const state=drag.update({x:20,y:97},616);
  assert.equal(sector(state),0);
});

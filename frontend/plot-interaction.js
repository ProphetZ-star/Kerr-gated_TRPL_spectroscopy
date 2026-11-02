// Captured pointer drags with a position readout and no synthetic click after drag.
export function bindMarkerDrag(canvas,{start,move,end}){
 let active=null,readout=null,suppress=false;
 const hide=()=>{if(readout){readout.remove?.();readout.hidden=true;readout=null;}};
 const show=(event,label)=>{if(!readout){readout=document.createElement('div');readout.className='marker-readout';readout.setAttribute('role','status');document.body.append(readout);}readout.textContent=label;readout.style.left=Math.max(4,Math.min((window.innerWidth||1280)-280,event.clientX+12))+'px';readout.style.top=Math.max(4,event.clientY-32)+'px';};
 canvas.addEventListener('pointerdown',event=>{
  if(event.button!==undefined&&event.button!==0)return;
  const value=start(event);if(!value)return;
  active={value,x:event.clientX,y:event.clientY,moved:false,id:event.pointerId};canvas.setPointerCapture?.(event.pointerId);event.preventDefault?.();if(value.label)show(event,value.label);
 });
 canvas.addEventListener('pointermove',event=>{
  if(!active)return;if(Math.hypot(event.clientX-active.x,event.clientY-active.y)>2)active.moved=true;
  if(active.moved){const label=move(event,active.value);if(label)show(event,label);}event.preventDefault?.();
 });
 const finish=(event,cancelled)=>{
  if(!active)return;const current=active;active=null;
  if(current.moved||cancelled)end?.(event,current.value,cancelled);
  suppress=current.moved&&!cancelled;hide();
  try{canvas.releasePointerCapture?.(current.id);}catch{}
  setTimeout(()=>{suppress=false;},0);
 };
 canvas.addEventListener('pointerup',event=>finish(event,false));canvas.addEventListener('pointercancel',event=>finish(event,true));
 canvas.addEventListener('click',event=>{if(suppress){event.preventDefault?.();event.stopImmediatePropagation?.();suppress=false;}},true);
}

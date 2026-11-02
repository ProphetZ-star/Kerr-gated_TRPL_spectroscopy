// Keep the statistics overlay inside its own plot, including after a resize.
export function bindOverlayDrag(element,parent){
 let active=null,position=null;
 const place=(x,y)=>{
  const box=parent.getBoundingClientRect(),size=element.getBoundingClientRect();
  const maxX=Math.max(0,box.width-size.width),maxY=Math.max(0,box.height-size.height);
  const left=Math.max(0,Math.min(maxX,x)),top=Math.max(0,Math.min(maxY,y));
  element.style.left=left+'px';element.style.top=top+'px';element.style.right='auto';
  position={x:maxX?left/maxX:0,y:maxY?top/maxY:0};
 };
 element.addEventListener('pointerdown',event=>{
  if(event.button!==undefined&&event.button!==0)return;
  const box=element.getBoundingClientRect(),bounds=parent.getBoundingClientRect();
  active={id:event.pointerId,x:event.clientX,y:event.clientY,left:box.left-bounds.left,top:box.top-bounds.top};
  element.setPointerCapture?.(event.pointerId);event.preventDefault();event.stopPropagation?.();
 });
 element.addEventListener('pointermove',event=>{if(!active||event.pointerId!==active.id)return;place(active.left+event.clientX-active.x,active.top+event.clientY-active.y);event.preventDefault();});
 const end=event=>{if(!active||event.pointerId!==active.id)return;try{element.releasePointerCapture?.(active.id);}catch{}active=null;};
 element.addEventListener('pointerup',end);element.addEventListener('pointercancel',end);
 new ResizeObserver(()=>{if(!position)return;const box=parent.getBoundingClientRect(),size=element.getBoundingClientRect();if(box.width&&box.height)place(position.x*Math.max(0,box.width-size.width),position.y*Math.max(0,box.height-size.height));}).observe(parent);
}

import {mapRGB} from './map-palette.js';
const colors=Array.from({length:256},(_,i)=>mapRGB(i/255));
let cached=null;
const stats={pixels:0,builds:0,draws:0};
export function mapRenderStats(){return {...stats};}
export function drawMapRaster(q,{groups,delays,axis,low,high,scale,vmax,background}){
 const height=Math.max(1,Math.ceil(q.p.b-q.p.t)),key=[height,q.ymin,q.ymax,low,high,scale,vmax].join('|');
 let full=!cached||cached.key!==key||cached.delays!==delays||cached.groups!==groups||cached.background!==background;
 if(full){
  const canvas=document.createElement('canvas');canvas.width=128;canvas.height=height;const ctx=canvas.getContext('2d'),pixels=ctx.createImageData(128,height),rows=[];
  for(let y=0;y<height;y++){
   const delay=q.ymin+(q.ymax-q.ymin)*(y+.5)/height;
   if(delay<low||delay>high){rows.push(-1);continue;}
   let a=0,b=delays.length-1;
   while(a<b){const mid=(a+b)>>1;if(delay>(delays[mid]+delays[mid+1])/2)a=mid+1;else b=mid;}
   rows.push(a);
  }
  cached={key,canvas,ctx,pixels,rows,versions:new Map(),groups,delays,background};stats.builds++;
 }
 const c=cached,changed=new Set();
 for(const [id,g]of groups)if(full||c.versions.get(id)!==g.version){changed.add(id);c.versions.set(id,g.version);}
 let dirty=full;
 if(changed.size||full)for(let y=0;y<height;y++){
  const id=c.rows[y];if(!full&&!changed.has(id))continue;
  const group=groups.get(id);dirty=true;
  for(let x=0;x<128;x++){
   const offset=(y*128+x)*4;let v=group?.bins[x];
   if(background&&group){let base=0;for(let k=x*8;k<(x+1)*8;k++)base+=background[k]/8;v-=base;}
   if(group&&(scale!=='log'||v>0)){
    const f=scale==='log'?Math.log10(Math.max(v,1))/Math.log10(Math.max(vmax,2)):Math.max(v,0)/vmax;
    const color=colors[Math.max(0,Math.min(255,Math.round(f*255)))];c.pixels.data[offset]=color[0];c.pixels.data[offset+1]=color[1];c.pixels.data[offset+2]=color[2];c.pixels.data[offset+3]=255;
   }else c.pixels.data[offset+3]=0;
   stats.pixels++;
  }
 }
 if(dirty)c.ctx.putImageData(c.pixels,0,0);
 const {ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.imageSmoothingEnabled=false;
 // Each spectral bin uses the experiment's actual wavelength boundaries.
 for(let x=0;x<128;x++){
  const a=q.x(axis[x*8]),b=q.x(axis[Math.min((x+1)*8,1023)]);
  ctx.drawImage(c.canvas,x,0,1,height,Math.min(a,b),p.t,Math.abs(b-a)+.5,p.b-p.t);
 }
 ctx.restore();stats.draws++;
}

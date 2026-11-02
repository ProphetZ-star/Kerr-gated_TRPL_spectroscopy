import {store,grouped,axisForRun,backgroundForRun} from './store.js';
import {plotRange,registerAxisEndpoints} from './plot-ranges.js';
import {bindMarkerDrag} from './plot-interaction.js';
import {MAP_GRADIENT} from './map-palette.js';
import {drawMapRaster} from './map-raster.js';
const $=id=>document.getElementById(id),css=k=>getComputedStyle(document.body).getPropertyValue(k).trim();
function setup(id,xmin,xmax,ymin,ymax,left=76,background='--plot',metadata={}){
 ({xmin,xmax,ymin,ymax}=plotRange(id,{xmin,xmax,ymin,ymax},metadata));
 const c=$(id),r=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
 if(r.width<1||r.height<1)return null;
 c.width=Math.round(r.width*dpr);c.height=Math.round(r.height*dpr);
 const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.font='13px Segoe UI';
 const p={l:left,r:r.width-22,t:14,b:r.height-48};
 ctx.fillStyle=css(background);ctx.fillRect(p.l,p.t,p.r-p.l,p.b-p.t);
 const x=v=>p.l+(v-xmin)/(xmax-xmin||1)*(p.r-p.l),y=v=>p.b-(v-ymin)/(ymax-ymin||1)*(p.b-p.t);
 return {id,ctx,p,x,y,w:r.width,h:r.height,xmin,xmax,ymin,ymax};
}
function axes(q,xlabel,ylabel,grid=true){
 if(!q)return;const {ctx,p,x,y}=q;ctx.fillStyle=css('--muted');ctx.strokeStyle=css('--chart-grid');ctx.lineWidth=1;
 for(let i=0;i<=4;i++){
  const xv=q.xmin+(q.xmax-q.xmin)*i/4,xx=x(xv);if(grid){ctx.beginPath();ctx.moveTo(xx,p.t);ctx.lineTo(xx,p.b);ctx.stroke();}ctx.textAlign='center';if(q.w>=220||i%2===0)ctx.fillText(format(xv),xx,p.b+20);
  const yv=q.ymin+(q.ymax-q.ymin)*i/4,yy=y(yv);if(grid){ctx.beginPath();ctx.moveTo(p.l,yy);ctx.lineTo(p.r,yy);ctx.stroke();}
  if(ylabel){ctx.textAlign='right';ctx.fillText(format(yv),p.l-8,yy+4);}
 }
 ctx.textAlign='center';ctx.fillText(xlabel,(p.l+p.r)/2,q.h-8);
 if(ylabel){ctx.save();ctx.translate(18,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.fillText(ylabel,0,0);ctx.restore();}
 ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t);registerAxisEndpoints(q,{x:true,y:!!ylabel,xY:p.b+20,yX:p.l-8,format});

}
function format(v){return Math.abs(v)>=1000?(v/1000).toFixed(1)+'k':Number(v.toFixed(2)).toString();}
function line(q,data,color,dash=[]){
 const {ctx,p,x,y}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.beginPath();let started=false;
 for(const [a,b] of data){if(!Number.isFinite(a)||!Number.isFinite(b)){started=false;continue;}if(started)ctx.lineTo(x(a),y(b));else ctx.moveTo(x(a),y(b));started=true;}
 ctx.strokeStyle=color;ctx.lineWidth=2;ctx.setLineDash(dash);ctx.stroke();
 const finite=data.filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b));
 if(finite.length===1){ctx.beginPath();ctx.fillStyle=color;ctx.arc(x(finite[0][0]),y(finite[0][1]),2.5,0,Math.PI*2);ctx.fill();}
 ctx.restore();
}
function empty(q,text){q.ctx.fillStyle=css('--muted');q.ctx.textAlign='center';q.ctx.fillText(text,(q.p.l+q.p.r)/2,(q.p.t+q.p.b)/2);}
let spectrumQ,mapQ;
export function initChartDrags(onChanged){
 for(const id of ['spectrum','map'])bindMarkerDrag($(id),{
  start(event){const q=id==='spectrum'?spectrumQ:mapQ,axis=id==='spectrum'?store.snapshot?.live_axis:axisForRun();if(!q||!axis)return null;
   const rect=$(id).getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;if(x<q.p.l||x>q.p.r||y<q.p.t||y>q.p.b||Math.abs(x-q.x(store.wave))>8)return null;return {wave:store.wave,label:store.wave.toFixed(3)+' nm'};},
  move(event){const q=id==='spectrum'?spectrumQ:mapQ,axis=id==='spectrum'?store.snapshot?.live_axis:axisForRun();if(!q||!axis)return null;
   const px=event.clientX-$(id).getBoundingClientRect().left,f=Math.max(0,Math.min(1,(px-q.p.l)/(q.p.r-q.p.l)));
   store.wave=Math.max(Math.min(...axis),Math.min(Math.max(...axis),q.xmin+f*(q.xmax-q.xmin)));store.fit=null;onChanged();return store.wave.toFixed(3)+' nm';},
  end(event,state,cancelled){if(cancelled){store.wave=state.wave;store.fit=null;onChanged();}}
 });
}
export function clickWave(id,clientX){
 const q=id==='spectrum'?spectrumQ:mapQ;if(!q)return;
 const c=$(id),px=clientX-c.getBoundingClientRect().left;
 if(px<q.p.l||px>q.p.r)return;
 const axis=id==='spectrum'?store.snapshot?.live_axis:axisForRun();if(!axis)return;
 store.wave=q.xmin+(px-q.p.l)/(q.p.r-q.p.l)*(q.xmax-q.xmin);
 store.fit=null;drawAll();
}
export function drawAll(){
 if(document.querySelector('.workspace').hidden)return;
 $('mapGradient').style.background=MAP_GRADIENT;
 const s=store.snapshot??{latest:null,live_axis:null,camera:{full_scale:65535},background:null};
 const live=s.latest,axis=s.live_axis,liveAxis=axis??Array.from({length:1024},(_,i)=>i);
 const log=store.specScale==='log',max=s.camera.full_scale;
 const q=spectrumQ=setup('spectrum',Math.min(...liveAxis),Math.max(...liveAxis),log?0:0,log?Math.log10(max)*1.05:max*1.05,76,'--plot',{xKey:axis?'spectrum:nm':'spectrum:pixel',xLabel:axis?'Wavelength / nm':'Pixel',yKey:log?'spectrum:log':'spectrum:counts',yLabel:log?'log₁₀ CCD counts':'CCD / counts'});
 if(!q)return;
 axes(q,axis?'Wavelength / nm':'Pixel',log?'log₁₀ CCD counts':'CCD / counts',true);
 const yval=v=>log?(v>0?Math.log10(v):NaN):v;
 if(live){line(q,live.spectrum.map((v,i)=>[liveAxis[i],yval(v)]),css('--data'));}
 else empty(q,'Start preview or acquisition');
 const bg=s.background;
 if(bg&&live&&bg.exposure_s===live.exposure_s)line(q,bg.spectrum.map((v,i)=>[liveAxis[i],yval(v)]),css('--amber'),[5,4]);
 if(axis&&live)line(q,[[store.wave,0],[store.wave,q.ymax]],css('--cursor'),[4,4]);
 const saturated=live?.saturated_pixels??0;$('saturation').hidden=!saturated;$('saturation').textContent=`⚠ ${saturated} saturated pixels`;
 const ra=axisForRun(),mapAxis=ra??Array.from({length:1024},(_,i)=>i),plan=store.run?.plan;
 const delays=plan?.delays_ps??[-2,20];let low=Math.min(...delays),high=Math.max(...delays);if(low===high){low-=.5;high+=.5;}
 const groups=grouped(),background=backgroundForRun();
 const m=mapQ=setup('map',Math.min(...mapAxis),Math.max(...mapAxis),low,high,76,'--plot',{xKey:ra?'map:nm':'map:pixel',xLabel:ra?'Wavelength / nm':'Pixel'});
 if(!m)return;
 m.y=t=>m.p.t+(t-m.ymin)/(m.ymax-m.ymin)*(m.p.b-m.p.t);
 let vmax=1;
 for(const g of groups.values()){
  if(!background)vmax=Math.max(vmax,g.mapMax);
  else for(let i=0;i<g.spectrum.length;i+=8)vmax=Math.max(vmax,g.spectrum[i]-(background[i]??0));
 }
 drawMapRaster(m,{groups,delays,axis:mapAxis,low,high,scale:store.mapScale,vmax,background});
 axes(m,ra?'Wavelength / nm':'Pixel','Delay / ps',false);
 const pixelIndices=ra?ra.map((w,i)=>Math.abs(w-store.wave)<=store.halfWindow?i:-1).filter(i=>i>=0):[];
 const decay=[];for(const g of groups.values())if(pixelIndices.length){let mean=0;for(const i of pixelIndices)mean+=g.spectrum[i]-(background?.[i]??0);decay.push([mean/pixelIndices.length,g.delay]);}
 decay.sort((a,b)=>a[1]-b[1]);
 const xmin=Math.min(0,...decay.map(v=>v[0])),xmax=Math.max(1,...decay.map(v=>v[0]));
 const d=setup('decay',xmin,xmax*1.1,low,high,12,'--plot');if(!d)return;d.y=t=>d.p.t+(t-d.ymin)/(d.ymax-d.ymin)*(d.p.b-d.p.t);
 axes(d,'counts / pixel','',true);line(d,decay,css('--data'));dots(d,decay,css('--data'));
 if(!groups.size){empty(m,'No acquired data');empty(d,'No data');}
 $('colorLabel').textContent=store.mapScale==='log'?'1':'0';$('colorMax').textContent=format(vmax);
 if(ra&&groups.size)line(m,[[store.wave,low],[store.wave,high]],css('--cursor'),[4,4]);
}

function dots(q,data,color){const {ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.fillStyle=color;for(const [a,b]of data)if(Number.isFinite(a+b)){ctx.beginPath();ctx.arc(q.x(a),q.y(b),3,0,2*Math.PI);ctx.fill();}ctx.restore();}

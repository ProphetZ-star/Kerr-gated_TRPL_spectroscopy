import {api} from './api.js';
import {store} from './store.js';
import {plotRange,registerAxisEndpoints} from './plot-ranges.js';
import {bindMarkerDrag} from './plot-interaction.js';
import {bindOverlayDrag} from './draggable-overlay.js';
const $=id=>document.getElementById(id),css=k=>getComputedStyle(document.body).getPropertyValue(k).trim();
const W=1024;let H=255;const demoPixels=[115.23,282.65,449.38,655.14,851.72];
let fullRows=[85,165];
function setFrameHeight(height){
 if(H!==1&&height===1)fullRows=[$('calRowStart').value,$('calRowEnd').value];
 for(const [i,id]of ['calRowStart','calRowEnd'].entries()){if(height===1)$(id).value=0;else if(H===1)$(id).value=fullRows[i];$(id).max=height-1;}
 H=height;
}
const demoWave=p=>490+.24*p+.00002*p*p;
let frame=null,frameMeta=null,profile=null,detected=[],image=null,imageTheme='',frameNo=0,selected=0,scale='pixel',solution=null,spectrumPlot=null,ccdPlot=null,acquiring=false;
let pairs=demoPixels.map((p,i)=>({id:i+1,pixel:+(p+[.07,-.11,.09,-.05,.08][i]).toFixed(3),wave:+demoWave(p).toFixed(3),enabled:true}));
let nextId=6,references=pairs.map(p=>({wave:p.wave,intensity:1})),referenceName='Illustrative demo lines',source='synthetic',session=null,changed=async()=>{};
const controlsLocked=()=>acquiring||!store.online||!!store.snapshot?.busy;
const fmt=(x,d=3)=>Number.isFinite(x)?Number(x.toFixed(d)).toString():'—';
function feedback(message,error=false){$('calFeedback').textContent=message;$('calFeedback').classList.toggle('error',error);}
function invalidate(){solution=null;scale='pixel';$('calExport').disabled=true;$('calApply').disabled=true;$('calRms').textContent='—';$('calMaxError').textContent='—';$('calRange').textContent='—';$('calFitState').textContent='Draft';$('calFitState').dataset.state='neutral';document.querySelectorAll('#calAxis button').forEach(b=>b.classList.toggle('selected',b.dataset.axis==='pixel'));}
function roi(){if(frameMeta?.readout==='fvb')return [0,0];const a=Number($('calRowStart').value),b=Number($('calRowEnd').value);if($('calRowStart').value===''||$('calRowEnd').value===''||!Number.isInteger(a)||!Number.isInteger(b)||a<0||b>=H||b<a)throw Error('Rows must be integers from 0 to 254, with end ≥ start.');return[a,b];}
function extractionRequest(){if(!frameMeta)throw Error('Acquire a calibration frame first.');const[a,b]=roi();return{frame_id:frameMeta.frame_id,row_start:a,row_end:b};}
async function extract(){
 const result=await api('/calibration/extract',extractionRequest());
 if(result.frame_id!==frameMeta?.frame_id)throw Error('Calibration frame changed; acquire again.');
 profile=result.profile_counts;detected=result.peaks_pixels;
 $('calRoiLabel').textContent=result.saturated_pixels?'⚠ '+result.saturated_pixels+' saturated pixels':'';
 $('calRoiLabel').hidden=!result.saturated_pixels;
}
function peaks(){return detected;}
async function receiveFrame(result){
 if(!result)return;
 if(![1,255].includes(result.shape?.[0])||result.shape?.[1]!==W||result.image_counts?.length!==W*result.shape[0])throw Error('Unexpected CCD frame shape.');
 setFrameHeight(result.shape[0]);$('calDimensions').textContent=W+' × '+H+' px';$('calReadout').value=result.readout==='fvb'?'fvb':'full_image';frameMeta=result;frame=new Int32Array(result.image_counts);image=null;frameNo++;invalidate();await extract();
 $('calFrameState').textContent='Simulated frame '+frameNo;
}
function clearFrame(){frame=null;frameMeta=null;profile=null;detected=[];image=null;invalidate();$('calFrameState').textContent='No frame';$('calRoiLabel').hidden=true;$('calRoiLabel').textContent='';}
async function calAction(fn){
 if(controlsLocked())return;
 acquiring=true;renderControls();
 try{await fn();}
 catch(error){feedback(error.message,true);}
 finally{acquiring=false;renderPairs();renderCalibration();await changed();}
}
function syncSession(){
 const current=store.snapshot?.worker_session_id;
 if(!store.online||store.snapshot?.busy||!current||current===session)return;
 session=current;clearFrame();
 void calAction(async()=>{await receiveFrame(await api('/calibration/frame'));});
}
function renderControls(){
 const busy=!!store.snapshot?.busy,blocked=controlsLocked();
 if(frameMeta&&JSON.stringify(frameMeta.spectrograph)!==JSON.stringify(store.snapshot?.spectrograph)){clearFrame();feedback('Manual spectrograph settings changed. Acquire a new frame.');}
 $('calCapture').disabled=blocked||busy;$('calCapture').textContent=acquiring?'Working…':'Acquire frame';
 $('calFindPeaks').disabled=blocked||!profile;$('calFitMapping').disabled=blocked||!frameMeta;
 $('calApply').disabled=blocked||busy||!solution;$('calExport').disabled=blocked||!solution;
 for(const id of ['calReadout','calExposure','calRowStart','calRowEnd','calOrder','calConvention','calReferenceFile','calDemoReference','calAddLine','calOpenFile','calInstalledFile'])$(id).disabled=blocked;
 $('calRowStart').disabled=blocked||$('calReadout').value==='fvb';$('calRowEnd').disabled=blocked||$('calReadout').value==='fvb';$('calExtractionNote').textContent=$('calReadout').value==='fvb'?'Full vertical binning · All CCD rows are combined.':'Mean of selected rows · Click the CCD image to move the band.';
 for(const input of $('calPairs').querySelectorAll('input,button'))input.disabled=blocked;
 for(const button of document.querySelectorAll('#calAxis button'))button.disabled=blocked||(button.dataset.axis==='wavelength'&&!solution);
 if(solution){const applied=store.snapshot?.calibration?.calibration_id===solution.wire.calibration_id;$('calFitState').textContent=applied?'Applied':'Draft fitted';$('calFitState').dataset.state='success';}
}
function renderPairs(){const table=$('calPairs');table.replaceChildren();for(let i=0;i<pairs.length;i++){const p=pairs[i],tr=document.createElement('tr');tr.className=i===selected?'is-selected':'';const td=document.createElement('td'),check=document.createElement('input');check.type='checkbox';check.checked=p.enabled;check.setAttribute('aria-label','Use line '+(i+1));check.onchange=()=>{p.enabled=check.checked;invalidate();renderPairs();renderCalibration()};td.append(check);tr.append(td);
 for(const[key,label]of [['pixel','Peak pixel'],['wave','Reference wavelength']]){const cell=document.createElement('td'),input=document.createElement('input');input.type='number';input.step='any';input.value=Number.isFinite(p[key])?fmt(p[key],3):'';input.setAttribute('aria-label',label+' '+(i+1));input.onfocus=()=>{selected=i;table.querySelectorAll('tr').forEach((r,j)=>r.classList.toggle('is-selected',i===j));drawCalibration()};input.onchange=()=>{p[key]=input.value===''?NaN:Number(input.value);invalidate();renderPairs();renderCalibration()};cell.append(input);tr.append(cell);}
 const delta=document.createElement('td');delta.className='cal-delta';delta.textContent=solution&&p.enabled?fmt(p.wave-solution.at(p.pixel),4):'—';tr.append(delta);const last=document.createElement('td'),remove=document.createElement('button');remove.className='cal-remove';remove.textContent='×';remove.setAttribute('aria-label','Remove line '+(i+1));remove.onclick=()=>{pairs.splice(i,1);selected=Math.max(0,Math.min(selected,pairs.length-1));invalidate();renderPairs();renderCalibration()};last.append(remove);tr.append(last);table.append(tr);}
 $('calLineCount').textContent=pairs.filter(p=>p.enabled).length+' enabled lines';
}
async function fit(){
 const request={...extractionRequest(),degree:Number($('calOrder').value),convention:$('calConvention').value,reference_name:referenceName,reference_source:source,pairs:pairs.filter(p=>p.enabled).map(p=>({pixel:p.pixel,wavelength_nm:p.wave,enabled:true}))};
 if(request.pairs.some(p=>!Number.isFinite(p.pixel)||!Number.isFinite(p.wavelength_nm)))throw Error('Assign finite pixels and wavelengths to each line.');
 const wire=await api('/calibration/fit',request),at=p=>wire.coefficients.reduce((sum,c,i)=>sum+c*((p-wire.pixel_origin)/wire.pixel_scale)**i,0);
 solution={wire,at,axis:wire.wavelengths_nm,coefficients:wire.coefficients,degree:wire.degree,rms:wire.rms_nm,max:wire.max_error_nm,range:wire.pixel_range};
 $('calRms').textContent=fmt(solution.rms,4)+' nm';$('calMaxError').textContent=fmt(solution.max,4)+' nm';$('calRange').textContent=wire.wavelength_range_nm.map(v=>fmt(v,2)).join('–')+' nm';
 feedback('Draft fitted · Not applied. '+wire.warnings.join(' '));
}
export function initCalibration(onChanged=async()=>{}){
 changed=onChanged;bindOverlayDrag($('calMetrics'),$('calMappingPlot'));
 $('calReadout').onchange=()=>{if(controlsLocked())return;clearFrame();setFrameHeight($('calReadout').value==='fvb'?1:255);$('calDimensions').textContent=W+' × '+H+' px';renderCalibration();};
 $('calOpenFile').onclick=()=>{if(!controlsLocked())$('calInstalledFile').click();};
 $('calInstalledFile').onchange=e=>calAction(async()=>{
  const file=e.target.files?.[0];if(!file)return;
  try{if(file.size>2*1024**2)throw Error('Calibration JSON must be smaller than 2 MiB');const json=JSON.parse(await file.text()),value=json.calibration??json;
   if(!Array.isArray(value.wavelengths_nm)||value.wavelengths_nm.length!==W)throw Error('Calibration needs 1024 wavelengths');
   await api('/calibration/load',{calibration_id:value.calibration_id,source:value.source,wavelengths_nm:value.wavelengths_nm});clearFrame();feedback('Loaded '+file.name+' for future acquisitions. Existing experiments retain their recorded calibration.');
  }finally{e.target.value='';}
 });
 $('calCapture').onclick=()=>calAction(async()=>{const exposure=Number($('calExposure').value);if($('calExposure').value===''||!Number.isFinite(exposure)||exposure<.001||exposure>3600)throw Error('Exposure must be from 0.001 to 3600 s.');clearFrame();await receiveFrame(await api('/calibration/frames',{exposure_s:exposure,seed:42,readout:$('calReadout').value}));feedback('Simulated CCD frame received from backend.');});
 for(const id of ['calRowStart','calRowEnd'])$(id).onchange=()=>calAction(async()=>{invalidate();profile=null;detected=[];await extract();feedback('');});
 $('calFindPeaks').onclick=()=>calAction(async()=>{await extract();const found=peaks();if(!found.length)throw Error('No peaks found. Acquire a frame first.');pairs=pairs.map(p=>({...p,pixel:+found.reduce((best,x)=>Math.abs(x-p.pixel)<Math.abs(best-p.pixel)?x:best,found[0]).toFixed(3)}));invalidate();feedback(found.length+' peaks detected · Review line assignments.');});
 $('calAddLine').onclick=()=>{if(controlsLocked())return;pairs.push({id:nextId++,pixel:512,wave:600,enabled:true});selected=pairs.length-1;invalidate();renderPairs();$('calPairs').lastElementChild?.scrollIntoView({block:'nearest'});renderCalibration()};
 $('calFitMapping').onclick=()=>calAction(async()=>{invalidate();await fit();});
 for(const id of ['calOrder','calConvention'])$(id).onchange=()=>{invalidate();renderPairs();feedback('');renderCalibration()};
 document.querySelectorAll('#calAxis button').forEach(b=>b.onclick=()=>{if(controlsLocked())return;if(b.dataset.axis==='wavelength'&&!solution){feedback('Fit a mapping to preview the wavelength axis.',true);return}scale=b.dataset.axis;document.querySelectorAll('#calAxis button').forEach(el=>el.classList.toggle('selected',el===b));drawCalibration()});
 $('calDemoReference').onclick=()=>{if(controlsLocked())return;references=demoPixels.map(p=>({wave:+demoWave(p).toFixed(3),intensity:1}));pairs=demoPixels.map((p,i)=>({id:nextId++,pixel:+(p+[.07,-.11,.09,-.05,.08][i]).toFixed(3),wave:references[i].wave,enabled:true}));source='synthetic';referenceName='Illustrative demo lines';selected=0;invalidate();renderPairs();feedback('Illustrative lines loaded; not a lamp database.');renderCalibration()};
 $('calReferenceFile').onchange=e=>calAction(async()=>{const file=e.target.files[0];if(!file)return;try{const text=await file.text(),rows=text.split(/\r?\n/).map(l=>l.trim()).filter(l=>l&&!l.startsWith('#'));if(rows.length&&/^wavelength_nm\s*,/i.test(rows[0]))rows.shift();const ref=rows.map((line,i)=>{const fields=line.split(',');if(fields.length>2)throw Error('CSV needs wavelength_nm and optional relative_intensity.');const wave=Number(fields[0]),intensity=fields.length===2?Number(fields[1]):1;if(!fields[0]||!Number.isFinite(wave)||wave<=0||!Number.isFinite(intensity)||intensity<0)throw Error('Invalid reference CSV row '+(i+1));return{wave,intensity}});if(ref.length<2||ref.length>100)throw Error('Import 2–100 reference lines.');if(new Set(ref.map(r=>r.wave)).size!==ref.length)throw Error('Reference wavelengths must be unique.');references=ref.sort((a,b)=>a.wave-b.wave);referenceName=file.name;source='measured';const found=peaks();pairs=references.map((r,i)=>({id:nextId++,pixel:found.length===references.length?found[i]:NaN,wave:r.wave,enabled:true}));selected=0;invalidate();feedback('Reference imported · Confirm each pixel-to-line assignment.');}finally{e.target.value=''}});
 $('calApply').onclick=()=>calAction(async()=>{if(!solution)throw Error('Fit a mapping first.');await api('/calibration/apply',{solution_id:solution.wire.solution_id});feedback('Applied to future acquisitions. Existing experiments retain their recorded calibration.');});
 $('calExport').onclick=()=>calAction(async()=>{if(!solution)return;const draft=await api('/calibration/solutions/'+solution.wire.solution_id);const url=URL.createObjectURL(new Blob([JSON.stringify({format:'kerr-spectral-calibration-v1',...draft},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='spectral_calibration_'+draft.calibration_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
 $('calSpectrum').onclick=e=>{if(controlsLocked()||!profile||!spectrumPlot||!pairs.length)return;const q=spectrumPlot,px=e.clientX-$('calSpectrum').getBoundingClientRect().left;if(px<q.p.l||px>q.p.r)return;let pixel=q.xmin+(px-q.p.l)/(q.p.r-q.p.l)*(q.xmax-q.xmin);if(scale==='wavelength'&&solution){const wave=q.xmin+(px-q.p.l)/(q.p.r-q.p.l)*(q.xmax-q.xmin);pixel=solution.axis.reduce((best,v,i)=>Math.abs(v-wave)<Math.abs(solution.axis[best]-wave)?i:best,0);}const found=peaks().filter(p=>Math.abs(p-pixel)<12);if(found.length)pixel=found.reduce((best,p)=>Math.abs(p-pixel)<Math.abs(best-pixel)?p:best,found[0]);pairs[selected].pixel=+pixel.toFixed(3);invalidate();renderPairs();feedback('Peak assigned · Enter or confirm its reference wavelength.');renderCalibration()};
 $('calImage').onclick=e=>{if(controlsLocked()||!frameMeta||frameMeta.readout==='fvb'||!ccdPlot)return;return calAction(async()=>{const q=ccdPlot,py=e.clientY-$('calImage').getBoundingClientRect().top;if(py<q.p.t||py>q.p.b)return;const y=q.ymin+(q.ymax-q.ymin)*(q.p.b-py)/(q.p.b-q.p.t);if(y<0||y>254)return;const[a,b]=roi(),half=(b-a)/2,start=Math.max(0,Math.min(254-(b-a),Math.round(y-half)));$('calRowStart').value=start;$('calRowEnd').value=start+(b-a);invalidate();await extract();});};
 initCalibrationDrags();renderPairs();new ResizeObserver(drawCalibration).observe(document.querySelector('.cal-results'));renderCalibration();
}
function initCalibrationDrags(){
 const rowAt=event=>{const q=ccdPlot,py=event.clientY-$('calImage').getBoundingClientRect().top;return q.ymin+(q.ymax-q.ymin)*(q.p.b-py)/(q.p.b-q.p.t);};
 bindMarkerDrag($('calImage'),{
  start(event){if(controlsLocked()||!frameMeta||frameMeta.readout==='fvb'||!ccdPlot)return null;const q=ccdPlot,rect=$('calImage').getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top,[a,b]=roi(),ya=q.y(a),yb=q.y(b);if(px<q.p.l||px>q.p.r||py<q.p.t||py>q.p.b)return null;
   const mode=Math.abs(py-ya)<7?'start':Math.abs(py-yb)<7?'end':py>=Math.min(ya,yb)&&py<=Math.max(ya,yb)?'band':null;return mode?{a,b,row:rowAt(event),mode,label:'Rows '+a+'–'+b}:null;},
  move(event,state){if(controlsLocked())return null;const row=Math.max(0,Math.min(254,Math.round(rowAt(event))));let a=state.a,b=state.b;
   if(state.mode==='start')a=Math.min(b,row);else if(state.mode==='end')b=Math.max(a,row);else{a=Math.max(0,Math.min(254-(state.b-state.a),state.a+Math.round(rowAt(event)-state.row)));b=a+state.b-state.a;}
   $('calRowStart').value=a;$('calRowEnd').value=b;drawCalibration();return 'Row '+row+' · ROI '+a+'–'+b;},
  end(event,state,cancelled){if(cancelled){$('calRowStart').value=state.a;$('calRowEnd').value=state.b;drawCalibration();}else void calAction(async()=>{invalidate();await extract();});}
 });
 bindMarkerDrag($('calSpectrum'),{
  start(event){if(controlsLocked()||!profile||!spectrumPlot)return null;const q=spectrumPlot,rect=$('calSpectrum').getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top;if(px<q.p.l||px>q.p.r||py<q.p.t||py>q.p.b)return null;
   let best=-1,distance=8;for(let i=0;i<pairs.length;i++){const x=scale==='wavelength'&&solution?solution.at(pairs[i].pixel):pairs[i].pixel,d=Math.abs(px-q.x(x));if(d<distance){best=i;distance=d;}}
   if(best<0)return null;selected=best;return {index:best,pixel:pairs[best].pixel,label:'Pixel '+fmt(pairs[best].pixel,3)};},
  move(event,state){if(controlsLocked())return null;const q=spectrumPlot,px=event.clientX-$('calSpectrum').getBoundingClientRect().left,f=Math.max(0,Math.min(1,(px-q.p.l)/(q.p.r-q.p.l))),value=q.xmin+f*(q.xmax-q.xmin);let pixel=value;
   if(scale==='wavelength'&&solution){let nearest=solution.axis.reduce((best,w,i)=>Math.abs(w-value)<Math.abs(solution.axis[best]-value)?i:best,0),other=nearest<1023?nearest+1:nearest-1;pixel=nearest+(value-solution.axis[nearest])/(solution.axis[other]-solution.axis[nearest])*(other-nearest);}
   pixel=Math.max(0,Math.min(1023,pixel));pairs[state.index].pixel=+pixel.toFixed(3);renderPairs();drawCalibration();return 'Pixel '+fmt(pixel,3)+(scale==='wavelength'?' · '+fmt(value,3)+' nm':'');},
  end(event,state,cancelled){if(cancelled)pairs[state.index].pixel=state.pixel;else{invalidate();feedback('Peak assigned at pixel '+fmt(pairs[state.index].pixel,3));}renderPairs();renderCalibration();}
 });
}
function canvas(id,xmin,xmax,ymin,ymax,left=66,bottom=32,empty=false,metadata={}){
 ({xmin,xmax,ymin,ymax}=plotRange(id,{xmin,xmax,ymin,ymax},metadata));
 const c=$(id),r=c.getBoundingClientRect();if(r.width<1||r.height<1)return;
 const d=devicePixelRatio||1;c.width=Math.round(r.width*d);c.height=Math.round(r.height*d);
 const ctx=c.getContext('2d');ctx.scale(d,d);ctx.font='12px Segoe UI';
 const p={l:left,r:r.width-22,t:13,b:r.height-bottom};ctx.fillStyle=css('--plot');ctx.fillRect(p.l,p.t,p.r-p.l,p.b-p.t);
 return{id,ctx,p,w:r.width,h:r.height,xmin,xmax,ymin,ymax,x:v=>p.l+(v-xmin)/(xmax-xmin||1)*(p.r-p.l),y:v=>p.b-(v-ymin)/(ymax-ymin||1)*(p.b-p.t)};
}
function axes(q,label,small=false,showX=true,grid=true){
 const{ctx,p}=q;ctx.fillStyle=css('--muted');ctx.strokeStyle=css('--chart-grid');ctx.lineWidth=1;
 for(let i=0;i<=4;i++){const v=q.xmin+(q.xmax-q.xmin)*i/4;if(grid){ctx.beginPath();ctx.moveTo(q.x(v),p.t);ctx.lineTo(q.x(v),p.b);ctx.stroke();}ctx.textAlign='center';if(showX&&(q.w>400||i%2===0))ctx.fillText(fmt(v,1),q.x(v),p.b+18)}
 for(let i=0;i<=(small?2:3);i++){const v=q.ymin+(q.ymax-q.ymin)*i/(small?2:3);if(grid){ctx.beginPath();ctx.moveTo(p.l,q.y(v));ctx.lineTo(p.r,q.y(v));ctx.stroke();}ctx.textAlign='right';ctx.fillText(Math.abs(v)>=1000?(v/1000).toFixed(1)+'k':v!==0&&Math.abs(v)<.01?Number(v.toPrecision(2)).toString():fmt(v,2),p.l-7,q.y(v)+4)}
 ctx.save();ctx.translate(17,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText(label,0,0);ctx.restore();ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t);registerAxisEndpoints(q,{x:showX,y:true,format:v=>fmt(v,2)});

}
function noData(q,label){q.ctx.fillStyle=css('--muted');q.ctx.textAlign='center';q.ctx.fillText(label,(q.p.l+q.p.r)/2,(q.p.t+q.p.b)/2);}
function line(q,points,color,dash=[]){const{ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.lineWidth=1.7;ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(q.x(x),q.y(y)):ctx.moveTo(q.x(x),q.y(y)));ctx.stroke();ctx.restore()}
function dot(q,x,y,color){if(!Number.isFinite(x+y))return;const{ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.fillStyle=color;ctx.beginPath();ctx.arc(q.x(x),q.y(y),3,0,Math.PI*2);ctx.fill();ctx.restore()}
function ccd(){
 const q=ccdPlot=canvas('calImage',0,1023,H===1?.5:254,H===1?-.5:0,66,32,!frame,{yKey:H===1?'ccd:fvb-row':'ccd:row'});if(!q)return;
 if(!frame){axes(q,'CCD row',false,true,false);noData(q,'No frame');return;}
 const{ctx,p}=q,theme=document.body.classList.contains('light')?'light':'dark';
 if(!image||imageTheme!==theme){imageTheme=theme;const off=document.createElement('canvas');off.width=W;off.height=H;const c=off.getContext('2d'),data=c.createImageData(W,H);for(let i=0;i<frame.length;i++){const f=Math.min(1,Math.pow(Math.max(0,frame[i]-55)/3300,.55));data.data[i*4]=Math.round(16+f*174);data.data[i*4+1]=Math.round(27+f*208);data.data[i*4+2]=Math.round(47+f*202);data.data[i*4+3]=255;}c.putImageData(data,0,0);image=off;}
 ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();
 ctx.drawImage(image,q.x(0),q.y(H===1?-.5:0),q.x(1023)-q.x(0),q.y(H===1?.5:254)-q.y(H===1?-.5:0));
 const[a,b]=roi(),y0=q.y(a),y1=q.y(b);ctx.fillStyle=css('--blue')+'16';ctx.fillRect(p.l,y0,p.r-p.l,y1-y0);
 for(const row of (H===1?[]:[a,b]))line(q,[[0,row],[1023,row]],css('--cursor'),[5,4]);ctx.restore();
 axes(q,'CCD row',false,true,false);
}
function spectrum(){
 const wave=scale==='wavelength'&&solution,low=wave?Math.min(...solution.axis):0,high=wave?Math.max(...solution.axis):1023;
 $('calSpectrumAxis').textContent=wave?'nm':'pixel';
 const q=spectrumPlot=canvas('calSpectrum',low,high,0,profile?Math.max(1,...profile)*1.18:65535,66,32,!profile,{xKey:wave?'calSpectrum:nm':'calSpectrum:pixel',xLabel:wave?'Wavelength / nm':'CCD pixel'});
 if(!q)return;axes(q,'CCD / counts');if(!profile){noData(q,'No spectrum');return;}
 line(q,profile.map((y,x)=>[wave?solution.at(x):x,y]),css('--data'));
 for(const [i,p]of pairs.entries())if(Number.isFinite(p.pixel)&&p.pixel>=0&&p.pixel<=1023){const x=wave?solution.at(p.pixel):p.pixel;if(x<q.xmin||x>q.xmax)continue;line(q,[[x,0],[x,q.ymax*.92]],i===selected?css('--cursor'):css('--amber'),[3,4]);dot(q,x,profile[Math.round(p.pixel)],css('--data'));q.ctx.fillStyle=i===selected?css('--text'):css('--muted');q.ctx.textAlign='center';q.ctx.fillText('L'+(i+1),q.x(x),q.p.t+12);}
 if(wave){const max=Math.max(...references.map(r=>r.intensity),1);for(const r of references)if(r.wave>=low&&r.wave<=high)line(q,[[r.wave,0],[r.wave,q.ymax*.8*r.intensity/max]],css('--amber'),[5,4]);}
}
function mapping(){
 const active=frameMeta?pairs.filter(p=>p.enabled&&Number.isFinite(p.pixel+p.wave)):[],ys=active.map(p=>p.wave);
 const lo=ys.length?Math.min(...ys)-15:490,hi=ys.length?Math.max(...ys)+15:770,q=canvas('calMapping',0,1023,lo,hi,62,32,!frameMeta);
 if(!q)return;axes(q,'λ / nm',true);
 if(solution)line(q,Array.from({length:128},(_,i)=>[i*1023/127,solution.at(i*1023/127)]),css('--amber'));
 for(const p of active)dot(q,p.pixel,p.wave,css('--data'));
 if(!frameMeta)noData(q,'No mapping');
 const bound=solution?Math.max(.002,solution.max*1.4):.1,r=canvas('calResidual',0,1023,-bound,bound,62,8,!solution);
 if(!r)return;axes(r,'Δ nm',true,false);line(r,[[0,0],[1023,0]],css('--cursor'),[4,4]);
 if(solution)for(const p of active)dot(r,p.pixel,p.wave-solution.at(p.pixel),css('--data'));
}
export function renderCalibration(){if(!$('calibrationPage'))return;syncSession();renderControls();$('calReferenceName').textContent=referenceName;$('calReferenceName').title=referenceName;drawCalibration()}
export function drawCalibration(){if(!$('calibrationPage')||$('calibrationPage').hidden)return;try{ccd();spectrum();mapping()}catch(error){feedback(error.message,true)}}

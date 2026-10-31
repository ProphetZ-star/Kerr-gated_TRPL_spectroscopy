import {api} from './api.js';
import {store} from './store.js';
const $=id=>document.getElementById(id),css=k=>getComputedStyle(document.body).getPropertyValue(k).trim();
const W=1024,H=255,demoPixels=[115.23,282.65,449.38,655.14,851.72];
const demoWave=p=>490+.24*p+.00002*p*p;
let frame=null,frameMeta=null,profile=null,detected=[],image=null,imageTheme='',frameNo=0,selected=0,scale='pixel',solution=null,spectrumPlot=null,ccdPlot=null,acquiring=false;
let pairs=demoPixels.map((p,i)=>({id:i+1,pixel:+(p+[.07,-.11,.09,-.05,.08][i]).toFixed(3),wave:+demoWave(p).toFixed(3),enabled:true}));
let nextId=6,references=pairs.map(p=>({wave:p.wave,intensity:1})),referenceName='Illustrative demo lines',source='synthetic',session=null,changed=async()=>{};
const controlsLocked=()=>acquiring||!store.online||!!store.snapshot?.busy;
const fmt=(x,d=3)=>Number.isFinite(x)?Number(x.toFixed(d)).toString():'—';
function feedback(message,error=false){$('calFeedback').textContent=message;$('calFeedback').classList.toggle('error',error);}
function invalidate(){solution=null;scale='pixel';$('calExport').disabled=true;$('calApply').disabled=true;$('calRms').textContent='—';$('calMaxError').textContent='—';$('calRange').textContent='—';$('calFitState').textContent='Draft';$('calFitState').dataset.state='neutral';document.querySelectorAll('#calAxis button').forEach(b=>b.classList.toggle('selected',b.dataset.axis==='pixel'));}
function roi(){const a=Number($('calRowStart').value),b=Number($('calRowEnd').value);if($('calRowStart').value===''||$('calRowEnd').value===''||!Number.isInteger(a)||!Number.isInteger(b)||a<0||b>=H||b<a)throw Error('Rows must be integers from 0 to 254, with end ≥ start.');return[a,b];}
function extractionRequest(){if(!frameMeta)throw Error('Acquire a calibration frame first.');const[a,b]=roi();return{frame_id:frameMeta.frame_id,row_start:a,row_end:b};}
async function extract(){
 const result=await api('/calibration/extract',extractionRequest());
 if(result.frame_id!==frameMeta?.frame_id)throw Error('Calibration frame changed; acquire again.');
 profile=result.profile_counts;detected=result.peaks_pixels;
 $('calRoiLabel').textContent='Rows '+result.row_start+'–'+result.row_end+' · Mean extraction · '+result.saturated_pixels+' saturated pixels';
}
function peaks(){return detected;}
async function receiveFrame(result){
 if(!result)return;
 if(result.shape?.[0]!==H||result.shape?.[1]!==W||result.image_counts?.length!==W*H)throw Error('Unexpected CCD frame shape.');
 frameMeta=result;frame=new Int32Array(result.image_counts);image=null;frameNo++;invalidate();await extract();
 $('calFrameState').textContent='Simulated frame '+frameNo;
}
function clearFrame(){frame=null;frameMeta=null;profile=null;detected=[];image=null;invalidate();$('calFrameState').textContent='No frame';}
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
 for(const id of ['calExposure','calRowStart','calRowEnd','calOrder','calConvention','calReferenceFile','calDemoReference','calAddLine'])$(id).disabled=blocked;
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
 const request={...extractionRequest(),degree:Number($('calOrder').value),convention:$('calConvention').value,reference_name:referenceName,reference_source:source,pairs:pairs.map(p=>({pixel:p.pixel,wavelength_nm:p.wave,enabled:p.enabled}))};
 if(request.pairs.some(p=>!Number.isFinite(p.pixel)||!Number.isFinite(p.wavelength_nm)))throw Error('Assign finite pixels and wavelengths to each line.');
 const wire=await api('/calibration/fit',request),at=p=>wire.coefficients.reduce((sum,c,i)=>sum+c*((p-wire.pixel_origin)/wire.pixel_scale)**i,0);
 solution={wire,at,axis:wire.wavelengths_nm,coefficients:wire.coefficients,degree:wire.degree,rms:wire.rms_nm,max:wire.max_error_nm,range:wire.pixel_range};
 $('calRms').textContent=fmt(solution.rms,4)+' nm';$('calMaxError').textContent=fmt(solution.max,4)+' nm';$('calRange').textContent=wire.wavelength_range_nm.map(v=>fmt(v,2)).join('–')+' nm';
 feedback('Draft fitted · Not applied. '+wire.warnings.join(' '));
}
export function initCalibration(onChanged=async()=>{}){
 changed=onChanged;
 $('calCapture').onclick=()=>calAction(async()=>{const exposure=Number($('calExposure').value);if($('calExposure').value===''||!Number.isFinite(exposure)||exposure<.001||exposure>3600)throw Error('Exposure must be from 0.001 to 3600 s.');clearFrame();await receiveFrame(await api('/calibration/frames',{exposure_s:exposure,seed:42}));feedback('Simulated CCD frame received from backend.');});
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
 $('calSpectrum').onclick=e=>{if(controlsLocked()||!spectrumPlot||!pairs.length)return;const q=spectrumPlot,px=e.clientX-$('calSpectrum').getBoundingClientRect().left;if(px<q.p.l||px>q.p.r)return;let pixel=(px-q.p.l)/(q.p.r-q.p.l)*1023;if(scale==='wavelength'&&solution){const wave=q.xmin+(px-q.p.l)/(q.p.r-q.p.l)*(q.xmax-q.xmin);pixel=solution.axis.reduce((best,v,i)=>Math.abs(v-wave)<Math.abs(solution.axis[best]-wave)?i:best,0);}const found=peaks().filter(p=>Math.abs(p-pixel)<12);if(found.length)pixel=found.reduce((best,p)=>Math.abs(p-pixel)<Math.abs(best-pixel)?p:best,found[0]);pairs[selected].pixel=+pixel.toFixed(3);invalidate();renderPairs();feedback('Peak assigned · Enter or confirm its reference wavelength.');renderCalibration()};
 $('calImage').onclick=e=>{if(controlsLocked()||!ccdPlot)return;return calAction(async()=>{const q=ccdPlot,y=(e.clientY-$('calImage').getBoundingClientRect().top-q.p.t)/(q.p.b-q.p.t)*254;if(y<0||y>254)return;const[a,b]=roi(),half=(b-a)/2,start=Math.max(0,Math.min(254-(b-a),Math.round(y-half)));$('calRowStart').value=start;$('calRowEnd').value=start+(b-a);invalidate();await extract();});};
 renderPairs();new ResizeObserver(drawCalibration).observe(document.querySelector('.cal-results'));renderCalibration();
}
function canvas(id,xmin,xmax,ymin,ymax,left=66){const c=$(id),r=c.getBoundingClientRect();if(r.width<1||r.height<1)return;const d=devicePixelRatio||1;c.width=Math.round(r.width*d);c.height=Math.round(r.height*d);const ctx=c.getContext('2d');ctx.scale(d,d);ctx.font='12px Segoe UI';const p={l:left,r:r.width-22,t:13,b:r.height-32};ctx.fillStyle=css('--plot');ctx.fillRect(p.l,p.t,p.r-p.l,p.b-p.t);return{ctx,p,w:r.width,h:r.height,xmin,xmax,ymin,ymax,x:v=>p.l+(v-xmin)/(xmax-xmin||1)*(p.r-p.l),y:v=>p.b-(v-ymin)/(ymax-ymin||1)*(p.b-p.t)};}
function axes(q,label,small=false){const{ctx,p}=q;ctx.fillStyle=css('--muted');ctx.strokeStyle=css('--chart-grid');ctx.lineWidth=1;for(let i=0;i<=4;i++){const v=q.xmin+(q.xmax-q.xmin)*i/4;ctx.beginPath();ctx.moveTo(q.x(v),p.t);ctx.lineTo(q.x(v),p.b);ctx.stroke();ctx.textAlign='center';if(q.w>400||i%2===0)ctx.fillText(fmt(v,1),q.x(v),p.b+18)}for(let i=0;i<=(small?2:3);i++){const v=q.ymin+(q.ymax-q.ymin)*i/(small?2:3);ctx.beginPath();ctx.moveTo(p.l,q.y(v));ctx.lineTo(p.r,q.y(v));ctx.stroke();ctx.textAlign='right';ctx.fillText(Math.abs(v)>=1000?(v/1000).toFixed(1)+'k':v!==0&&Math.abs(v)<.01?Number(v.toPrecision(2)).toString():fmt(v,2),p.l-7,q.y(v)+4)}ctx.save();ctx.translate(17,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText(label,0,0);ctx.restore();ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t)}
function line(q,points,color,dash=[]){const{ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.lineWidth=1.7;ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(q.x(x),q.y(y)):ctx.moveTo(q.x(x),q.y(y)));ctx.stroke();ctx.restore()}
function dot(q,x,y,color){if(!Number.isFinite(x+y))return;q.ctx.fillStyle=color;q.ctx.beginPath();q.ctx.arc(q.x(x),q.y(y),3,0,Math.PI*2);q.ctx.fill()}
function ccd(){const q=ccdPlot=canvas('calImage',0,1023,254,0);if(!q||!frame)return;const{ctx,p}=q;const theme=document.body.classList.contains('light')?'light':'dark';if(!image||imageTheme!==theme){imageTheme=theme;const off=document.createElement('canvas');off.width=W;off.height=H;const c=off.getContext('2d'),data=c.createImageData(W,H);for(let i=0;i<frame.length;i++){const v=Math.pow(Math.max(0,frame[i]-55)/3300,.55);const f=Math.min(1,v);data.data[i*4]=Math.round(16+f*174);data.data[i*4+1]=Math.round(27+f*208);data.data[i*4+2]=Math.round(47+f*202);data.data[i*4+3]=255;}c.putImageData(data,0,0);image=off;}ctx.drawImage(image,p.l,p.t,p.r-p.l,p.b-p.t);const[a,b]=roi(),y0=q.y(a),y1=q.y(b);ctx.fillStyle=css('--blue')+'16';ctx.fillRect(p.l,y0,p.r-p.l,y1-y0);for(const y of [y0,y1])line(q,[[0,q.ymin+(p.b-y)/(p.b-p.t)*(q.ymax-q.ymin)],[1023,q.ymin+(p.b-y)/(p.b-p.t)*(q.ymax-q.ymin)]],css('--cursor'),[5,4]);ctx.fillStyle=css('--muted');ctx.textAlign='right';for(const y of [0,128,254])ctx.fillText(y,p.l-8,q.y(y)+4);ctx.textAlign='center';for(const x of [0,256,512,768,1023])if(q.w>400||x===0||x===512||x===1023)ctx.fillText(x,q.x(x),p.b+18);ctx.save();ctx.translate(17,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.fillText('CCD row',0,0);ctx.restore();ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t)}
function spectrum(){const wave=scale==='wavelength'&&solution,low=wave?Math.min(...solution.axis):0,high=wave?Math.max(...solution.axis):1023;const q=spectrumPlot=canvas('calSpectrum',low,high,0,Math.max(...profile)*1.18);if(!q)return;axes(q,'CCD / counts');line(q,profile.map((y,x)=>[wave?solution.at(x):x,y]),css('--data'));for(const [i,p]of pairs.entries())if(Number.isFinite(p.pixel)&&p.pixel>=0&&p.pixel<=1023){const x=wave?solution.at(p.pixel):p.pixel;line(q,[[x,0],[x,q.ymax*.92]],i===selected?css('--cursor'):css('--amber'),[3,4]);dot(q,x,profile[Math.round(p.pixel)],css('--data'));q.ctx.fillStyle=i===selected?css('--text'):css('--muted');q.ctx.textAlign='center';q.ctx.fillText('L'+(i+1),q.x(x),q.p.t+12);}if(wave){const max=Math.max(...references.map(r=>r.intensity),1);for(const r of references)if(r.wave>=low&&r.wave<=high)line(q,[[r.wave,0],[r.wave,q.ymax*.8*r.intensity/max]],css('--amber'),[5,4]);}$('calSpectrumAxis').textContent=wave?'Wavelength / nm':'Pixel';}
function mapping(){const active=pairs.filter(p=>p.enabled&&Number.isFinite(p.pixel+p.wave)),ys=active.map(p=>p.wave),lo=ys.length?Math.min(...ys)-15:490,hi=ys.length?Math.max(...ys)+15:770,q=canvas('calMapping',0,1023,lo,hi,62);if(!q)return;axes(q,'λ / nm',true);if(solution)line(q,Array.from({length:128},(_,i)=>[i*1023/127,solution.at(i*1023/127)]),css('--amber'));for(const p of active)dot(q,p.pixel,p.wave,css('--data'));const bound=solution?Math.max(.002,solution.max*1.4):.1,r=canvas('calResidual',0,1023,-bound,bound,62);if(!r)return;axes(r,'Δ nm',true);line(r,[[0,0],[1023,0]],css('--cursor'),[4,4]);if(solution)for(const p of active)dot(r,p.pixel,p.wave-solution.at(p.pixel),css('--data'));}
export function renderCalibration(){if(!$('calibrationPage'))return;syncSession();renderControls();$('calReferenceName').textContent=referenceName;$('calReferenceName').title=referenceName;drawCalibration()}
export function drawCalibration(){if(!$('calibrationPage')||$('calibrationPage').hidden||!profile)return;try{ccd();spectrum();mapping()}catch(error){feedback(error.message,true)}}

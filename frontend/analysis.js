import {store,axisForRun} from './store.js';
const $=id=>document.getElementById(id),css=k=>getComputedStyle(document.body).getPropertyValue(k).trim();
let fitBusy=false,fitError='',lastSelection='',lastResult=undefined,lastRun=null,plotScale='linear';
const fmt=(v,d=4)=>Number.isFinite(v)?(Math.abs(v)>=1e5||Math.abs(v)<.001&&v!==0?v.toExponential(2):Number(v.toPrecision(d)).toString()):'—';
export function fitOptions(){
 for(const input of $('analysisSelection').querySelectorAll('input'))if(!input.checkValidity())throw Error('Check '+input.getAttribute('aria-label')+'.');
 const number=(id,optional=false)=>{if(optional&&$(id).value==='')return null;const v=Number($(id).value);if($(id).value===''||!Number.isFinite(v))throw Error('Enter a finite value for '+$(id).getAttribute('aria-label'));return v;};
 const options={wavelength_nm:store.wave,half_window_nm:store.halfWindow,model:$('fitModel').value,irf_fwhm_ps:number('irfWidth'),min_delay_ps:number('fitStart',true),max_delay_ps:number('fitEnd',true)};
 if(options.irf_fwhm_ps<=0||options.half_window_nm<=0)throw Error('IRF width and wavelength half-window must be positive.');
 if(options.min_delay_ps!==null&&options.max_delay_ps!==null&&options.max_delay_ps<=options.min_delay_ps)throw Error('Fit end must be greater than fit start.');
 return options;
}
export function analysisFitState(busy,error=''){fitBusy=busy;fitError=error;renderAnalysis();}
function clearResult(){store.fit=null;lastResult=undefined;$('fit').checked=false;$('tauValues').hidden=true;fitError='';}
export function initAnalysis(redraw){
 for(const [id,key,shared]of [['analysisWave','wave','waveSelect'],['analysisWindow','halfWindow','windowSelect']])$(id).onchange=()=>{const value=Number($(id).value);if($(id).value===''||!Number.isFinite(value)||(key==='halfWindow'&&value<=0)){ $(id).setCustomValidity('Enter a valid positive window or finite wavelength.');$(id).reportValidity();return;}$(id).setCustomValidity('');store[key]=value;$(shared).value=value;clearResult();redraw();renderAnalysis();};
 for(const id of ['fitModel','irfWidth','fitStart','fitEnd'])$(id).onchange=()=>{clearResult();redraw();renderAnalysis();};
 document.querySelectorAll('#analysisScale button').forEach(b=>b.onclick=()=>{plotScale=b.dataset.scale;document.querySelectorAll('#analysisScale button').forEach(el=>el.classList.toggle('selected',el===b));drawAnalysis();});
 $('exportFit').onclick=()=>{const f=store.fit;if(!f)return;const rows=['# Kerr Studio fit; raw counts / exposure; user-specified Gaussian IRF','delay_ps,observed_counts_per_s,predicted_counts_per_s,residual_counts_per_s',...f.delays_ps.map((t,i)=>[t,f.observed[i],f.predicted[i],f.residuals[i]].join(','))];const url=URL.createObjectURL(new Blob([rows.join('\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='kerr_fit_'+(store.run?.run_id??'result')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 new ResizeObserver(drawAnalysis).observe(document.querySelector('.analysis-results'));
}
function observed(){const groups=new Map(),axis=axisForRun();if(!axis)return [];const pixels=axis.map((v,i)=>Math.abs(v-store.wave)<=store.halfWindow?i:-1).filter(i=>i>=0);if(!pixels.length)return [];
 for(const r of store.records){if(!r.exposure_s)continue;const value=pixels.reduce((s,i)=>s+r.spectrum[i],0)/pixels.length/r.exposure_s;let g=groups.get(r.delay_ps);if(!g)groups.set(r.delay_ps,g={sum:0,n:0});g.sum+=value;g.n++;}
 return [...groups].map(([t,g])=>[t,g.sum/g.n]).sort((a,b)=>a[0]-b[0]);
}
function selectedFit(){const f=store.fit;return f?.observed?.length?f:null;}
export function renderAnalysis(){
 if(!$('analysisPage'))return;
 const run=store.run,f=selectedFit(),axis=axisForRun(),selection=store.wave+'|'+store.halfWindow;
 if(selection!==lastSelection){$('analysisWave').value=store.wave;$('analysisWindow').value=store.halfWindow;lastSelection=selection;}
 if(run?.run_id!==lastRun){lastRun=run?.run_id;fitError='';}
 $('analysisSource').textContent=run?(run.path?run.path.split(/[\\/]/).at(-1):'Current experiment'):'No experiment loaded';$('analysisSource').title=run?.path??run?.run_id??'';
 $('analysisFrames').textContent=store.records.length+' frames';$('analysisPoints').textContent=new Set(store.records.map(r=>r.delay_ps)).size+' delays';
 $('analysisWindowLabel').textContent=fmt(store.wave)+' ± '+fmt(store.halfWindow)+' nm';
 $('analysisModelLabel').textContent=f?(f.model==='double'?'Double exponential':'Single exponential')+' · Gaussian IRF':'All frames & rounds · Raw counts / exposure';
 $('analysisSelection').disabled=fitBusy;const blocked=fitBusy||!!store.snapshot?.busy||!store.online||!store.records.length||!axis;
 $('runFit').disabled=blocked;$('runFit').textContent=fitBusy?'Fitting…':'▶ Run fit';$('openRun').disabled=fitBusy||!!store.snapshot?.busy||!store.online;$('exportFit').disabled=!f||fitBusy;
 let text=fitBusy?'Fitting':fitError?'Fit failed':f?(f.warnings?.length?'Review warnings':'Converged'):!run?'No data':!axis?'Calibration required':'Ready to fit';
 $('analysisFitState').textContent=text;$('analysisFitState').dataset.state=fitError?'error':f?(f.warnings?.length?'warning':'success'):'neutral';
 $('fitStatus').textContent=fitError|| (fitBusy?'Optimizing the selected observed curve…':!run?'Open an experiment or finish an acquisition.':!axis?'This experiment has no recorded wavelength calibration. Applying a new calibration affects future acquisitions only.':store.snapshot?.busy?'Finish acquisition before fitting.':'');
 $('fitSampleCount').textContent=f?f.delays_ps.length+' fitted delays':'No fit result';
 $('fitRmse').textContent=f?fmt(Math.sqrt(f.residuals.reduce((s,v)=>s+v*v,0)/f.residuals.length)):'—';
 $('fitCondition').textContent=f?fmt(f.jacobian_condition,3):'—';
 $('fitWarning').textContent=f?.warnings?.join(' · ')??'';$('fitWarning').hidden=!f?.warnings?.length;
 $('fitIrFLabel').textContent=f?'Gaussian IRF · '+fmt(f.irf_fwhm_ps)+' ps FWHM':'User-specified Gaussian IRF';
 if(f!==lastResult){lastResult=f;const tbody=$('fitParameters');tbody.replaceChildren();if(!f){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.className='table-empty';td.textContent='Run a fit to inspect lifetimes and parameter uncertainty.';tr.append(td);tbody.append(tr);}else{
  const names={tau_ps:['τ','ps'],tau_1_ps:['τ₁','ps'],tau_2_ps:['τ₂','ps'],amplitude:['Amplitude','counts/s'],amplitude_1:['Amplitude 1','counts/s'],amplitude_2:['Amplitude 2','counts/s'],t0_ps:['t₀','ps'],offset_counts_s:['Offset','counts/s']};
  for(const key of ['tau_ps','tau_1_ps','tau_2_ps','amplitude','amplitude_1','amplitude_2','t0_ps','offset_counts_s'])if(key in f.parameters){const tr=document.createElement('tr');if(key.startsWith('tau'))tr.className='lifetime';for(const value of [names[key][0],fmt(f.parameters[key]),fmt(f.standard_errors?.[key]),names[key][1]]){const td=document.createElement('td');td.textContent=value;tr.append(td);}tbody.append(tr);}
 }}
 drawAnalysis();
}
function canvas(id,xmin,xmax,ymin,ymax){const c=$(id),rect=c.getBoundingClientRect();if(rect.width<1||rect.height<1)return;const d=devicePixelRatio||1;c.width=Math.round(rect.width*d);c.height=Math.round(rect.height*d);const ctx=c.getContext('2d');ctx.scale(d,d);ctx.font='12px Segoe UI';const p={l:78,r:rect.width-23,t:15,b:rect.height-35};ctx.fillStyle=css('--plot');ctx.fillRect(p.l,p.t,p.r-p.l,p.b-p.t);return{ctx,p,w:rect.width,h:rect.height,xmin,xmax,ymin,ymax,x:v=>p.l+(v-xmin)/(xmax-xmin||1)*(p.r-p.l),y:v=>p.b-(v-ymin)/(ymax-ymin||1)*(p.b-p.t)};}
function axes(q,label,residual=false){const{ctx,p}=q;ctx.fillStyle=css('--muted');ctx.strokeStyle=css('--chart-grid');ctx.lineWidth=1;for(let i=0;i<=4;i++){const xv=q.xmin+(q.xmax-q.xmin)*i/4,x=q.x(xv);ctx.beginPath();ctx.moveTo(x,p.t);ctx.lineTo(x,p.b);ctx.stroke();ctx.textAlign='center';ctx.fillText(fmt(xv,3),x,p.b+18);}for(let i=0;i<=(residual?2:4);i++){const v=q.ymin+(q.ymax-q.ymin)*i/(residual?2:4),y=q.y(v);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(p.r,y);ctx.stroke();ctx.textAlign='right';ctx.fillText(fmt(v,3),p.l-9,y+4);}ctx.save();ctx.translate(20,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText(label,0,0);ctx.restore();ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t);}
function curve(q,points,color,dash=[],dots=false){const{ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=1.7;ctx.setLineDash(dash);ctx.beginPath();let start=false;for(const[t,v]of points){if(!Number.isFinite(v)){start=false;continue;}if(start)ctx.lineTo(q.x(t),q.y(v));else ctx.moveTo(q.x(t),q.y(v));start=true;}ctx.stroke();if(dots)for(const[t,v]of points)if(Number.isFinite(v)){ctx.beginPath();ctx.arc(q.x(t),q.y(v),2.3,0,Math.PI*2);ctx.fill();}ctx.restore();}
function empty(q,label){q.ctx.fillStyle=css('--muted');q.ctx.textAlign='center';q.ctx.fillText(label,(q.p.l+q.p.r)/2,(q.p.t+q.p.b)/2);}
export function drawAnalysis(){if(!$('analysisPage')||$('analysisPage').hidden)return;const f=selectedFit();const data=f?f.delays_ps.map((t,i)=>[t,f.observed[i]]):observed();let lo=data.length?data[0][0]:-2,hi=data.length?data.at(-1)[0]:20;if(lo===hi){lo-=1;hi+=1;}
 const log=plotScale==='log',transform=v=>log?(v>0?Math.log10(v):NaN):v;const vals=data.map(d=>transform(d[1])).concat(f?f.predicted.map(transform):[]).filter(Number.isFinite);let min=log?(vals.length?Math.floor(Math.min(...vals)):0):Math.min(0,...vals),max=vals.length?Math.max(...vals):1;if(max===min)max=min+1;max+=(max-min)*.1;
 const q=canvas('analysisCurve',lo,hi,min,max),res=f?.residuals??[],abs=Math.max(1,...res.map(Math.abs))*1.15,r=canvas('analysisResidual',lo,hi,-abs,abs);if(!q||!r)return;
 axes(q,log?'log₁₀ counts / s':'counts / s');axes(r,'Residual',true);curve(r,[[lo,0],[hi,0]],css('--cursor'),[4,4]);
 if(data.length)curve(q,data.map(([t,y])=>[t,transform(y)]),css('--data'),[],true);else empty(q,'Open an experiment to view its decay');
 if(f){curve(q,f.delays_ps.map((t,i)=>[t,transform(f.predicted[i])]),css('--amber'),[6,4]);curve(r,f.delays_ps.map((t,i)=>[t,f.residuals[i]]),css('--data'),[],true);}else empty(r,'Residuals appear after fitting');
}

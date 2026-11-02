import {store,analysisContext} from './store.js';
const axisForRun=()=>analysisContext().run?.metadata?.calibration?.wavelengths_nm??null;
import {plotRange,registerAxisEndpoints} from './plot-ranges.js';
import {spectraFor,delayFraction} from './analysis-spectra.js';
import {mapColor,MAP_GRADIENT} from './map-palette.js';
const $=id=>document.getElementById(id),css=k=>getComputedStyle(document.body).getPropertyValue(k).trim();
let fitBusy=false,fitError='',lastSelection='',lastResult=undefined,lastRun=null,plotScale='linear',analysisPlot=null,selectedPoint=null;
let view='decay',lastSpectrumUI=null,lastSpectrumLegend=null;
const viewScales={decay:'linear',spectrum:'linear'},spectrumStates=new WeakMap();
function spectrumState(){
 const context=analysisContext(),id=context.run?.run_id;let state=spectrumStates.get(context);
 if(!state||state.id!==id){state={id,selected:new Set(),initialized:false,pick:null};spectrumStates.set(context,state);}
 return state;
}
const fmt=(v,d=4)=>Number.isFinite(v)?(Math.abs(v)>=1e5||Math.abs(v)<.001&&v!==0?v.toExponential(2):Number(v.toPrecision(d)).toString()):'—';
export function fitOptions(){
 for(const input of $('analysisSelection').querySelectorAll('input'))if(!input.checkValidity())throw Error('Check '+input.getAttribute('aria-label')+'.');
 const number=(id,optional=false)=>{if(optional&&$(id).value==='')return null;const v=Number($(id).value);if($(id).value===''||!Number.isFinite(v))throw Error('Enter a finite value for '+$(id).getAttribute('aria-label'));return v;};
 const options={wavelength_nm:analysisContext().wave,half_window_nm:analysisContext().halfWindow,model:$('fitModel').value,irf_fwhm_ps:number('irfWidth'),min_delay_ps:number('fitStart',true),max_delay_ps:number('fitEnd',true)};
 if(options.irf_fwhm_ps<=0||options.half_window_nm<=0)throw Error('IRF width and wavelength half-window must be positive.');
 if(options.min_delay_ps!==null&&options.max_delay_ps!==null&&options.max_delay_ps<=options.min_delay_ps)throw Error('Fit end must be greater than fit start.');
 return options;
}
export function analysisFitState(busy,error=''){fitBusy=busy;fitError=error;renderAnalysis();}
function clearResult(){analysisContext().fit=null;lastResult=undefined;fitError='';}
export function initAnalysis(redraw){
 $('analysisView').value=view;
 $('analysisView').onchange=()=>{view=$('analysisView').value==='spectrum'?'spectrum':'decay';plotScale=viewScales[view];analysisPlot=null;$('analysisPoint').hidden=true;renderAnalysis();};
 $('analysisDelay').onchange=()=>{spectrumState().pick=Number($('analysisDelay').value);renderAnalysis();};
 $('analysisAddDelay').onclick=()=>{const value=Number($('analysisDelay').value);if($('analysisDelay').value!==''&&spectraFor(analysisContext()).has(value)){spectrumState().selected.add(value);spectrumState().initialized=true;renderAnalysis();}};
 $('analysisClearDelays').onclick=()=>{const state=spectrumState();state.selected.clear();state.initialized=true;renderAnalysis();};
 $('analysisCurve').onclick=event=>{
  if(!analysisPlot)return;const {q,data,transform,key}=analysisPlot,rect=$('analysisCurve').getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  if(x<q.p.l||x>q.p.r||y<q.p.t||y>q.p.b)return;
  let best=null,distance=12;
  for(const [t,value]of data){const yy=transform(value);if(!Number.isFinite(yy))continue;const px=q.x(t),py=q.y(yy);if(px<q.p.l||px>q.p.r||py<q.p.t||py>q.p.b)continue;const d=Math.hypot(x-px,y-py);if(d<distance){distance=d;best={t,value,key};}}
  selectedPoint=best;drawAnalysis();
 };
 for(const [id,key]of [['analysisWave','wave'],['analysisWindow','halfWindow']])$(id).onchange=()=>{const value=Number($(id).value);if($(id).value===''||!Number.isFinite(value)||(key==='halfWindow'&&value<=0)){ $(id).setCustomValidity('Enter a valid positive window or finite wavelength.');$(id).reportValidity();return;}$(id).setCustomValidity('');analysisContext()[key]=value;clearResult();redraw();renderAnalysis();};
 for(const id of ['fitModel','irfWidth','fitStart','fitEnd'])$(id).onchange=()=>{clearResult();redraw();renderAnalysis();};
 document.querySelectorAll('#analysisScale button').forEach(b=>b.onclick=()=>{plotScale=b.dataset.scale;viewScales[view]=plotScale;document.querySelectorAll('#analysisScale button').forEach(el=>el.classList.toggle('selected',el===b));drawAnalysis();});
 $('exportFit').onclick=()=>{const f=analysisContext().fit;if(!f)return;const rows=['# Kerr Studio fit; raw counts / exposure; user-specified Gaussian IRF','delay_ps,observed_counts_per_s,predicted_counts_per_s,residual_counts_per_s',...f.delays_ps.map((t,i)=>[t,f.observed[i],f.predicted[i],f.residuals[i]].join(','))];const url=URL.createObjectURL(new Blob([rows.join('\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='kerr_fit_'+(analysisContext().run?.run_id??'result')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 new ResizeObserver(drawAnalysis).observe(document.querySelector('.analysis-results'));
}
let observedCache=null;
function observed(){
 const context=analysisContext(),groups=spectraFor(context),axis=axisForRun();if(!axis)return [];
 if(observedCache&&observedCache.groups===groups&&observedCache.revision===groups.revision&&observedCache.wave===context.wave&&observedCache.window===context.halfWindow)return observedCache.data;
 const pixels=axis.map((v,i)=>Math.abs(v-context.wave)<=context.halfWindow?i:-1).filter(i=>i>=0);
 const data=pixels.length?[...groups].map(([delay,g])=>[delay,pixels.reduce((sum,i)=>sum+g.values[i],0)/pixels.length]):[];
 observedCache={groups,revision:groups.revision,wave:context.wave,window:context.halfWindow,data};return data;
}
function selectedFit(){const f=analysisContext().fit;return f?.observed?.length?f:null;}
function renderView(){
 const spectrum=view==='spectrum';
 $('analysisFitControls').hidden=spectrum;$('analysisSpectrumControls').hidden=!spectrum;
 $('analysisResidualWrap').hidden=spectrum;$('analysisBottom').hidden=spectrum;
 $('analysisDecayLegend').hidden=spectrum;$('analysisTimeKey').hidden=!spectrum;$('analysisSpectrumLegend').hidden=!spectrum;
 $('analysisResults').classList.toggle('is-spectrum',spectrum);$('analysisPlotPanel').classList.toggle('is-spectrum',spectrum);
 $('analysisPlotTitle').textContent=spectrum?'Spectrum':'Decay & fit';$('analysisWindowLabel').hidden=spectrum;
 $('analysisCurve').setAttribute('aria-label',spectrum?'Overlaid wavelength spectra at selected delays':'Observed decay and fitted model versus delay');
 document.querySelectorAll('#analysisScale button').forEach(b=>b.classList.toggle('selected',b.dataset.scale===plotScale));
 if(!spectrum)return;
 const groups=spectraFor(analysisContext()),delays=[...groups.keys()],state=spectrumState();
 if(!state.initialized&&delays.length){state.pick=delays.reduce((best,t)=>Math.abs(t)<Math.abs(best)?t:best,delays[0]);state.selected.add(state.pick);state.initialized=true;}
 for(const delay of state.selected)if(!groups.has(delay))state.selected.delete(delay);
 if(!groups.has(state.pick))state.pick=delays[0]??null;
 const signature=state.id+'|'+delays.join(',');
 if(signature!==lastSpectrumUI){$('analysisDelay').replaceChildren();for(const delay of delays){const option=document.createElement('option');option.value=String(delay);option.textContent=fmt(delay,7)+' ps';$('analysisDelay').append(option);}lastSpectrumUI=signature;}
 $('analysisDelay').value=state.pick===null?'':String(state.pick);$('analysisDelay').disabled=!delays.length;
 $('analysisAddDelay').disabled=state.pick===null||state.selected.has(state.pick);$('analysisClearDelays').disabled=!state.selected.size;
 $('analysisSpectrumStatus').textContent=delays.length?state.selected.size+' overlays · '+delays.length+' acquired delays'+(axisForRun()?'':' · No wavelength calibration; pixel axis'):'No acquired spectra';
 $('analysisFitState').textContent=delays.length?'Viewing spectra':'No data';$('analysisFitState').dataset.state='neutral';
 const lo=delays[0]??0,hi=delays.at(-1)??0;
 $('analysisTimeMin').textContent=fmt(lo,7);$('analysisTimeMax').textContent=fmt(hi,7);$('analysisTimeGradient').style.background=MAP_GRADIENT;
 const legendKey=signature+'|'+[...state.selected].sort((a,b)=>a-b).map(t=>t+':'+groups.get(t).count).join(',');
 if(legendKey===lastSpectrumLegend)return;lastSpectrumLegend=legendKey;
 const legend=$('analysisSpectrumLegend');legend.replaceChildren();
 for(const delay of [...state.selected].sort((a,b)=>a-b)){
  const button=document.createElement('button'),swatch=document.createElement('i'),label=document.createElement('span');
  button.className='spectrum-delay-key';button.title='Remove spectrum at '+delay+' ps';button.setAttribute('aria-label',button.title);
  swatch.style.background=mapColor(delayFraction(delay,lo,hi));label.textContent=fmt(delay,7)+' ps · '+groups.get(delay).count+' frames ×';
  button.append(swatch,label);button.onclick=()=>{state.selected.delete(delay);renderAnalysis();};legend.append(button);
 }
}
export function renderAnalysis(){
 if(!$('analysisPage'))return;
 const run=analysisContext().run,f=selectedFit(),axis=axisForRun(),selection=analysisContext().wave+'|'+analysisContext().halfWindow;
 if(selection!==lastSelection){$('analysisWave').value=analysisContext().wave;$('analysisWindow').value=analysisContext().halfWindow;lastSelection=selection;}
 if(run?.run_id!==lastRun){lastRun=run?.run_id;fitError='';}
 $('analysisSource').textContent=run?(run.path?run.path.split(/[\\/]/).at(-1):'Current experiment'):'No experiment loaded';$('analysisSource').title=run?.path??run?.run_id??'';
 const delays=spectraFor(analysisContext()).size;$('analysisPoints').textContent=delays+' delays';$('analysisFrames').textContent=fmt(delays?analysisContext().records.length/delays:0,5)+' repeat times';
 $('analysisWindowLabel').textContent=fmt(analysisContext().wave)+' ± '+fmt(analysisContext().halfWindow)+' nm';
 $('analysisSelection').disabled=fitBusy;const blocked=fitBusy||(!store.analysis.imported&&!!store.snapshot?.busy)||!store.online||!analysisContext().records.length||!axis;
 $('runFit').disabled=blocked||view==='spectrum';$('runFit').textContent=fitBusy?'Fitting…':'▶ Run fit';$('openRun').disabled=fitBusy||!store.online;$('exportFit').disabled=!f||fitBusy||view==='spectrum';
 $('saveRun').disabled=fitBusy||!run||run.saved||(!store.analysis.imported&&!!store.snapshot?.busy);$('discardRun').disabled=fitBusy||!run||(!store.analysis.imported&&!!store.snapshot?.busy);$('discardRun').textContent=store.analysis.imported?'Close Analysis file':'Discard in-memory run…';
 let text=fitBusy?'Fitting':fitError?'Fit failed':f?(f.warnings?.length?'Review warnings':'Converged'):!run?'No data':!axis?'Calibration required':'Ready to fit';
 $('analysisFitState').textContent=text;$('analysisFitState').dataset.state=fitError?'error':f?(f.warnings?.length?'warning':'success'):'neutral';
 $('fitStatus').textContent=fitError|| (fitBusy?'Optimizing the selected observed curve…':!run?'Open an experiment or finish an acquisition.':!axis?'This experiment has no recorded wavelength calibration. Applying a new calibration affects future acquisitions only.':(!store.analysis.imported&&store.snapshot?.busy)?'Finish acquisition before fitting.':'');
 $('fitSampleCount').textContent=f?f.delays_ps.length+' fitted delays':'No fit result';
 $('fitRmse').textContent=f?fmt(Math.sqrt(f.residuals.reduce((s,v)=>s+v*v,0)/f.residuals.length)):'—';
 $('fitCondition').textContent=f?fmt(f.jacobian_condition,3):'—';
 $('fitWarning').textContent=f?.warnings?.join(' · ')??'';$('fitWarning').hidden=!f?.warnings?.length;
 if(f!==lastResult){lastResult=f;const tbody=$('fitParameters');tbody.replaceChildren();if(!f){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.className='table-empty';td.textContent='Run a fit to inspect lifetimes and parameter uncertainty.';tr.append(td);tbody.append(tr);}else{
  const names={tau_ps:['τ','ps'],tau_1_ps:['τ₁','ps'],tau_2_ps:['τ₂','ps'],amplitude:['Amplitude','counts/s'],amplitude_1:['Amplitude 1','counts/s'],amplitude_2:['Amplitude 2','counts/s'],t0_ps:['t₀','ps'],offset_counts_s:['Offset','counts/s']};
  for(const key of ['tau_ps','tau_1_ps','tau_2_ps','amplitude','amplitude_1','amplitude_2','t0_ps','offset_counts_s'])if(key in f.parameters){const tr=document.createElement('tr');if(key.startsWith('tau'))tr.className='lifetime';for(const value of [names[key][0],fmt(f.parameters[key]),fmt(f.standard_errors?.[key]),names[key][1]]){const td=document.createElement('td');td.textContent=value;tr.append(td);}tbody.append(tr);}
 }}
 renderView();drawAnalysis();
}
function canvas(id,xmin,xmax,ymin,ymax,metadata={}){({xmin,xmax,ymin,ymax}=plotRange(id,{xmin,xmax,ymin,ymax},metadata));const c=$(id),rect=c.getBoundingClientRect();if(rect.width<1||rect.height<1)return;const d=devicePixelRatio||1;c.width=Math.round(rect.width*d);c.height=Math.round(rect.height*d);const ctx=c.getContext('2d');ctx.scale(d,d);ctx.font='12px Segoe UI';const p={l:78,r:rect.width-23,t:15,b:rect.height-(id==='analysisResidual'?10:44)};ctx.fillStyle=css('--plot');ctx.fillRect(p.l,p.t,p.r-p.l,p.b-p.t);return{id,ctx,p,w:rect.width,h:rect.height,xmin,xmax,ymin,ymax,x:v=>p.l+(v-xmin)/(xmax-xmin||1)*(p.r-p.l),y:v=>p.b-(v-ymin)/(ymax-ymin||1)*(p.b-p.t)};}
function axes(q,label,residual=false,xlabel='Delay / ps'){const{ctx,p}=q;ctx.fillStyle=css('--muted');ctx.strokeStyle=css('--chart-grid');ctx.lineWidth=1;for(let i=0;i<=4;i++){const xv=q.xmin+(q.xmax-q.xmin)*i/4,x=q.x(xv);ctx.beginPath();ctx.moveTo(x,p.t);ctx.lineTo(x,p.b);ctx.stroke();ctx.textAlign='center';if(!residual)ctx.fillText(fmt(xv,3),x,p.b+18);}for(let i=0;i<=(residual?2:4);i++){const v=q.ymin+(q.ymax-q.ymin)*i/(residual?2:4),y=q.y(v);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(p.r,y);ctx.stroke();ctx.textAlign='right';ctx.fillText(fmt(v,3),p.l-9,y+4);}ctx.save();ctx.translate(20,(p.t+p.b)/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText(label,0,0);ctx.restore();ctx.strokeStyle=css('--plot-border');ctx.strokeRect(p.l,p.t,p.r-p.l,p.b-p.t);if(!residual){ctx.textAlign='center';ctx.fillStyle=css('--muted');ctx.fillText(xlabel,(p.l+p.r)/2,q.h-5);}registerAxisEndpoints(q,{x:!residual,y:true,yX:p.l-9,format:v=>fmt(v,3)});}
function curve(q,points,color,dash=[],dots=false,connect=true){const{ctx,p}=q;ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,p.r-p.l,p.b-p.t);ctx.clip();ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=1.7;ctx.setLineDash(dash);ctx.beginPath();let start=false;for(const[t,v]of points){if(!Number.isFinite(v)){start=false;continue;}if(start)ctx.lineTo(q.x(t),q.y(v));else ctx.moveTo(q.x(t),q.y(v));start=true;}if(connect)ctx.stroke();if(dots)for(const[t,v]of points)if(Number.isFinite(v)){ctx.beginPath();ctx.arc(q.x(t),q.y(v),3.3,0,Math.PI*2);ctx.fill();}ctx.restore();}
function empty(q,label){q.ctx.fillStyle=css('--muted');q.ctx.textAlign='center';q.ctx.fillText(label,(q.p.l+q.p.r)/2,(q.p.t+q.p.b)/2);}
function drawSpectra(){
 analysisPlot=null;$('analysisPoint').hidden=true;
 const groups=spectraFor(analysisContext()),state=spectrumState(),delays=[...groups.keys()],lo=delays[0]??0,hi=delays.at(-1)??0;
 const chosen=[...state.selected].sort((a,b)=>a-b).map(delay=>groups.get(delay)).filter(Boolean),record=groups.values().next().value;
 const axis=axisForRun(),xs=axis??Array.from({length:record?.values.length??1024},(_,i)=>i),log=plotScale==='log',transform=v=>log?(v>0?Math.log10(v):NaN):v;
 let xmin=Infinity,xmax=-Infinity,min=log?Infinity:0,max=-Infinity;
 for(const x of xs){xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);}
 if(xmin===xmax){xmin-=.5;xmax+=.5;}
 for(const group of chosen)for(const value of group.values){const v=transform(value);if(Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}}
 if(!Number.isFinite(min))min=0;if(!Number.isFinite(max))max=1;if(log)min=Math.floor(min);if(max<=min)max=min+1;max+=(max-min)*.1;
 const xlabel=axis?'Wavelength / nm':'CCD pixel',ylabel=log?'log₁₀ counts / s':'counts / s';
 const q=canvas('analysisCurve',xmin,xmax,min,max,{title:'Spectrum',xKey:axis?'analysisSpectrum:nm':'analysisSpectrum:pixel',yKey:log?'analysisSpectrum:log':'analysisSpectrum:counts',xLabel:xlabel,yLabel:ylabel});
 if(!q)return;axes(q,ylabel,false,xlabel);
 for(const group of chosen)curve(q,Array.from(group.values,(v,i)=>[xs[i],transform(v)]),mapColor(delayFraction(group.delay,lo,hi)));
 if(!groups.size)empty(q,'Open an experiment to view its spectra');else if(!chosen.length)empty(q,'Select a delay and click Overlay');
}
export function drawAnalysis(){if(!$('analysisPage')||$('analysisPage').hidden)return;if(view==='spectrum'){drawSpectra();return;}const f=selectedFit();const data=f?f.delays_ps.map((t,i)=>[t,f.observed[i]]):observed();let lo=data.length?data[0][0]:-2,hi=data.length?data.at(-1)[0]:20;if(lo===hi){lo-=1;hi+=1;}
 const log=plotScale==='log',transform=v=>log?(v>0?Math.log10(v):NaN):v;const vals=data.map(d=>transform(d[1])).concat(f?f.predicted.map(transform):[]).filter(Number.isFinite);let min=log?(vals.length?Math.floor(Math.min(...vals)):0):Math.min(0,...vals),max=vals.length?Math.max(...vals):1;if(max===min)max=min+1;max+=(max-min)*.1;
 const q=canvas('analysisCurve',lo,hi,min,max,{yKey:log?'analysis:log':'analysis:counts',yLabel:log?'log₁₀ counts / s':'Counts / s'}),res=f?.residuals??[],abs=Math.max(1,...res.map(Math.abs))*1.15,r=canvas('analysisResidual',lo,hi,-abs,abs);if(!q||!r)return;
 const key=String(analysisContext().run?.run_id)+'|'+analysisContext().wave+'|'+analysisContext().halfWindow;
 analysisPlot={q,data,transform,key};if(selectedPoint?.key!==key)selectedPoint=null;
 axes(q,log?'log₁₀ counts / s':'counts / s');axes(r,'Residual',true);curve(r,[[lo,0],[hi,0]],css('--cursor'),[4,4]);
 if(data.length)curve(q,data.map(([t,y])=>[t,transform(y)]),css('--data'),[],true,false);else empty(q,'Open an experiment to view its decay');
 if(f){curve(q,f.delays_ps.map((t,i)=>[t,transform(f.predicted[i])]),css('--amber'));curve(r,f.delays_ps.map((t,i)=>[t,f.residuals[i]]),css('--data'),[],true,false);}else empty(r,'Residuals appear after fitting');
 if(selectedPoint){const point=data.find(([t])=>t===selectedPoint.t);if(!point)selectedPoint=null;else{
  selectedPoint.value=point[1];const px=q.x(point[0]),py=q.y(transform(point[1]));
  if(Number.isFinite(px+py)&&px>=q.p.l&&px<=q.p.r&&py>=q.p.t&&py<=q.p.b){q.ctx.beginPath();q.ctx.strokeStyle=css('--cursor');q.ctx.lineWidth=2;q.ctx.arc(px,py,5.5,0,Math.PI*2);q.ctx.stroke();}
 }}
 $('analysisPoint').hidden=!selectedPoint;$('analysisPoint').textContent=selectedPoint?'x = '+fmt(selectedPoint.t,6)+' ps · y = '+fmt(selectedPoint.value,6)+' counts/s':'';
}

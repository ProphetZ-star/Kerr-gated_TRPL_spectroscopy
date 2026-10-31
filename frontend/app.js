import {api,initialize,requestId,subscribe} from './api.js';
import {store,axisForRun} from './store.js';
import {drawAll,clickWave} from './charts.js';
import {initAnalysis,renderAnalysis,fitOptions,analysisFitState} from './analysis.js';
import {initCalibration,renderCalibration} from './calibration.js';
import {setHeaderPage,acquisitionPage,headerLocked,syncHeaderLock,updateProgress} from './header.js';
import {newestUnseenFit} from './analysis-state.js';
const $=id=>document.getElementById(id),n=id=>Number($(id).value);
let segments=[{start:-2,end:0,step:.1},{start:0,end:2,step:.05},{start:2,end:20,step:.5}];
function renderViews(){drawAll();renderAnalysis();renderCalibration();}
let fitRunId=null;
let fitSelection=null;
const seenFits=new Set();
let updating=false,pending=false,fetching=false,planTimer,toastTimer,fitTask=null;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
async function action(fn){if(pending)return;pending=true;try{await fn();await refresh();}catch(e){toast(e.message);}finally{pending=false;}}
function modal(title,body){$('modalTitle').textContent=title;$('modalBody').replaceChildren(body);$('modal').showModal();}
function renderSegments(){
 $('segments').replaceChildren();segments.forEach((segment,index)=>{const row=document.createElement('div');row.className='segment-row';
 for(const key of ['start','end','step']){const input=document.createElement('input');input.type='number';input.step='any';input.value=segment[key];input.dataset.index=index;input.dataset.key=key;input.setAttribute('aria-label',`Segment ${index+1} ${key}`);input.oninput=()=>{segments[index][key]=input.value===''?NaN:Number(input.value);schedulePlan();};row.append(input);}
 const b=document.createElement('button');b.className='remove-segment';b.textContent='×';b.disabled=segments.length===1;b.dataset.remove=index;b.setAttribute('aria-label','Delete segment');b.onclick=()=>{segments.splice(index,1);renderSegments();schedulePlan();};row.append(b);$('segments').append(row);});
}
function request(){
 const numeric=['exposure','framesPerPoint','seed','speedup','settle','repeats'];
 if(numeric.some(id=>$(id).value===''||!Number.isFinite(n(id)))||segments.some(s=>Object.values(s).some(v=>!Number.isFinite(v))))throw Error('Enter valid finite acquisition parameters');
 return {mode:store.selectedMode,segments,repeats:n('repeats'),exposure_s:n('exposure'),frames_per_point:n('framesPerPoint'),
  save_path:$('savePath').value.trim()||null,autosave:$('autosave').checked,seed:n('seed'),speedup:n('speedup'),settle_s:n('settle'),
  fault:{kind:$('fault').value,point_index:n('faultPoint')},request_id:requestId()};
}
function schedulePlan(){clearTimeout(planTimer);planTimer=setTimeout(plan,180);}
async function plan(){
 if(store.snapshot?.busy)return;
 try{const p=await api('/scan/plan',request());$('validation').textContent='';updateProgress({percent:0,text:`${p.delays_ps.length} points / scan · ${p.rounds} scans`,remaining:`Estimated ${p.estimated_seconds.toFixed(1)} s / ×${n('speedup')} simulation`});$('run').disabled=headerLocked()||!store.online;}
 catch(e){$('validation').textContent=e.message;$('run').disabled=true;}
}
async function syncRecords(){
 if(fetching||!store.run)return;fetching=true;
 try{const id=store.run.run_id;
  while(id===store.run?.run_id){const page=await api(`/runs/${id}/records?offset=${store.records.length}&limit=100`);if(id!==store.run?.run_id)break;store.records.push(...page.records);if(!page.records.length||store.records.length>=page.total)break;}
 }finally{fetching=false;renderViews();}
}
function renderStatus(s){
 const finalProgress=syncHeaderLock();
 const busy=Boolean(s.busy),run=store.run,active=s.busy==='scan';
 ['stageFields','detectorFields','scanFields','simulationFields'].forEach(id=>$(id).disabled=busy||!store.online);
 $('captureBackground').disabled=busy||!store.online;$('preview').disabled=(busy&&s.busy!=='preview')||!store.online;
 $('preview').textContent=s.busy==='preview'?'Stop preview':'Live preview';
 if(document.activeElement!==$('delay'))$('delay').value=s.stage.delay_ps.toFixed(3);
 $('stageStatus').replaceChildren();const dot=document.createElement('span');dot.className='dot';$('stageStatus').append(dot,document.createTextNode(s.stage.moving?'Moving':s.connected?'In position · simulated':'Disconnected'));
 $('stageStatus').classList.toggle('moving',s.stage.moving);
 document.querySelectorAll('.connected').forEach(el=>el.textContent=s.connected?'● Simulated':'Disconnected');
 document.querySelector('.badge').textContent=store.online?'SIMULATION · Hardware offline':'SERVICE OFFLINE';
 const label=active?(run?.status??'running'):busy?s.busy:run?.status??'ready';
 $('runStatus').textContent=label.charAt(0).toUpperCase()+label.slice(1);$('runStatus').className='run-sub '+(run?.status==='error'?'status-danger':'status-neutral');
 $('run').textContent=active?(['paused','pausing'].includes(run.status)?'▶ Resume':'Ⅱ Pause'):'▶ Start';
 $('run').disabled=headerLocked()||!store.online||(busy&&!active)||(['preparing','stopping','finalizing'].includes(run?.status)&&active)||(!s.connected);
 $('stop').disabled=headerLocked()||(!active&&s.busy!=='move')||run?.status==='stopping';
 $('autosave').disabled=busy;$('savePath').disabled=busy;$('path').disabled=busy;
 if(run){
  const pct=100*run.completed_points/run.plan.total_points;
  updateProgress({percent:pct,text:`${run.completed_points} / ${run.plan.total_points} points · ${run.committed_frames} frames`,remaining:active?run.step:run.status},finalProgress);
  $('saveStatus').textContent=run.error?`Error · ${run.error}`:run.saved?`Saved · ${run.path}`:run.request.autosave?`Writing · ${run.committed_frames} frames`:'Memory only · Not saved';
 }else{$('saveStatus').textContent='No experiment saved';}
 $('saveStatus').title=$('saveStatus').textContent;
 $('export').disabled=!store.records.length;$('saveRun').disabled=busy||!run||run.saved;$('fit').disabled=busy||!store.records.length||!axisForRun();
 $('runFit').disabled=busy||!store.records.length||!axisForRun();$('discardRun').disabled=busy||!run;
 $('subtractBackground').disabled=!run?.metadata?.background||run.metadata.background.exposure_s!==run.request.exposure_s;
 const cal=s.calibration;$('calibrationState').textContent=cal.wavelengths_nm?`${cal.source} · ${cal.calibration_id}`:'No wavelength calibration · pixel axis';
 $('cameraFacts').textContent=`${s.camera.pixels} pixels · FVB · 16 bit · Andor Newton DU920P simulation`;
 $('frameContext').textContent=s.latest?`Last acquired frame · ${s.latest.delay_ps.toFixed(3)} ps · ${s.latest.exposure_s} s exposure${Math.abs(s.latest.delay_ps-s.stage.delay_ps)>.001?' · Stage moved since this frame':''}`:'No acquired frame';
}
async function refresh(){
 if(updating)return;updating=true;
 try{
  const s=await api('/status');const first=!store.snapshot;store.online=true;store.snapshot=s;
  const changed=s.run?.run_id!==store.run?.run_id;
  if(changed){seenFits.clear();store.records=[];store.fit=null;$('fit').checked=false;$('tauValues').hidden=true;
   $('roundSelect').replaceChildren(new Option('All rounds','all'));for(let i=0;i<(s.run?.plan.rounds??0);i++)$('roundSelect').add(new Option(`Round ${i+1}`,String(i)));store.round='all';
   if(s.run){const r=s.run.request;segments=structuredClone(r.segments);renderSegments();store.selectedMode=r.mode;
    document.querySelectorAll('#mode button').forEach(b=>b.classList.toggle('selected',b.dataset.mode===r.mode));$('scanParams').hidden=r.mode==='single';$('repeatWrap').hidden=r.mode!=='repeat';
    for(const [id,key]of [['exposure','exposure_s'],['framesPerPoint','frames_per_point'],['seed','seed'],['speedup','speedup'],['settle','settle_s'],['repeats','repeats']])$(id).value=r[key];
    $('fault').value=r.fault.kind;$('faultPoint').value=r.fault.point_index;$('autosave').checked=r.autosave;
   }
  }
  if(first){$('center').value=s.spectrograph.center_nm;$('grating').value=s.spectrograph.grating;$('slit').value=s.spectrograph.slit_um;$('exposure').value=s.camera.exposure_s;}
  store.run=s.run;
  if(!fitTask){const restored=newestUnseenFit(store.run,seenFits);if(restored)showFit(restored);}
  renderStatus(s);await syncRecords();renderViews();
 }catch(e){store.online=false;$('runStatus').textContent='Service offline';$('run').disabled=true;$('stop').disabled=true;document.querySelector('.badge').textContent='SERVICE OFFLINE';}
 finally{updating=false;}
}
async function startFit(){
 if(!store.run||fitTask)return;
 try{const options=fitOptions();fitSelection=JSON.stringify(options);analysisFitState(true);const runId=store.run.run_id;const result=await api(`/runs/${runId}/fits`,options);fitTask=result.task_id;fitRunId=runId;}
 catch(e){analysisFitState(false,e.message);throw e;}
}
async function checkFit(){
 if(!fitTask)return;const result=await api('/tasks/'+fitTask);if(result.status==='running')return;seenFits.add(fitTask);fitTask=null;
 if(fitRunId!==store.run?.run_id){analysisFitState(false);return;}
 if(fitSelection!==JSON.stringify(fitOptions())){analysisFitState(false,'Fit controls changed; run the fit again to display a matching result.');return;}
 if(result.status==='error'){store.fit=null;$('fit').checked=false;$('tauValues').hidden=true;analysisFitState(false,result.error);toast(result.error);renderViews();return;}
 showFit(result.result);analysisFitState(false);renderViews();
}
function showFit(result){
 if(result.task_id)seenFits.add(result.task_id);
 store.fit=result;const options=result.request;
 if(options){store.wave=options.wavelength_nm;store.halfWindow=options.half_window_nm;$('waveSelect').value=store.wave;$('windowSelect').value=store.halfWindow;$('fitModel').value=options.model;$('irfWidth').value=options.irf_fwhm_ps;$('fitStart').value=options.min_delay_ps??'';$('fitEnd').value=options.max_delay_ps??'';}
 $('fit').checked=true;$('tauValues').hidden=false;$('tauValues').replaceChildren();
 const p=result.parameters;for(const key of ['tau_ps','tau_1_ps','tau_2_ps'])if(key in p){const span=document.createElement('span');span.textContent=`${key.replace('_ps','')} ${p[key].toFixed(3)} ps`;$('tauValues').append(span);}renderAnalysis();
}
$('run').onclick=()=>action(async()=>{
 if(headerLocked())return;
 if(store.snapshot?.busy==='scan'){await api(`/runs/${store.run.run_id}/${['paused','pausing'].includes(store.run.status)?'resume':'pause'}`,{});return;}
 await api('/runs',request());
});
$('stop').onclick=()=>{if(!headerLocked())return action(()=>api(store.snapshot?.busy==='move'?'/stage/stop':`/runs/${store.run.run_id}/stop`,{}));};
$('moveAbsolute').onclick=()=>action(()=>api('/stage/move',{delay_ps:n('delay'),relative:false,request_id:requestId()}));
$('delay').onkeydown=e=>{if(e.key==='Enter')$('moveAbsolute').click();};
document.querySelectorAll('[data-jog]').forEach(b=>b.onclick=()=>action(()=>api('/stage/move',{delay_ps:Number(b.dataset.jog),relative:true,request_id:requestId()})));
$('zero').onclick=()=>{const box=document.createElement('div'),p=document.createElement('p'),b=document.createElement('button');p.textContent='Set the current position as optical delay zero? The stage will not move.';b.textContent='Confirm zero';b.className='primary';b.onclick=()=>action(async()=>{await api('/stage/zero',{});$('modal').close();});box.append(p,b);modal('Set delay zero',box);};
$('closeModal').onclick=()=>$('modal').close();
document.querySelectorAll('#mode button').forEach(b=>b.onclick=()=>{store.selectedMode=b.dataset.mode;document.querySelectorAll('#mode button').forEach(x=>x.classList.toggle('selected',x===b));$('scanParams').hidden=store.selectedMode==='single';$('repeatWrap').hidden=store.selectedMode!=='repeat';schedulePlan();});
$('addSegment').onclick=()=>{if(segments.length>=10)return toast('Up to 10 segments');const start=segments.at(-1).end;segments.push({start,end:start+10,step:1});renderSegments();schedulePlan();};
for(const id of ['repeats','exposure','framesPerPoint','seed','speedup','settle','fault','faultPoint'])$(id).addEventListener('input',schedulePlan);
$('exposure').onchange=()=>action(()=>api('/camera/configure',{exposure_s:n('exposure')}));
$('center').onchange=()=>action(async()=>{await api('/spectrograph/config',{center_nm:n('center'),grating:n('grating'),slit_um:n('slit')});toast('Manual setting recorded. Wavelength calibration must be reloaded.');});
for(const id of ['grating','slit'])$(id).onchange=$('center').onchange;
$('preview').onclick=()=>action(()=>api('/preview/'+(store.snapshot?.busy==='preview'?'stop':'start'),{}));
$('captureBackground').onclick=()=>action(async()=>{await api('/backgrounds',{kind:$('backgroundKind').value,condition:$('backgroundCondition').value});});
$('waveSelect').onchange=()=>{store.wave=n('waveSelect');store.fit=null;$('fit').checked=false;$('tauValues').hidden=true;renderViews();};
$('windowSelect').onchange=()=>{store.halfWindow=n('windowSelect');store.fit=null;$('fit').checked=false;$('tauValues').hidden=true;renderViews();};
$('roundSelect').onchange=()=>{store.round=$('roundSelect').value;renderViews();};
$('subtractBackground').onchange=()=>{store.subtract=$('subtractBackground').checked;store.fit=null;$('fit').checked=false;$('tauValues').hidden=true;renderViews();};
$('fit').onchange=()=>{if($('fit').checked)action(startFit);else{store.fit=null;$('tauValues').hidden=true;renderViews();}};
$('runFit').onclick=()=>action(startFit);
for(const [id,key]of [['spectrumScale','specScale'],['mapScale','mapScale']])document.querySelectorAll('#'+id+' button').forEach(b=>b.onclick=()=>{store[key]=b.dataset.scale;document.querySelectorAll('#'+id+' button').forEach(x=>x.classList.toggle('selected',x===b));renderViews();});
for(const id of ['spectrum','map'])$(id).onclick=e=>{clickWave(id,e.clientX);$('fit').checked=false;$('tauValues').hidden=true;};
$('theme').onclick=()=>{document.body.classList.toggle('light');renderViews();};
function navigate(){const name=['analysis','calibration'].includes(location.hash.slice(1))?location.hash.slice(1):'acquisition';setHeaderPage(name);document.querySelector('.workspace').hidden=name!=='acquisition';document.querySelector('.foot').hidden=name!=='acquisition';$('analysisPage').hidden=name!=='analysis';$('calibrationPage').hidden=name!=='calibration';for(const id of ['acquisition','analysis','calibration'])$(id).classList.toggle('active',id===name);if(store.snapshot){renderStatus(store.snapshot);if(acquisitionPage()&&!store.run&&!store.snapshot.busy)plan();}requestAnimationFrame(renderViews);}
for(const id of ['acquisition','analysis','calibration'])$(id).onclick=()=>location.hash=id;window.addEventListener('hashchange',navigate);
$('path').onclick=()=>{const p=document.createElement('p');p.textContent='Enter an output filename in the Save path field. A blank path creates a unique .h5 file in the project data folder. Paths refer to this Windows computer.';modal('Local output path',p);};
$('export').onclick=async()=>{if(!store.run)return;const url=`/api/v1/runs/${store.run.run_id}/export?center_nm=${store.wave}&half_window_nm=${store.halfWindow}`;try{const r=await fetch(url);if(!r.ok)throw Error((await r.json()).detail);const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=`kerr_${store.run.run_id}_simulation.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);}catch(e){toast(e.message);}};
$('saveRun').onclick=()=>action(()=>api(`/runs/${store.run.run_id}/save`,{path:$('savePath').value.trim()||`data/${store.run.run_id}.h5`}));
$('discardRun').onclick=()=>{const box=document.createElement('div'),p=document.createElement('p'),b=document.createElement('button');p.textContent='Discard the in-memory experiment? Existing files are retained.';b.textContent='Discard memory';b.onclick=()=>action(async()=>{await api(`/runs/${store.run.run_id}/discard`,{});$('modal').close();});box.append(p,b);modal('Discard experiment',box);};
$('openRun').onclick=()=>action(async()=>{await api('/runs/open',{path:$('openPath').value});store.run=null;store.records=[];});
initAnalysis(renderViews);initCalibration(refresh);renderSegments();navigate();new ResizeObserver(renderViews).observe(document.querySelector('.data'));
try{await initialize();store.online=true;await refresh();await plan();subscribe(message=>{if(message.type==='spectrum.preview'&&store.snapshot){store.snapshot.latest=message.payload;renderViews();}if(message.type==='run.error')toast(message.payload.message);},connected=>{store.online=connected;});}
catch(e){toast('Start the local service using start-sim.ps1. '+e.message);}
setInterval(async()=>{await refresh();try{await checkFit();}catch(e){fitTask=null;analysisFitState(false,e.message);toast(e.message);}},600);

import {api,initialize,requestId,subscribe,download,uploadExperiment} from './api.js';
import {store,axisForRun,analysisContext} from './store.js';
import {drawAll,clickWave,initChartDrags} from './charts.js';
import {initAnalysis,renderAnalysis,fitOptions,analysisFitState} from './analysis.js';
import {initCalibration,renderCalibration} from './calibration.js';
import {setHeaderPage,acquisitionPage,headerLocked,syncHeaderLock,updateProgress} from './header.js';
import {newestUnseenFit} from './analysis-state.js';
import {DEFAULT_FILE_NAME,outputPath,splitOutputPath} from './output.js';
import {initPlotRanges} from './plot-ranges.js';
import {createStatusSync} from './status-sync.js';
import {createRenderScheduler} from './render-scheduler.js';
const $=id=>document.getElementById(id),n=id=>Number($(id).value);
let segments=[{start:-2,end:0,step:.1},{start:0,end:2,step:.05},{start:2,end:20,step:.5}];
function renderViews(){drawAll();if(!$('analysisPage').hidden)renderAnalysis();if(!$('calibrationPage').hidden)renderCalibration();}
const rendering=createRenderScheduler(renderViews),readStatus=createStatusSync(api,()=>store.snapshot);
const fitJobs={acquisition:null,analysis:null};
let acquisitionFitOptions=null;
const analysisSeenFits=new Set();
const seenFits=new Set();
const hiddenFits=new Set();
let updating=false,pending=false,fetching=false,fullSyncPending=false,planTimer,toastTimer;
let startupReady=false;
let acquisitionParameters={frames_per_point:1,seed:42,speedup:50,settle_s:.05,fault:{kind:'none',point_index:2}};
function output(){return outputPath(store.outputDirectory,$('savePath').value);}
function renderOutput(){
 const name=$('savePath').value.trim()||DEFAULT_FILE_NAME;
 $('sampleName').textContent=name;$('sampleName').title=name;
 $('path').title='Save folder: '+store.outputDirectory;
}
function ready(){
 startupReady=true;document.body.classList.remove('is-starting');$('appShell').inert=false;
 $('appShell').setAttribute('aria-busy','false');$('startupOverlay').hidden=true;
}
function startupError(error){
 if(startupReady)return;
 $('startupOverlay').classList.add('is-error');$('startupMessage').textContent=error.message;
 $('startupRetry').hidden=false;
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
async function action(fn){if(pending)return;pending=true;try{await fn();await refresh();}catch(e){toast(e.message);}finally{pending=false;}}
function modal(title,body){$('modalTitle').textContent=title;$('modalBody').replaceChildren(body);if(!$('modal').open)$('modal').showModal();}
async function withOutputConfirmation(path,perform,cancelLabel='Cancel acquisition'){
 const info=await api('/output/check',{path});
 const execute=async token=>{
  try{await perform(token);$('modal').close();}
  catch(error){if(error.status===409&&/Output file.*(exists|changed)/.test(error.message))return withOutputConfirmation(path,perform,cancelLabel);throw error;}
 };
 if(!info.conflicts.length)return execute(null);
 const box=document.createElement('div'),message=document.createElement('p'),files=document.createElement('p'),buttons=document.createElement('div'),cancel=document.createElement('button'),overwrite=document.createElement('button');
 message.textContent='Existing experiment files will be replaced. No backup will be created.';
 files.textContent=info.conflicts.join('\n');files.className='folder-path mono';buttons.className='modal-foot';cancel.textContent=cancelLabel;cancel.onclick=()=>$('modal').close();overwrite.textContent='Overwrite';overwrite.className='primary';
 overwrite.onclick=()=>action(async()=>{
  const current=await api('/output/check',{path});
  if(current.overwrite_token!==info.overwrite_token)return withOutputConfirmation(path,perform,cancelLabel);
  await execute(info.overwrite_token);
 });
 buttons.append(cancel,overwrite);box.append(message,files,buttons);modal('Overwrite existing experiment?',box);
}
function renderSegments(){
 $('segments').replaceChildren();segments.forEach((segment,index)=>{const row=document.createElement('div');row.className='segment-row';
 for(const key of ['start','end','step']){const input=document.createElement('input');input.type='number';input.step='any';input.value=segment[key];input.dataset.index=index;input.dataset.key=key;input.setAttribute('aria-label',`Segment ${index+1} ${key}`);input.oninput=()=>{segments[index][key]=input.value===''?NaN:Number(input.value);schedulePlan();};row.append(input);}
 const b=document.createElement('button');b.className='remove-segment';b.textContent='×';b.disabled=segments.length===1;b.dataset.remove=index;b.setAttribute('aria-label','Delete segment');b.onclick=()=>{segments.splice(index,1);renderSegments();schedulePlan();};row.append(b);$('segments').append(row);});
}
function request(){
 const numeric=['exposure','repeats'];
 if(numeric.some(id=>$(id).value===''||!Number.isFinite(n(id)))||segments.some(s=>Object.values(s).some(v=>!Number.isFinite(v))))throw Error('Enter valid finite acquisition parameters');
 return {...acquisitionParameters,mode:store.selectedMode,segments,repeats:n('repeats'),exposure_s:n('exposure'),
  save_path:output(),autosave:$('autosave').checked,request_id:requestId()};
}
function schedulePlan(){clearTimeout(planTimer);planTimer=setTimeout(plan,180);}
async function plan(){
 if(store.snapshot?.busy)return;
 try{const p=await api('/scan/plan',request());$('validation').textContent='';updateProgress({percent:0,text:`${p.delays_ps.length} points / scan · ${p.rounds} scans`,remaining:`Estimated ${p.estimated_seconds.toFixed(1)} s / ×${acquisitionParameters.speedup} simulation`});$('run').disabled=headerLocked()||!store.online;}
 catch(e){$('validation').textContent=e.message;$('run').disabled=true;}
}
async function syncRecords(force=false){
 if(fetching||!store.run||!force&&store.records.length>=store.run.committed_frames)return;fetching=true;
 try{const id=store.run.run_id;
  let more=false;
  for(let batch=0;batch<4&&id===store.run?.run_id;batch++){
   const page=await api(`/runs/${id}/records?offset=${store.records.length}&limit=100`);if(id!==store.run?.run_id)break;
   store.records.push(...page.records);more=!!page.records.length&&store.records.length<page.total;if(!more)break;
  }
  if(more)setTimeout(()=>{void syncRecords(true).catch(error=>toast(error.message));},0);
 }catch(error){
  if(error.status===404||error.status===409){store.run=null;store.records=[];store.fit=null;if(!store.analysis.imported)analysisFitState(false);toast('Experiment changed on the backend. Reloading its current state.');}
  else throw error;
 }finally{fetching=false;rendering.request();}
}
function renderStatus(s){
 store.subtract=false;
 const finalProgress=syncHeaderLock();
 const busy=Boolean(s.busy),run=store.run,active=s.busy==='scan';
 ['stageFields','detectorFields'].forEach(id=>$(id).disabled=(busy&&s.busy!=='preview')||s.stage.moving||!store.online);$('scanFields').disabled=busy||!store.online;
 $('captureBackground').disabled=busy||!store.online;$('preview').disabled=(busy&&s.busy!=='preview')||!store.online;
 $('preview').textContent=s.busy==='preview'?'Stop preview':'Live preview';
 if(document.activeElement!==$('delay'))$('delay').value=s.stage.delay_ps.toFixed(3);
 $('stageStatus').replaceChildren();const dot=document.createElement('span');dot.className='dot';$('stageStatus').append(dot,document.createTextNode(s.stage.moving?'Moving':s.connected?'In position':'Disconnected'));
 $('stageStatus').classList.toggle('moving',s.stage.moving);
 document.querySelectorAll('.connected').forEach(el=>el.textContent=s.connected?'● Simulated':'Disconnected');
 document.querySelector('.badge').textContent=store.online?(s.source_mode==='simulation'?'SIMULATION · Hardware offline ▾':'HARDWARE · '+(s.connected?'Connected':'Offline')+' ▾'):'SERVICE OFFLINE';
 $('hardwareMode').disabled=busy||!store.online;
 const label=active?(run?.status??'running'):busy?s.busy:run?.status??'ready';
 $('runStatus').textContent=label.charAt(0).toUpperCase()+label.slice(1);$('runStatus').className='run-sub '+(run?.status==='error'?'status-danger':'status-neutral');
 $('run').textContent=active?(['paused','pausing'].includes(run.status)?'▶ Resume':'\u275A\u275A Pause'):'▶ Start';
 $('run').disabled=headerLocked()||!store.online||(busy&&!active)||(['preparing','stopping','finalizing'].includes(run?.status)&&active)||(!s.connected);
 $('stop').disabled=headerLocked()||!['scan','move','background','preview'].includes(s.busy)||run?.status==='stopping';
 $('autosave').disabled=busy;$('savePath').disabled=busy;$('path').disabled=busy;
 if(run){
  const pct=100*run.completed_points/run.plan.total_points;
  updateProgress({percent:pct,text:`${run.completed_points} / ${run.plan.total_points} points · ${run.committed_frames} frames`,remaining:active?run.step:run.status},finalProgress);
  $('saveStatus').textContent=run.error?`Error · ${run.error}`:run.saved?`Saved · ${run.path}`:run.request.autosave?`Writing · ${run.committed_frames} frames`:'Memory only · Not saved';
 }else{$('saveStatus').textContent='No experiment saved';}
 $('saveStatus').title=$('saveStatus').textContent;
 $('export').disabled=!store.records.length||!axisForRun();
 
 const cal=s.calibration;$('calibrationState').textContent=s.calibration_warning|| (cal.wavelengths_nm?`${cal.source} · ${cal.calibration_id}`:'No wavelength calibration · pixel axis');
 $('calibrationState').classList.toggle('error',!!s.calibration_warning);
}
async function refresh(force=false){
 if(updating){if(force===true)fullSyncPending=true;return;}updating=true;let reachable=false;
 try{
  const s=await readStatus(force===true);reachable=true;const first=!store.snapshot;
  if(store.snapshot&&store.snapshot.worker_session_id!==s.worker_session_id){await initialize();store.records=[];store.run=null;store.fit=null;fitJobs.acquisition=null;fitJobs.analysis=null;analysisFitState(false);store.analysis={imported:false,run:null,records:[],fit:null,wave:600,halfWindow:5};analysisSeenFits.clear();}
  store.online=true;store.snapshot=s;
  const changed=s.run?.run_id!==store.run?.run_id;
  if(changed){seenFits.clear();store.records=[];store.fit=null;
   store.round='all';
   if(s.run){const r=s.run.request;segments=structuredClone(r.segments);renderSegments();store.selectedMode=r.mode;
    document.querySelectorAll('#mode button').forEach(b=>b.classList.toggle('selected',b.dataset.mode===r.mode));$('scanParams').hidden=r.mode==='single';$('repeatWrap').hidden=r.mode!=='repeat';
    for(const [id,key]of [['exposure','exposure_s'],['repeats','repeats']])$(id).value=r[key];
    for(const key of Object.keys(acquisitionParameters))if(key!=='fault')acquisitionParameters[key]=structuredClone(r[key]??acquisitionParameters[key]);
    $('autosave').checked=r.autosave;
    if(r.save_path){const saved=splitOutputPath(r.save_path);store.outputDirectory=saved.directory;$('savePath').value=saved.fileName;renderOutput();}
   }
  }
  if(first){$('center').value=s.spectrograph.center_nm;$('grating').value=s.spectrograph.grating;$('slit').value=s.spectrograph.slit_um;$('exposure').value=s.camera.exposure_s;}
  store.run=s.run;
  if(!fitJobs.acquisition){const restored=newestUnseenFit(store.run,seenFits);if(restored)showFit(restored);}
  renderStatus(s);await syncRecords();rendering.request();ready();
 }catch(e){startupError(e);if(reachable){toast(e.message);return;}store.online=false;$('hardwareMode').disabled=true;$('runStatus').textContent=e.status===503?'Backend unavailable · restart local service':'Service offline';$('run').disabled=true;$('stop').disabled=true;document.querySelector('.badge').textContent=e.status===503?'BACKEND UNAVAILABLE · Restart local service':'SERVICE OFFLINE';}
 finally{updating=false;if(fullSyncPending){fullSyncPending=false;void refresh(true);}}
}
function currentFitOptions(scope){
 if(scope==='acquisition'&&store.analysis.imported)return {...(acquisitionFitOptions??store.fit?.request??{model:'double',irf_fwhm_ps:.25,min_delay_ps:null,max_delay_ps:null}),wavelength_nm:store.wave,half_window_nm:store.halfWindow};
 return fitOptions();
}
async function startFit(scope='acquisition'){
 const context=scope==='analysis'?analysisContext():store,key=context===store?'acquisition':'analysis';if(!context.run||fitJobs[key])return;
 try{const options=currentFitOptions(key);if(context===analysisContext())analysisFitState(true);const runId=context.run.run_id,prefix=key==='analysis'?'/analysis/runs/':'/runs/';const result=await api(prefix+runId+'/fits',options);fitJobs[key]={id:result.task_id,runId,selection:JSON.stringify(options)};}
 catch(error){if(context===analysisContext())analysisFitState(false,error.message);throw error;}
}
async function checkFit(){
 for(const scope of ['acquisition','analysis']){
  const job=fitJobs[scope];if(!job)continue;const id=job.id,result=await api('/tasks/'+id);if(result.status==='running')continue;
  const context=scope==='analysis'?store.analysis:store,visible=context===analysisContext();
  (scope==='analysis'?analysisSeenFits:seenFits).add(id);fitJobs[scope]=null;
  if(hiddenFits.has(id)||result.status==='cancelled'||job.runId!==context.run?.run_id){if(visible)analysisFitState(false);continue;}
  if(job.selection!==JSON.stringify(currentFitOptions(scope))){if(visible)analysisFitState(false,'Fit controls changed; run the fit again to display a matching result.');continue;}
  if(result.status==='error'){context.fit=null;if(visible)analysisFitState(false,result.error);toast(result.error);renderViews();continue;}
  showFit(result.result,scope);if(visible)analysisFitState(false);renderViews();
 }
}
function showFit(result,scope='acquisition'){
 const context=scope==='analysis'?analysisContext():store;
 if(result.task_id)(context===store?seenFits:analysisSeenFits).add(result.task_id);
 context.fit=result;const options=result.request;
 if(options){context.wave=options.wavelength_nm;context.halfWindow=options.half_window_nm;if(context===store)acquisitionFitOptions={...options};
  if(context!==store||!store.analysis.imported){$('fitModel').value=options.model;$('irfWidth').value=options.irf_fwhm_ps;$('fitStart').value=options.min_delay_ps??'';$('fitEnd').value=options.max_delay_ps??'';}}
 renderAnalysis();
}
async function loadAnalysisFile(run){
 const records=[];try{while(true){const page=await api('/analysis/runs/'+run.run_id+'/records?offset='+records.length+'&limit=100');records.push(...page.records);if(!page.records.length||records.length>=page.total)break;}}
 catch(error){void api('/analysis/runs/'+run.run_id+'/close',{}).catch(()=>{});throw error;}
 const previous=store.analysis.run?.run_id;
 if(!store.analysis.imported){try{acquisitionFitOptions=fitOptions();}catch{acquisitionFitOptions=store.fit?.request??null;}}
 store.analysis={imported:true,run,records,fit:null,wave:600,halfWindow:5};analysisSeenFits.clear();
 const restored=newestUnseenFit(run,analysisSeenFits);if(restored)showFit(restored,'analysis');
 if(previous)void api('/analysis/runs/'+previous+'/close',{}).catch(()=>{});
 analysisFitState(false);renderViews();
}
$('run').onclick=()=>action(async()=>{
 if(headerLocked())return;
 if(store.snapshot?.busy==='scan'){await api(`/runs/${store.run.run_id}/${['paused','pausing'].includes(store.run.status)?'resume':'pause'}`,{});return;}
 const body=request();
 if(body.autosave)await withOutputConfirmation(body.save_path,token=>api('/runs',{...body,overwrite_token:token}));
 else await api('/runs',body);
});
$('stop').onclick=()=>{if(!headerLocked())return action(()=>api(({move:'/stage/stop',preview:'/preview/stop',background:'/backgrounds/stop'})[store.snapshot?.busy]??`/runs/${store.run?.run_id}/stop`,{}));};
$('moveAbsolute').onclick=()=>action(()=>api('/stage/move',{delay_ps:n('delay'),relative:false,request_id:requestId()}));
$('returnZero').onclick=()=>action(()=>api('/stage/move',{delay_ps:0,relative:false,request_id:requestId()}));
$('delay').onkeydown=e=>{if(e.key==='Enter')$('moveAbsolute').click();};
document.querySelectorAll('[data-jog]').forEach(b=>b.onclick=()=>action(()=>api('/stage/move',{delay_ps:Number(b.dataset.jog),relative:true,request_id:requestId()})));
$('zero').onclick=()=>{const box=document.createElement('div'),p=document.createElement('p'),b=document.createElement('button');p.textContent='Set the current position as optical delay zero? The stage will not move.';b.textContent='Confirm zero';b.className='primary';b.onclick=()=>action(async()=>{await api('/stage/zero',{});$('modal').close();});box.append(p,b);modal('Set delay zero',box);};
$('closeModal').onclick=()=>$('modal').close();
document.querySelectorAll('#mode button').forEach(b=>b.onclick=()=>{store.selectedMode=b.dataset.mode;document.querySelectorAll('#mode button').forEach(x=>x.classList.toggle('selected',x===b));$('scanParams').hidden=store.selectedMode==='single';$('repeatWrap').hidden=store.selectedMode!=='repeat';schedulePlan();});
$('addSegment').onclick=()=>{if(segments.length>=10)return toast('Up to 10 segments');const start=segments.at(-1).end;segments.push({start,end:start+10,step:1});renderSegments();schedulePlan();};
for(const id of ['repeats','exposure'])$(id).addEventListener('input',schedulePlan);
$('exposure').onchange=()=>action(()=>api('/camera/configure',{exposure_s:n('exposure')}));
$('center').onchange=()=>action(async()=>{await api('/spectrograph/config',{center_nm:n('center'),grating:n('grating'),slit_um:n('slit')});toast('Manual setting recorded. Wavelength calibration must be reloaded.');});
for(const id of ['grating','slit'])$(id).onchange=$('center').onchange;
$('preview').onclick=()=>action(()=>api('/preview/'+(store.snapshot?.busy==='preview'?'stop':'start'),{}));
$('captureBackground').onclick=()=>action(async()=>{await api('/backgrounds',{kind:'dark',condition:'Synthetic shutter closed'});});
$('runFit').onclick=()=>action(()=>startFit('analysis'));
for(const [id,key]of [['spectrumScale','specScale'],['mapScale','mapScale']])document.querySelectorAll('#'+id+' button').forEach(b=>b.onclick=()=>{store[key]=b.dataset.scale;document.querySelectorAll('#'+id+' button').forEach(x=>x.classList.toggle('selected',x===b));renderViews();});
for(const id of ['spectrum','map'])$(id).onclick=e=>{clickWave(id,e.clientX);};
$('theme').onclick=()=>{document.body.classList.toggle('light');renderViews();};
function navigate(){const name=['analysis','calibration'].includes(location.hash.slice(1))?location.hash.slice(1):'acquisition';setHeaderPage(name);document.querySelector('.workspace').hidden=name!=='acquisition';document.querySelector('.foot').hidden=name!=='acquisition';$('analysisPage').hidden=name!=='analysis';$('calibrationPage').hidden=name!=='calibration';for(const id of ['acquisition','analysis','calibration'])$(id).classList.toggle('active',id===name);if(store.snapshot){renderStatus(store.snapshot);if(acquisitionPage()&&!store.run&&!store.snapshot.busy)plan();}requestAnimationFrame(renderViews);}
for(const id of ['acquisition','analysis','calibration'])$(id).onclick=()=>location.hash=id;window.addEventListener('hashchange',navigate);
$('savePath').oninput=()=>{renderOutput();schedulePlan();};
$('path').onclick=async()=>{
 if(store.snapshot?.busy)return;
 const choose=window.pywebview?.api?.select_output_folder;
 if(choose){try{const folder=await choose(store.outputDirectory);if(folder){store.outputDirectory=folder;renderOutput();schedulePlan();}}catch(error){toast(error.message);}return;}
 const box=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),save=document.createElement('button');
 label.textContent='Save folder';input.className='folder-input mono';input.value=store.outputDirectory;input.setAttribute('aria-label','Save folder');
 save.textContent='Use folder';save.className='primary';save.onclick=()=>{if(!input.value.trim())return toast('Enter a save folder.');store.outputDirectory=input.value.trim();renderOutput();schedulePlan();$('modal').close();};
 box.append(label,input,save);modal('Choose save folder',box);
};
$('hardwareMode').onclick=()=>{
 if(!store.online||store.snapshot?.busy)return;
 const box=document.createElement('div'),info=document.createElement('p'),options=document.createElement('div');options.className='source-options';
 info.textContent='Andor Newton DU920P · PI M-414.32S (USB). Hardware mode requires commissioned drivers and the PI controller configuration.';
 for(const [mode,label]of [['simulation','Simulation'],['hardware','Real hardware']]){const button=document.createElement('button');button.textContent=label;button.onclick=()=>action(async()=>{await api('/devices/connect',{mode});$('modal').close();});options.append(button);}
 box.append(info,options);modal('Acquisition source',box);
};
$('export').onclick=async()=>{if(!store.run||!axisForRun())return;const url=`/runs/${store.run.run_id}/export?center_nm=${store.wave}&half_window_nm=${store.halfWindow}`;try{const r=await download(url);const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=`kerr_${store.run.run_id}_simulation.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);}catch(e){toast(e.message);}};
$('saveRun').onclick=()=>action(async()=>{if(store.analysis.imported)return;const path=output(),id=store.run.run_id;await withOutputConfirmation(path,token=>api(`/runs/${id}/save`,{path,overwrite_token:token}),'Cancel save');});
$('discardRun').onclick=()=>{if(store.analysis.imported)return action(async()=>{await api('/analysis/runs/'+store.analysis.run.run_id+'/close',{});store.analysis={imported:false,run:null,records:[],fit:null,wave:600,halfWindow:5};fitJobs.analysis=null;if(acquisitionFitOptions){$('fitModel').value=acquisitionFitOptions.model;$('irfWidth').value=acquisitionFitOptions.irf_fwhm_ps;$('fitStart').value=acquisitionFitOptions.min_delay_ps??'';$('fitEnd').value=acquisitionFitOptions.max_delay_ps??'';}analysisFitState(!!fitJobs.acquisition);renderViews();});const box=document.createElement('div'),p=document.createElement('p'),b=document.createElement('button');p.textContent='Discard the in-memory experiment? Existing files are retained.';b.textContent='Discard memory';b.onclick=()=>action(async()=>{await api(`/runs/${store.run.run_id}/discard`,{});$('modal').close();});box.append(p,b);modal('Discard experiment',box);};
$('openRun').onclick=async()=>{
 if(!store.online)return;
 const choose=window.pywebview?.api?.select_hdf5_file;
 if(!choose){$('openFile').click();return;}
 try{const path=await choose($('openPath').value||store.outputDirectory);if(path)await action(async()=>{const run=await api('/analysis/open',{path});$('openPath').value=path;await loadAnalysisFile(run);});}
 catch(error){toast(error.message);}
};
$('openFile').onchange=e=>action(async()=>{const file=e.target.files?.[0];if(!file)return;try{const run=await uploadExperiment(file);$('openPath').value=file.name;await loadAnalysisFile(run);}finally{e.target.value='';}});
initPlotRanges(renderViews);initChartDrags(()=>{renderViews();});initAnalysis(renderViews);initCalibration(refresh);renderSegments();renderOutput();navigate();new ResizeObserver(renderViews).observe(document.querySelector('.data'));
$('startupRetry').onclick=async()=>{ $('startupOverlay').classList.remove('is-error');$('startupRetry').hidden=true;$('startupMessage').textContent='Connecting to the local service…';try{await initialize();await refresh();await plan();}catch(error){startupError(error);} };
try{await initialize();store.online=true;await refresh();await plan();subscribe(message=>{if(message.type==='stream.gap')void refresh(true);if(message.type==='spectrum.preview'&&store.snapshot&&(!message.worker_session_id||message.worker_session_id===store.snapshot.worker_session_id)){store.snapshot.latest=message.payload;rendering.request();}if(message.type==='run.error')toast(message.payload.message);},connected=>{if(connected)store.online=true;});}
catch(e){startupError(e);toast('Close the app and reopen TRPL.exe to restart the local service. '+e.message);}
setInterval(async()=>{await refresh();try{await checkFit();}catch(e){fitJobs.acquisition=null;fitJobs.analysis=null;analysisFitState(false,e.message);toast(e.message);}},600);

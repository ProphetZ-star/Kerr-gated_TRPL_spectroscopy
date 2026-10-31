// Execute actual frontend modules in a minimal DOM, using genuine API fixtures. No browser/UI.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const fixtures=JSON.parse(fs.readFileSync(path.join(root,'test-output/data-flow-fixtures.json'),'utf8'));
const clone=value=>JSON.parse(JSON.stringify(value));
class Element{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.style={};this.dataset={};this.attrs={};this.classes=new Set();this.classList={toggle:(c,v)=>v===undefined?(this.classes.has(c)?this.classes.delete(c):this.classes.add(c)):v?this.classes.add(c):this.classes.delete(c),contains:c=>this.classes.has(c)};}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]??this.id;}
 setCustomValidity(v){this.invalid=v;}reportValidity(){}checkValidity(){return !this.invalid;}
 getBoundingClientRect(){return{width:0,height:0,left:0,top:0};}scrollIntoView(){}click(){}
 querySelectorAll(selector){return this.children.flatMap(c=>c instanceof Element?[...(selector.split(',').some(s=>s===c.tagName)?[c]:[]),...c.querySelectorAll(selector)]:[]);}
 get lastElementChild(){return this.children.at(-1);}
}
const elements=new Map(),get=id=>{if(!elements.has(id)){const e=new Element();e.id=id;elements.set(id,e);}return elements.get(id);};
const bar=new Element(),container=new Element();
const axisButtons=['pixel','wavelength'].map(axis=>Object.assign(new Element('button'),{dataset:{axis}}));
const scaleButtons=['linear','log'].map(scale=>Object.assign(new Element('button'),{dataset:{scale}}));
const document={getElementById:get,body:new Element(),createElement:tag=>new Element(tag),
 querySelector:s=>s==='[role=progressbar]'?bar:container,
 querySelectorAll:s=>s==='#calAxis button'?axisButtons:s==='#analysisScale button'?scaleButtons:[]};
for(const[id,value]of Object.entries({calExposure:'1',calRowStart:'85',calRowEnd:'165',calOrder:'2',calConvention:'vacuum',irfWidth:'.25',fitStart:'-.5',fitEnd:'8',fitModel:'single',analysisWave:'600',analysisWindow:'5'}))get(id).value=value;
get('analysisPage').hidden=true;get('calibrationPage').hidden=true;
const requests=[],blobs=[];let applied=false,bodyError=false;
const context=vm.createContext({console,document,Blob,Response,Float32Array,Int32Array,Float64Array,
 setTimeout:()=>0,clearTimeout:()=>{},devicePixelRatio:1,
 ResizeObserver:class{observe(){}},getComputedStyle:()=>({getPropertyValue:()=> '#555555'}),
 URL:{createObjectURL:b=>{blobs.push(b);return 'blob:fixture';},revokeObjectURL:()=>{}},
 fetch:async(url,options={})=>{
  const body=options.body?JSON.parse(options.body):undefined;requests.push({url,body});
  if(url==='/api/v1/session')return Response.json({token:'fixture-session'});
  if(options.method==='POST')assert.equal(options.headers['X-Session-Token'],'fixture-session');
  if(url==='/api/v1/calibration/frame')return Response.json(null);
  if(url==='/api/v1/calibration/frames'){assert.deepEqual(body,{exposure_s:1,seed:42});return Response.json(fixtures.frame);}
  if(url==='/api/v1/calibration/extract'){assert.equal(body.frame_id,fixtures.frame.frame_id);assert.equal(body.row_start,85);assert.equal(body.row_end,165);return Response.json(fixtures.extracted);}
  if(url==='/api/v1/calibration/fit'){
   if(bodyError)return Response.json({detail:'at least 5 enabled reference lines are required'},{status:409});
   assert.deepEqual(body,fixtures.mapping_request);return Response.json(fixtures.solution);
  }
  if(url==='/api/v1/calibration/apply'){assert.deepEqual(body,{solution_id:fixtures.solution.solution_id});applied=true;return Response.json(fixtures.applied_status);}
  if(url==='/api/v1/calibration/solutions/'+fixtures.solution.solution_id)return Response.json(fixtures.exported);
  throw Error('Unexpected frontend request '+url);
 }});
const modules=new Map();
async function load(relative){const filename=path.resolve(root,relative);if(modules.has(filename))return modules.get(filename);const module=new vm.SourceTextModule(fs.readFileSync(filename,'utf8'),{context,identifier:filename});modules.set(filename,module);await module.link((specifier,parent)=>load(path.relative(root,path.resolve(path.dirname(parent.identifier),specifier))));await module.evaluate();return module;}
async function settle(){for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
 const api=(await load('frontend/api.js')).namespace;await api.initialize();
 const store=(await load('frontend/store.js')).namespace.store;store.online=true;store.snapshot=clone(fixtures.initial_status);
 const calibration=(await load('frontend/calibration.js')).namespace;
 calibration.initCalibration(async()=>{if(applied)store.snapshot=clone(fixtures.applied_status);calibration.renderCalibration();});await settle();
 await get('calCapture').onclick();assert.equal(get('calFrameState').textContent,'Simulated frame 1');
 await get('calFindPeaks').onclick();await get('calFitMapping').onclick();
 assert.equal(get('calFitState').textContent,'Draft fitted');assert.equal(get('calApply').disabled,false);
 await get('calApply').onclick();assert.equal(get('calFitState').textContent,'Applied');
 await get('calExport').onclick();const calibrationExport=JSON.parse(await blobs.at(-1).text());
 assert.equal(calibrationExport.source,'synthetic');assert.equal(calibrationExport.convention,'vacuum');assert.equal(calibrationExport.applied,true);
 assert.deepEqual(calibrationExport.wavelengths_nm,fixtures.solution.wavelengths_nm);
 store.snapshot.busy='scan';calibration.renderCalibration();
 for(const id of ['calCapture','calFindPeaks','calFitMapping','calApply','calExport','calDemoReference','calAddLine','calExposure','calRowStart','calOrder','calConvention','calReferenceFile'])assert.equal(get(id).disabled,true,id+' blocked during acquisition');
 assert.ok(axisButtons.every(b=>b.disabled));assert.ok(get('calPairs').querySelectorAll('input,button').every(e=>e.disabled));
 const beforeLockedActions=requests.length;await get('calCapture').onclick();await get('calFitMapping').onclick();await get('calApply').onclick();await get('calExport').onclick();get('calDemoReference').onclick();get('calAddLine').onclick();assert.equal(requests.length,beforeLockedActions);
 store.snapshot.run={status:'paused'};calibration.renderCalibration();assert.equal(get('calFitMapping').disabled,true);
 store.snapshot.busy=null;calibration.renderCalibration();assert.equal(get('calFitMapping').disabled,false);assert.equal(get('calApply').disabled,false);assert.equal(axisButtons[1].disabled,false);
 get('calOrder').value='3';get('calOrder').onchange();assert.equal(get('calApply').disabled,true);
 bodyError=true;await get('calFitMapping').onclick();assert.match(get('calFeedback').textContent,/at least 5/);assert.equal(get('calExport').disabled,true);
 store.run=clone(fixtures.run);store.records=clone(fixtures.records);store.fit=clone(fixtures.task.result);store.wave=600;store.halfWindow=5;
 store.snapshot={...store.snapshot,busy:null};
 const analysis=(await load('frontend/analysis.js')).namespace;analysis.initAnalysis(()=>{});
 assert.deepEqual(clone(analysis.fitOptions()),fixtures.fit_request);
 analysis.renderAnalysis();assert.equal(get('analysisFitState').textContent,fixtures.task.result.warnings.length?'Review warnings':'Converged');
 assert.equal(get('fitSampleCount').textContent,fixtures.task.result.delays_ps.length+' fitted delays');
 assert.ok(get('fitParameters').children.length>=4);get('exportFit').onclick();
 const csv=(await blobs.at(-1).text()).split('\n');assert.equal(csv.length,fixtures.task.result.delays_ps.length+2);
 assert.deepEqual(csv[2].split(',').map(Number),[fixtures.task.result.delays_ps[0],fixtures.task.result.observed[0],fixtures.task.result.predicted[0],fixtures.task.result.residuals[0]]);
 get('fitStart').value='10';get('fitEnd').value='2';assert.throws(()=>analysis.fitOptions(),/greater/);
 const state=(await load('frontend/analysis-state.js')).namespace,seen=new Set();
 const running=clone(fixtures.run);running.metadata.analysis={};assert.equal(state.newestUnseenFit(running,seen),null);
 running.metadata.analysis[fixtures.task.result.task_id]=clone(fixtures.task.result);
 assert.deepEqual(clone(state.newestUnseenFit(running,seen)),fixtures.task.result);
 assert.equal(state.newestUnseenFit(running,seen),null); // Do not restore a result the user has cleared.
 const newer={...clone(fixtures.task.result),task_id:'newer',finished_utc:'2099-01-01T00:00:00Z'};
 running.metadata.analysis.newer=newer;assert.equal(state.newestUnseenFit(running,seen).task_id,'newer');
 const header=(await load('frontend/header.js')).namespace;header.updateProgress({percent:20,text:'20 points',remaining:'exposing'});
 header.setHeaderPage('analysis');assert.equal(header.headerLocked(),false);header.updateProgress({percent:30,text:'30 points'});assert.equal(get('progressFill').style.width,'30%');assert.equal(bar.attrs['aria-disabled'],'false');
 header.setHeaderPage('calibration');header.updateProgress({percent:80,text:'new progress',remaining:'completed'});assert.equal(get('progressFill').style.width,'30%');assert.equal(get('progressText').textContent,'30 points');assert.equal(get('run').disabled,true);assert.equal(get('stop').disabled,true);assert.equal(bar.attrs['aria-disabled'],'true');
 store.snapshot.busy='scan';header.syncHeaderLock();header.updateProgress({percent:80,text:'80 points',remaining:'paused'});assert.equal(get('progressFill').style.width,'80%');assert.equal(bar.attrs['aria-disabled'],'false');
 store.snapshot.busy=null;const finished=header.syncHeaderLock();header.updateProgress({percent:100,text:'completed'},finished);assert.equal(finished,true);assert.equal(get('progressFill').style.width,'100%');assert.equal(bar.attrs['aria-disabled'],'true');header.updateProgress({percent:0,text:'plan'});assert.equal(get('progressFill').style.width,'100%');
 header.setHeaderPage('acquisition');header.updateProgress({percent:100,text:'completed'});assert.equal(get('progressFill').style.width,'100%');assert.equal(bar.attrs['aria-disabled'],'false');
 const report={passed:true,mode:'Node VM and genuine backend fixtures; no browser or desktop',checks:['capture-request-response','backend-ROI-profile','backend-peak-positions','mapping-request-contract','apply-solution-identity','export-axis-provenance','invalidated-draft-blocked','backend-error-displayed','analysis-fit-options','analysis-result-rendering','analysis-CSV-values','invalid-fit-range','fit-completes-after-page-refresh','cleared-fit-not-restored-again','newer-fit-restored','analysis-header-unlocked','calibration-idle-header-locked','calibration-acquiring-header-live','completion-progress-then-freeze','calibration-controls-disabled-during-scan','paused-scan-keeps-calibration-locked','locked-calibration-sends-no-requests','calibration-idle-controls-restored','return-to-live-progress'],requests:requests.map(r=>r.url)};
 fs.writeFileSync(path.join(root,'test-output/workbench-frontend-flow.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
})().catch(error=>{console.error(error);process.exitCode=1;});

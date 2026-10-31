'use strict';
const assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {createHarness,bootApp,fixtures,clone,ROOT}=require('../qa/frontend/lib/harness.cjs');
const checks=[];const check=(name,fn)=>checks.push({name,fn});
async function boot(options={}){const h=createHarness(options);await bootApp(h);await h.settle();return h;}
const clear=(h,id)=>{const c=h.ctxOf(id);c.ops.length=0;c.strokes.length=0;};
const runRequest=h=>h.requests.findLast(r=>r.url==='/api/v1/runs'&&r.method==='POST');

check('startup unlocks only after a successful status response and retries failures',async()=>{
 const h=createHarness();h.state.statusError='Backend is starting';await bootApp(h);
 assert.equal(h.get('startupRetry').hidden,false);assert.equal(h.get('startupOverlay').hidden,false);
 h.state.statusError=null;await h.click('startupRetry');assert.equal(h.get('appShell').inert,false);
 assert.equal(h.get('startupOverlay').hidden,true);assert.equal(h.get('appShell').getAttribute('aria-busy'),'false');
});
check('file name updates the header and acquisition path',async()=>{
 const h=await boot();h.setValue('savePath','ZnO 01');h.get('savePath').oninput();
 assert.equal(h.get('sampleName').textContent,'ZnO 01');await h.click('run');
 assert.equal(runRequest(h).body.save_path,'data/ZnO 01.h5');
});
check('native folder selection is separate from the filename and cancel retains it',async()=>{
 const h=await boot();h.setValue('savePath','中文 sample');let selected='D:\\TRPL data';
 h.context.window.pywebview={api:{select_output_folder:async()=>selected}};
 await h.click('path');assert.equal(h.store().outputDirectory,selected);assert.equal(h.get('savePath').value,'中文 sample');
 selected=null;await h.click('path');assert.equal(h.store().outputDirectory,'D:\\TRPL data');
 await h.click('run');assert.equal(runRequest(h).body.save_path,'D:\\TRPL data/中文 sample.h5');
});
check('browser folder entry provides the same acquisition path',async()=>{
 const h=await boot();await h.click('path');const box=h.get('modalBody').children[0],input=box.children[1],save=box.children[2];
 input.value='D:/Measurements';save.onclick();await h.click('run');
 assert.equal(runRequest(h).body.save_path,'D:/Measurements/Sample 01 TRPL.h5');
});
check('a filename cannot be used to inject a folder path',async()=>{
 const h=await boot();h.setValue('savePath','../escape');await h.click('run');
 assert.equal(h.requestsMatching('/api/v1/runs').length,0);assert.match(h.get('toast').textContent,/file name/);
 const output=await h.loadModule('frontend/output.js');
 assert.equal(output.namespace.outputPath('D:/','Sample.h5'),'D:/Sample.h5');
 for(const name of ['CON','aux.h5','a:b','a.'])assert.throws(()=>output.namespace.outputPath('data',name));
});
check('memory save uses the selected folder and file name',async()=>{
 const fx=fixtures(),run=clone(fx.run);run.saved=false;
 const h=await boot({status:{...clone(fx.initial_status),busy:null,run},records:clone(fx.records)});
 h.store().outputDirectory='D:/saved';h.setValue('savePath','memory test');await h.click('saveRun');
 assert.equal(h.lastRequest('/api/v1/runs/'+run.run_id+'/save').body.path,'D:/saved/memory test.h5');
});
check('hardware selection calls the real mode API and preserves failure feedback',async()=>{
 const h=await boot();h.state.errors['/api/v1/devices/connect']={status:409,detail:'Hardware mode is not commissioned'};
 await h.click('hardwareMode');const options=h.get('modalBody').children[0].children[1];await options.children[1].onclick();
 assert.equal(h.lastRequest('/api/v1/devices/connect').body.mode,'hardware');
 assert.match(h.get('toast').textContent,/not commissioned/);assert.equal(h.store().snapshot.source_mode,'simulation');
});
check('source switching is blocked during acquisition',async()=>{
 const h=await boot({status:{...clone(fixtures().initial_status),busy:'preview'}});
 assert.equal(h.get('hardwareMode').disabled,true);await h.click('hardwareMode');assert.equal(!!h.get('modal').open,false);
});
check('empty plots share a background and the partial map never draws grid lines',async()=>{
 const h=await boot(),charts=await h.loadModule('frontend/charts.js');
 h.store().snapshot.latest=null;h.store().run=null;h.store().records=[];
 for(const light of [true,false]){
  h.document.body.classList.toggle('light',light);for(const id of ['spectrum','map','decay'])clear(h,id);
  charts.namespace.drawAll();
  for(const id of ['spectrum','map','decay'])assert.equal(h.opsOf(id).find(x=>x.op==='fillRect').color,'#plot');
 }
 const fx=fixtures();h.store().run=clone(fx.run);h.store().records=[clone(fx.records[0])];clear(h,'map');charts.namespace.drawAll();
 assert.equal(h.findStrokes('map','#grid').length,0);assert.ok(h.opsOf('map').some(x=>x.op==='fillRect'&&x.color.startsWith('rgb(')));
});
check('empty calibration retains axes, ignores peak clicks, and uses shared residual ticks',async()=>{
 const h=await boot();await h.navigate('calibration');await h.settle();
 assert.ok(h.opsOf('calImage').some(x=>x.text==='No frame'));
 assert.ok(h.opsOf('calSpectrum').some(x=>x.text==='No spectrum'));
 assert.equal(h.get('calSpectrumAxis').textContent,'pixel');
 const before=h.requestsMatching('/api/v1/calibration/extract').length;
 await h.click('calImage',{clientX:200,clientY:120});assert.equal(h.requestsMatching('/api/v1/calibration/extract').length,before);
 const upper=h.findStrokes('calMapping','#grid').filter(s=>s.points.length===2&&s.points[0][0]===s.points[1][0]).slice(-5).map(s=>s.points[0][0]);
 const lower=h.findStrokes('calResidual','#grid').filter(s=>s.points.length===2&&s.points[0][0]===s.points[1][0]).slice(-5).map(s=>s.points[0][0]);
 assert.deepEqual(upper,lower);assert.equal(h.opsOf('calResidual').filter(x=>x.op==='fillText'&&x.y>h.get('calResidual').getBoundingClientRect().height).length,0);
});
check('calibration reports only nonzero saturation beside the title and uses nm',async()=>{
 const h=await boot();await h.navigate('calibration');await h.settle();
 h.state.extracted={...clone(fixtures().extracted),saturated_pixels:0};await h.click('calCapture');assert.equal(h.get('calRoiLabel').hidden,true);
 h.state.extracted.saturated_pixels=3;await h.click('calFindPeaks');assert.equal(h.get('calRoiLabel').hidden,false);assert.match(h.get('calRoiLabel').textContent,/3 saturated/);
 await h.click('calFitMapping');const buttons=h.document.querySelectorAll('#calAxis button');await buttons[1].onclick();
 assert.equal(h.get('calSpectrumAxis').textContent,'nm');
});

(async()=>{const results=[];for(const c of checks){try{await c.fn();results.push({name:c.name,passed:true});}catch(e){results.push({name:c.name,passed:false,error:e.stack});console.error(c.name,e);}}
 const output={passed:results.every(r=>r.passed),checks:results};fs.mkdirSync(path.join(ROOT,'test-output'),{recursive:true});fs.writeFileSync(path.join(ROOT,'test-output/ui-usability.json'),JSON.stringify(output,null,2));console.log(JSON.stringify({passed:output.passed,checks:results.length}));if(!output.passed)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});

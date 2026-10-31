'use strict';
const assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {createHarness,bootApp,fixtures,clone,ROOT}=require('../qa/frontend/lib/harness.cjs');
const checks=[];const check=(name,fn)=>checks.push({name,fn});
async function boot(options={}){const h=createHarness(options);await bootApp(h);await h.settle();return h;}
const runs=h=>h.requests.filter(r=>r.url==='/api/v1/runs'&&r.method==='POST');
const modalButtons=h=>h.get('modalBody').children[0].children.at(-1).children;

check('overwrite warning cancels without a new run or discarded memory',async()=>{
 const h=await boot();h.state.outputCheck={path:'data/Sample.h5',conflicts:['data/Sample.h5'],overwrite_token:'a'.repeat(64)};
 await h.click('run');assert.match(h.get('modalTitle').textContent,/Overwrite/);assert.equal(runs(h).length,0);
 modalButtons(h)[0].onclick();assert.equal(h.get('modal').open,false);assert.equal(runs(h).length,0);assert.equal(h.requests.filter(r=>r.url.endsWith('/discard')).length,0);
});
check('explicit overwrite sends exactly the reviewed token',async()=>{
 const h=await boot();h.state.outputCheck={path:'data/Sample.h5',conflicts:['data/Sample.h5'],overwrite_token:'b'.repeat(64)};
 await h.click('run');await modalButtons(h)[1].onclick();assert.equal(runs(h).length,1);assert.equal(runs(h)[0].body.overwrite_token,'b'.repeat(64));
});
check('files changed while the warning was open require another confirmation',async()=>{
 const h=await boot();h.state.outputCheck={path:'data/Sample.h5',conflicts:['data/Sample.h5'],overwrite_token:'c'.repeat(64)};
 await h.click('run');h.state.outputCheck.overwrite_token='d'.repeat(64);await modalButtons(h)[1].onclick();assert.equal(runs(h).length,0);
 await modalButtons(h)[1].onclick();assert.equal(runs(h)[0].body.overwrite_token,'d'.repeat(64));
});
check('a new memory scan needs no save or discard dialog',async()=>{
 const h=await boot();h.setChecked('autosave',false);await h.click('run');assert.equal(runs(h).length,1);assert.equal(h.requestsMatching('/api/v1/output/check').length,0);
 assert.equal(h.requests.filter(r=>r.url.endsWith('/discard')).length,0);
});
check('go to t0 issues an absolute zero move without setting a new zero',async()=>{
 const h=await boot();await h.click('returnZero');const move=h.lastRequest('/api/v1/stage/move');assert.equal(move.body.delay_ps,0);assert.equal(move.body.relative,false);assert.equal(h.requestsMatching('/api/v1/stage/zero').length,0);
});
check('native Open selects an HDF5 file and cancel leaves the experiment alone',async()=>{
 const h=await boot();let picked=null;h.context.window.pywebview={api:{select_hdf5_file:async()=>picked}};
 await h.click('openRun');assert.equal(h.requestsMatching('/api/v1/runs/open').length,0);
 picked='D:/Runs/sample.h5';await h.click('openRun');assert.equal(h.lastRequest('/api/v1/runs/open').body.path,picked);
});
check('browser Open uses a file picker and uploads the chosen HDF5 file',async()=>{
 const h=await boot();let opened=false;h.get('openFile').click=()=>{opened=true;};await h.click('openRun');assert.equal(opened,true);
 const file=Object.assign(new Blob(['fixture']),{name:'sample.h5'});await h.change('openFile',{target:{files:[file],value:'sample.h5'}});
 const request=h.lastRequest('/api/v1/runs/import');assert.equal(request.body,file);assert.equal(request.headers['X-File-Name'],'sample.h5');
});
check('live and decay backgrounds remain identical with data in both themes',async()=>{
 const fx=fixtures(),h=await boot({status:{...clone(fx.initial_status),run:clone(fx.run),latest:clone(fx.records[0])},records:clone(fx.records)}),charts=await h.loadModule('frontend/charts.js');
 for(const light of [true,false]){h.document.body.classList.toggle('light',light);for(const id of ['spectrum','decay'])h.ctxOf(id).ops.length=0;charts.namespace.drawAll();for(const id of ['spectrum','decay'])assert.equal(h.opsOf(id).find(x=>x.op==='fillRect').color,'#plot');}
});
check('analysis residuals reuse the upper delay scale without repeating x labels',async()=>{
 const h=await boot();await h.navigate('analysis');const a=h.findStrokes('analysisCurve','#grid').filter(s=>s.points.length===2&&s.points[0][0]===s.points[1][0]).slice(-5).map(s=>s.points[0][0]);
 const b=h.findStrokes('analysisResidual','#grid').filter(s=>s.points.length===2&&s.points[0][0]===s.points[1][0]).slice(-5).map(s=>s.points[0][0]);assert.deepEqual(a,b);
 assert.equal(h.get('analysisModelLabel').textContent,'');assert.equal(h.opsOf('analysisResidual').filter(x=>x.op==='fillText'&&x.y>h.get('analysisResidual').getBoundingClientRect().height).length,0);
});
check('all nine plots omit inward tick segments',async()=>{
 const h=await boot();await h.navigate('analysis');await h.navigate('calibration');
 for(const id of ['spectrum','map','decay','analysisCurve','analysisResidual','calImage','calSpectrum','calMapping','calResidual']){
  assert.equal(h.findStrokes(id,'#muted').length,0,id);
 }
 assert.equal(fs.existsSync(path.join(ROOT,'frontend/plot-ticks.js')),false);
});
(async()=>{const results=[];for(const c of checks){try{await c.fn();results.push({name:c.name,passed:true});}catch(e){results.push({name:c.name,passed:false,error:e.stack});console.error(c.name,e);}}
 const output={passed:results.every(r=>r.passed),checks:results};fs.mkdirSync(path.join(ROOT,'test-output'),{recursive:true});fs.writeFileSync(path.join(ROOT,'test-output/save-workflow.json'),JSON.stringify(output,null,2));console.log(JSON.stringify({passed:output.passed,checks:results.length}));if(!output.passed)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});

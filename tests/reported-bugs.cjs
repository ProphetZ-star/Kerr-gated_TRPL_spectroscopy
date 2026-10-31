'use strict';
// BUG_REPORT.md regressions only. No browser or native window is opened.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),vm=require('vm');
const {createHarness,bootApp,fixtures,clone,ROOT}=require('../qa/frontend/lib/harness.cjs');
const fx=fixtures(),checks=[];
const check=(id,fn)=>checks.push({id,fn});
async function loaded(changes={}){const run=clone(fx.run);run.metadata.analysis={};Object.assign(run,changes.run||{});const h=createHarness({status:{...clone(fx.initial_status),busy:null,run,...changes.status},records:changes.records||clone(fx.records)});await bootApp(h);await h.tick(600);return h;}

check('FE-01: expired token refreshes once and retries rejected write',async()=>{
 const h=createHarness();await bootApp(h);const n=h.requestsMatching('/api/v1/session').length;h.state.token='rotated';await h.click('run');
 assert.equal(h.requestsMatching('/api/v1/session').length,n+1);const requests=h.requestsMatching('/api/v1/runs').filter(r=>r.url==='/api/v1/runs');assert.equal(requests.filter(r=>r.rejected).length,1);assert.equal(requests.filter(r=>!r.rejected).length,1);
});
check('FE-01: worker session change refreshes token proactively',async()=>{
 const h=createHarness();await bootApp(h);h.state.token='new';h.setStatus({worker_session_id:'restarted'});await h.tick(600);await h.click('run');assert.ok(h.requestsMatching('/api/v1/session').length>=2);assert.ok(!h.requestsMatching('/api/v1/runs').some(r=>r.rejected));
});
check('FE-02: stale records 409 never marks reachable service offline',async()=>{
 const h=await loaded();h.state.recordsError='Run not found';await h.tick(600);await h.tick(600);assert.equal(h.store().online,true);assert.notEqual(h.special.badge.textContent,'SERVICE OFFLINE');assert.equal(h.store().run,null);assert.equal(h.store().records.length,0);
});
check('P2-7: worker 503 has actionable restart message and recovers',async()=>{
 const h=createHarness();await bootApp(h);h.state.statusError='Acquisition worker stopped';await h.tick(600);assert.match(h.special.badge.textContent,/restart/i);assert.equal(h.get('run').disabled,true);h.state.statusError=null;await h.tick(600);assert.equal(h.store().online,true);
});
check('FE-03: calibration restore warning is visible',async()=>{
 const h=await loaded({status:{calibration_warning:'Saved calibration is damaged'}});assert.match(h.get('calibrationState').textContent,/damaged/);assert.ok(h.get('calibrationState').classList.contains('error'));
});
check('FE-04: 422 errors are readable field messages',async()=>{
 const h=createHarness();await bootApp(h);h.state.errors['/api/v1/runs']={status:422,detail:[{loc:['body','exposure_s'],msg:'Must be positive'}]};await h.click('run');assert.equal(h.get('toast').textContent,'exposure_s: Must be positive');
});
check('FE-05: non-JSON CSV errors show HTTP status',async()=>{
 const h=await loaded();h.state.errors['/api/v1/runs/'+fx.run.run_id+'/export']={status:502,raw:'<html>bad gateway</html>'};await h.click('export');assert.match(h.get('toast').textContent,/502/);assert.doesNotMatch(h.get('toast').textContent,/SyntaxError|Unexpected token/);
});
check('FE-06: export without experiment calibration is blocked',async()=>{
 const h=await loaded();h.patchStatus(s=>{s.run.metadata.calibration.wavelengths_nm=null;return s;});await h.tick(600);assert.equal(h.get('export').disabled,true);const count=h.requests.length;await h.click('export');assert.equal(h.requests.length,count);
});
check('FE-07: mismatched background clears checkbox and subtraction state',async()=>{
 const h=await loaded();h.store().subtract=true;h.setChecked('subtractBackground',true);h.patchStatus(s=>{s.run.metadata.background={exposure_s:99,spectrum:Array(1024).fill(10)};return s;});await h.tick(600);assert.equal(h.get('subtractBackground').disabled,true);assert.equal(h.get('subtractBackground').checked,false);assert.equal(h.store().subtract,false);
});
check('FE-08: disabled empty calibration row does not block fit',async()=>{
 const h=createHarness();await bootApp(h);await h.navigate('calibration');await h.click('calCapture');await h.click('calAddLine');
 let inputs=h.get('calPairs').children.at(-1).querySelectorAll('input');inputs[0].checked=false;inputs[0].onchange();
 inputs=h.get('calPairs').children.at(-1).querySelectorAll('input');inputs[1].value='';inputs[1].onchange();
 inputs=h.get('calPairs').children.at(-1).querySelectorAll('input');inputs[2].value='';inputs[2].onchange();
 await h.click('calFitMapping');assert.equal(h.lastRequest('/api/v1/calibration/fit').body.pairs.length,5);
});
check('FE-09/S5: unchecking fit cancels and suppresses late result',async()=>{
 const h=await loaded();await h.click('runFit');const id=Object.keys(h.state.tasks).at(-1);h.setChecked('fit',false);await h.change('fit');await h.settle();assert.ok(h.lastRequest('/api/v1/tasks/'+id+'/cancel'));
 h.state.tasks[id]={status:'completed',run_id:fx.run.run_id,result:clone(fx.task.result)};h.state.status.run.metadata.analysis[id]=clone(fx.task.result);await h.tick(600);await h.tick(600);assert.equal(h.get('fit').checked,false);assert.equal(h.store().fit,null);
});
check('FE-10: a single decay point renders a visible marker',async()=>{
 const h=await loaded({records:[clone(fx.records[0])]});const chart=(await h.loadModule('frontend/charts.js')).namespace;chart.drawAll();assert.ok(h.ctxOf('decay').arcs.length>0);
});
check('FE-11: spectrum click uses the same live axis as drawing',async()=>{
 const h=await loaded();h.patchStatus(s=>{s.calibration.wavelengths_nm=null;s.live_axis=Array.from({length:1024},(_,i)=>500+300*i/1023);return s;});await h.tick(600);const chart=(await h.loadModule('frontend/charts.js')).namespace;chart.drawAll();h.store().wave=600;chart.clickWave('spectrum',400);assert.notEqual(h.store().wave,600);
});
check('FE-12: reusing an open dialog does not throw',async()=>{
 const h=createHarness();await bootApp(h);await h.click('path');assert.equal(h.get('modal').open,true);await h.click('zero');assert.equal(h.get('modal').open,true);assert.equal(h.get('modalTitle').textContent,'Set delay zero');
});
check('FE-13: selected saturated frames have a clear time-window hint',async()=>{
 const records=clone(fx.records);records[0].saturated_pixels=1;const h=await loaded({records});await h.navigate('analysis');assert.match(h.get('fitStatus').textContent,/saturated frames/);assert.match(h.get('fitStatus').textContent,/Fit start/);
});
check('F4/E10: background operation has an explicit stop route',async()=>{
 const h=await loaded({status:{busy:'background'}});assert.equal(h.get('stop').disabled,false);await h.click('stop');assert.ok(h.lastRequest('/api/v1/backgrounds/stop'));
});
check('request timeout releases a hung fetch',async()=>{
 let abort,timer;const context=vm.createContext({Response,AbortController,crypto:{randomUUID:()=>''},setTimeout:fn=>(timer=fn,1),clearTimeout:()=>{},fetch:(url,options)=>new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>{const error=new Error();error.name='AbortError';reject(error);});abort=options.signal;})});
 const module=new vm.SourceTextModule(fs.readFileSync(path.join(ROOT,'frontend/api.js'),'utf8'),{context});await module.link(()=>{});await module.evaluate();const request=module.namespace.api('/status');assert.ok(abort);timer();await assert.rejects(request,/timed out/);
});

(async()=>{const results=[];for(const item of checks){try{await item.fn();results.push({id:item.id,passed:true});}catch(error){results.push({id:item.id,passed:false,error:error.stack});console.error(item.id,error);}}
 const output={scope:'BUG_REPORT.md regressions only; no browser or App',passed:results.every(r=>r.passed),checks:results};fs.mkdirSync(path.join(ROOT,'test-output'),{recursive:true});fs.writeFileSync(path.join(ROOT,'test-output/reported-bugs-frontend.json'),JSON.stringify(output,null,2));console.log(JSON.stringify({passed:output.passed,checks:results.length,failed:results.filter(r=>!r.passed).map(r=>r.id)}));if(!output.passed)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});

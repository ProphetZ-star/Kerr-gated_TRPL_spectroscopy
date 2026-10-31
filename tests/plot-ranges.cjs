'use strict';
const assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {createHarness,bootApp,fixtures,clone,ROOT}=require('../qa/frontend/lib/harness.cjs');
const all=['spectrum','map','decay','analysisCurve','analysisResidual','calImage','calSpectrum','calMapping','calResidual'];
const checks=[];const check=(name,fn)=>checks.push({name,fn});
async function boot(options={}){const h=createHarness(options);await bootApp(h);await h.settle();return h;}
async function ranges(h){return (await h.loadModule('frontend/plot-ranges.js')).namespace;}
async function show(h,id){await h.navigate(id.startsWith('cal')?'calibration':id.startsWith('analysis')?'analysis':'acquisition');await h.settle();}
async function redraw(h,id){await (await h.loadModule(id.startsWith('cal')?'frontend/calibration.js':id.startsWith('analysis')?'frontend/analysis.js':'frontend/charts.js')).namespace[id.startsWith('cal')?'drawCalibration':id.startsWith('analysis')?'drawAnalysis':'drawAll']();}
function field(h,id){return h.get('modalBody').children[0].querySelector('#'+id);}
function setField(h,axis,min,max){const auto=field(h,'axis'+axis+'Auto');auto.checked=false;auto.onchange();field(h,'axis'+axis+'Min').value=String(min);field(h,'axis'+axis+'Max').value=String(max);}
const apply=h=>h.get('modalBody').children[0].children.at(-1).children[1].onclick();
const clear=(h,id)=>{h.ctxOf(id).ops.length=0;h.ctxOf(id).strokes.length=0;};

check('all nine plots expose working min/max editors',async()=>{
 for(const id of all){const h=await boot();await show(h,id);await h.click('axes-'+id);assert.equal(h.get('modal').open,true,id);
  setField(h,'X',100,200);setField(h,'Y',10,20);apply(h);const state=(await ranges(h)).plotState(id);
  assert.equal(state.range.xmin,100,id);assert.equal(state.range.xmax,200,id);
  assert.equal(Math.min(state.range.ymin,state.range.ymax),10,id);assert.equal(Math.max(state.range.ymin,state.range.ymax),20,id);assert.equal(h.get('modal').open,false,id);
 }
});
check('invalid limits are rejected atomically and Reset restores auto',async()=>{
 const h=await boot(),api=await ranges(h);await h.click('axes-spectrum');setField(h,'X',600,600);setField(h,'Y',5,20);apply(h);
 assert.equal(h.get('modal').open,true);assert.match(field(h,'axisError').textContent,/maximum greater/);assert.equal(api.plotState('spectrum').manual.y,false);
 setField(h,'X',550,650);apply(h);assert.equal(api.plotState('spectrum').manual.x,true);
 await h.click('axes-spectrum');h.get('modalBody').children[0].children.at(-1).children[0].onclick();assert.equal(api.plotState('spectrum').manual.x,false);
 await h.click('axes-spectrum');setField(h,'X','',650);apply(h);assert.match(field(h,'axisError').textContent,/both minimum/);
});
check('shared map/decay and residual axes stay linked',async()=>{
 const h=await boot(),api=await ranges(h);api.setPlotLimits('map',{y:{min:-1,max:8}});await redraw(h,'map');
 assert.equal(api.plotState('decay').range.ymin,-1);assert.equal(api.plotState('decay').range.ymax,8);
 await show(h,'analysisCurve');api.setPlotLimits('analysisResidual',{x:{min:1,max:7}});await redraw(h,'analysisCurve');assert.equal(api.plotState('analysisCurve').range.xmin,1);assert.equal(api.plotState('analysisCurve').range.xmax,7);
 await show(h,'calMapping');api.setPlotLimits('calMapping',{x:{min:200,max:700}});await redraw(h,'calMapping');assert.equal(api.plotState('calResidual').range.xmin,200);assert.equal(api.plotState('calResidual').range.xmax,700);
});
check('linear/log and nm/pixel limits remain separate',async()=>{
 const h=await boot(),api=await ranges(h);h.store().snapshot.live_axis=Array.from({length:1024},(_,i)=>500+300*i/1023);await redraw(h,'spectrum');
 api.setPlotLimits('spectrum',{x:{min:550,max:650},y:{min:100,max:2000}});await redraw(h,'spectrum');
 h.document.querySelectorAll('#spectrumScale button')[1].onclick();assert.equal(api.plotState('spectrum').manual.y,false);assert.equal(api.plotState('spectrum').range.xmin,550);
 h.document.querySelectorAll('#spectrumScale button')[0].onclick();assert.equal(api.plotState('spectrum').range.ymin,100);
 h.store().snapshot.live_axis=null;await redraw(h,'spectrum');assert.equal(api.plotState('spectrum').manual.x,false);assert.equal(api.plotState('spectrum').range.xmax,1023);
});
check('editing display limits leaves experiment records and fit inputs unchanged',async()=>{
 const fx=fixtures(),h=await boot({status:{...clone(fx.initial_status),run:clone(fx.run)},records:clone(fx.records)}),api=await ranges(h);
 const before=JSON.stringify({run:h.store().run,records:h.store().records,wave:h.store().wave,window:h.store().halfWindow}),count=h.requests.length;
 api.setPlotLimits('map',{x:{min:550,max:650},y:{min:0,max:10}});await redraw(h,'map');
 assert.equal(JSON.stringify({run:h.store().run,records:h.store().records,wave:h.store().wave,window:h.store().halfWindow}),before);assert.equal(h.requests.length,count);
});
check('spectrum clicks use the edited wavelength range',async()=>{
 const h=await boot(),api=await ranges(h);h.store().snapshot.live_axis=Array.from({length:1024},(_,i)=>500+300*i/1023);await redraw(h,'spectrum');api.setPlotLimits('spectrum',{x:{min:610,max:630}});await redraw(h,'spectrum');
 await h.click('spectrum',{clientX:76+(820-22-76)/2});assert.equal(h.store().wave,620);
});
check('CCD zoom transforms the image and ROI clicks without grid overlays',async()=>{
 const h=await boot();await show(h,'calImage');assert.equal(h.findStrokes('calImage','#grid').length,0);
 await h.click('calCapture');const api=await ranges(h);api.setPlotLimits('calImage',{x:{min:100,max:400},y:{min:50,max:150}});clear(h,'calImage');await redraw(h,'calImage');
 const state=api.plotState('calImage');assert.equal(state.range.ymin,150);assert.equal(state.range.ymax,50);
 const image=h.opsOf('calImage').find(x=>x.op==='drawImage');assert.ok(image);assert.ok(image.args[3]>820-22-66);assert.ok(image.args[2]<13);assert.equal(h.findStrokes('calImage','#grid').length,0);
 await h.click('calImage',{clientY:13+.7*(300-32-13),clientX:400});const extraction=h.lastRequest('/api/v1/calibration/extract');assert.equal(extraction.body.row_start,80);assert.equal(extraction.body.row_end,160);
});
check('calibration peak assignment uses the edited pixel coordinates',async()=>{
 const h=await boot();await show(h,'calSpectrum');await h.click('calCapture');const api=await ranges(h);api.setPlotLimits('calSpectrum',{x:{min:100,max:200}});await redraw(h,'calSpectrum');
 await h.click('calSpectrum',{clientX:66+(820-22-66)/2});const inputs=h.get('calPairs').children[0].querySelectorAll('input');assert.equal(Number(inputs[1].value),150);
});
check('all plot backgrounds match, and spectrum/decay use only coarse grid lines',async()=>{
 const h=await boot();for(const id of all){await show(h,id);clear(h,id);await redraw(h,id);assert.equal(h.opsOf(id).find(x=>x.op==='fillRect').color,'#plot',id);assert.equal(h.findStrokes(id,'#muted').length,0,id);}
 await show(h,'spectrum');for(const id of ['spectrum','decay']){clear(h,id);await redraw(h,id);const lines=h.findStrokes(id,'#grid');assert.equal(lines.length,10,id);assert.ok(lines.every(s=>s.points.length===2));}
 assert.equal(h.findStrokes('map','#grid').length,0);
});
check('acquisition preview and defaults work without Additional settings',async()=>{
 const h=await boot();await h.click('preview');assert.ok(h.lastRequest('/api/v1/preview/start'));await h.click('preview');assert.ok(h.lastRequest('/api/v1/preview/stop'));
 await h.click('run');const request=h.requests.findLast(x=>x.url==='/api/v1/runs'&&x.method==='POST');assert.equal(request.body.frames_per_point,1);assert.equal(request.body.speedup,50);assert.equal(request.body.fault.kind,'none');
});
check('Analysis wavelength/window editing no longer needs deleted controls',async()=>{
 const h=await boot();h.setValue('analysisWave','612.345');await h.change('analysisWave');h.setValue('analysisWindow','2.5');await h.change('analysisWindow');assert.equal(h.store().wave,612.345);assert.equal(h.store().halfWindow,2.5);
});
check('opening an old fault-injection run cannot silently inject faults into new scans',async()=>{
 const fx=fixtures(),run=clone(fx.run);run.request.fault={kind:'camera_disconnect',point_index:0};
 const h=await boot({status:{...clone(fx.initial_status),busy:null,run},records:clone(fx.records)});h.setChecked('autosave',false);await h.click('run');
 assert.equal(h.requests.findLast(x=>x.url==='/api/v1/runs'&&x.method==='POST').body.fault.kind,'none');
});
check('an editor cannot apply old nm limits after plot units change',async()=>{
 const h=await boot();h.store().snapshot.live_axis=Array.from({length:1024},(_,i)=>500+300*i/1023);await redraw(h,'spectrum');await h.click('axes-spectrum');setField(h,'X',550,650);
 h.store().snapshot.live_axis=null;await redraw(h,'spectrum');apply(h);assert.match(field(h,'axisError').textContent,/units changed/);assert.equal(h.get('modal').open,true);
});
(async()=>{const results=[];for(const c of checks){try{await c.fn();results.push({name:c.name,passed:true});}catch(e){results.push({name:c.name,passed:false,error:e.stack});console.error(c.name,e);}}
 const output={passed:results.every(r=>r.passed),checks:results};fs.mkdirSync(path.join(ROOT,'test-output'),{recursive:true});fs.writeFileSync(path.join(ROOT,'test-output/plot-ranges.json'),JSON.stringify(output,null,2));console.log(JSON.stringify({passed:output.passed,checks:results.length}));if(!output.passed)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});

// Presentation-only limits. Shared keys keep linked plots on the same scale.
const definitions={
 spectrum:{title:'Live Spectrum',xKey:'spectrum:pixel',yKey:'spectrum:counts',xLabel:'Pixel',yLabel:'CCD / counts'},
 map:{title:'TRPL Map',xKey:'map:nm',yKey:'trpl:delay',xLabel:'Wavelength / nm',yLabel:'Delay / ps'},
 decay:{title:'TRPL Decay',xKey:'decay:counts',yKey:'trpl:delay',xLabel:'Intensity / counts per pixel',yLabel:'Delay / ps'},
 analysisCurve:{title:'Decay & fit',xKey:'analysis:delay',yKey:'analysis:counts',xLabel:'Delay / ps',yLabel:'Counts / s'},
 analysisResidual:{title:'Fit residuals',xKey:'analysis:delay',yKey:'analysis:residual',xLabel:'Delay / ps',yLabel:'Residual / counts per s'},
 calImage:{title:'CCD frame',xKey:'ccd:pixel',yKey:'ccd:row',xLabel:'CCD pixel',yLabel:'CCD row'},
 calSpectrum:{title:'Extracted spectrum',xKey:'calSpectrum:pixel',yKey:'calSpectrum:counts',xLabel:'CCD pixel',yLabel:'CCD / counts'},
 calMapping:{title:'Pixel → wavelength',xKey:'mapping:pixel',yKey:'mapping:nm',xLabel:'CCD pixel',yLabel:'Wavelength / nm'},
 calResidual:{title:'Mapping residuals',xKey:'mapping:pixel',yKey:'mapping:residual',xLabel:'CCD pixel',yLabel:'Residual / nm'},
};
const limits=new Map(),plots=new Map();
const $=id=>document.getElementById(id);
function validate(value){
 if(value===null)return;
 if(!value||!Number.isFinite(value.min)||!Number.isFinite(value.max)||!(value.max>value.min)||!Number.isFinite(value.max-value.min))
  throw Error('Axis limits must be finite numbers with maximum greater than minimum.');
}
export function plotRange(id,automatic,metadata={}){
 const meta={...definitions[id],...metadata},range={...automatic};
 for(const axis of ['x','y']){
  const manual=limits.get(meta[axis+'Key']);
  if(manual){const reverse=automatic[axis+'min']>automatic[axis+'max'];range[axis+'min']=reverse?manual.max:manual.min;range[axis+'max']=reverse?manual.min:manual.max;}
 }
 plots.set(id,{id,meta,automatic:{...automatic},range});return range;
}
export function plotState(id){
 const state=plots.get(id);if(!state)return null;
 return {...state,manual:{x:limits.has(state.meta.xKey),y:limits.has(state.meta.yKey)}};
}
export function setPlotLimits(id,{x=null,y=null}){
 const state=plots.get(id);if(!state)throw Error('This plot is not ready yet.');
 validate(x);validate(y);
 for(const [axis,value]of [['x',x],['y',y]]){const key=state.meta[axis+'Key'];if(value===null)limits.delete(key);else limits.set(key,{...value});}
}
const endpoints=new Map();let editor=null;
export function registerAxisEndpoints(q,{x=true,y=true,xY=q.p.b+18,yX=q.p.l-7,format=v=>Number(v.toPrecision(5)).toString()}={}){
 const hit=[];
 const add=(axis,value,cx,cy,right=false)=>{const label=format(value),width=Math.max(24,q.ctx.measureText?.(label).width??label.length*7);hit.push({axis,bound:value===Math.min(q[axis+'min'],q[axis+'max'])?'min':'max',cx:right?cx-width/2:cx,cy,w:width+10,h:22});};
 if(x)for(const value of [q.xmin,q.xmax])add('x',value,q.x(value),xY-4);
 if(y)for(const value of [q.ymin,q.ymax])add('y',value,yX,q.y(value),true);
 endpoints.set(q.id,hit);
}
function closeEditor(){if(editor){editor.wrapper.remove?.();editor.wrapper.hidden=true;editor=null;}}
export function setAxisEndpoint(id,axis,bound,value){
 const state=plotState(id);if(!state)throw Error('Plot is not ready.');
 const next={x:limits.get(state.meta.xKey)??null,y:limits.get(state.meta.yKey)??null};
 if(value===null)next[axis]=null;
 else{const range=state.range;next[axis]={min:Math.min(range[axis+'min'],range[axis+'max']),max:Math.max(range[axis+'min'],range[axis+'max'])};next[axis][bound]=value;}
 setPlotLimits(id,next);
}
export function initPlotRanges(redraw){
 for(const id of Object.keys(definitions)){
  const canvas=$(id);canvas.title='Click an axis minimum or maximum to edit. Enter applies; Esc cancels.';
  canvas.addEventListener('click',event=>{
   const rect=canvas.getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top;
   const target=(endpoints.get(id)??[]).filter(e=>Math.abs(px-e.cx)<=e.w/2&&Math.abs(py-e.cy)<=e.h/2).sort((a,b)=>Math.hypot(px-a.cx,py-a.cy)-Math.hypot(px-b.cx,py-b.cy))[0];
   if(!target)return;event.preventDefault?.();event.stopImmediatePropagation?.();closeEditor();
   const state=plotState(id),wrapper=document.createElement('div'),input=document.createElement('input'),auto=document.createElement('button'),error=document.createElement('span');
   wrapper.id='axisInlineEditor';wrapper.className='axis-inline-editor';input.id='axisInlineValue';input.type='number';input.step='any';input.value=String(target.bound==='min'?Math.min(state.range[target.axis+'min'],state.range[target.axis+'max']):Math.max(state.range[target.axis+'min'],state.range[target.axis+'max']));input.setAttribute('aria-label',target.axis.toUpperCase()+' '+target.bound+' '+state.meta[target.axis+'Label']);auto.textContent='Auto';auto.title='Restore this axis to automatic limits';error.id='axisInlineError';error.setAttribute('role','alert');
   wrapper.style.left=Math.max(3,Math.min((window.innerWidth||1280)-184,rect.left+target.cx-66))+'px';wrapper.style.top=Math.max(3,rect.top+target.cy-14)+'px';wrapper.append(input,auto,error);document.body.append(wrapper);editor={wrapper,input,auto};input.focus();input.select?.();
   const apply=value=>{try{const current=plotState(id);if(current.meta[target.axis+'Key']!==state.meta[target.axis+'Key'])throw Error('Axis units changed. Click the current endpoint again.');setAxisEndpoint(id,target.axis,target.bound,value);closeEditor();redraw();}catch(problem){error.textContent=problem.message;input.setAttribute('aria-invalid','true');}};
   input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault?.();apply(input.value.trim()===''?null:Number(input.value));}else if(e.key==='Escape'){e.preventDefault?.();closeEditor();}};
   input.onblur=e=>{if(e.relatedTarget!==auto)setTimeout(()=>{if(editor?.input===input)closeEditor();},0);};auto.onclick=()=>apply(null);
  },true);
 }
 window.addEventListener('pointerdown',event=>{if(editor&&event.target!==editor.input&&event.target!==editor.auto)closeEditor();},true);
}

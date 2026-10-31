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
export function initPlotRanges(openModal,redraw){
 for(const id of Object.keys(definitions))$('axes-'+id).onclick=()=>{
  const state=plotState(id);if(!state)return;
  const box=document.createElement('div'),fields={};box.className='axis-form';
  for(const axis of ['x','y']){
   const section=document.createElement('fieldset'),legend=document.createElement('legend'),autoLabel=document.createElement('label'),auto=document.createElement('input'),grid=document.createElement('div');
   legend.textContent=axis.toUpperCase()+' · '+state.meta[axis+'Label'];auto.type='checkbox';auto.checked=!state.manual[axis];auto.id='axis'+axis.toUpperCase()+'Auto';autoLabel.className='row';autoLabel.append(auto,document.createTextNode('Auto'));grid.className='grid';
   const inputs=[];
   for(const [bound,label]of [['min','Minimum'],['max','Maximum']]){
    const holder=document.createElement('label'),input=document.createElement('input');holder.textContent=label;input.type='number';input.step='any';input.id='axis'+axis.toUpperCase()+(bound==='min'?'Min':'Max');input.setAttribute('aria-label',axis.toUpperCase()+' '+label.toLowerCase());
    const a=state.range[axis+'min'],b=state.range[axis+'max'];input.value=String(bound==='min'?Math.min(a,b):Math.max(a,b));input.disabled=auto.checked;holder.append(input);grid.append(holder);inputs.push(input);
   }
   auto.onchange=()=>inputs.forEach(input=>input.disabled=auto.checked);fields[axis]={auto,inputs};section.append(legend,autoLabel,grid);box.append(section);
  }
  const error=document.createElement('p'),buttons=document.createElement('div'),reset=document.createElement('button'),apply=document.createElement('button');error.id='axisError';error.className='error';error.setAttribute('role','alert');buttons.className='modal-foot';reset.textContent='Reset to auto';apply.textContent='Apply';apply.className='primary';
  reset.onclick=()=>{setPlotLimits(id,{x:null,y:null});$('modal').close();redraw();};
  apply.onclick=()=>{
   try{const current=plotState(id);if(current.meta.xKey!==state.meta.xKey||current.meta.yKey!==state.meta.yKey)throw Error('Plot units changed. Close and reopen axis limits.');const next={};for(const axis of ['x','y']){const {auto,inputs}=fields[axis];if(!auto.checked&&inputs.some(input=>input.value.trim()===''))throw Error('Enter both minimum and maximum, or use Auto.');next[axis]=auto.checked?null:{min:Number(inputs[0].value),max:Number(inputs[1].value)};}setPlotLimits(id,next);$('modal').close();redraw();}
   catch(problem){error.textContent=problem.message;}
  };
  buttons.append(reset,apply);box.append(error,buttons);openModal(state.meta.title+' · Axis limits',box);
 };
}

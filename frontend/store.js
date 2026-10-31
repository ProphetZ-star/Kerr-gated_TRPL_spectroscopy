export const store={snapshot:null,run:null,records:[],wave:600,halfWindow:5,round:'all',subtract:false,fit:null,specScale:'linear',mapScale:'log',selectedMode:'scan',online:false};
export function axisForRun(){return store.run?.metadata?.calibration?.wavelengths_nm??null;}
export function grouped(){
 const groups=new Map();
 for(const r of store.records){
  if(store.round!=='all'&&r.round_index!==Number(store.round))continue;
  if(!groups.has(r.point_index))groups.set(r.point_index,{delay:r.delay_ps,count:0,spectrum:new Float64Array(r.spectrum.length)});
  const g=groups.get(r.point_index);g.count++;
  r.spectrum.forEach((v,i)=>g.spectrum[i]+=v);
 }
 for(const g of groups.values())for(let i=0;i<g.spectrum.length;i++)g.spectrum[i]/=g.count;
 return groups;
}
export function backgroundForRun(){
 const bg=store.run?.metadata?.background;
 return store.subtract&&bg&&bg.exposure_s===store.run?.request?.exposure_s?bg.spectrum:null;
}

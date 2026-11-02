export const store={snapshot:null,run:null,records:[],wave:600,halfWindow:5,round:'all',subtract:false,fit:null,specScale:'linear',mapScale:'log',selectedMode:'scan',online:false,outputDirectory:'data'};
store.analysis={imported:false,run:null,records:[],wave:600,halfWindow:5,fit:null};
export function analysisContext(){return store.analysis.imported?store.analysis:store;}
export function axisForRun(){return store.run?.metadata?.calibration?.wavelengths_nm??null;}
let groupCache=null;
export function grouped(){
 const records=store.records,run=store.run?.run_id;
 if(!groupCache||groupCache.records!==records||groupCache.run!==run||groupCache.round!==store.round||records.length<groupCache.count)
  groupCache={records,run,round:store.round,count:0,processed:0,groups:new Map()};
 const cache=groupCache,groups=cache.groups,touched=new Set();
 for(let j=cache.count;j<records.length;j++){
  const r=records[j];cache.processed++;
  if(store.round!=='all'&&r.round_index!==Number(store.round))continue;
  if(!groups.has(r.point_index))groups.set(r.point_index,{delay:r.delay_ps,count:0,sum:new Float64Array(r.spectrum.length),spectrum:new Float64Array(r.spectrum.length),bins:new Float64Array(128),version:0,mapMax:1});
  const g=groups.get(r.point_index);g.count++;touched.add(g);
  for(let i=0;i<r.spectrum.length;i++)g.sum[i]+=r.spectrum[i];
 }
 for(const g of touched){
  const sum=g.sum,spectrum=g.spectrum,bins=g.bins,count=g.count;let max=1;
  for(let bin=0;bin<128;bin++){
   const offset=bin*8;let value=0;
   for(let i=offset;i<offset+8;i++){const v=sum[i]/count;spectrum[i]=v;value+=v;}
   bins[bin]=value/8;max=Math.max(max,spectrum[offset]);
  }
  g.mapMax=max;
  g.version++;
 }
 if(cache.count!==records.length)groups.revision=(groups.revision??0)+1;
 cache.count=records.length;return groups;
}
export function aggregationStats(){return groupCache?{processed:groupCache.processed,groups:groupCache.groups.size,revision:groupCache.groups.revision}:null;}
export function backgroundForRun(){
 const bg=store.run?.metadata?.background;
 return store.subtract&&bg&&bg.exposure_s===store.run?.request?.exposure_s?bg.spectrum:null;
}

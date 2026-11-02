// Read only the Analysis context, averaging exposure-normalized frames per delay.
const cache=new WeakMap();
export function spectraFor(context){
 const records=context.records??[],run=context.run?.run_id,axis=context.run?.metadata?.calibration?.wavelengths_nm;
 let item=cache.get(context);
 if(!item||item.records!==records||item.run!==run||records.length<item.count){item={records,run,count:0,processed:0,groups:new Map()};cache.set(context,item);}
 if(item.count===records.length)return item.groups;
 const groups=item.groups,touched=new Set();let added=false;
 for(let j=item.count;j<records.length;j++){
  const record=records[j],spectrum=record.spectrum,exposure=record.exposure_s,delay=record.delay_ps;item.processed++;
  if(!Number.isFinite(delay)||!Number.isFinite(exposure)||exposure<=0||!spectrum?.length||axis&&spectrum.length!==axis.length)continue;
  if(!spectrum.every(Number.isFinite))continue;
  let group=groups.get(delay);
  if(!group){group={delay,count:0,sum:new Float64Array(spectrum.length),values:new Float64Array(spectrum.length)};groups.set(delay,group);added=true;}
  if(group.values.length!==spectrum.length)continue;
  for(let i=0;i<spectrum.length;i++)group.sum[i]+=spectrum[i]/exposure;
  group.count++;touched.add(group);
 }
 for(const group of touched)for(let i=0;i<group.values.length;i++)group.values[i]=group.sum[i]/group.count;
 item.groups=added?new Map([...groups].sort(([a],[b])=>a-b)):groups;
 item.groups.revision=(groups.revision??0)+1;item.count=records.length;return item.groups;
}
export function spectraStats(context){const item=cache.get(context);return item?{processed:item.processed,revision:item.groups.revision}:null;}
export function delayFraction(delay,min,max){return max>min?(delay-min)/(max-min):.5;}

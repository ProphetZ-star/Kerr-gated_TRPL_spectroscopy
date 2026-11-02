export function mergeStatus(previous,update){
 if(!update._sync)return update; // Compatibility with a service without compact status.
 const same=previous?.worker_session_id===update.worker_session_id;
 if(!update._sync.full&&!same)throw Error('A full status sync is required');
 const base=same?previous:{};
 const run=update.run===null?null:update.run?.run_id===base.run?.run_id?{...base.run,...update.run}:update.run;
 if(run&&(!run.metadata||!run.plan||!run.request))throw Error('Experiment metadata is missing; a full status sync is required');
 return {...base,...update,run};
}
export function createStatusSync(api,previous,clock=()=>typeof performance==='undefined'?Date.now():performance.now()){
 let lastFull=-Infinity;
 return async(force=false)=>{
  const before=previous(),full=force||clock()-lastFull>=60000||!before?._sync;
  let path='/status?compact=1';
  if(!full)path+='&since='+encodeURIComponent(before._sync.token)+'&latest_version='+before._sync.latest_version;
  const update=await api(path);
  try{const merged=mergeStatus(before,update);if(update._sync?.full||!update._sync)lastFull=clock();return merged;}
  catch(error){if(full)throw error;const update=await api('/status?compact=1');lastFull=clock();return mergeStatus(null,update);}
 };
}

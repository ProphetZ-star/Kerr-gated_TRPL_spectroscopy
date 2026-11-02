// Coalesce streaming/polling bursts, while user-triggered renders remain immediate.
export function createRenderScheduler(render,{interval=100,clock=()=>typeof performance==='undefined'?Date.now():performance.now(),defer=setTimeout,cancel=clearTimeout}={}){
 let last=-Infinity,timer=null;
 const run=()=>{timer=null;last=clock();render();};
 return {request(){const delay=Math.max(0,interval-(clock()-last));if(!delay){if(timer!==null)cancel(timer);run();}else if(timer===null)timer=defer(run,delay);},
  flush(){if(timer!==null)cancel(timer);run();}};
}

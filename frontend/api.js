let token='',sessionRequest=null;
export class ApiError extends Error{constructor(message,status=0){super(message);this.status=status;}}
async function fetchLocal(url,options={}){
 const controller=typeof AbortController==='function'?new AbortController():null;
 const timer=controller?setTimeout(()=>controller.abort(),50000):null;
 try{return await fetch(url,{...options,...(controller?{signal:controller.signal}:{})});}
 catch(error){if(error.name==='AbortError')throw new ApiError('Local service request timed out. It may still be finishing.');throw error;}
 finally{if(timer!==null)clearTimeout(timer);}
}
export async function responseError(response){
 const data=await response.json().catch(()=>null),detail=data?.detail;
 const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map(d=>(d.loc??[]).filter(p=>p!=='body').join('.')+': '+(d.msg??'Invalid value')).join('; '):response.statusText||'HTTP '+response.status;
 return new ApiError(message,response.status);
}
export async function initialize(){
 if(!sessionRequest)sessionRequest=(async()=>{const r=await fetchLocal('/api/v1/session');if(!r.ok)throw await responseError(r);token=(await r.json()).token;})().finally(()=>{sessionRequest=null;});
 return sessionRequest;
}
async function request(path,body,retry=true){
 const used=token,options={method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Session-Token':used},body:body===undefined?undefined:JSON.stringify(body)};
 const r=await fetchLocal('/api/v1'+path,options);
 if(r.ok)return r;
 const error=await responseError(r);
 if(retry&&r.status===403&&error.message==='Missing local session token'){
  if(token===used)await initialize();
  return request(path,body,false);
 }
 throw error;
}
export async function api(path,body){return (await request(path,body)).json();}
export async function download(path){return request(path);}
export const requestId=()=>crypto.randomUUID();
export function subscribe(onMessage,onConnection){
 let stopped=false,socket;
 const open=()=>{if(stopped)return;socket=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/api/v1/events');socket.onopen=()=>onConnection(true);socket.onmessage=e=>onMessage(JSON.parse(e.data));socket.onclose=async e=>{onConnection(false);if(stopped)return;if(e.code===1008)try{await initialize();}catch{}if(!stopped)setTimeout(open,1500);};};open();
 return ()=>{stopped=true;socket?.close();};
}

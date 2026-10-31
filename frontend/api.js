let token='';
export async function initialize(){const r=await fetch('/api/v1/session');if(!r.ok)throw Error('Local service unavailable');token=(await r.json()).token;}
export async function api(path,body){
 const r=await fetch('/api/v1'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:body===undefined?undefined:JSON.stringify(body)});
 if(!r.ok){const data=await r.json().catch(()=>({detail:r.statusText}));throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
 return r.json();
}
export const requestId=()=>crypto.randomUUID();
export function subscribe(onMessage,onConnection){
 let stopped=false,socket;
 const open=()=>{if(stopped)return;socket=new WebSocket(`ws://${location.host}/api/v1/events`);socket.onopen=()=>onConnection(true);socket.onmessage=e=>onMessage(JSON.parse(e.data));socket.onclose=()=>{onConnection(false);if(!stopped)setTimeout(open,1500);};};open();
 return ()=>{stopped=true;socket?.close();};
}

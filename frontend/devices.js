import {api} from './api.js';
import {store} from './store.js';
const $=id=>document.getElementById(id);
const TRANSPORTS={usb:'USB',serial:'RS-232 / COM',manual:'Manual',tcpip:'TCP/IP',visa:'VISA'};
export function deviceAvailability(snapshot,online){
 const a=snapshot?.available??{};
 return {stage:!!online&&!!a.stage,camera:!!online&&!!a.camera,spectrometer:!!online&&!!a.spectrometer,
  spectrograph:!!online&&!!a.spectrograph_config,spectralControls:online?(a.spectrograph_controls??[]):[],
  scan:!!online&&!!a.scan,preview:!!online&&!!a.preview,background:!!online&&!!a.background,calibration:!!online&&!!a.calibration_capture};
}
export function renderDeviceConnections(){
 const s=store.snapshot,busy=!!s?.busy,sim=s?.source_mode==='simulation',devices=s?.devices??{};
 for(const [id,names] of [['stageConnection',['stage']],['detectorConnection',['camera','spectrometer']]]){
  const b=$(id);if(!b)continue;
  const physical=names.filter(n=>!devices[n]?.capabilities?.manual),count=physical.filter(n=>devices[n]?.connected).length;
  b.disabled=!store.online||busy;
  b.dataset.state=sim?'simulated':count===physical.length?'connected':count?'partial':'disconnected';
  b.textContent=sim?'● Simulated ▾':names.length===1?(count?'● C-663.12 ▾':'○ Connect…'):devices.spectrometer?.state==='manual'?`CCD ${devices.camera?.connected?'on':'off'} · Manual ▾`:`${count}/${physical.length} connected ▾`;
  b.title=names.map(n=>`${n}: ${devices[n]?.error??(devices[n]?.state==='manual'?'Manual settings':devices[n]?.connected?'Connected':'Disconnected')}`).join('\n');
 }
 const badge=$('hardwareMode'),physical=Object.values(devices).filter(d=>!d.capabilities?.manual),count=physical.filter(d=>d.connected).length;
 badge.textContent=!store.online?'SERVICE OFFLINE':sim?'SIMULATION ▾':`HARDWARE · ${count}/${physical.length} connected ▾`;
 badge.disabled=busy||!store.online;
}
function option(select,value,label){const el=document.createElement('option');el.value=value;el.textContent=label;select.append(el);}
function field(parent,title,input){const label=document.createElement('label');label.textContent=title;label.append(input);parent.append(label);return input;}
export function initDeviceConnections({modal,action,onSourceChanged}){
 $('hardwareMode').onclick=()=>{
  if(!store.online||store.snapshot?.busy)return;
  const box=document.createElement('div'),p=document.createElement('p'),buttons=document.createElement('div');p.textContent='Hardware mode is available without instruments. Each connection enables only its own controls.';buttons.className='source-options';
  for(const [mode,label]of [['hardware','Hardware'],['simulation','Simulation']]){const b=document.createElement('button');b.textContent=label;b.classList.toggle('primary',store.snapshot?.source_mode===mode);b.onclick=()=>action(async()=>{await api('/devices/connect',{mode});$('modal').close();onSourceChanged?.();});buttons.append(b);}
  box.append(p,buttons);modal('Acquisition source',box);
 };
 for(const [id,names,title]of [['stageConnection',['stage'],'Delay stage connection'],['detectorConnection',['camera','spectrometer'],'Detection connections']])$(id).onclick=async()=>{
  if(!store.online||store.snapshot?.busy)return;
  const box=document.createElement('div');box.className='device-connections';modal(title,box);
  if(store.snapshot?.source_mode==='simulation'){
   const p=document.createElement('p');p.textContent='Simulation is active. Switch to Hardware to configure instruments.';const b=document.createElement('button');b.textContent='Use Hardware';b.className='primary';b.onclick=()=>action(async()=>{await api('/devices/connect',{mode:'hardware'});$('modal').close();onSourceChanged?.();});box.append(p,b);return;
  }
  try{const catalog=await api('/devices/drivers');for(const name of names)buildCard(box,name,catalog.drivers.filter(d=>d.family===name));}
  catch(e){const p=document.createElement('p');p.className='error';p.textContent=e.message;box.append(p);}
 };
 function buildCard(box,name,profiles){
  const link=store.snapshot?.devices?.[name]??{},saved=link.config??{},drafts={};
  const card=document.createElement('section'),heading=document.createElement('h3'),message=document.createElement('p'),fields=document.createElement('fieldset'),grid=document.createElement('div'),buttons=document.createElement('div'),hint=document.createElement('p');
  card.className='device-card';heading.textContent={stage:'Delay stage · PI C-663.12',camera:'CCD · USB / SDK2',spectrometer:'Spectrometer'}[name];grid.className='grid';buttons.className='device-actions';hint.className='device-hint';message.className='device-message';message.setAttribute('role','status');
  const driver=field(fields,'Instrument / driver',document.createElement('select'));
  for(const d of profiles)option(driver,d.id,d.label);driver.value=profiles.some(d=>d.id===saved.driver)?saved.driver:profiles[0].id;
  const transport=field(fields,'Connection',document.createElement('select'));
  const connect=document.createElement('button'),disconnect=document.createElement('button'),refresh=document.createElement('button');connect.className='primary';disconnect.textContent='Disconnect';
  let inputs={},profile,working=false;
  function payload(){const p={device:name,driver:driver.value,transport:transport.value};for(const [k,input]of Object.entries(inputs))p[k]=['baudrate','camera_index'].includes(k)?Number(input.value):input.value.trim();return p;}
  function controls(){const current=store.snapshot?.devices?.[name],connected=!!current?.connected;fields.disabled=working||connected;connect.disabled=working||connected||!profile.implemented;disconnect.disabled=working||!connected;disconnect.hidden=profile.manual;refresh.disabled=working||connected;refresh.hidden=profile.manual||name==='spectrometer'&&transport.value!=='serial';connect.textContent=profile.manual?'Use manual settings':profile.implemented?'Connect':'Driver required';}
  function input(key,title,value='',select=false){const el=field(grid,title,document.createElement(select?'select':'input'));inputs[key]=el;if(!select){el.value=value;el.autocomplete='off';}return el;}
  async function loadPorts(){const list=await api('/devices/ports');if(list.error)throw Error(list.error);const port=inputs.port,selected=port.value;port.replaceChildren();option(port,'','Choose a port…');for(const p of list.ports)option(port,p.port,p.label);if(selected&&!list.ports.some(p=>p.port===selected))option(port,selected,selected+' (not detected)');port.value=selected;}
  function drawConnection(cfg){
   inputs={};grid.replaceChildren();hint.textContent=profile.note;
   if(name==='spectrometer')input('model','Instrument model',cfg.model??profile.defaults.model??profile.label);
   const t=transport.value;
   if(t==='serial'){
    const port=input('port','COM port','',true);option(port,'','Choose a port…');if(cfg.port)option(port,cfg.port,cfg.port);port.value=cfg.port??'';
    const baud=input('baudrate','Baud rate','',true);for(const rate of (profile.defaults.baudrates??[9600,19200,38400,57600,115200]))option(baud,String(rate),String(rate));baud.value=String(cfg.baudrate??profile.defaults.baudrate??38400);refresh.textContent='Refresh ports';
    void loadPorts().catch(e=>{message.textContent=e.message;});
   }
   if(name==='stage'){
    input('axis','Axis',cfg.axis??'1');
    if(t==='usb'){input('pi_dll','PI GCS2 DLL (optional)',cfg.pi_dll??'');const device=input('usb_device','C-663.12 USB device','',true);option(device,'','Find and select a controller…');if(cfg.usb_device)option(device,cfg.usb_device,cfg.usb_device);device.value=cfg.usb_device??'';refresh.textContent='Find PI USB';}
   }else if(name==='camera'){
    input('dll_directory','SDK2 folder',cfg.dll_directory??'');inputs.dll_directory.placeholder='Folder containing atmcd64d.dll';const camera=input('camera_index','CCD','',true);option(camera,String(cfg.camera_index??0),'Andor SDK2 camera '+(cfg.camera_index??0));camera.value=String(cfg.camera_index??0);refresh.textContent='Find cameras';
   }else if(t!=='manual'&&t!=='serial'){input('resource',t==='usb'?'Device / USB resource':'Instrument resource',cfg.resource??'');input('dll_directory','Driver / SDK folder (optional)',cfg.dll_directory??'');}
   if(name==='camera')hint.textContent+=' CCD uses USB / SDK2, not a COM port. No cooling or shutter state is changed on connection.';
   controls();
  }
  function drawProfile(){profile=profiles.find(d=>d.id===driver.value);transport.replaceChildren();for(const t of profile.transports)option(transport,t,TRANSPORTS[t]??t);const cfg=drafts[profile.id]??(saved.driver===profile.id?saved:profile.defaults);transport.value=profile.transports.includes(cfg.transport)?cfg.transport:profile.defaults.transport??profile.transports[0];drawConnection(cfg);}
  let previous=driver.value;driver.onchange=()=>{drafts[previous]=payload();previous=driver.value;drawProfile();};transport.onchange=()=>{const cfg=payload();drawConnection(cfg);};
  async function perform(fn,changed=false){return action(async()=>{working=true;controls();message.classList.remove('error');message.textContent='Working…';try{const info=await fn();const s=await api('/status');store.snapshot=s;const state=s.devices[name];message.textContent=info??(state.state==='manual'?'Manual settings · '+state.identity:state.connected?'Connected · '+state.identity:'Disconnected');if(changed)onSourceChanged?.();}catch(e){message.textContent=e.message;message.classList.add('error');throw e;}finally{working=false;controls();}});}
  refresh.onclick=()=>perform(async()=>{
   if(name==='camera'){const list=await api('/devices/cameras',{dll_directory:inputs.dll_directory.value.trim()}),camera=inputs.camera_index;camera.replaceChildren();for(const c of list.cameras)option(camera,String(c.index),c.label);if(list.cameras.length)camera.value=String(list.cameras[0].index);inputs.dll_directory.value=list.dll_directory;if(!list.cameras.length)throw Error('No Andor SDK2 cameras found. Check power and USB.');return list.cameras.length+' camera(s) found';}
   if(name==='stage'&&transport.value==='usb'){const list=await api('/devices/pi-usb',{pi_dll:inputs.pi_dll.value.trim()}),device=inputs.usb_device;device.replaceChildren();option(device,'','Select a controller…');for(const d of list.devices)option(device,d.id,d.label);if(list.devices.length===1)device.value=list.devices[0].id;return list.devices.length+' PI USB controller(s) found';}
   await loadPorts();return 'COM port list updated';
  });
  connect.onclick=()=>perform(()=>api('/devices/connect-one',payload()).then(()=>null),true);
  disconnect.onclick=()=>perform(()=>api('/devices/disconnect-one',{device:name}).then(()=>null),true);
  fields.append(grid);buttons.append(refresh,disconnect,connect);card.append(heading,message,fields,hint,buttons);box.append(card);drawProfile();message.textContent=link.error??(link.state==='manual'?'Manual settings · '+link.identity:link.connected?'Connected · '+link.identity:'Not connected');
 }
}

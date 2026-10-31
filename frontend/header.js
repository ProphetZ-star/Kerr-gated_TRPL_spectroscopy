// Navigation locks presentation and controls; acquisition continues in the backend.
import {store} from './store.js';
let page='acquisition';
let wasBusy=false;
const $=id=>document.getElementById(id);
export const acquisitionPage=()=>page==='acquisition';
export const headerLocked=()=>page==='calibration'&&!store.snapshot?.busy;
export function setHeaderPage(name){
 page=name;wasBusy=!!store.snapshot?.busy;syncHeaderLock();
}
export function syncHeaderLock(){
 const busy=!!store.snapshot?.busy,finished=page==='calibration'&&wasBusy&&!busy;wasBusy=busy;
 const locked=headerLocked(),bar=document.querySelector('[role=progressbar]');
 bar.classList.toggle('is-locked',locked);bar.setAttribute('aria-disabled',String(locked));
 bar.title=locked?'Acquisition controls are locked while the calibration workbench is available.':'';
 for(const id of ['run','stop']){const button=$(id);button.title=locked?'Return to Acquisition or Analysis to start acquisition.':'';if(locked)button.disabled=true;}
 return finished;
}
export function updateProgress({percent,text,remaining},finalUpdate=false){
 if(headerLocked()&&!finalUpdate)return;
 if(percent!==undefined){$('progressFill').style.width=percent+'%';document.querySelector('[role=progressbar]').setAttribute('aria-valuenow',Number(percent).toFixed(0));}
 if(text!==undefined)$('progressText').textContent=text;
 if(remaining!==undefined)$('remaining').textContent=remaining;
}

// Restore newly completed backend fits once, including after a page refresh mid-fit.
export function newestUnseenFit(run,seen){
 const entries=Object.entries(run?.metadata?.analysis??{}).sort((a,b)=>(a[1].finished_utc??'').localeCompare(b[1].finished_utc??''));
 if(!entries.length)return null;
 const [identifier,result]=entries.at(-1),fresh=!seen.has(identifier);
 for(const [id]of entries)seen.add(id);
 return fresh?result:null;
}

export const DEFAULT_FILE_NAME='Sample 01 TRPL';
export function outputPath(directory,fileName){
 const name=fileName.trim()||DEFAULT_FILE_NAME;
 if(/[<>:"/\\|?*\x00-\x1f]/.test(name)||/[. ]$/.test(name)||/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)||name.length>180)
  throw Error('Enter a file name without folders or reserved Windows characters.');
 const folder=directory.trim()||'data';
 return folder.replace(/[\\/]+$/,'')+'/'+name+(/\.h5$/i.test(name)?'':'.h5');
}
export function splitOutputPath(path){
 const parts=String(path).replaceAll('\\','/').split('/');
 const name=parts.pop()||DEFAULT_FILE_NAME;
 return {directory:parts.join('/')||'data',fileName:name.replace(/\.h5$/i,'')};
}

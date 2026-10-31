const {chromium}=require('playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8765');await page.waitForFunction(()=>document.querySelector('#cameraFacts').textContent.includes('1024'));
  const results=[];
  for(const [width,height] of [[1440,900],[1366,768],[1024,768],[1000,700]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(200);
   const result=await page.evaluate(()=>{
    const rect=id=>document.getElementById(id).getBoundingClientRect();
    const foot=document.querySelector('.foot').getBoundingClientRect();
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,footTop:foot.top,exportBottom:rect('export').bottom,charts:['spectrum','map','decay'].map(id=>({id,width:rect(id).width,height:rect(id).height,bottom:rect(id).bottom}))};
   });
   if(result.scrollWidth>width||result.scrollHeight>height+1||result.exportBottom>height+1||result.charts.some(c=>c.height<159||c.bottom>result.footTop))throw Error(JSON.stringify(result));
   results.push(result);
  }
  await page.setViewportSize({width:1440,height:900});
  for(const id of ['analysis','calibration']){await page.locator('#'+id).click();await page.locator('#'+id+'Page').waitFor({state:'visible'});if(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+1))throw Error('Page overflow '+id);}
  await page.locator('#acquisition').click();await page.locator('.workspace').waitFor({state:'visible'});
  await page.screenshot({path:'test-output/desktop-dark.png'});await page.locator('#theme').click();await page.screenshot({path:'test-output/desktop-light.png'});
  if(errors.length)throw Error(errors.join('\n'));
  fs.writeFileSync('test-output/desktop-layout.json',JSON.stringify({passed:true,results,errors},null,2));
  console.log(JSON.stringify({passed:true,viewports:results.map(r=>[r.width,r.height]),errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

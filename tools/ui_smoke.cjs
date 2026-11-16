// Only this repository's synthetic demo; no personal browser profile or server.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),state=path.join(root,'private/monitoring');
const launchFile=path.join(state,'launch-url.txt');
const previous=fs.existsSync(launchFile)?fs.readFileSync(launchFile,'utf8').trim():null;
const child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),
 ['infrastructure/monitoring/console.py','--demo','--no-browser','--port','18776'],{cwd:root,stdio:'ignore',windowsHide:true});
let browser;
(async()=>{
 try{
  const deadline=Date.now()+15000;let launch;
  while(Date.now()<deadline){
   try{const value=fs.readFileSync(launchFile,'utf8').trim(),url=new URL(value);
    if(value!==previous&&url.protocol==='http:'&&url.hostname==='127.0.0.1'&&url.port==='18776'){
     const response=await fetch(url.origin+'/api/status',{headers:{Authorization:'Bearer '+url.hash.slice(1)}});
     if(response.ok&&(await response.json()).demo===true){launch=value;break;}
    }}catch{}
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!launch)throw Error('Demo did not create its own launch URL');
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));await page.goto(launch);
  await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('Démonstration'));
  if(await page.locator('.card').count()!==4)throw Error('Missing metrics cards');
  if(!/\d{2}:\d{2}/.test(await page.locator('#chart-time-labels').innerText()))throw Error('Missing graph hours');
  fs.mkdirSync(path.join(root,'.work/evidence'),{recursive:true});
  await page.screenshot({path:path.join(root,'.work/evidence/demo-desktop.png'),fullPage:true});
  await page.locator('#theme-toggle').click();
  await page.screenshot({path:path.join(root,'.work/evidence/demo-light.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile overflow');
  await page.screenshot({path:path.join(root,'.work/evidence/demo-mobile.png'),fullPage:true});
  if(errors.length)throw Error('Demo browser errors');
  const url=new URL(launch);
  const status=await page.request.post(url.origin+'/api/action',{headers:{Authorization:'Bearer '+url.hash.slice(1),Origin:url.origin},data:{action:'windows-shutdown',confirmation:'ARRETER CE PC WINDOWS'}});
  if(status.status()!==400)throw Error('Demo did not reject action');
  await page.request.post(url.origin+'/api/quit',{headers:{Authorization:'Bearer '+url.hash.slice(1),Origin:url.origin},data:{}});
  console.log('DEMO_UI_OK: synthetic desktop/light/mobile, graph hours, no JS error, action refused');
 }finally{if(browser)await browser.close();child.kill();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});

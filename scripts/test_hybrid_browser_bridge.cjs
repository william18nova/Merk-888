// Ejecuta Edge desde Windows sin depender de WSLInterop. Solo escenarios locales.
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const assert=require('node:assert/strict');
const bridge=path.resolve(process.argv[2]);
fs.mkdirSync(bridge,{recursive:true});
(async()=>{
  const input=path.join(bridge,'request.json'), deadline=Date.now()+300000;
  while(!fs.existsSync(input)){assert.ok(Date.now()<deadline,'No llego el ensayo del navegador');await new Promise(resolve=>setTimeout(resolve,100));}
  const data=JSON.parse(fs.readFileSync(input,'utf8'));
  const args=[path.join(__dirname,'test_hybrid_acceptance_browser.cjs')];
  for(const [key,value] of Object.entries(data))args.push('--'+key,String(value));
  const result=spawnSync(process.execPath,args,{encoding:'utf8',windowsHide:true,timeout:100000});
  const out=path.join(bridge,'response.json');
  fs.writeFileSync(out+'.tmp',JSON.stringify({code:result.status,stdout:result.stdout,stderr:result.stderr,error:String(result.error||'')}));
  fs.renameSync(out+'.tmp',out);
  process.stdout.write(result.stdout||'');process.stderr.write(result.stderr||'');process.exitCode=result.status||0;
})().catch(error=>{console.error(error);process.exitCode=1;});

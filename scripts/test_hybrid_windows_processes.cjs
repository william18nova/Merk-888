// Solo invocado por test_hybrid_acceptance; entradas ficticias por stdin.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const net=require('node:net');
const readline=require('node:readline');
const {randomUUID}=require('node:crypto');
const bridge=process.argv[2]==='--bridge'?path.resolve(process.argv[3]):null;
if(bridge)fs.mkdirSync(bridge,{recursive:true});
const lines=bridge?null:readline.createInterface({input:process.stdin,crlfDelay:Infinity})[Symbol.asyncIterator]();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let inputIndex=0, outputIndex=0;
const next=async()=>{
  if(!bridge){const row=await lines.next();assert.ok(!row.done,'Control de ensayo desconectado');return JSON.parse(row.value);}
  const file=path.join(bridge,'input-'+(inputIndex++)+'.json'), deadline=Date.now()+300000;
  while(!fs.existsSync(file)){assert.ok(Date.now()<deadline,'No llego control del ensayo');await sleep(100);}
  return JSON.parse(fs.readFileSync(file,'utf8'));
};
const report=value=>{
  if(!bridge)return process.stdout.write(JSON.stringify(value)+'\n');
  const file=path.join(bridge,'output-'+(outputIndex++)+'.json');
  fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);
};
async function port(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const p=server.address().port;await new Promise(resolve=>server.close(resolve));return p;}

(async()=>{
  const config=await next();
  assert.match(config.cloud,/^https:\/\/127\.0\.0\.1:\d+$/);
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'novapos-acceptance-win-'));
  const ca=path.join(root,'test-ca.pem');fs.writeFileSync(ca,config.ca);
  const regs=config.registers.map((r,i)=>({...r,dir:path.join(root,'caja-'+i),process:null}));
  async function stop(reg){
    if(!reg.process)return;
    const child=reg.process;
    if(child.exitCode===null){const done=new Promise(resolve=>child.once('exit',resolve));child.kill();await done;}
    reg.process=null;
  }
  async function start(reg){
    fs.mkdirSync(reg.dir,{recursive:true});
    reg.url='http://127.0.0.1:'+await port();
    reg.process=spawn(config.executable,['--data-dir',reg.dir,'--port',reg.url.split(':').at(-1),'--no-browser'],
      {windowsHide:true,stdio:['ignore','ignore','ignore'],env:{...process.env,SSL_CERT_FILE:ca,NO_PROXY:'127.0.0.1,localhost'}});
    const deadline=Date.now()+15000;
    while(Date.now()<deadline){
      try{const html=await(await fetch(reg.url+'/')).text();reg.token=html.match(/name="local-token" content="([^"]+)"/)[1];return;}
      catch(error){if(reg.process.exitCode!==null)throw new Error('El paquete Windows no arranco');await sleep(80);}
    }
    throw new Error('El servidor Windows no respondio');
  }
  async function api(reg,endpoint,data){
    const response=await fetch(reg.url+'/api/'+endpoint,{method:data===undefined?'GET':'POST',
      headers:{'Content-Type':'application/json',Origin:reg.url,'X-Local-Token':reg.token},
      body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(12000)});
    const body=await response.json();assert.equal(response.status,200,JSON.stringify(body));return body;
  }
  try{
    for(const reg of regs){
      await start(reg);
      await api(reg,'enroll',{url:config.cloud,code:reg.code});
      const status=await api(reg,'start',{username:reg.username,password:config.password,pin:config.pin});
      assert.equal(status.ready,true);reg.session=status.session.session_id;
      reg.sale={operation_id:randomUUID(),session_id:reg.session,items:[{id:config.product,quantity:500}],cash_received:'2000'};
    }
    report({phase:'ready'});assert.equal((await next()).phase,'offline');
    await Promise.all(regs.map(async reg=>{
      for(let i=0;i<12;i++){
        const data=i===0?reg.sale:{...reg.sale,operation_id:randomUUID()};
        assert.equal((await api(reg,'checkout',data)).state,'pending');
      }
    }));
    for(const reg of regs){await stop(reg);await start(reg);assert.equal((await api(reg,'status')).unlocked,false);await api(reg,'unlock',{pin:config.pin});}
    report({phase:'queued',pending:await Promise.all(regs.map(async reg=>(await api(reg,'status')).pending))});
    assert.equal((await next()).phase,'online');
    const deadline=Date.now()+45000;
    while(Date.now()<deadline){
      const statuses=await Promise.all(regs.map(reg=>api(reg,'status')));
      if(statuses.every(s=>s.pending===0)||statuses.some(s=>s.conflicts))break;
      await sleep(200);
    }
    for(const reg of regs){
      const status=await api(reg,'status');
      assert.equal(status.pending,0,status.error || 'Pendientes sin sincronizar');
      assert.equal((await api(reg,'checkout',reg.sale)).state,'accepted');
      await api(reg,'release',{session_id:reg.session});
      assert.equal((await api(reg,'status')).session,false);
      assert.ok(fs.existsSync(path.join(reg.dir,'backup.sqlite3')));
    }
    report({phase:'completed',sales:24});
  }finally{
    for(const reg of regs)await stop(reg);
    // Únicamente el directorio temporal creado aquí, nunca una instalación real.
    const resolved=path.resolve(root), base=path.resolve(os.tmpdir());
    assert.equal(path.dirname(resolved),base);assert.ok(path.basename(resolved).startsWith('novapos-acceptance-win-'));
    fs.rmSync(resolved,{recursive:true});
  }
})().catch(error=>{report({phase:'error',error:String(error)});process.exitCode=1;}).finally(()=>process.stdin.destroy());

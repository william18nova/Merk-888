const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createTransport} = require('../mainApp/static/javascript/integrated_business_transport.js');
const id = '550e8400-e29b-41d4-a716-446655440000';
function make(fetchRequest) {
  const values = new Map();
  return createTransport({storage: {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
    randomUUID:()=>id, fetchRequest, userId: 1});
}
test('UUID survives lost response; no second reference', async()=>{
  const seen=[];
  const api=make(async(url,options)=>{seen.push(options.headers['X-Nova-Operation-ID']);throw Error('network');});
  await assert.rejects(api.submit('/expense',{},'form','csrf'));
  await assert.rejects(api.submit('/expense',{},'form','csrf'));
  assert.deepEqual(seen,[id,id]);
});
test('storage failure prevents POST', async()=>{
  let calls=0;
  const api=createTransport({storage:{getItem:()=>null,setItem:()=>{throw Error('storage');}},randomUUID:()=>id,
    fetchRequest:()=>{calls++;},userId:1});
  await assert.rejects(api.submit('/expense',{},'form','csrf')); assert.equal(calls,0);
});
test('no automatic retry after malformed acknowledgement',async()=>{
  let calls=0;
  const api=make(async()=>{calls++;return {ok:true,json:async()=>{throw Error('html');}}});
  await assert.rejects(api.submit('/expense',{},'form','csrf')); assert.equal(calls,1);
});
test('CSRF and original body retained',async()=>{
  const body={concepto:'FICTIONAL'};
  const api=make(async(url,options)=>{
    assert.equal(options.body,body);assert.equal(options.headers['X-CSRFToken'],'csrf');
    assert.equal(options.headers['X-Nova-Business'],'1');
    return {ok:true,json:async()=>({success:true,redirect:'/expense?nova_ack='+id})};
  });
  assert.equal((await api.submit('/expense',body,'form','csrf')).success,true);
});
test('HTTP error cannot claim success',async()=>{
  const api=make(async()=>({ok:false,json:async()=>({success:true})}));
  await assert.rejects(api.submit('/expense',{},'form','csrf'));
});
test('acknowledgement on another page releases the ORIGINAL form reference',()=>{
  const values=new Map(); let serial=0;
  const api=createTransport({storage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
    randomUUID:()=>serial++===0?id:'650e8400-e29b-41d4-a716-446655440000',fetchRequest:()=>{},userId:1});
  assert.equal(api.reference('/ver_venta/123/'),id);
  api.acknowledgeReturn(id); // The redirect was to /visualizar_ventas/, not the form.
  assert.notEqual(api.reference('/ver_venta/123/'),id);
});
test('an unrelated acknowledgement cannot release a pending reference',()=>{
  const api=make(()=>{});
  assert.equal(api.reference('/expense'),id);
  api.acknowledgeReturn('650e8400-e29b-41d4-a716-446655440000');
  assert.equal(api.reference('/expense'),id);
});

"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const money = value => new Intl.NumberFormat("es-CO", {style:"currency", currency:"COP", maximumFractionDigits:2}).format(value);
let products=[], cart=[], operation=crypto.randomUUID(), currentSession=null, total=0, busy=false, loaded=false, previewRevision=0;
let productMap=new Map(), searchable=[], previewValid=false, lastStatus=null, syncing=false, catalogStamp='', refreshRunning=false;
let printerConfig={backend:'browser',paper:'80'}, receiptRow=null, receiptPolling=false;
const printRequests=new Map();
async function api(path, data){let r;try{r=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Local-Token':token},body:data===undefined?undefined:JSON.stringify(data)});}catch{throw new Error(path==='checkout'?'No llegó la confirmación de la aplicación local. Conserva este carrito y revisa el historial antes de reintentar; no vuelvas a registrar la venta en la web.':'No responde la aplicación local. Comprueba que Nova POS siga abierto.');}const value=await r.json();if(!r.ok){const error=new Error(value.error||'No se completó la operación.');error.status=r.status;throw error;}return value;}
function notice(message=''){$('notice').textContent=message;$('notice').hidden=!message;}
function showError(message){if($('payment-dialog').open){$('payment-error').textContent=message;$('payment-error').hidden=false;}else notice(message);}
async function action(fn){if(busy)return;busy=true;document.querySelectorAll('button, #pos input, #cash').forEach(b=>b.disabled=true);try{notice();await fn();}catch(e){showError(e.message);}finally{busy=false;document.querySelectorAll('button, #pos input, #cash').forEach(b=>b.disabled=false);await refresh().catch(()=>{});await preview();renderPrintState();}}
function form(id,path){$(id).addEventListener('submit',event=>{event.preventDefault();action(async()=>{const data=Object.fromEntries(new FormData(event.target));await api(path,data);event.target.reset();loaded=false;});});}
form('enroll-form','enroll');form('start-form','start');form('unlock-form','unlock');
function normalize(value){return String(value).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();}
function setProducts(rows){products=rows;productMap=new Map(rows.map(p=>[p.id,p]));searchable=rows.map(p=>({p,name:normalize(p.name),id:String(p.id),code:String(p.barcode||'')}));}
const searches=[{input:$('search'),list:$('suggestions'),mode:'name',rows:[],active:-1},{input:$('barcode'),list:$('barcode-suggestions'),mode:'barcode',rows:[],active:-1}];
function closeSearch(s){s.list.hidden=true;s.input.setAttribute('aria-expanded','false');s.input.removeAttribute('aria-activedescendant');s.active=-1;}
function closeSearches(){searches.forEach(closeSearch);}
function suggestions(s=searches[0]){
    const q=normalize(s.input.value.trim()), words=q.split(/\s+/);
    s.rows=q?searchable.filter(r=>s.mode==='barcode'?r.code&&r.code.startsWith(q):(r.id.startsWith(q)||words.every(w=>r.name.includes(w))))
        .sort((a,b)=>Number(b.id===q||b.code===q||b.name===q)-Number(a.id===q||a.code===q||a.name===q)||Number(b.name.startsWith(q))-Number(a.name.startsWith(q))).slice(0,12).map(r=>r.p):[];
    s.active=-1;s.list.replaceChildren();s.input.removeAttribute('aria-activedescendant');
    if(!q){closeSearch(s);return;}
    s.list.hidden=false;s.input.setAttribute('aria-expanded','true');
    if(!s.rows.length){const empty=document.createElement('p');empty.className='no-match';empty.textContent='Sin coincidencias en el catálogo descargado.';s.list.append(empty);}
    for(const [index,p] of s.rows.entries()){
        const button=document.createElement('button');button.type='button';button.tabIndex=-1;button.id=s.list.id+'-'+index;button.setAttribute('role','option');button.setAttribute('aria-selected','false');
        const name=document.createElement('span');name.textContent=p.name;
        const info=document.createElement('small');info.textContent=`ID ${p.id} · ${p.barcode||'Sin código'} · Stock ${p.stock}`;name.append(info);
        const price=document.createElement('span');price.textContent=money(p.price);button.append(name,price);button.onclick=()=>add(p,s.input);s.list.append(button);
    }
}
function selectSuggestion(s,index){
    if(!s.rows.length)return;s.active=(index+s.rows.length)%s.rows.length;
    [...s.list.querySelectorAll('button')].forEach((button,i)=>button.setAttribute('aria-selected',String(i===s.active)));
    const button=s.list.querySelectorAll('button')[s.active];s.input.setAttribute('aria-activedescendant',button.id);button.scrollIntoView({block:'nearest'});
}
function add(product,focus=$('search')){
    if(busy||$('payment-dialog').open)return;
    const qty=Number($('quantity').value),old=cart.find(x=>x.id===product.id);
    if(!Number.isInteger(qty)||qty<1||qty>1000000||(old&&old.quantity+qty>1000000))return notice('La cantidad debe estar entre 1 y 1.000.000, en unidades o gramos.');
    if(!old&&cart.length>=100)return notice('El carrito admite hasta 100 productos diferentes.');
    if(old)old.quantity+=qty;else cart.push({id:product.id,quantity:qty});
    $('search').value='';$('barcode').value='';$('quantity').value='1';closeSearches();notice();
    $('added-notice').textContent=`Agregado: ${product.name} × ${qty}`;renderCart();focus.focus();
}
for(const s of searches){
    s.input.addEventListener('input',()=>{searches.filter(x=>x!==s).forEach(closeSearch);suggestions(s);});
    s.input.addEventListener('focus',()=>{if(s.input.value) suggestions(s);});
    s.input.addEventListener('keydown',event=>{
        if(busy||event.isComposing||event.repeat)return;
        if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();if(s.list.hidden)suggestions(s);selectSuggestion(s,s.active<0?(event.key==='ArrowDown'?0:s.rows.length-1):s.active+(event.key==='ArrowDown'?1:-1));}
        if(event.key==='Escape'||event.key==='Tab')closeSearch(s);
        if(event.key==='Enter'){
            event.preventDefault();const q=s.input.value.trim();if(!q)return;
            if(s.mode==='barcode'&&s.active<0){
                const exact=products.filter(p=>p.barcode===q);
                if(exact.length===1)add(exact[0],s.input);
                else notice(exact.length?'Hay varios productos con ese código. Selecciona el correcto.':'No se encontró ese código completo. No se agregó otro producto.');
            }else{if(s.list.hidden)suggestions(s);const p=s.rows[Math.max(0,s.active)];if(p)add(p,s.input);}
        }
    });
}
$('barcode').addEventListener('paste',event=>{const text=event.clipboardData?.getData('text')||'';if(text.trim().split(/[\r\n]+/).filter(Boolean).length>1){event.preventDefault();$('barcode').value='';closeSearches();notice('Ingresa un solo código de barras a la vez.');}});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('.search-field'))closeSearches();});
function filterCart(){const q=normalize($('cart-search').value.trim());let visible=0;for(const row of $('cart').children){const p=productMap.get(Number(row.dataset.id));row.hidden=!!q&&!normalize(`${p?.name} ${p?.barcode} ${row.dataset.id}`).includes(q);if(!row.hidden)visible++;}$('cart-no-results').hidden=!cart.length||visible>0;}
$('cart-search').addEventListener('input',filterCart);
function renderCart(){
    $('cart').replaceChildren();
    for(const item of cart){
        const p=productMap.get(item.id),row=document.createElement('tr');row.className='cart-row';row.dataset.id=item.id;
        const name=document.createElement('td');name.textContent=p?.name||`Producto ${item.id}`;
        const small=document.createElement('small');small.textContent=`ID ${item.id}${p?.barcode?' · '+p.barcode:''}`;name.append(small);
        const quantityCell=document.createElement('td');quantityCell.dataset.label='Cantidad';
        const qty=document.createElement('input');qty.type='number';qty.min='1';qty.max='1000000';qty.step='1';qty.value=item.quantity;qty.setAttribute('aria-label','Cantidad de '+(p?.name||item.id));
        qty.addEventListener('change',()=>{const n=Number(qty.value);if(Number.isInteger(n)&&n>0&&n<=1000000){item.quantity=n;preview();}else{qty.value=item.quantity;notice('Cantidad inválida. Usa unidades o gramos enteros.');}});quantityCell.append(qty);
        const price=document.createElement('td');price.className='row-price';price.dataset.label='Precio U.';price.textContent=money(p?.price||0);
        const subtotal=document.createElement('td');subtotal.className='row-subtotal';subtotal.dataset.label='Subtotal';subtotal.textContent='…';
        const actions=document.createElement('td'),remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Quitar '+(p?.name||item.id));remove.onclick=()=>{cart=cart.filter(x=>x!==item);renderCart();};actions.append(remove);
        row.append(name,quantityCell,price,subtotal,actions);$('cart').append(row);
    }
    $('empty-cart').hidden=!!cart.length;$('cart-table-wrap').hidden=!cart.length;$('cart-count').textContent=`${cart.length} producto${cart.length===1?'':'s'}`;filterCart();preview();
}
function updatePayment(){
    const received=Number($('cash').value),enough=$('cash').value!==''&&Number.isFinite(received)&&received>=total;
    $('payment-total').textContent=money(total);$('payment-summary').textContent=`${cart.length} producto${cart.length===1?'':'s'} · ${lastStatus?.session?.point||''}`;
    $('change').textContent=!$('cash').value?'':enough?'Cambio: '+money(received-total):'Faltan '+money(Math.max(0,total-received));
    $('change').classList.toggle('is-short',!enough);
    const authorized=lastStatus?.unlocked&&lastStatus?.ready&&!lastStatus?.conflicts&&Date.parse(lastStatus.session.expires_at)>Date.now();
    $('generar-venta').disabled=busy||!previewValid||!cart.length||!authorized;
    $('checkout').disabled=busy||!previewValid||!cart.length||!enough||!authorized;
}
async function preview(){
    const revision=++previewRevision;previewValid=false;updatePayment();
    if(!cart.length){total=0;$('total').textContent=money(0);updatePayment();return;}
    try{
        const result=await api('preview',{items:cart});if(revision!==previewRevision)return;
        total=Number(result.total);previewValid=true;$('total').textContent=money(total);
        const lines=new Map(),free=new Map();for(const line of result.details){lines.set(line.productoid,(lines.get(line.productoid)||0)+Number(line.subtotal));if(Number(line.precio_unitario)===0)free.set(line.productoid,(free.get(line.productoid)||0)+line.cantidad);}
        for(const row of $('cart').children){const id=Number(row.dataset.id),subtotal=row.querySelector('.row-subtotal');subtotal.textContent=money(lines.get(id)||0);if(free.get(id)){const hint=document.createElement('small');hint.textContent=`Promo: ${free.get(id)} gratis`;subtotal.append(hint);}row.querySelector('.row-price').textContent=money(productMap.get(id)?.price||0);}
        updatePayment();
    }catch(e){if(revision===previewRevision)showError(e.message);}
}
$('cash').addEventListener('input',()=>{$('payment-error').hidden=true;updatePayment();});
function openPayment(){if(busy||$('generar-venta').disabled)return;closeSearches();$('payment-error').hidden=true;updatePayment();$('payment-dialog').showModal();$('cash').focus();$('cash').select();}
$('generar-venta').onclick=openPayment;
$('cancel-payment').onclick=()=>{if(!busy)$('payment-dialog').close();};
$('payment-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
$('clear').onclick=()=>{if(cart.length)$('clear-dialog').showModal();};
$('cancel-clear').onclick=()=>$('clear-dialog').close();
$('confirm-clear').onclick=()=>{cart=[];operation=crypto.randomUUID();$('cash').value='';$('cart-search').value='';$('added-notice').textContent='';$('clear-dialog').close();renderCart();$('search').focus();};
$('scope-settings').onclick=()=>$('scope-dialog').showModal();$('close-scope').onclick=()=>$('scope-dialog').close();
document.addEventListener('keydown',event=>{
    if(event.repeat||event.isComposing||busy||$('pos').hidden)return;
    if(event.altKey&&event.code==='Space'){
        event.preventDefault();if($('payment-dialog').open){if(!$('checkout').disabled)$('payment-form').requestSubmit();}else if(!document.querySelector('dialog[open]'))openPayment();
    }
    if(event.ctrlKey&&!event.altKey&&!document.querySelector('dialog[open]')){
        const input={'1':'search','2':'barcode','4':'quantity'}[event.key];if(input){event.preventDefault();$(input).focus();$(input).select();}
    }
});
function showReceipt(row){receiptRow=row;document.body.dataset.paper=printerConfig.paper;$('receipt-state').textContent=row.state==='accepted'?`Venta #${row.sale_id} confirmada`:row.state==='pending'?'Guardada en el equipo · pendiente de nube':'Requiere revisión · no repetir cobro';$('receipt').textContent=(row.state==='pending'?'PENDIENTE DE SINCRONIZAR\n\n':'')+`Ref. local: ${row.id}\n\n`+row.receipt;renderPrintState();if(!$('receipt-dialog').open)$('receipt-dialog').showModal();}
function renderPrintState(){
    if(!receiptRow)return;
    const job=receiptRow.printing||{}, inProgress=['queued','sending'].includes(job.state);
    const labels={queued:'Comprobante en cola. Puedes cerrar esta ventana y seguir vendiendo.',sending:'Enviando a la impresora… Puedes seguir vendiendo.',not_requested:printerConfig.backend==='browser'?'Imprime con el diálogo del navegador.':'No se solicitó impresión automática.'};
    $('print-state').textContent=job.message||labels[job.state]||'';
    $('print').disabled=busy||inProgress||receiptRow.state==='conflict';
    $('print').textContent=inProgress?'Impresión en proceso':job.id?'Pedir copia':'Imprimir';
}
async function printReceipt(confirmCopy=false){
    if(!receiptRow)return;
    if(printerConfig.backend==='browser'){window.print();return;}
    const id=receiptRow.id;
    let request=printRequests.get(id);
    if(!request){request={id:crypto.randomUUID(),confirmCopy};printRequests.set(id,request);}
    $('print').disabled=true;
    try{
        const job=await api('print',{session_id:currentSession,operation_id:id,request_id:request.id,confirm_copy:request.confirmCopy});
        printRequests.delete(id);
        if(receiptRow?.id===id)receiptRow.printing=job;
    }catch(e){
        if(e.status>=400&&e.status<500)printRequests.delete(id);
        $('print-state').textContent=e.message+' Revisa el papel antes de intentar otra vez. La venta ya está guardada.';
        // Conserva la misma solicitud para que una respuesta perdida no duplique la impresión.
        return;
    }finally{$('print').disabled=false;}
    renderPrintState();
}
$('print').onclick=()=>{
    if(printerConfig.backend!=='browser'&&receiptRow?.printing?.id&&!printRequests.has(receiptRow.id))$('copy-dialog').showModal();
    else printReceipt();
};
$('confirm-copy').onclick=()=>{$('copy-dialog').close();printReceipt(true);};
$('cancel-copy').onclick=()=>$('copy-dialog').close();
function printerFields(){
    const backend=$('printer-backend').value;
    $('agent-fields').hidden=backend!=='agent';$('cups-fields').hidden=backend!=='cups';
    $('native-fields').hidden=backend==='browser';$('browser-print-help').hidden=backend!=='browser';
}
$('printer-backend').onchange=printerFields;
$('printer-settings').onclick=()=>action(async()=>{
    printerConfig=await api('printer');$('printer-form').reset();
    $('printer-backend').value=printerConfig.backend;$('printer-paper').value=printerConfig.paper;
    $('agent-port').value=printerConfig.agent_port;$('cups-printer').value=printerConfig.printer;
    $('cups-option').disabled=!printerConfig.linux_available;
    for(const key of ['automatic','cut','drawer'])$('printer-'+key).checked=printerConfig[key];
    $('agent-token-help').textContent=printerConfig.has_token?'Ya hay un token guardado. Deja vacío para conservarlo.':'Debe coincidir con el token del agente de impresión.';
    $('printer-error').hidden=true;printerFields();$('printer-dialog').showModal();
});
$('cancel-printer').onclick=()=>$('printer-dialog').close();
$('printer-dialog').addEventListener('close',()=>$('printer-form').reset());
$('printer-form').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    const data=Object.fromEntries(new FormData(event.target));data.agent_port=Number(data.agent_port);data.session_id=currentSession;
    for(const key of ['automatic','cut','drawer'])data[key]=$('printer-'+key).checked;
    try{printerConfig=await api('printer',data);document.body.dataset.paper=printerConfig.paper;$('printer-dialog').close();notice('Configuración de impresión guardada para este equipo.');}
    catch(e){$('printer-error').textContent=e.message;$('printer-error').hidden=false;}
    finally{button.disabled=false;event.target.elements.pin.value='';$('agent-token').value='';}
};
$('backup-settings').onclick=()=>{$('backup-form').reset();$('backup-error').hidden=true;$('backup-dialog').showModal();};
$('cancel-backup').onclick=()=>$('backup-dialog').close();
$('backup-dialog').addEventListener('close',()=>$('backup-form').reset());
$('backup-form').onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const form=event.target, button=event.submitter;
    const data=Object.fromEntries(new FormData(form));data.session_id=currentSession;
    busy=true;button.disabled=true;button.textContent='Protegiendo copia…';
    try{
        const response=await fetch('/api/backup',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(data)});
        if(!response.ok){const error=await response.json();throw new Error(error.error||'No se pudo crear la copia.');}
        const blob=await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
        link.href=url;link.download='NovaPOS-'+new Date().toISOString().replace(/[:.]/g,'-')+'.novabackup';
        document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
        $('backup-dialog').close();notice('Copia cifrada enviada al navegador. Comprueba la descarga, guárdala fuera de este disco y verifica su contraseña con el administrador.');
    }catch(e){$('backup-error').textContent=e.message;$('backup-error').hidden=false;}
    finally{form.reset();data.pin=data.password=data.confirmation='';busy=false;button.disabled=false;button.textContent='Descargar copia cifrada';}
};
$('payment-form').onsubmit=event=>{
    event.preventDefault();if(busy||$('checkout').disabled)return;
    action(async()=>{
        $('payment-error').hidden=true;syncing=true;renderConnection();
        try{
            const row=await api('checkout',{session_id:currentSession,operation_id:operation,items:cart.map(item=>({...item})),cash_received:$('cash').value,expected_total:String(total)});
            if(row.state==='conflict'){showError(row.error+' No vuelvas a cobrar; solicita revisión del pendiente.');return;}
            $('payment-dialog').close();showReceipt(row);cart=[];operation=crypto.randomUUID();$('cash').value='';$('cart-search').value='';$('added-notice').textContent='';renderCart();await history();
        }finally{syncing=false;renderConnection();}
    });
};
async function history(){const rows=await api('history');$('history-count').textContent=String(rows.length);$('history').replaceChildren();if(!rows.length){$('history').textContent='Todavía no hay ventas en esta sesión.';return;}for(const row of rows){const line=document.createElement('div');line.className='history-row';line.dataset.state=row.state;const label=document.createElement('span');label.textContent=row.state==='accepted'?`Venta #${row.sale_id} · confirmada`:row.state==='pending'?'Pendiente de sincronizar':'Necesita revisión';const ref=document.createElement('small');ref.textContent=row.id;label.append(ref);const b=document.createElement('button');b.className='secondary';b.textContent='Comprobante';b.onclick=()=>showReceipt(row);line.append(label,b);$('history').append(line);}}
function renderConnection(){
    const s=lastStatus,badge=$('connection');let state='loading',text='Preparando conexión…';
    if(syncing){state='syncing';text='Sincronizando…';}
    else if(s?.conflicts){state='review';text=`${s.conflicts} venta${s.conflicts===1?'':'s'} por revisar`;}
    else if(s?.unlocked&&Date.parse(s.session.expires_at)<=Date.now()){state='review';text='Autorización vencida';}
    else if(s?.paired&&s?.session){state=s.online?'online':'offline';text=s.online?'Conectado':'Sin conexión';if(s.pending)text+=` · ${s.pending} pendiente${s.pending===1?'':'s'}`;}
    else if(s){text=s.paired?'Inicia tu sesión':'Equipo sin vincular';}
    badge.dataset.state=state;badge.textContent=text;badge.title=s?.error||'Último estado confirmado por la aplicación local.';
}
async function refresh(){
    if(refreshRunning)return;refreshRunning=true;
    try{
        const s=await api('status');lastStatus=s;renderConnection();
        for(const id of ['enrollment','login','unlock','pos'])$(id).hidden=true;
        if(!s.paired)$('enrollment').hidden=false;else if(!s.session)$('login').hidden=false;else if(!s.unlocked)$('unlock').hidden=false;
        else{
            $('pos').hidden=false;$('operator').textContent=s.session.user;$('branch').value=s.session.branch;$('point').value=s.session.point;
            const until=new Date(s.session.expires_at).toLocaleString('es-CO',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
            $('sync-summary').textContent=`${s.ready?'Catálogo disponible en este equipo':'Descarga incompleta: pulsa Sincronizar'} · ${s.pending?'Las ventas pendientes se enviarán automáticamente.':'Sin ventas pendientes.'} · Autorización: ${until}`;
            if(!loaded||currentSession!==s.session.session_id){
                if(currentSession&&currentSession!==s.session.session_id){cart=[];operation=crypto.randomUUID();$('cash').value='';$('cart-search').value='';closeSearches();for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();}
                setProducts(await api('products'));printerConfig=await api('printer');currentSession=s.session.session_id;loaded=true;catalogStamp=s.catalog_updated;renderCart();
            }else if(catalogStamp!==s.catalog_updated){
                setProducts(await api('products'));catalogStamp=s.catalog_updated;await preview();
                for(const search of searches)if(!search.list.hidden)suggestions(search);
            }
            await history();if(s.error&&(s.conflicts||!s.ready))notice(s.error);
        }
        if(!s.unlocked){$('operator').textContent='';closeSearches();for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();receiptRow=null;}
        updatePayment();
    }finally{refreshRunning=false;}
}
$('sync').onclick=()=>action(async()=>{syncing=true;renderConnection();try{await api('sync',{});setProducts(await api('products'));for(const s of searches)if(!s.list.hidden)suggestions(s);await history();await preview();}finally{syncing=false;renderConnection();}});
$('lock').onclick=()=>action(async()=>{await api('lock',{});loaded=false;});
$('release').onclick=()=>{$('release-error').hidden=true;$('confirm-dialog').showModal();};
$('cancel-release').onclick=()=>$('confirm-dialog').close();
$('confirm-release').onclick=()=>action(async()=>{
    $('release-error').hidden=true;
    try{
        if(cart.length)throw new Error('Termina o vacía el carrito antes de finalizar.');
        await api('release',{session_id:currentSession});
        $('confirm-dialog').close();loaded=false;
    }catch(error){
        // El aviso permanece en el modal y no lo reemplaza el estado de red.
        $('release-error').textContent=error.message;$('release-error').hidden=false;
    }
});
$('close-receipt').onclick=()=>{$('receipt-dialog').close();$('search').focus();};
setInterval(async()=>{
    if(busy||receiptPolling||!receiptRow||!$('receipt-dialog').open)return;
    receiptPolling=true;const id=receiptRow.id;
    try{const rows=await api('history');const updated=rows.find(row=>row.id===id);if(updated&&receiptRow?.id===id&&$('receipt-dialog').open)showReceipt(updated);}catch(_e){/* La venta queda guardada aunque falle esta consulta. */}finally{receiptPolling=false;}
},2000);
refresh().catch(e=>notice(e.message));setInterval(()=>{if(!busy)refresh().catch(()=>notice('No responde la aplicación local. Abre NovaPOS de nuevo; las ventas registradas siguen guardadas.'));},5000);

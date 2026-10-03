'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="lab-token"]').content;
const money = value => new Intl.NumberFormat('es-CO', {style:'currency', currency:'COP', maximumFractionDigits:0}).format(Number(value));
const price = value => new Intl.NumberFormat('es-CO', {style:'currency', currency:'COP', minimumFractionDigits:Number.isInteger(Number(value))?0:2, maximumFractionDigits:2}).format(Number(value));
let working = false, stopped = false;
function item(parent, className, name, value, detail) {
  const row = document.createElement('div'); row.className = className;
  const label = document.createElement('span'); label.textContent = name;
  if (detail) { const small = document.createElement('small'); small.textContent = detail; label.append(small); }
  const number = document.createElement('strong'); number.textContent = value;
  if (Number(value) < 0) number.className = 'negative';
  row.append(label, number); parent.append(row);
}
async function refresh() {
  if (stopped) return;
  try {
    const state = await (await fetch('/api/state')).json();
    $('phase').textContent = state.phase;
    $('mode').textContent = state.ready ? (state.online ? 'Conectado al servidor LOCAL de pruebas.' : 'SIN CONEXIÓN SIMULADA · Las ventas se guardan en este PC.') : 'Espera a que se prepare el laboratorio.';
    $('error').hidden = !state.error; $('error').textContent = state.error;
    $('open').classList.toggle('disabled', !state.ready);
    $('open').disabled = !state.ready || working || state.busy || state.stopping;
    for (const id of ['offline','online','sync','restart','session']) $(id).disabled = working || state.busy || state.stopping || !state.ready;
    if (state.online) $('online').disabled = true; else $('offline').disabled = true;
    $('pending').textContent = state.local?.pending ?? '—';
    $('conflicts').textContent = state.local?.conflicts ?? '—';
    $('sales').textContent = state.cloud?.sales ?? '—';
    $('total').textContent = state.cloud ? money(state.cloud.total) : '—';
    if (state.cloud) {
      $('products').replaceChildren();
      for (const p of state.cloud.products) item($('products'), 'product', p.name, String(p.stock), `ID ${p.id} · ${price(p.price)} por unidad / gramo`);
      $('recent').replaceChildren();
      for (const v of state.cloud.recent) item($('recent'), 'sale', `Venta #${v.ventaid} · ${v.fecha}`, money(v.total));
      if (!state.cloud.recent.length) $('recent').textContent = 'Todavía no hay ventas.';
    }
  } catch { if (!stopped) $('phase').textContent = 'El laboratorio está cerrado o reiniciándose. Ábrelo desde el acceso directo.'; }
}
async function action(endpoint, data, message) {
  if (working) return;
  working = true; $('message').textContent = 'Procesando…';
  try {
    const response = await fetch('/api/' + endpoint, {method:'POST', headers:{'Content-Type':'application/json','X-Lab-Token':token}, body:JSON.stringify(data)});
    const result = await response.json(); if (!response.ok) throw Error(result.error);
    $('message').textContent = message;
    if (endpoint === 'stop') { stopped = true; $('phase').textContent = 'Cerrando el laboratorio'; $('mode').textContent = 'Los datos se conservan. Puedes cerrar estas pestañas y volver a abrir el acceso directo cuando quieras.'; $('open').classList.add('disabled'); document.querySelectorAll('button').forEach(b=>b.disabled=true); }
  } catch (error) { $('message').textContent = error.message; }
  finally { working = false; if (!stopped) await refresh(); }
}
$('offline').onclick = () => action('mode',{online:false},'Corte simulado activado. Ahora registra una venta en la caja.');
$('open').onclick = () => action('open',{},'Abriendo la caja en tu navegador predeterminado…');
$('online').onclick = () => action('mode',{online:true},'Conexión restablecida. Revisa que los pendientes lleguen una sola vez.');
$('sync').onclick = () => action('sync',{},'Sincronización solicitada. Revisa los contadores.');
$('session').onclick = () => action('session',{},'Sesión preparada. Recarga la pestaña de la caja si estaba abierta.');
$('restart').onclick = () => { if (confirm('Se reiniciará SOLO la caja de pruebas. Las ventas confirmadas y pendientes se conservan. Termina primero cualquier carrito sin cobrar. ¿Continuar?')) action('restart',{},'Caja reiniciada. Recarga su pestaña para seguir.'); };
$('stop').onclick = () => { if (confirm('¿Cerrar el laboratorio? No se borrarán las ventas ni los pendientes.')) action('stop',{},'Cerrando sin borrar datos…'); };
void refresh(); setInterval(refresh,2500);

importScripts("parser.js");
const QUERY = "https://ptm.transacciones.com.co/DistribuidoresTFCC/controller/FCConsultaTransacciones";
const RESULT = QUERY + ".do.consultar";
const HOME = "https://ptm.transacciones.com.co/DistribuidoresTFCC/FCInicio";
const MENU = "https://ptm.transacciones.com.co/DistribuidoresTFCC/controller/FCVentaServicios";
const ALARM = "nova-ptm-read";
const errors = new Set(["sesion", "estructura", "conexion", "parcial", "limite", "cuenta"]);
let serial = Promise.resolve();
const exclusive = action => { const task = serial.then(action); serial = task.catch(() => {}); return task; };
async function config() { return (await chrome.storage.local.get("config")).config; }
async function state(value) { await chrome.storage.local.set({status: {...value, at: new Date().toISOString()}}); }
async function send(payload) {
  const c = await config();
  if (!c?.enabled) throw Error("Lector pausado");
  const origin = NovaPTMParser.endpoint(c.origin);
  const r = await fetch(origin + "/api/ptm/lector/", {method: "POST", redirect: "error", credentials: "omit",
    headers: {"Content-Type": "application/json", "Accept": "application/json", Authorization: "Bearer " + c.token},
    body: JSON.stringify(payload), signal: AbortSignal.timeout(12000)});
  if (!r.ok) throw Error(r.status === 401 ? "Clave revocada, vencida o sin permiso" : "NovaPOS rechazó el lote (" + r.status + ")");
  const result = await r.json();
  if (result.ok !== true) throw Error("Respuesta de NovaPOS no confirmada");
}
async function flush() {
  const {pending} = await chrome.storage.local.get("pending");
  if (pending) { await send(pending); await chrome.storage.local.remove("pending"); }
}
async function health(code, date) {
  await send({accion: "estado", estado: code, ...(date ? {fecha: date} : {})}).catch(() => {});
}
function dayNow() {
  return new Intl.DateTimeFormat("en-CA", {timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
}
function daysSince(last) {
  const today = dayNow(), end = new Date(today + "T12:00:00Z");
  const days = [];
  const start = new Date((last || today) + "T12:00:00Z");
  start.setUTCDate(start.getUTCDate() - 1); // Re-read yesterday to catch late updates.
  const gap = Math.floor((end - start) / 86400000);
  if (!Number.isFinite(gap) || gap < 0) throw Error("Fecha de lectura inválida");
  if (gap > 6) return {gap: true, days: [today]}; // Never silently skip a long outage.
  for (let d = start; d <= end; d = new Date(+d + 86400000)) days.push(d.toISOString().slice(0, 10));
  return {gap: false, days};
}
async function begin() {
  const c = await config();
  if (!c?.enabled) return;
  await flush(); // Durable unacknowledged batch first, before opening another report.
  const {job, readerTab} = await chrome.storage.session.get(["job", "readerTab"]);
  if (job && Date.now() - job.started < 240000) return;
  if (job) { await health("sesion"); await chrome.storage.session.remove("job"); }
  const stored = await chrome.storage.local.get(["lastDay", "manualDate", "gap"]);
  const plan = stored.manualDate ? {days: [stored.manualDate], gap: false} : daysSince(stored.lastDay);
  if (plan.gap) await chrome.storage.local.set({gap: true});
  const jobState = {dates: plan.days, date: plan.days[0], phase: "query", started: Date.now(), count: 0, manual: !!stored.manualDate};
  await chrome.storage.session.set({job: jobState});
  let tab;
  if (Number.isInteger(readerTab)) tab = await chrome.tabs.get(readerTab).catch(() => null);
  if (tab?.url?.includes("/seguridad/")) {
    await chrome.storage.session.remove("job");
    await state({message: "Inicia sesión manualmente en la pestaña lectora de PTM. No se guardan contraseñas."});
    await health("sesion");
    return;
  }
  if (tab && (tab.url === HOME || tab.url?.startsWith(QUERY))) {
    await chrome.tabs.update(tab.id, {url: HOME, active: false});
  } else {
    tab = await chrome.tabs.create({url: "about:blank", active: false});
    await chrome.storage.session.set({readerTab: tab.id});
    await chrome.tabs.update(tab.id, {url: HOME});
  }
  await state({message: "Consultando " + jobState.date + (stored.gap || plan.gap ? " · Hay días anteriores pendientes de recuperación manual" : "")});
}
async function onMessage(message, sender) {
  const ownOptions = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("options.html");
  if (ownOptions && message.type === "run") { await begin(); return {ok: true}; }
  if (ownOptions && message.type === "configure") {
    const origin = NovaPTMParser.endpoint(message.config?.origin);
    const token = String(message.config?.token || "");
    if (!/^[a-f0-9-]{36}\.[A-Za-z0-9_-]{43}$/.test(token)) throw Error("Clave de lector inválida");
    const old = await chrome.storage.local.get(["pending", "config"]);
    const {job} = await chrome.storage.session.get("job");
    const changesTarget = old.config && (old.config.origin !== origin || old.config.token !== token);
    if (changesTarget && (old.pending || job)) throw Error("Hay una lectura o lote pendiente. Primero completa el envío al destino original.");
    await chrome.storage.local.set({config: {origin, token, enabled: message.config.enabled === true}});
    if (changesTarget) await chrome.storage.local.remove(["lastDay", "manualDate", "gap"]);
    if (!message.config.enabled) await chrome.storage.session.remove("job");
    return {ok: true};
  }
  const {readerTab, job} = await chrome.storage.session.get(["readerTab", "job"]);
  if (!job || sender.id !== chrome.runtime.id || sender.tab?.id !== readerTab || ![MENU, QUERY, RESULT].includes(sender.url)) return {};
  const c = await config();
  if (!c?.enabled) return {};
  if (message.type === "ready") {
    if (job.phase === "query" && sender.url === MENU) return {action: "navigate", date: job.date};
    if (job.phase === "query" && sender.url === QUERY) {
      job.phase = "collect";
      job.frameId = sender.frameId;
      await chrome.storage.session.set({job});
      return {action: "query", date: job.date};
    }
    if (job.phase === "collect" && sender.url === RESULT && sender.frameId === job.frameId) return {action: "collect", date: job.date};
    return {};
  }
  if (message.type === "error") {
    const code = errors.has(message.error) ? message.error : "estructura";
    await state({message: "Lectura interrumpida: " + code + ". Revisa la sesión de PTM y NovaPOS."});
    await health(code);
    await chrome.storage.session.remove("job");
    return {ok: true};
  }
  if (sender.url !== RESULT || sender.frameId !== job.frameId || message.date !== job.date || job.phase !== "collect") return {};
  if (message.type === "batch") {
    if (!Array.isArray(message.rows) || message.rows.length > 100) throw Error("Lote inválido");
    // Strip everything except the fields needed for reconciliation.
    const keys = ["id_tx", "inicio", "fin", "producto", "valor", "estado", "id_operador", "usuario"];
    const rows = message.rows.map(row => Object.fromEntries(keys.map(k => [k, String(row[k] || "")])));
    const payload = {fecha: job.date, leido_en: new Date().toISOString(), filas: rows};
    if (JSON.stringify(payload).length > 120000) throw Error("Lote demasiado grande");
    await flush();
    await chrome.storage.local.set({pending: payload});
    await flush();
    job.count += rows.length;
    job.started = Date.now();
    await chrome.storage.session.set({job});
    return {ok: true};
  }
  if (message.type === "complete") {
    if (message.count !== job.count) throw Error("Conteo incompleto");
    // Only this acknowledgement means all observed pages were delivered.
    await send({accion: "estado", estado: "ok", fecha: job.date});
    job.dates.shift();
    if (job.dates.length) {
      job.date = job.dates[0]; job.phase = "query"; job.count = 0; job.started = Date.now();
      await chrome.storage.session.set({job});
      await chrome.tabs.update(readerTab, {url: HOME, active: false});
    } else {
      if (!job.manual) await chrome.storage.local.set({lastDay: job.date});
      await chrome.storage.local.remove("manualDate");
      await chrome.storage.session.remove("job");
      const {gap} = await chrome.storage.local.get("gap");
      await state({message: "Lectura enviada · " + job.date + (gap ? " · Revisa días antiguos pendientes" : "")});
    }
    return {ok: true};
  }
  return {};
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  exclusive(() => onMessage(message, sender)).then(respond).catch(async error => {
    await state({message: error.message});
    const ownOptions = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("options.html");
    respond({ok: false, error: ownOptions ? error.message : "conexion"});
  });
  return true;
});
chrome.alarms.onAlarm.addListener(a => { if (a.name === ALARM) exclusive(begin).catch(e => state({message: e.message})); });
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onStartup.addListener(() => exclusive(begin).catch(e => state({message: e.message})));
chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
chrome.alarms.get(ALARM).then(a => { if (!a) chrome.alarms.create(ALARM, {periodInMinutes: 2}); });

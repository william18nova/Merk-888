// Laboratorio de este PC: nunca inicia NovaPOS con su carpeta de datos habitual.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const {spawn, execFileSync} = require('node:child_process');
const {randomUUID, randomBytes, timingSafeEqual} = require('node:crypto');
const ROOT = path.join(process.env.LOCALAPPDATA || '', 'NovaPOS-Lab');
const REPO = path.resolve(__dirname, '..');
const BRIDGE = path.join(ROOT, 'bridge');
const ASSETS = path.join(__dirname, 'hybrid_lab_assets');
const PANEL = 'http://127.0.0.1:8895';
const POS = 'http://127.0.0.1:8793';
// Conservar la versión instalada hasta aprobar la nueva compilación en Windows.
const EXE = path.join(process.env.LOCALAPPDATA || '', 'NovaPOS', 'versions', '0.4.1-pilot', 'NovaPOS.exe');
const APP = 'nova-pos-local-lab-v1-NOT-PRODUCTION';
const runId = randomUUID(), nonce = randomBytes(32).toString('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, cloudProcess, clientProcess, localToken, ready, timer, stopping = false, busy = false, initialized = false;
let phase = 'Iniciando el laboratorio…', phaseError = '', online = true;
const read = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return {}; } };
function write(file, value) { fs.writeFileSync(file + '.tmp', JSON.stringify(value)); fs.renameSync(file + '.tmp', file); }
const winToWsl = file => { const p = path.resolve(file); if (!/^[A-Za-z]:\\/.test(p)) throw Error('Ruta Windows no admitida.'); return '/mnt/' + p[0].toLowerCase() + p.slice(2).replaceAll('\\', '/'); };
function openBrowser(url) {
  if (![PANEL, POS].includes(url)) throw Error('Solo se permiten las páginas fijas del laboratorio.');
  const child = spawn('rundll32.exe', ['url.dll,FileProtocolHandler', url], {windowsHide: true, stdio: 'ignore'});
  child.on('error', error => { phaseError = error.message; }); child.unref();
}
function openPanel() { openBrowser(PANEL); }
function ownRoot() {
  if (process.platform !== 'win32' || !process.env.LOCALAPPDATA) throw Error('Este asistente está preparado para este PC Windows con WSL.');
  if (fs.existsSync(ROOT) && fs.lstatSync(ROOT).isSymbolicLink()) throw Error('La carpeta de pruebas no puede ser un enlace.');
  const marker = path.join(ROOT, 'lab-marker.json');
  fs.mkdirSync(ROOT, {recursive: true});
  if (fs.existsSync(marker)) {
    if (read(marker).application !== APP) throw Error('La carpeta encontrada no pertenece al laboratorio.');
  } else {
    if (fs.readdirSync(ROOT).length) throw Error('No se utilizará una carpeta existente sin identificar.');
    write(marker, {application: APP});
  }
  for (const dir of [BRIDGE, path.join(ROOT, 'logs'), path.join(ROOT, 'data')]) fs.mkdirSync(dir, {recursive: true});
}
function shortcut() {
  const quote = value => "'" + value.replaceAll("'", "''") + "'";
  const launch = `& ${quote(process.execPath)} ${quote(__filename)}`;
  const encoded = Buffer.from(launch, 'utf16le').toString('base64');
  const script = `$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nova POS - Pruebas.lnk')); $s.TargetPath=Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'; $s.Arguments='-NoProfile -WindowStyle Hidden -EncodedCommand ${encoded}'; $s.WorkingDirectory=${quote(REPO)}; $s.IconLocation=${quote(EXE + ',0')}; $s.Description='Laboratorio local con datos ficticios. No usa la base real.'; $s.WindowStyle=7; $s.Save()`;
  execFileSync('powershell.exe', ['-NoProfile', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {windowsHide: true, stdio: 'pipe'});
}
function loggedSpawn(command, args, log, env = process.env) {
  const descriptor = fs.openSync(path.join(ROOT, 'logs', log), 'a');
  try {
    const child = spawn(command, args, {cwd: REPO, windowsHide: true, env, stdio: ['ignore', descriptor, descriptor]});
    child.on('error', error => { phaseError = error.message; });
    return child;
  } finally { fs.closeSync(descriptor); }
}
async function api(endpoint, data) {
  const response = await fetch(POS + '/api/' + endpoint, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', Origin: POS, 'X-Local-Token': localToken || ''},
    body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'La caja no confirmó la solicitud.');
  return result;
}
async function stopClient() {
  if (clientProcess && clientProcess.exitCode === null && !clientProcess.killed) {
    const child = clientProcess;
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill();
    await Promise.race([exited, sleep(8000)]);
    if (child.exitCode === null && child.signalCode === null) throw Error('No se pudo detener el proceso de pruebas. No se abrió una segunda caja.');
  }
  clientProcess = null;
}
function control(data) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({run_id: runId, ...data});
    const req = https.request(ready.cloud + '/__lab/control', {method:'POST',
      ca:fs.readFileSync(path.join(BRIDGE, 'lab-ca.pem')), timeout:5000,
      headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),Authorization:'Bearer ' + ready.control_token}}, res => {
      res.resume(); res.on('end', () => res.statusCode === 200 ? resolve() : reject(Error('No se confirmó el control del laboratorio.')));
    });
    req.on('timeout', () => req.destroy(Error('El servidor local no respondió.')));
    req.on('error', reject); req.end(body);
  });
}
async function startClient() {
  try {
    await fetch(POS + '/health', {signal: AbortSignal.timeout(700)});
    throw Error('El puerto 8793 ya está ocupado. No se tomará control de una caja ajena.');
  } catch (error) { if (error.message.includes('ya está ocupado')) throw error; }
  clientProcess = loggedSpawn(EXE, ['--data-dir', path.join(ROOT, 'data'), '--port', '8793', '--no-browser'], 'caja.log',
    {...process.env, SSL_CERT_FILE: path.join(BRIDGE, 'lab-ca.pem'), NO_PROXY: '127.0.0.1,localhost'});
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (clientProcess.exitCode !== null) throw Error('La caja de pruebas no arrancó. Revisa el registro caja.log.');
    try {
      const html = await (await fetch(POS + '/', {signal: AbortSignal.timeout(1000)})).text();
      const match = html.match(/name="local-token" content="([^"]+)"/);
      if (match) { localToken = match[1]; break; }
    } catch {}
    await sleep(150);
  }
  if (!localToken) throw Error('La caja no respondió a tiempo.');
  let state = await api('status');
  if (!state.paired) state = await api('enroll', {url: ready.cloud, code: ready.code});
  if (state.session && !state.unlocked) state = await api('unlock', {pin: ready.pin});
  // Renovar una autorización vencida exige primero confirmar todos sus pendientes.
  if (online && state.session && Date.parse(state.session.expires_at) <= Date.now()) {
    state = await api('sync', {});
    if (state.pending) throw Error('Hay ventas pendientes de la sesión anterior. No se reinició ni se borró esa sesión.');
    state = await api('release', {session_id: state.session.session_id});
  }
  if (!state.session) {
    if (!online) throw Error('Para abrir una nueva sesión, reconecta el laboratorio.');
    state = await api('start', {username: ready.username, password: ready.password, pin: ready.pin});
  }
  await api('sync', {});
}
async function boot() {
  if (!fs.existsSync(EXE)) throw Error('Falta Nova POS 0.4.1 en la instalación de este PC.');
  cloudProcess = loggedSpawn('wsl.exe', ['-d', 'Ubuntu-22.04', '-u', 'novapos-test', '--',
    '/home/novapos-test/hybrid-venv/bin/python', '-B', winToWsl(path.join(__dirname, 'hybrid_lab_server.py')),
    '--bridge', winToWsl(BRIDGE), '--run-id', runId], 'servidor.log');
  const deadline = Date.now() + 150000;
  while (Date.now() < deadline) {
    if (cloudProcess.exitCode !== null) throw Error('El servidor de pruebas no arrancó. Revisa servidor.log en la carpeta de pruebas.');
    const value = read(path.join(BRIDGE, 'ready.json'));
    if (value.run_id === runId) { ready = value; break; }
    await sleep(250);
  }
  if (!ready || ready.cloud !== 'https://127.0.0.1:8894') throw Error('No llegó una configuración válida del servidor local.');
  // WSL puede publicar el archivo antes de habilitar el reenvío localhost
  // hacia Windows. Esperar ese arranque evita fallos tras un reinicio normal.
  const controlDeadline = Date.now() + 45000;
  for (;;) {
    try { await control({online:true}); break; }
    catch (error) {
      if (Date.now() >= controlDeadline || cloudProcess.exitCode !== null) throw error;
      await sleep(500);
    }
  }
  timer = setInterval(() => { control({}).catch(error => { phaseError = error.message; }); }, 5000);
  phase = 'Preparando la caja y su catálogo…';
  await startClient();
  initialized = true;
  phase = 'Listo para tus pruebas';
}
async function shutdown() {
  if (stopping) return;
  stopping = true;
  phase = 'Cerrando sin borrar los datos…';
  clearInterval(timer);
  await stopClient();
  if (ready) await control({stop:true}).catch(() => {});
  if (cloudProcess && cloudProcess.exitCode === null) {
    await Promise.race([new Promise(resolve => cloudProcess.once('exit', resolve)), sleep(40000)]);
  }
  if (server) server.close();
  process.exitCode = 0;
}
function reply(res, code, value, type = 'application/json; charset=utf-8') {
  const body = Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value));
  res.writeHead(code, {'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"});
  res.end(body);
}
function authorized(req) {
  const received = Buffer.from(String(req.headers['x-lab-token'] || ''));
  const expected = Buffer.from(nonce);
  return req.headers.origin === PANEL && received.length === expected.length && timingSafeEqual(received, expected)
    && req.headers['content-type'] === 'application/json';
}
async function request(req, res) {
  if (req.headers.host !== '127.0.0.1:8895' || !['none', 'same-origin'].includes(req.headers['sec-fetch-site'] || 'none'))
    return reply(res, 403, {error: 'Origen no permitido.'});
  try {
    if (req.method === 'GET') {
      if (req.url === '/health') return reply(res, 200, {application: APP, phase});
      if (req.url === '/api/state') {
        let local = null;
        try { if (localToken && !stopping) local = await api('status'); } catch {}
        const cloud = read(path.join(BRIDGE, 'cloud-state.json'));
        return reply(res, 200, {phase, error: phaseError, online, ready: initialized && !!local,
          busy, stopping, local, cloud: cloud.run_id === runId ? cloud : null});
      }
      const files = {'/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/app.css': ['app.css', 'text/css']};
      if (files[req.url]) {
        const [name, type] = files[req.url];
        let body = fs.readFileSync(path.join(ASSETS, name));
        if (name === 'index.html') body = Buffer.from(body.toString('utf8').replace('__LAB_NONCE__', nonce));
        return reply(res, 200, body, type + '; charset=utf-8');
      }
      return reply(res, 404, {error: 'No encontrado.'});
    }
    if (req.method !== 'POST' || !authorized(req)) return reply(res, 403, {error: 'Solicitud no autorizada.'});
    if (busy || stopping) return reply(res, 409, {error: 'Espera a que termine la acción anterior.'});
    const length = Number(req.headers['content-length']);
    if (!(length > 0 && length <= 1000)) return reply(res, 400, {error: 'Solicitud inválida.'});
    const chunks = []; let size = 0;
    for await (const chunk of req) { size += chunk.length; if (size > 1000) throw Error('Solicitud demasiado grande.'); chunks.push(chunk); }
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (req.url === '/api/stop') { reply(res, 200, {ok: true}); void shutdown(); return; }
    if (!ready || !localToken) return reply(res, 409, {error: 'El laboratorio todavía no está preparado.'});
    busy = true;
    try {
      if (req.url === '/api/open') {
        // El POS rechaza navegaciones entre puertos por seguridad. Abrir desde
        // el SO produce una navegación directa sin relajar esa protección.
        openBrowser(POS);
      } else if (req.url === '/api/mode') {
        if (typeof data.online !== 'boolean') throw Error('Modo inválido.');
        await control({online:data.online});
        online = data.online;
        if ((await api('status')).unlocked) await api('sync', {});
      } else if (req.url === '/api/sync') {
        await api('sync', {});
      } else if (req.url === '/api/restart') {
        await stopClient(); localToken = null; await startClient();
      } else if (req.url === '/api/session') {
        if (!online) throw Error('Reconecta antes de abrir una sesión.');
        let status = await api('status');
        if (status.session && !status.unlocked) status = await api('unlock', {pin: ready.pin});
        if (!status.session) await api('start', {username: ready.username, password: ready.password, pin: ready.pin});
        else if (Date.parse(status.session.expires_at) <= Date.now()) {
          status = await api('sync', {});
          if (status.pending) throw Error('Sincroniza los pendientes antes de renovar.');
          await api('release', {session_id: status.session.session_id});
          await api('start', {username: ready.username, password: ready.password, pin: ready.pin});
        }
      } else return reply(res, 404, {error: 'No encontrado.'});
      phaseError = ''; reply(res, 200, {ok: true});
    } finally { busy = false; }
  } catch (error) { reply(res, 400, {error: error.message}); }
}
async function main() {
  if (process.argv.includes('--install-shortcut')) { shortcut(); console.log('Acceso directo Nova POS - Pruebas creado.'); return; }
  try {
    const response = await fetch(PANEL + '/health', {signal: AbortSignal.timeout(900)});
    if ((await response.json()).application === APP) { if (!process.argv.includes('--no-browser')) openPanel(); return; }
  } catch {}
  ownRoot();
  server = http.createServer((req, res) => { void request(req, res); });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(8895, '127.0.0.1', resolve); });
  process.on('SIGINT', () => { void shutdown(); }); process.on('SIGTERM', () => { void shutdown(); });
  if (!process.argv.includes('--no-browser')) openPanel();
  try { await boot(); }
  catch (error) {
    initialized = false;
    phase = 'El laboratorio necesita revisión'; phaseError = error.message;
    fs.appendFileSync(path.join(ROOT, 'logs', 'asistente.log'), new Date().toISOString() + ' ' + error.message + '\n');
    await stopClient();
    if (ready) await control({stop:true}).catch(() => {});
    clearInterval(timer);
  }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = {winToWsl, authorized};

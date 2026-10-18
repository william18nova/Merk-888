const $ = id => document.getElementById(id);
async function status() {
  const s = await chrome.storage.local.get(["status", "pending", "gap"]);
  $("status").textContent = (s.status?.message || "Todavía no hay lecturas.") + (s.pending ? " · Hay un lote guardado esperando envío." : "") + (s.gap ? " · Recuperación histórica pendiente." : "");
}
chrome.storage.local.get("config").then(({config: c}) => {
  if (c) { $("origin").value = c.origin; $("token").value = c.token; $("enabled").checked = c.enabled; }
  status();
});
$("config").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const origin = NovaPTMParser.endpoint($("origin").value.trim());
    const token = $("token").value.trim();
    if (!/^[a-f0-9-]{36}\.[A-Za-z0-9_-]{43}$/.test(token)) throw Error("La clave del lector no tiene el formato esperado.");
    const parsed = new URL(origin);
    const allowed = await chrome.permissions.request({origins: [parsed.protocol + "//" + parsed.hostname + "/*"]});
    if (!allowed) throw Error("Debes permitir únicamente el acceso a tu servidor NovaPOS.");
    const result = await chrome.runtime.sendMessage({type: "configure", config: {origin, token, enabled: $("enabled").checked}});
    if (!result?.ok) throw Error(result?.error || "No se pudo guardar la configuración.");
    $("status").textContent = "Configuración guardada. Puedes consultar ahora.";
  } catch (error) { $("status").textContent = error.message; }
});
async function run() {
  const r = await chrome.runtime.sendMessage({type: "run"});
  if (!r?.ok) throw Error("No se pudo iniciar. Revisa el estado del lector.");
  await status();
}
$("run").onclick = () => run().catch(e => { $("status").textContent = e.message; });
$("recover").onclick = async () => {
  const {job} = await chrome.storage.session.get("job");
  if (job) { $("status").textContent = "Espera a que termine la lectura actual."; return; }
  if (!/^\d{4}-\d{2}-\d{2}$/.test($("date").value)) return;
  await chrome.storage.local.set({manualDate: $("date").value});
  run().catch(e => { $("status").textContent = e.message; });
};
$("clearGap").onclick = async () => { await chrome.storage.local.remove("gap"); await status(); };
chrome.storage.onChanged.addListener((changes, area) => { if (area === "local" && (changes.status || changes.pending || changes.gap)) status(); });

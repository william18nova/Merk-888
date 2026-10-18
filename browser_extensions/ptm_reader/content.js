/* Runs ONLY on the transaction QUERY page and ONLY in the extension's own tab. */
(async () => {
  const msg = data => chrome.runtime.sendMessage(data);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function until(test) {
    for (let i = 0; i < 80; i++) { if (test()) return; await pause(100); }
    throw Error("estructura");
  }
  function table() {
    const node = document.querySelector("#DataTables_Table_0");
    if (!node) throw Error("estructura");
    return NovaPTMParser.parse([...node.querySelectorAll("thead th")].map(n => n.innerText),
      [...node.querySelectorAll("tbody tr")].map(tr => [...tr.cells].map(c => c.innerText)));
  }
  function pagination() {
    const s = document.querySelector(".dataTables_info")?.innerText || "";
    const m = s.match(/registros del\s+(\d+)\s+al\s+(\d+)\s+de un total de\s+(\d+)\s+registros/i);
    if (!m) throw Error("estructura");
    return m.slice(1).map(Number);
  }
  try {
    const job = await msg({type: "ready"});
    if (!job?.action) return; // User's PTM tabs are never changed.
    if (job.action === "navigate") {
      const target = "https://ptm.transacciones.com.co/DistribuidoresTFCC/controller/FCConsultaTransacciones";
      const link = [...document.querySelectorAll("a[href]")].find(a => a.href === target);
      if (!link) throw Error("estructura");
      link.click();
      return;
    }
    const form = document.querySelector("form#formConsultaTX");
    const date = document.querySelector("#fechaTransaccion");
    if (!form || !date || form.getAttribute("action") !== "FCConsultaTransacciones") throw Error("sesion");
    if (job.action === "query") {
      const service = form.querySelector("#cdTipoServicio");
      const product = form.querySelector("#cdProducto");
      const contract = form.querySelector("#contrato");
      const button = form.querySelector('button[name="bt_consultar"]');
      if (!service || !product || !contract || !button || button.type !== "button") throw Error("estructura");
      // Same readonly consultation form used by the user. No sales/control endpoints.
      service.selectedIndex = 0; product.selectedIndex = 0; contract.value = "";
      date.value = job.date.replaceAll("-", "/");
      button.click();
      return;
    }
    if (date.value !== job.date.replaceAll("-", "/")) throw Error("estructura");
    const length = document.querySelector('select[name="DataTables_Table_0_length"]');
    if (!length || ![...length.options].some(o => o.value === "100")) throw Error("estructura");
    length.value = "100";
    length.dispatchEvent(new Event("change", {bubbles: true}));
    await until(() => { const [start, end, total] = pagination(); return !total || (start === 1 && end === Math.min(100, total)); });
    const seen = new Set();
    let count = 0;
    for (let page = 0; page < 100; page++) {
      const [start, end, total] = pagination();
      const rows = table();
      if (rows.length !== (total ? end - start + 1 : 0) || (total && start !== count + 1)) throw Error("estructura");
      for (const row of rows) {
        if (seen.has(row.id_tx) || !row.inicio.startsWith(job.date.replaceAll("-", "/"))) throw Error("estructura");
        seen.add(row.id_tx);
      }
      const ack = await msg({type: "batch", date: job.date, rows});
      if (!ack?.ok) throw Error(ack?.error || "conexion");
      count += rows.length;
      if (end === total && count === total) {
        await msg({type: "complete", date: job.date, count});
        return;
      }
      const next = document.querySelector("#DataTables_Table_0_next");
      if (!next || next.classList.contains("disabled")) throw Error("estructura");
      const nextButton = next.querySelector("a,button") || next;
      nextButton.click();
      await until(() => pagination()[0] === end + 1);
    }
    throw Error("limite");
  } catch (error) {
    await msg({type: "error", error: ["estructura", "sesion", "limite"].includes(error.message) ? error.message : "conexion"}).catch(() => {});
  }
})();

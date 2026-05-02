(() => {
  "use strict";

  const rawData = document.getElementById("retiro-data");
  const DATA = rawData ? JSON.parse(rawData.textContent || "{}") : {};

  const POS_AGENT_URL = (window.POS_AGENT_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");
  const POS_AGENT_TOKEN = (window.POS_AGENT_TOKEN || "").trim();
  const TURNO_CAJA_URL = window.TURNO_CAJA_URL || "/turno_caja/";

  const UNIT = 50;
  const DENOMS = [
    { key: "b100000", value: 100000, label: "Billete $100.000", unit: "billete" },
    { key: "b50000", value: 50000, label: "Billete $50.000", unit: "billete", reserve: 1 },
    { key: "b20000", value: 20000, label: "Billete $20.000", unit: "billete", reserve: 4 },
    { key: "b10000", value: 10000, label: "Billete $10.000", unit: "billete", reserve: 5 },
    { key: "b5000", value: 5000, label: "Billete $5.000", unit: "billete", reserve: 1 },
    { key: "b2000", value: 2000, label: "Billete $2.000", unit: "billete", reserve: 1 },
    { key: "m1000", value: 1000, label: "Moneda $1.000", unit: "moneda", reserve: 1 },
    { key: "m500", value: 500, label: "Moneda $500", unit: "moneda", reserve: 1 },
    { key: "m200", value: 200, label: "Moneda $200", unit: "moneda", reserve: 1 },
    { key: "m100", value: 100, label: "Moneda $100", unit: "moneda", reserve: 1 },
    { key: "m50", value: 50, label: "Moneda $50", unit: "moneda", reserve: 1 },
    { key: "pack_monedas", value: 10000, label: "Paquete monedas", unit: "paquete", locked: true },
    { key: "pack_m50", value: 2000, label: "Paquete monedas de 50", unit: "paquete", locked: true },
  ];

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const denomInputs = $("#denomInputs");
  const retiroRows = $("#retiroRows");
  const baseInput = $("#base_final");
  const totalContadoEl = $("#totalContado");
  const baseFinalEl = $("#baseFinal");
  const retiroSugeridoEl = $("#retiroSugerido");
  const quedaCajaEl = $("#quedaCaja");
  const planNoteEl = $("#planNote");
  const ticketPreview = $("#ticketPreview");
  const btnPrint = $("#btnPrint");

  let currentPlan = null;

  function money(v) {
    const n = Number.isFinite(Number(v)) ? Number(v) : 0;
    return n.toLocaleString("es-CO", {
      style: "currency",
      currency: "COP",
      maximumFractionDigits: 0,
    });
  }

  function plainMoney(v) {
    const n = Number.isFinite(Number(v)) ? Number(v) : 0;
    return `$${Math.round(n).toLocaleString("es-CO")}`;
  }

  function num(v) {
    const n = Number(String(v ?? "").replace(",", ".").trim());
    return Number.isFinite(n) ? n : 0;
  }

  function intVal(v) {
    const n = Math.floor(num(v));
    return n > 0 ? n : 0;
  }

  function denomsKey() {
    return `tc_retiro_denoms_${DATA.turno_id || ""}`;
  }

  function storedDenomPayload() {
    try {
      const raw = sessionStorage.getItem(denomsKey());
      if (!raw) return null;
      const payload = JSON.parse(raw);
      if (!payload || typeof payload !== "object") return null;
      if (Date.now() - (payload.ts || 0) > 24 * 3600 * 1000) return null;
      return payload;
    } catch {
      return null;
    }
  }

  function applyStoredCounts() {
    const payload = storedDenomPayload();
    const counts = payload?.counts;
    if (!counts || typeof counts !== "object") return;
    DENOMS.forEach((d) => {
      const input = $(`[data-denom='${d.key}']`);
      if (input) input.value = String(intVal(counts[d.key] || 0));
    });
  }

  function line(text = "", width = 48) {
    return String(text || "").slice(0, width);
  }

  function lr(left, right, width = 48) {
    left = String(left || "");
    right = String(right || "");
    const spaces = Math.max(1, width - left.length - right.length);
    return left + " ".repeat(spaces) + right;
  }

  function plural(unit, count) {
    return count === 1 ? unit : `${unit}s`;
  }

  function readCounts() {
    const counts = {};
    DENOMS.forEach((d) => {
      counts[d.key] = intVal($(`[data-denom='${d.key}']`)?.value || 0);
    });
    return counts;
  }

  function totalFromCounts(counts) {
    return DENOMS.reduce((acc, d) => acc + (counts[d.key] || 0) * d.value, 0);
  }

  function reserveCounts(counts) {
    const reserved = {};
    DENOMS.forEach((d) => {
      if (d.locked) {
        reserved[d.key] = counts[d.key] || 0;
      } else {
        reserved[d.key] = Math.min(counts[d.key] || 0, d.reserve || 0);
      }
    });
    return reserved;
  }

  function withdrawableCounts(counts) {
    const out = {};
    DENOMS.forEach((d) => {
      out[d.key] = d.locked ? 0 : (counts[d.key] || 0);
    });
    return out;
  }

  function subtractCounts(a, b) {
    const out = {};
    DENOMS.forEach((d) => {
      out[d.key] = Math.max(0, (a[d.key] || 0) - (b[d.key] || 0));
    });
    return out;
  }

  function emptyCounts() {
    const out = {};
    DENOMS.forEach((d) => { out[d.key] = 0; });
    return out;
  }

  function solveWithdrawal(availableCounts, targetAmount) {
    const targetUnits = Math.max(0, Math.floor(targetAmount / UNIT));
    const out = emptyCounts();
    if (targetUnits <= 0) {
      return { amount: 0, pieces: 0, counts: out, exact: true };
    }

    const maxStates = 300000;
    if (targetUnits > maxStates) {
      let remaining = targetAmount;
      let amount = 0;
      let pieces = 0;
      DENOMS.forEach((d) => {
        const take = Math.min(availableCounts[d.key] || 0, Math.floor(remaining / d.value));
        out[d.key] = take;
        amount += take * d.value;
        pieces += take;
        remaining -= take * d.value;
      });
      return { amount, pieces, counts: out, exact: amount === Math.floor(targetAmount / UNIT) * UNIT };
    }

    const INF = 1_000_000_000;
    const dp = new Int32Array(targetUnits + 1);
    const prevAmount = new Int32Array(targetUnits + 1);
    const prevDenom = new Int16Array(targetUnits + 1);
    const prevCount = new Int32Array(targetUnits + 1);
    dp.fill(INF);
    prevAmount.fill(-1);
    prevDenom.fill(-1);
    dp[0] = 0;

    DENOMS.forEach((d, denomIndex) => {
      let count = availableCounts[d.key] || 0;
      let batch = 1;
      while (count > 0) {
        const use = Math.min(batch, count);
        const amountUnits = (d.value / UNIT) * use;
        for (let a = targetUnits; a >= amountUnits; a -= 1) {
          const candidate = dp[a - amountUnits] + use;
          if (candidate < dp[a]) {
            dp[a] = candidate;
            prevAmount[a] = a - amountUnits;
            prevDenom[a] = denomIndex;
            prevCount[a] = use;
          }
        }
        count -= use;
        batch *= 2;
      }
    });

    let best = targetUnits;
    while (best > 0 && dp[best] >= INF) best -= 1;
    const bestUnits = best;
    while (best > 0) {
      const idx = prevDenom[best];
      const used = prevCount[best];
      if (idx < 0 || used <= 0) break;
      out[DENOMS[idx].key] += used;
      best = prevAmount[best];
    }

    return {
      amount: bestUnits * UNIT,
      pieces: dp[bestUnits] >= INF ? 0 : dp[bestUnits],
      counts: out,
      exact: bestUnits === targetUnits,
    };
  }

  function choosePlan(counts, target) {
    const reserved = reserveCounts(counts);
    const availableWithReserve = withdrawableCounts(subtractCounts(counts, reserved));
    const protectedPlan = solveWithdrawal(availableWithReserve, target);
    return { ...protectedPlan, relaxedReserve: false, reserved };
  }

  function buildInputs() {
    if (!denomInputs) return;
    denomInputs.innerHTML = DENOMS.map((d) => `
      <div class="cr-denom">
        <label for="${d.key}">
          <span>${d.label}</span>
          <strong>${plainMoney(d.value)}</strong>
        </label>
        <input id="${d.key}" data-denom="${d.key}" type="number" min="0" step="1" inputmode="numeric" value="0" readonly>
      </div>
    `).join("");
  }

  function renderPlan(plan, counts, total, base) {
    const rows = DENOMS.map((d) => {
      const available = counts[d.key] || 0;
      const take = plan.counts[d.key] || 0;
      const left = Math.max(0, available - take);
      return `
        <tr>
          <td>${d.label}</td>
          <td class="num">${available}</td>
          <td class="num"><b>${take}</b></td>
          <td class="num">${left}</td>
          <td class="num">${money(take * d.value)}</td>
        </tr>
      `;
    }).join("");
    if (retiroRows) retiroRows.innerHTML = rows;

    const remaining = total - plan.amount;
    if (totalContadoEl) totalContadoEl.textContent = money(total);
    if (baseFinalEl) baseFinalEl.textContent = money(base);
    if (retiroSugeridoEl) retiroSugeridoEl.textContent = money(plan.amount);
    if (quedaCajaEl) quedaCajaEl.textContent = money(remaining);

    let note = "";
    if (total < base) {
      note = "El efectivo contado es menor que la base indicada.";
    } else if (remaining > base) {
      note = `Quedan ${money(remaining - base)} por encima de la base porque se conservan paquetes y denominaciones para devueltas.`;
    }
    if (planNoteEl) planNoteEl.textContent = note;

    currentPlan = { ...plan, removeCounts: plan.counts, availableCounts: counts, total, base, remaining };
    if (ticketPreview) ticketPreview.textContent = buildTicket(currentPlan);
  }

  function recalc() {
    const counts = readCounts();
    const total = totalFromCounts(counts);
    const base = Math.max(0, num(baseInput?.value || 0));
    const target = Math.max(0, total - base);
    const plan = choosePlan(counts, target);
    renderPlan(plan, counts, total, base);
  }

  function medioLines() {
    const medios = Array.isArray(DATA.medios) ? DATA.medios : [];
    return medios
      .map((m) => lr(`  ${m.label}:`, plainMoney(m.vendido || 0)));
  }

  function retiroLines(plan) {
    const lines = [];
    const removeCounts = plan.removeCounts || plan.counts || {};
    const availableCounts = plan.availableCounts || {};
    DENOMS.forEach((d) => {
      const take = removeCounts[d.key] || 0;
      if (!take) return;
      const all = take === (availableCounts[d.key] || 0);
      const action = all ? "Saca todos" : "Saca";
      lines.push(lr(`${action} ${take} x ${plainMoney(d.value)}`, plainMoney(take * d.value)));
    });
    return lines.length ? lines : [line("No se recomienda retirar efectivo.")];
  }

  function lockedLines(plan) {
    const lines = [];
    const availableCounts = plan?.availableCounts || {};
    DENOMS.forEach((d) => {
      if (!d.locked) return;
      const count = availableCounts[d.key] || 0;
      if (!count) return;
      lines.push(lr(`  ${d.label}: ${count} x ${plainMoney(d.value)}`, plainMoney(count * d.value)));
    });
    return lines;
  }

  function buildTicket(plan) {
    plan = plan || currentPlan || { removeCounts: emptyCounts(), amount: 0, total: 0, base: 0, remaining: 0 };
    const out = [];
    out.push(line("MERK888"));
    out.push(line("CIERRE DE TURNO"));
    out.push("-".repeat(48));
    out.push(lr("Turno:", `#${DATA.turno_id || ""}`));
    out.push(line(`Punto: ${DATA.puntopago || ""}`));
    out.push(line(`Cajero: ${DATA.cajero || ""}`));
    out.push(line(`Inicio: ${DATA.inicio || ""}`));
    out.push(line(`Fin: ${DATA.fin || ""}`));
    out.push("-".repeat(48));
    out.push(lr("Ventas totales:", plainMoney(DATA.ventas_total_vendido || DATA.ventas_total || 0)));
    out.push(line("Vendido por medio:"));
    out.push(...medioLines());
    out.push(lr("Facturas pagadas:", plainMoney(DATA.facturas_pagadas || 0)));
    out.push(lr("Efectivo fisico:", plainMoney(DATA.efectivo_real || 0)));
    out.push("-".repeat(48));
    out.push(lr("Base a dejar:", plainMoney(plan.base || 0)));
    out.push(lr("Total contado:", plainMoney(plan.total || 0)));
    out.push(lr("Retiro:", plainMoney(plan.amount || 0)));
    out.push(lr("Queda caja:", plainMoney(plan.remaining || 0)));
    out.push("-".repeat(48));
    out.push(line("Denominaciones a sacar:"));
    out.push(...retiroLines(plan));
    const paquetes = lockedLines(plan);
    if (paquetes.length) {
      out.push("-".repeat(48));
      out.push(line("Paquetes que quedan en caja:"));
      out.push(...paquetes);
    }
    out.push("");
    out.push("");
    return out.join("\n");
  }

  async function agentPrintSafe(text, { timeout = 700 } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(`${POS_AGENT_URL}/print`, {
        method: "POST",
        keepalive: true,
        headers: {
          "Content-Type": "application/json",
          "X-Pos-Agent-Token": POS_AGENT_TOKEN,
        },
        body: JSON.stringify({ text }),
        signal: ctrl.signal,
      });
    } catch {
    } finally {
      clearTimeout(timer);
    }
  }

  async function agentKickSafe({ timeout = 450 } = {}) {
    if (!POS_AGENT_TOKEN) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      await fetch(`${POS_AGENT_URL}/kick`, {
        method: "POST",
        keepalive: true,
        headers: { "X-Pos-Agent-Token": POS_AGENT_TOKEN },
        signal: ctrl.signal,
      });
    } catch {
    } finally {
      clearTimeout(timer);
    }
  }

  (function agentWarmup() {
    if (!POS_AGENT_TOKEN) return;
    fetch(`${POS_AGENT_URL}/ping`, {
      method: "GET",
      keepalive: true,
      headers: { "X-Pos-Agent-Token": POS_AGENT_TOKEN },
    }).catch(() => {});
  })();

  function settleWithDeadline(promises, maxWaitMs = 250) {
    return Promise.race([
      Promise.allSettled(promises),
      new Promise((resolve) => setTimeout(resolve, maxWaitMs)),
    ]);
  }

  async function printCurrent() {
    if (btnPrint) btnPrint.disabled = true;
    recalc();
    const text = buildTicket(currentPlan) + "\n\n\n\n";
    if (ticketPreview) ticketPreview.textContent = text;
    try {
      const p1 = agentKickSafe({ timeout: 450 });
      const p2 = agentPrintSafe(text, { timeout: 850 });
      await settleWithDeadline([p1, p2], 250);
    } catch {
    } finally {
      try { sessionStorage.removeItem(denomsKey()); } catch {}
      setTimeout(() => window.location.assign(TURNO_CAJA_URL), 300);
    }
  }

  buildInputs();
  applyStoredCounts();
  if (baseInput && !baseInput.value) {
    baseInput.value = String(Math.max(0, Math.round(num(DATA.base_apertura || 0))));
  }
  recalc();

  baseInput?.addEventListener("input", recalc);
  denomInputs?.addEventListener("input", (event) => {
    const target = event.target;
    if (target && target.matches("input[data-denom]")) {
      if (num(target.value) < 0) target.value = "0";
      recalc();
    }
  });
  btnPrint?.addEventListener("click", printCurrent);
})();

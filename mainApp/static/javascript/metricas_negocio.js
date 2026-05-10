(function(){
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const money = (value) => new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    maximumFractionDigits: 0
  }).format(Number(value || 0));
  const num = (value) => new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 }).format(Number(value || 0));

  const charts = {};
  const palette = ["#4da6ff", "#32d583", "#ffd166", "#ff5b6b", "#b8dbff", "#3d8cff", "#e0f2fe", "#fca5a5"];

  const els = {
    desde: $("#mn-desde"),
    hasta: $("#mn-hasta"),
    sucursal: $("#mn-sucursal"),
    puntopago: $("#mn-puntopago"),
    refresh: $("#mn-refresh"),
    error: $("#mn-error"),
    rangeLabel: $("#metrics-range-label")
  };

  const allPuntoOptions = els.puntopago
    ? Array.from(els.puntopago.options).map((option) => ({
      value: option.value,
      text: option.textContent,
      sucursal: option.dataset.sucursal || ""
    }))
    : [];

  function iso(date) {
    return date.toISOString().slice(0, 10);
  }

  function setError(message) {
    if (!els.error) return;
    els.error.hidden = !message;
    els.error.textContent = message || "";
  }

  function setQuickRange(range) {
    const today = new Date();
    let start = new Date(today);
    let end = new Date(today);

    if (range === "7") start.setDate(today.getDate() - 6);
    if (range === "30") start.setDate(today.getDate() - 29);
    if (range === "month") start = new Date(today.getFullYear(), today.getMonth(), 1);

    els.desde.value = iso(start);
    els.hasta.value = iso(end);
    $$(".quick-range").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.range === range));
    loadMetrics();
  }

  function refreshPuntoPagoOptions() {
    if (!els.puntopago) return;
    const selectedSucursal = els.sucursal.value || "";
    const current = els.puntopago.value;
    els.puntopago.innerHTML = "";

    allPuntoOptions.forEach((optionData) => {
      if (optionData.value && selectedSucursal && optionData.sucursal !== selectedSucursal) return;
      const option = document.createElement("option");
      option.value = optionData.value;
      option.textContent = optionData.value ? optionData.text : (selectedSucursal ? "Todos los puntos de la sucursal" : "Todos los puntos");
      if (optionData.sucursal) option.dataset.sucursal = optionData.sucursal;
      els.puntopago.appendChild(option);
    });

    if ([...els.puntopago.options].some((option) => option.value === current)) {
      els.puntopago.value = current;
    }
  }

  function changeText(value) {
    if (value === null || value === undefined) return { text: "Sin comparacion previa", cls: "" };
    const v = Number(value);
    if (!Number.isFinite(v) || v === 0) return { text: "0% vs periodo anterior", cls: "" };
    return {
      text: `${v > 0 ? "+" : ""}${v.toFixed(2)}% vs periodo anterior`,
      cls: v > 0 ? "up" : "down"
    };
  }

  function updateSummary(data) {
    const summary = data.summary || {};
    const map = {
      total_sales: money(summary.total_sales),
      sale_count: num(summary.sale_count),
      avg_ticket: money(summary.avg_ticket),
      units_sold: num(summary.units_sold),
      active_customers: num(summary.active_customers),
      cash_total: money(summary.cash_total),
      non_cash_total: money(summary.non_cash_total),
      negative_stock_count: num(summary.negative_stock_count)
    };

    Object.entries(map).forEach(([key, value]) => {
      const el = document.querySelector(`[data-metric="${key}"]`);
      if (el) el.textContent = value;
    });

    Object.entries(data.comparison || {}).forEach(([key, value]) => {
      const el = document.querySelector(`[data-change="${key}"]`);
      if (!el) return;
      const info = changeText(value);
      el.textContent = info.text;
      el.className = info.cls;
    });

    const f = data.filters || {};
    els.rangeLabel.textContent = `${f.desde || ""} a ${f.hasta || ""} - ${num(f.dias)} dias`;
  }

  function makeChart(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas || !window.Chart) return;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(canvas, config);
  }

  function commonOptions(extra = {}) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#d8e8ff" } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const label = ctx.dataset.label ? `${ctx.dataset.label}: ` : "";
              return label + money(ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed);
            }
          }
        }
      },
      scales: {
        x: { ticks: { color: "#d8e8ff" }, grid: { color: "rgba(255,255,255,.08)" } },
        y: { ticks: { color: "#d8e8ff" }, grid: { color: "rgba(255,255,255,.08)" } }
      },
      ...extra
    };
  }

  function renderCharts(data) {
    const chartsData = data.charts || {};
    const daily = chartsData.daily || [];
    makeChart("chart-daily", {
      type: "line",
      data: {
        labels: daily.map((row) => row.label),
        datasets: [
          { label: "Total vendido", data: daily.map((row) => row.total), borderColor: "#4da6ff", backgroundColor: "rgba(77,166,255,.20)", tension: .28, fill: true, yAxisID: "y" },
          { label: "Ventas", data: daily.map((row) => row.ventas), borderColor: "#32d583", backgroundColor: "rgba(50,213,131,.14)", tension: .28, yAxisID: "y1" }
        ]
      },
      options: commonOptions({
        scales: {
          x: { ticks: { color: "#d8e8ff", maxRotation: 0, autoSkip: true }, grid: { color: "rgba(255,255,255,.08)" } },
          y: { position: "left", ticks: { color: "#d8e8ff", callback: (v) => money(v) }, grid: { color: "rgba(255,255,255,.08)" } },
          y1: { position: "right", ticks: { color: "#d8e8ff" }, grid: { drawOnChartArea: false } }
        }
      })
    });

    const payments = chartsData.payments || [];
    makeChart("chart-payments", {
      type: "doughnut",
      data: {
        labels: payments.map((row) => row.label),
        datasets: [{ data: payments.map((row) => row.total), backgroundColor: palette, borderColor: "rgba(255,255,255,.18)" }]
      },
      options: commonOptions({ scales: {} })
    });

    const byHour = chartsData.by_hour || [];
    makeChart("chart-hour", {
      type: "bar",
      data: {
        labels: byHour.map((row) => row.label),
        datasets: [{ label: "Total vendido", data: byHour.map((row) => row.total), backgroundColor: "rgba(77,166,255,.72)" }]
      },
      options: commonOptions()
    });

    const byWeekday = chartsData.by_weekday || [];
    makeChart("chart-weekday", {
      type: "bar",
      data: {
        labels: byWeekday.map((row) => row.label),
        datasets: [{ label: "Promedio vendido", data: byWeekday.map((row) => row.average_total), backgroundColor: "rgba(255,209,102,.78)" }]
      },
      options: commonOptions()
    });

    const categories = chartsData.categories || [];
    makeChart("chart-categories", {
      type: "bar",
      data: {
        labels: categories.map((row) => row.label),
        datasets: [{ label: "Total vendido", data: categories.map((row) => row.total), backgroundColor: "rgba(50,213,131,.70)" }]
      },
      options: commonOptions()
    });

    const byMonthDay = chartsData.by_month_day || [];
    makeChart("chart-month-day", {
      type: "bar",
      data: {
        labels: byMonthDay.map((row) => row.label),
        datasets: [{ label: "Promedio vendido", data: byMonthDay.map((row) => row.average_total), backgroundColor: "rgba(184,219,255,.74)" }]
      },
      options: commonOptions({
        scales: {
          x: { ticks: { color: "#d8e8ff", autoSkip: false, maxRotation: 0 }, grid: { color: "rgba(255,255,255,.08)" } },
          y: { ticks: { color: "#d8e8ff", callback: (v) => money(v) }, grid: { color: "rgba(255,255,255,.08)" } }
        }
      })
    });

    const products = chartsData.top_products || [];
    makeChart("chart-products", {
      type: "bar",
      data: {
        labels: products.map((row) => row.producto),
        datasets: [{ label: "Total vendido", data: products.map((row) => row.total), backgroundColor: "rgba(255,209,102,.78)" }]
      },
      options: commonOptions({ indexAxis: "y" })
    });
  }

  function renderRows(tbodyId, rows, columns, emptyText) {
    const body = document.getElementById(tbodyId);
    if (!body) return;
    body.innerHTML = "";
    if (!rows || !rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = columns.length;
      td.textContent = emptyText;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        if (col.className) td.className = col.className(row) || "";
        td.textContent = col.value(row);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function renderTables(data) {
    const tables = data.tables || {};
    renderRows("table-products", tables.top_products, [
      { value: (r) => r.producto },
      { value: (r) => num(r.cantidad) },
      { value: (r) => money(r.total) }
    ], "Sin ventas de productos en este rango.");

    renderRows("table-cashiers", tables.cashiers, [
      { value: (r) => r.nombre },
      { value: (r) => num(r.ventas) },
      { value: (r) => money(r.total) },
      { value: (r) => money(r.promedio) }
    ], "Sin cajeros para este rango.");

    renderRows("table-stock", tables.low_stock, [
      { value: (r) => r.producto },
      { value: (r) => r.sucursal },
      { value: (r) => num(r.cantidad), className: (r) => Number(r.cantidad) < 0 ? "is-negative" : "" }
    ], "Sin alertas de stock bajo.");

    renderRows("table-orders", tables.orders, [
      { value: (r) => r.estado },
      { value: (r) => num(r.cantidad) },
      { value: (r) => money(r.total) }
    ], "Sin pedidos en este rango.");

    renderRows("table-returns", tables.returns, [
      { value: (r) => r.tipo },
      { value: (r) => r.estado },
      { value: (r) => num(r.cantidad) }
    ], "Sin cambios o devoluciones.");
  }

  async function loadMetrics() {
    setError("");
    const params = new URLSearchParams({
      desde: els.desde.value || "",
      hasta: els.hasta.value || "",
      sucursal_id: els.sucursal.value || "",
      puntopago_id: els.puntopago.value || ""
    });

    try {
      const response = await fetch(`${window.metricasNegocioDataUrl}?${params.toString()}`, {
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "XMLHttpRequest"
        }
      });
      const contentType = response.headers.get("content-type") || "";
      const raw = await response.text();
      let data = {};
      if (contentType.includes("application/json")) {
        data = raw ? JSON.parse(raw) : {};
      } else {
        const redirected = response.redirected ? " Revisa si la sesion expiro o si falta un permiso." : "";
        throw new Error(`El servidor respondio HTML en vez de JSON.${redirected}`);
      }
      if (!response.ok || !data.success) throw new Error(data.error || "No se pudieron cargar las metricas.");
      updateSummary(data);
      renderCharts(data);
      renderTables(data);
    } catch (err) {
      setError(err.message || "Error cargando metricas.");
    }
  }

  els.sucursal?.addEventListener("change", () => {
    refreshPuntoPagoOptions();
    loadMetrics();
  });
  els.puntopago?.addEventListener("change", loadMetrics);
  els.desde?.addEventListener("change", loadMetrics);
  els.hasta?.addEventListener("change", loadMetrics);
  els.refresh?.addEventListener("click", loadMetrics);
  $$(".quick-range").forEach((btn) => btn.addEventListener("click", () => setQuickRange(btn.dataset.range)));

  refreshPuntoPagoOptions();
  loadMetrics();
})();

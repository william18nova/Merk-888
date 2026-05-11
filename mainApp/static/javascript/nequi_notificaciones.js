(function () {
  const page = document.querySelector(".nequi-page");
  if (!page) return;

  const feedUrl = page.dataset.feedUrl;
  const list = document.getElementById("nequi-list");
  const refreshBtn = document.getElementById("nequi-refresh");
  const status = document.getElementById("nequi-status");
  const summaryEls = {
    hoy_total: document.querySelector('[data-nequi-summary="hoy_total"]'),
    hoy_count: document.querySelector('[data-nequi-summary="hoy_count"]'),
    ultima_monto: document.querySelector('[data-nequi-summary="ultima_monto"]'),
    ultima_hora: document.querySelector('[data-nequi-summary="ultima_hora"]'),
  };

  let lastSeenId = Math.max(
    0,
    ...Array.from(document.querySelectorAll(".nequi-item[data-id]"))
      .map((node) => Number(node.dataset.id || 0))
      .filter(Number.isFinite)
  );
  let firstLoad = true;
  let timer = null;

  function formatMoney(value) {
    if (value === null || value === undefined || value === "") return "-";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return `$ ${value}`;
    return new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency: "COP",
      maximumFractionDigits: 0,
    }).format(amount);
  }

  function setStatus(text, ok) {
    if (!status) return;
    status.classList.toggle("is-error", !ok);
    status.lastChild.textContent = ` ${text}`;
  }

  function setLoading(isLoading) {
    if (!refreshBtn) return;
    refreshBtn.disabled = isLoading;
    refreshBtn.textContent = isLoading ? "Actualizando" : "Actualizar";
  }

  function textNode(text) {
    return document.createTextNode(text || "");
  }

  function pill(text) {
    const span = document.createElement("span");
    span.textContent = text;
    return span;
  }

  function renderItem(item, isNew) {
    const article = document.createElement("article");
    article.className = "nequi-item";
    if (isNew) article.classList.add("is-new");
    article.dataset.id = item.id;

    const main = document.createElement("div");
    main.className = "nequi-item__main";

    const title = document.createElement("div");
    title.className = "nequi-item__title";

    const amount = document.createElement("strong");
    amount.textContent = item.monto ? formatMoney(item.monto) : "Pago Nequi";

    const time = document.createElement("span");
    time.textContent = `${item.fecha || ""} ${item.hora || ""}`.trim();

    title.append(amount, time);

    const text = document.createElement("p");
    text.appendChild(textNode(item.texto || item.titulo || "Notificacion de Nequi"));

    const meta = document.createElement("div");
    meta.className = "nequi-item__meta";
    if (item.remitente) meta.appendChild(pill(item.remitente));
    if (item.referencia) meta.appendChild(pill(`Ref. ${item.referencia}`));
    if (item.app) meta.appendChild(pill(item.app));
    if (item.paquete) meta.appendChild(pill(item.paquete));

    main.append(title, text, meta);
    article.appendChild(main);

    if (isNew) {
      window.setTimeout(() => article.classList.remove("is-new"), 2800);
    }

    return article;
  }

  function renderSummary(summary) {
    if (!summary) return;
    if (summaryEls.hoy_total) summaryEls.hoy_total.textContent = formatMoney(summary.hoy_total);
    if (summaryEls.hoy_count) summaryEls.hoy_count.textContent = summary.hoy_count ?? "0";
    if (summaryEls.ultima_monto) summaryEls.ultima_monto.textContent = summary.ultima_monto ? formatMoney(summary.ultima_monto) : "-";
    if (summaryEls.ultima_hora) summaryEls.ultima_hora.textContent = summary.ultima_hora || "Sin registros";
  }

  function renderList(items) {
    const nextMax = Math.max(0, ...items.map((item) => Number(item.id || 0)));
    list.replaceChildren();

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "nequi-empty";
      empty.textContent = "Todavia no hay notificaciones de Nequi.";
      list.appendChild(empty);
      lastSeenId = nextMax;
      firstLoad = false;
      return;
    }

    items.forEach((item) => {
      const id = Number(item.id || 0);
      list.appendChild(renderItem(item, !firstLoad && id > lastSeenId));
    });

    lastSeenId = nextMax;
    firstLoad = false;
  }

  async function loadFeed(manual) {
    if (!feedUrl) return;
    if (manual) setLoading(true);

    try {
      const response = await fetch(feedUrl, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || "No se pudo consultar Nequi.");
      }
      renderSummary(data.summary);
      renderList(data.items || []);
      setStatus("En vivo", true);
    } catch (error) {
      setStatus("Sin conexion", false);
      console.error(error);
    } finally {
      if (manual) setLoading(false);
    }
  }

  refreshBtn?.addEventListener("click", () => loadFeed(true));

  loadFeed(false);
  timer = window.setInterval(() => loadFeed(false), 4000);
  window.addEventListener("beforeunload", () => {
    if (timer) window.clearInterval(timer);
  });
})();

(function () {
  const page = document.querySelector(".nequi-page");
  if (!page) return;

  const feedUrl = page.dataset.feedUrl;
  const deleteUrlTemplate = page.dataset.deleteUrlTemplate || "";
  const bulkDeleteUrl = page.dataset.bulkDeleteUrl || "";
  const list = document.getElementById("nequi-list");
  const refreshBtn = document.getElementById("nequi-refresh");
  const selectAll = document.getElementById("nequi-select-all");
  const deleteSelectedBtn = document.getElementById("nequi-delete-selected");
  const selectedCount = document.getElementById("nequi-selected-count");
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
  const selectedIds = new Set();

  function getCSRF() {
    const m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[2]) : "";
  }

  function deleteUrlFor(id) {
    if (!deleteUrlTemplate || !id) return "";
    return deleteUrlTemplate.replace("/0/", `/${encodeURIComponent(id)}/`);
  }

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

  function updateBulkActions() {
    const count = selectedIds.size;
    if (selectedCount) selectedCount.textContent = String(count);
    if (deleteSelectedBtn) deleteSelectedBtn.disabled = count === 0;

    if (selectAll && list) {
      const checks = Array.from(list.querySelectorAll(".nequi-row-check:not(:disabled)"));
      const checked = checks.filter((input) => input.checked);
      selectAll.checked = checks.length > 0 && checked.length === checks.length;
      selectAll.indeterminate = checked.length > 0 && checked.length < checks.length;
      selectAll.disabled = checks.length === 0;
    }
  }

  function renderItem(item, isNew) {
    const article = document.createElement("article");
    article.className = "nequi-item";
    if (isNew) article.classList.add("is-new");
    article.dataset.id = item.id;

    const checkLabel = document.createElement("label");
    checkLabel.className = "nequi-check";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "nequi-row-check";
    check.value = item.id;
    check.disabled = !!(item.usada || item.venta_id);
    check.checked = selectedIds.has(String(item.id)) && !check.disabled;
    checkLabel.append(check, document.createElement("span"));

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
    if (item.venta_id) meta.appendChild(pill(`Usada en venta #${item.venta_id}`));

    main.append(title, text, meta);

    const actions = document.createElement("div");
    actions.className = "nequi-item__actions";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "nequi-delete";
    if (item.usada || item.venta_id) {
      del.textContent = "Usada";
      del.disabled = true;
    } else {
      del.textContent = "Eliminar";
      del.dataset.deleteId = item.id;
    }
    actions.appendChild(del);

    article.append(checkLabel, main, actions);

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
    const selectable = new Set(
      items
        .filter((item) => !(item.usada || item.venta_id))
        .map((item) => String(item.id))
    );
    for (const id of Array.from(selectedIds)) {
      if (!selectable.has(id)) selectedIds.delete(id);
    }

    list.replaceChildren();

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "nequi-empty";
      empty.textContent = "Todavia no hay notificaciones de Nequi.";
      list.appendChild(empty);
      lastSeenId = nextMax;
      firstLoad = false;
      updateBulkActions();
      return;
    }

    items.forEach((item) => {
      const id = Number(item.id || 0);
      list.appendChild(renderItem(item, !firstLoad && id > lastSeenId));
    });

    lastSeenId = nextMax;
    firstLoad = false;
    updateBulkActions();
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

  async function deleteNotification(id, button) {
    const url = deleteUrlFor(id);
    if (!url) return;
    if (!window.confirm("¿Eliminar esta notificación de Nequi?")) return;

    const oldText = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Eliminando";
    }

    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": getCSRF(),
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || "No se pudo eliminar la notificación.");
      }
      renderSummary(data.summary);
      await loadFeed(false);
      setStatus("En vivo", true);
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = oldText || "Eliminar";
      }
      window.alert(error.message || "No se pudo eliminar la notificación.");
      console.error(error);
    }
  }

  async function deleteSelectedNotifications() {
    const ids = Array.from(selectedIds);
    if (!ids.length || !bulkDeleteUrl) return;
    if (!window.confirm(`¿Eliminar ${ids.length} notificación(es) seleccionada(s)?`)) return;

    deleteSelectedBtn.disabled = true;
    const oldText = deleteSelectedBtn.firstChild ? deleteSelectedBtn.firstChild.textContent : "";
    if (deleteSelectedBtn.firstChild) deleteSelectedBtn.firstChild.textContent = "Eliminando seleccionadas ";

    try {
      const response = await fetch(bulkDeleteUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRF(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ ids }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || "No se pudieron eliminar las notificaciones.");
      }

      selectedIds.clear();
      renderSummary(data.summary);
      await loadFeed(false);
      const skipped = Number(data.protected || 0);
      setStatus(skipped > 0 ? `Eliminadas, ${skipped} usadas se conservaron` : "En vivo", true);
    } catch (error) {
      window.alert(error.message || "No se pudieron eliminar las notificaciones.");
      console.error(error);
    } finally {
      if (deleteSelectedBtn.firstChild) deleteSelectedBtn.firstChild.textContent = oldText || "Eliminar seleccionadas ";
      updateBulkActions();
    }
  }

  refreshBtn?.addEventListener("click", () => loadFeed(true));
  list?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-id]");
    if (button) {
      event.preventDefault();
      deleteNotification(button.dataset.deleteId, button);
    }
  });

  list?.addEventListener("change", (event) => {
    const check = event.target.closest(".nequi-row-check");
    if (!check) return;
    const id = String(check.value || "");
    if (!id) return;
    if (check.checked) selectedIds.add(id);
    else selectedIds.delete(id);
    updateBulkActions();
  });

  selectAll?.addEventListener("change", () => {
    const checks = Array.from(list.querySelectorAll(".nequi-row-check:not(:disabled)"));
    for (const check of checks) {
      check.checked = selectAll.checked;
      const id = String(check.value || "");
      if (!id) continue;
      if (check.checked) selectedIds.add(id);
      else selectedIds.delete(id);
    }
    updateBulkActions();
  });
  deleteSelectedBtn?.addEventListener("click", deleteSelectedNotifications);

  loadFeed(false);
  timer = window.setInterval(() => loadFeed(false), 4000);
  window.addEventListener("beforeunload", () => {
    if (timer) window.clearInterval(timer);
  });
})();

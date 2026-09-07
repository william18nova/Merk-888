(() => {
  "use strict";
  const configElement = document.getElementById("schedule-config");
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent);
  const $ = id => document.getElementById(id);
  const root = $("schedule-calendar");
  const dialog = $("schedule-dialog");
  const dayMs = 86400000;
  const dateParts = new Intl.DateTimeFormat("en", {timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(new Date());
  const part = type => dateParts.find(item => item.type === type).value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  const date = str => new Date(`${str}T00:00:00Z`);
  const iso = value => value.toISOString().slice(0, 10);
  const addDays = (str, count) => iso(new Date(date(str).getTime() + count * dayMs));
  const niceDate = str => date(str).toLocaleDateString("es-CO", {timeZone: "UTC", day: "numeric", month: "long", year: "numeric"});
  let focus = today, view = "week", events = [], selected = null, busy = false;
  let request = null, pendingWrite = null;
  const fields = ["shift-employee", "shift-branch", "shift-start", "shift-end", "shift-notes"];

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function option(select, value, label) {
    const item = new Option(label, value);
    select.add(item);
  }
  config.empleados.forEach(emp => {
    option($("shift-employee"), emp.id, emp.nombre);
    if ($("schedule-employee-filter")) option($("schedule-employee-filter"), emp.id, emp.nombre);
  });
  config.sucursales.forEach(branch => {
    option($("shift-branch"), branch.sucursalid, branch.nombre);
    if ($("schedule-branch-filter")) option($("schedule-branch-filter"), branch.sucursalid, branch.nombre);
  });
  $("schedule-date").value = today;

  function bounds() {
    if (view === "month") {
      const first = focus.slice(0, 8) + "01";
      const start = addDays(first, -(date(first).getUTCDay() + 6) % 7);
      return [start, addDays(start, 41)];
    }
    const start = addDays(focus, -(date(focus).getUTCDay() + 6) % 7);
    return [start, addDays(start, 6)];
  }

  function status(message = "", error = false) {
    $("schedule-status").textContent = message;
    $("schedule-status").classList.toggle("is-error", error);
  }

  function periodLabel(start, end) {
    $("schedule-period").textContent = view === "month"
      ? date(focus).toLocaleDateString("es-CO", {timeZone: "UTC", month: "long", year: "numeric"})
      : `${date(start).getUTCDate()} – ${niceDate(end)}`;
  }

  async function refresh() {
    if (request) request.abort();
    request = new AbortController();
    const current = request;
    const [start, end] = bounds();
    periodLabel(start, end);
    root.setAttribute("aria-busy", "true");
    status("Consultando horarios…");
    const url = new URL(config.datosUrl, location.origin);
    url.searchParams.set("desde", start);
    url.searchParams.set("hasta", end);
    if ($("schedule-employee-filter")?.value) url.searchParams.set("empleado_id", $("schedule-employee-filter").value);
    if ($("schedule-branch-filter")?.value) url.searchParams.set("sucursal_id", $("schedule-branch-filter").value);
    try {
      const response = await fetch(url, {signal: current.signal, cache: "no-store", headers: {Accept: "application/json"}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "No se pudo consultar el calendario.");
      if (current !== request) return;
      events = data.eventos;
      render();
      status(events.length ? "" : "No hay jornadas programadas para este intervalo y los filtros seleccionados.");
      return true;
    } catch (error) {
      if (error.name !== "AbortError") {
        status(error.message || "No hay conexión. No se guardan cambios sin conexión.", true);
        events = [];
        $("schedule-count").textContent = "—";
        $("schedule-hours").textContent = "—";
        root.replaceChildren(node("div", "sch-empty", "No se pudieron cargar los horarios. Pulsa Actualizar para reintentar."));
      }
      return false;
    } finally {
      if (current === request) root.setAttribute("aria-busy", "false");
    }
  }

  function eventOnDay(event, day) {
    const begin = new Date(`${day}T00:00:00-05:00`).getTime();
    return new Date(event.inicio).getTime() < begin + dayMs && new Date(event.fin).getTime() > begin;
  }

  function eventCard(event, day) {
    const card = node("button", "sch-event");
    card.type = "button";
    const startTime = event.inicio.slice(11, 16), endTime = event.fin.slice(11, 16);
    const overnight = event.inicio.slice(0, 10) !== event.fin.slice(0, 10);
    card.append(node("span", "sch-event-time", `${startTime} – ${endTime}${overnight ? " · nocturno" : ""}`), node("strong", "", event.empleado), node("small", "", event.sucursal));
    card.title = `${event.empleado} · ${niceDate(event.inicio.slice(0, 10))} ${startTime} a ${niceDate(event.fin.slice(0, 10))} ${endTime}. ${event.notas}`;
    card.setAttribute("aria-label", `${event.empleado}, ${day}, ${startTime} a ${endTime}, ${event.sucursal}. Ver turno`);
    const colors = ["#80c5ff", "#6ed7c4", "#bbabef", "#f0bc86", "#8bd0d8"];
    card.style.borderLeftColor = colors[event.empleado_id % colors.length];
    let pointer = null, suppressClick = false;
    card.addEventListener("click", e => {
      if (suppressClick) { suppressClick = false; e.preventDefault(); return; }
      openEvent(event);
    });
    if (config.editable && view !== "list") {
      card.classList.add("is-movable");
      // Pointer capture evita depender del arrastre HTML5 del navegador. En
      // pantallas táctiles se conserva el desplazamiento normal y la edición.
      card.addEventListener("pointerdown", e => {
        if (e.button !== 0 || e.pointerType === "touch" || busy) return;
        suppressClick = false;
        pointer = {id: e.pointerId, x: e.clientX, y: e.clientY, moving: false};
        card.setPointerCapture(e.pointerId);
      });
      card.addEventListener("pointermove", e => {
        if (!pointer || e.pointerId !== pointer.id) return;
        if (Math.hypot(e.clientX - pointer.x, e.clientY - pointer.y) < 8 && !pointer.moving) return;
        pointer.moving = true;
        card.classList.add("is-dragging");
        root.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
        const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".sch-day");
        if (target && root.contains(target)) target.classList.add("is-drop-target");
      });
      const finish = e => {
        if (!pointer || pointer.id !== e.pointerId) return;
        const moved = pointer.moving;
        pointer = null;
        suppressClick = moved;
        if (card.hasPointerCapture(e.pointerId)) card.releasePointerCapture(e.pointerId);
        card.classList.remove("is-dragging");
        root.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
        const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".sch-day");
        if (e.type === "pointerup" && moved && target && root.contains(target) && target.dataset.date !== day) {
          const delta = (date(target.dataset.date).getTime() - date(day).getTime()) / dayMs;
          const shifted = stamp => addDays(stamp.slice(0, 10), delta) + stamp.slice(10, 16);
          openEvent(event, null, {inicio: shifted(event.inicio), fin: shifted(event.fin)});
        }
      };
      card.addEventListener("pointerup", finish);
      card.addEventListener("pointercancel", finish);
    }
    return card;
  }

  function render() {
    const [start, end] = bounds();
    const rangeStart = new Date(`${start}T00:00:00-05:00`).getTime();
    const rangeEnd = new Date(`${addDays(end, 1)}T00:00:00-05:00`).getTime();
    $("schedule-count").textContent = events.length;
    const hours = events.reduce((total, item) => total + Math.max(0, Math.min(new Date(item.fin).getTime(), rangeEnd) - Math.max(new Date(item.inicio).getTime(), rangeStart)) / 3600000, 0);
    $("schedule-hours").textContent = `${hours.toLocaleString("es-CO", {maximumFractionDigits: 1})} h`;
    root.replaceChildren();
    if (view === "list") {
      const list = node("div", "sch-list");
      if (!events.length) {
        const empty = node("div", "sch-empty");
        empty.append(node("b", "", "Una semana por organizar"), node("span", "", config.editable ? "Asigna el primer turno para verlo aquí." : "Aquí aparecerán tus jornadas cuando sean asignadas."));
        list.append(empty);
      }
      let lastDay = "";
      events.forEach(event => {
        const day = event.inicio.slice(0, 10);
        if (day !== lastDay) list.append(node("h3", "sch-list-date", niceDate(day)));
        list.append(eventCard(event, day));
        lastDay = day;
      });
      root.append(list);
      return;
    }
    const grid = node("div", "sch-grid" + (view === "month" ? " sch-month" : ""));
    for (let day = start; day <= end; day = addDays(day, 1)) {
      const value = day;
      const column = node("section", "sch-day");
      column.dataset.date = day;
      column.setAttribute("aria-label", niceDate(day));
      column.classList.toggle("is-today", day === today);
      column.classList.toggle("is-other-month", view === "month" && day.slice(0, 7) !== focus.slice(0, 7));
      const heading = node("div", "sch-day-heading");
      heading.append(node("span", "", date(day).toLocaleDateString("es-CO", {timeZone: "UTC", weekday: "short"}).replace(".", "").toUpperCase()), node("span", "sch-day-number", date(day).getUTCDate()));
      column.append(heading);
      const dailyEvents = events.filter(event => eventOnDay(event, day));
      dailyEvents.forEach(event => column.append(eventCard(event, day)));
      if (config.editable) {
        const add = node("button", "sch-day-add", "+ Asignar");
        add.type = "button";
        add.setAttribute("aria-label", `Asignar turno el ${niceDate(day)}`);
        add.addEventListener("click", () => openEvent(null, value));
        column.append(add);
      } else if (!dailyEvents.length) {
        column.append(node("p", "sch-empty-day", "Sin turnos"));
      }
      grid.append(column);
    }
    root.append(grid);
  }

  function ensureOption(select, value, label) {
    if (![...select.options].some(item => item.value === String(value))) option(select, value, label);
  }

  function openEvent(event, day = focus, shifted = null) {
    if (busy || (!config.editable && !event)) return;
    selected = event;
    pendingWrite = null;
    $("schedule-form-error").textContent = "";
    $("schedule-dialog-title").textContent = shifted ? "Mover turno" : event ? (config.editable ? "Editar turno" : "Detalle de mi turno") : "Asignar turno";
    $("schedule-dialog-hint").textContent = shifted ? "Revisa las nuevas fechas y confirma el movimiento con Guardar turno." : "Fecha y hora de Colombia. Los cambios se reflejan en el calendario del empleado.";
    if (event) {
      ensureOption($("shift-employee"), event.empleado_id, event.empleado);
      ensureOption($("shift-branch"), event.sucursal_id, event.sucursal);
    }
    $("shift-employee").value = event?.empleado_id || $("schedule-employee-filter")?.value || config.empleados[0]?.id || "";
    const employee = config.empleados.find(item => item.id === Number($("shift-employee").value));
    $("shift-branch").value = event?.sucursal_id || $("schedule-branch-filter")?.value || employee?.sucursal_id || config.sucursales[0]?.sucursalid || "";
    $("shift-start").value = shifted?.inicio || event?.inicio.slice(0, 16) || `${day}T08:00`;
    $("shift-end").value = shifted?.fin || event?.fin.slice(0, 16) || `${day}T17:00`;
    $("shift-notes").value = event?.notas || "";
    fields.forEach(id => $(id).disabled = !config.editable);
    $("schedule-audit").textContent = event ? `Turno #${event.id} · Último cambio: ${event.actualizado_por || "administración"}${event.actualizado_en ? ", " + new Date(event.actualizado_en).toLocaleString("es-CO", {timeZone: "America/Bogota"}) : ""}` : "";
    if ($("schedule-cancel-turn")) $("schedule-cancel-turn").hidden = !event;
    dialog.showModal();
  }

  function closeDialog() { if (!busy) dialog.close(); }
  $("schedule-close").addEventListener("click", closeDialog);
  $("schedule-dismiss").addEventListener("click", closeDialog);
  dialog.addEventListener("cancel", e => { if (busy) e.preventDefault(); });
  $("shift-employee").addEventListener("change", () => {
    const employee = config.empleados.find(item => item.id === Number($("shift-employee").value));
    if (employee?.sucursal_id) $("shift-branch").value = employee.sucursal_id;
  });

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
  }
  async function save(cancel = false) {
    if (busy || !config.editable) return;
    const payload = {operacion: cancel ? "cancelar" : selected ? "editar" : "crear"};
    if (selected) Object.assign(payload, {id: selected.id, version: selected.version});
    if (!cancel) Object.assign(payload, {empleado_id: Number($("shift-employee").value), sucursal_id: Number($("shift-branch").value), inicio: $("shift-start").value, fin: $("shift-end").value, notas: $("shift-notes").value});
    const signature = JSON.stringify(payload);
    if (!pendingWrite || pendingWrite.signature !== signature) pendingWrite = {signature, id: uuid()};
    payload.solicitud_id = pendingWrite.id;
    busy = true;
    $("schedule-form-error").textContent = "";
    dialog.querySelectorAll("button").forEach(button => button.disabled = true);
    fields.forEach(id => $(id).disabled = true);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(config.guardarUrl, {signal: controller.signal, method: "POST", headers: {"Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": document.querySelector("#schedule-form [name=csrfmiddlewaretoken]").value}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "No se pudo guardar el turno.");
      dialog.close();
      if (await refresh()) status(cancel ? "Turno cancelado. Se conserva su historial de cambios." : "Turno guardado. Ya está disponible en el horario del empleado.");
    } catch (error) {
      $("schedule-form-error").textContent = error.name === "AbortError" || error instanceof TypeError ? "No se recibió confirmación del servidor. Reintenta sin cambiar los datos para evitar duplicar la solicitud." : error.message;
    } finally {
      clearTimeout(timeout);
      busy = false;
      dialog.querySelectorAll("button").forEach(button => button.disabled = false);
      fields.forEach(id => $(id).disabled = !config.editable);
    }
  }
  $("schedule-form").addEventListener("submit", e => { e.preventDefault(); if ($("schedule-form").reportValidity()) save(); });
  $("schedule-cancel-turn")?.addEventListener("click", () => { if (selected && window.confirm(`¿Cancelar el turno de ${selected.empleado}? Desaparecerá de su horario, pero conservará el historial.`)) save(true); });
  $("schedule-new")?.addEventListener("click", () => openEvent(null, focus));
  $("schedule-refresh").addEventListener("click", refresh);
  $("schedule-today").addEventListener("click", () => { focus = today; $("schedule-date").value = focus; refresh(); });
  $("schedule-date").addEventListener("change", e => { if (e.target.value && e.target.validity.valid) { focus = e.target.value; refresh(); } });
  $("schedule-employee-filter")?.addEventListener("change", refresh);
  $("schedule-branch-filter")?.addEventListener("change", refresh);
  function navigate(delta) {
    if (view === "month") {
      const value = date(focus); value.setUTCDate(1); value.setUTCMonth(value.getUTCMonth() + delta); focus = iso(value);
    } else focus = addDays(focus, delta * 7);
    $("schedule-date").value = focus;
    refresh();
  }
  $("schedule-prev").addEventListener("click", () => navigate(-1));
  $("schedule-next").addEventListener("click", () => navigate(1));
  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
    view = button.dataset.view;
    document.querySelectorAll("[data-view]").forEach(item => { item.classList.toggle("is-active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
    refresh();
  }));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !busy && !dialog.open) refresh();
  });
  refresh();
})();

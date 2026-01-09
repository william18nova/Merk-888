/*  static/javascript/editar_cliente.js
    ───────────────────────────────────────────────────────────────
    · Envío AJAX con flashes y errores por campo
    · Limpieza de errores on-the-fly
    · Enter: avanzar/retroceder entre campos (Shift+Enter = atrás)
      y SOLO en el último campo envía el formulario.
      (Capturamos la tecla en fase de captura para evitar submits “nativos”)
------------------------------------------------------------------*/
(() => {
  "use strict";

  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form   = $("#clienteForm");
  const okBox  = $("#success-message");
  const errBox = $("#error-message");

  const csrftoken =
    document.cookie.split(";")
      .map(c => c.trim())
      .find(c => c.startsWith("csrftoken="))
      ?.split("=")[1] || "";

  /* ───────── helpers ───────── */
  const iErr = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iOk  = t => `<i class="fas fa-check-circle"></i> ${t}`;

  const hide = el => {
    if (!el) return;
    el.style.display = "none";
    el.innerHTML     = "";
    el.classList.remove("visible");
  };
  const show = (el, html) => {
    if (!el) return;
    el.innerHTML = html;
    el.style.display = "block";
    el.classList.add("visible");
  };

  function resetUI() {
    hide(errBox); hide(okBox);
    $$(".field-error").forEach(hide);
    $$(".input-error").forEach(inp => inp.classList.remove("input-error"));
  }

  function toObj(errors) {
    return (typeof errors === "string") ? JSON.parse(errors) : errors;
  }

  /* pinta errores globales + por campo */
  function renderErrors(raw) {
    const errs = toObj(raw);

    if (errs.__all__) {
      show(errBox, errs.__all__.map(e => iErr(e.message)).join("<br>"));
    }
    Object.entries(errs).forEach(([field, arr]) => {
      if (field === "__all__") return;
      const inp = $(`#id_${field}`);
      const div = $(`#error-id_${field}`);
      if (inp) inp.classList.add("input-error");
      if (div) show(div, arr.map(e => iErr(e.message)).join("<br>"));
    });
  }

  /* ───────── Navegación con Enter (captura) ───────── */
  const FOCUSABLE_SEL = "input:not([type='hidden']):not([disabled]), select:not([disabled]), textarea:not([disabled])";

  function isVisible(el){
    if (!el) return false;
    const cs = window.getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") return false;
    const r = el.getBoundingClientRect();
    return (r.width || r.height);
  }
  function tabbables(){
    // DOM order; respeta tabindex si se desea (opcional)
    return Array.from(form.querySelectorAll(FOCUSABLE_SEL)).filter(isVisible);
  }
  function focusField(el){
    el.focus();
    if (typeof el.select === "function" &&
        /^(text|search|tel|url|email|number|date|time|datetime-local|month|week|password)$/.test(el.type || "")) {
      el.select();
    }
  }
  function advance(current, backwards=false){
    const list = tabbables();
    if (!list.length) return;
    let idx = list.indexOf(current);
    // Si no está en la lista (raro), foca el primero
    if (idx === -1) { focusField(list[0]); return; }

    if (backwards){
      if (idx > 0) focusField(list[idx-1]);
      else focusField(list[0]); // ya estás al inicio
      return;
    }

    if (idx < list.length - 1){
      focusField(list[idx+1]);
    } else {
      // último campo -> enviar (dispara nuestro handler AJAX)
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable:true, bubbles:true }));
    }
  }

  // Capturamos Enter ANTES de que el navegador intente enviar el form
  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    if (!t.matches(FOCUSABLE_SEL)) return;       // ignorar si no es un campo
    if (t.tagName === "TEXTAREA") return;        // permitir Enter en textarea

    e.preventDefault();
    e.stopImmediatePropagation();
    advance(t, e.shiftKey);                      // Shift+Enter = atrás
  }, { capture: true });

  /* ───────── submit AJAX ───────── */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    try {
      const res = await fetch(form.action, {
        method : "POST",
        headers: {
          "X-CSRFToken"     : csrftoken,
          "X-Requested-With": "XMLHttpRequest",
          "Accept"          : "application/json",
        },
        body   : new FormData(form)
      });
      const data = await res.json();

      if (data.success) {
        // guardamos flash para la lista y redirigimos
        sessionStorage.setItem("flash-cliente", "Cliente editado correctamente.");
        window.location.href = data.redirect_url;
      } else {
        renderErrors(data.errors);
      }
    } catch (e) {
      console.error(e);
      show(errBox, iErr("Error de red o servidor."));
    }
  });

  /* ───────── limpiar error on-input ───────── */
  $$("#clienteForm input, #clienteForm select, #clienteForm textarea").forEach(inp => {
    inp.addEventListener("input", () => {
      if (inp.classList.contains("input-error")) {
        inp.classList.remove("input-error");
        const name = inp.id?.replace("id_", "");
        if (name) hide($(`#error-id_${name}`));
        hide(errBox);
      }
    });
  });

})();

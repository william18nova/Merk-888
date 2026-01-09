/*  static/javascript/agregar_rol.js
    ──────────────────────────────────────────────────
    • Envío AJAX + resaltado de errores
    • Enter → siguiente campo; si es el último, envía
----------------------------------------------------*/
(() => {
  "use strict";

  /* ---------- helpers ---------- */
  const $        = s => document.querySelector(s);
  const iconErr  = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const iconOk   = txt => `<i class="fas fa-check-circle"></i> ${txt}`;

  const form       = $("#rolForm");
  const divError   = $("#error-message");
  const divSuccess = $("#success-message");
  const fieldErrs  = document.querySelectorAll(".field-error");
  const inputs     = form.querySelectorAll("input, textarea");
  const submitBtn  = form.querySelector('button[type="submit"], .btn-agregar-rol');

  /* ---------- UI helpers ---------- */
  const hide = el => { el.style.display = "none"; el.innerHTML = ""; };
  const show = (el, html) => { el.innerHTML = html; el.style.display = "block"; };

  function clearAll() {
    hide(divError); hide(divSuccess);
    fieldErrs.forEach(hide);
    inputs.forEach(i => i.classList.remove("input-error"));
  }

  function showFieldError(name, html) {
    const errBox = document.getElementById(`error-${name}`);
    const inp    = document.getElementById(`id_${name}`);
    if (errBox) show(errBox, html);
    if (inp)    inp.classList.add("input-error");
  }

  /* ---------- Enter → siguiente / enviar ---------- */
  function visible(el){
    return !!(el && el.offsetParent !== null);
  }
  function focusNextOrSubmit(current){
    // Orden natural de tabulación dentro del form
    const focusables = Array.from(
      form.querySelectorAll(
        'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])'
      )
    ).filter(visible);

    const idx = focusables.indexOf(current);
    const next = focusables[idx + 1];

    if (next && next.tagName !== "BUTTON") {
      next.focus();
      // Seleccionar texto si es input/textarea
      if (/^(INPUT|TEXTAREA)$/.test(next.tagName) && typeof next.select === "function") {
        try { next.select(); } catch(_){}
      }
    } else {
      // Último campo → enviar
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitBtn || undefined);
      } else if (submitBtn) {
        submitBtn.click();
      } else {
        form.dispatchEvent(new Event("submit", { cancelable:true }));
      }
    }
  }

  // Manejo de Enter en todo el formulario
  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;

    const t = e.target;
    // En TEXTAREA permitimos salto de línea con Shift+Enter
    if (t.tagName === "TEXTAREA") {
      if (e.shiftKey) return; // permite nueva línea
      e.preventDefault();
      focusNextOrSubmit(t);
      return;
    }
    // Inputs y selects: siempre avanzar
    if (t.tagName === "INPUT" || t.tagName === "SELECT") {
      e.preventDefault();
      focusNextOrSubmit(t);
    }
  });

  /* ---------- submit (AJAX) ---------- */
  form.addEventListener("submit", async e => {
    e.preventDefault();
    clearAll();

    try {
      const resp = await fetch(form.action, {
        method : "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body   : new FormData(form)
      });
      const data = await resp.json();

      if (resp.ok && data.success) {
        show(divSuccess, iconOk(data.message || "Rol agregado correctamente."));
        form.reset();
      } else {
        const errs = data.errors || {};
        if (errs.__all__) {
          show(divError, errs.__all__.map(e => iconErr(e.message)).join("<br>"));
        }
        Object.entries(errs).forEach(([field, arr]) => {
          if (field === "__all__") return;
          showFieldError(field, arr.map(e => iconErr(e.message)).join("<br>"));
        });
      }
    } catch (err) {
      console.error(err);
      show(divError, iconErr("Error de red. Inténtalo nuevamente."));
    }
  });

  /* ---------- quitar resaltado en tiempo real ---------- */
  inputs.forEach(inp => {
    inp.addEventListener("input", () => {
      if (inp.classList.contains("input-error")) {
        inp.classList.remove("input-error");
        const box = document.getElementById(`error-${inp.id.replace("id_", "")}`);
        if (box) hide(box);
        hide(divError);
      }
    });
  });
})();

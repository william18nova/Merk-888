/* static/javascript/editar_sucursal.js */
(() => {
  "use strict";

  const $          = s => document.querySelector(s);
  const form       = $("#sucursalForm");
  const okBox      = $("#success-message");
  const errBox     = $("#error-message");
  const csrftoken  =
    document.cookie.split(";").map(c => c.trim())
      .find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";

  /* --- helpers visibilidad --- */
  const icon = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;

  const show = (el, html = "") => {
    if (!el) return;
    el.innerHTML = html;
    el.style.display = "block";
    el.classList.add("visible");
  };

  const hide = el => {
    if (!el) return;
    el.style.display = "none";
    el.innerHTML     = "";
    el.classList.remove("visible");
  };

  /* --- obtener campos focuseables en orden de DOM --- */
  function getFocusable() {
    return Array.from(
      form.querySelectorAll(
        'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
      )
    );
  }

  /* --- navegación: Enter = siguiente, último = enviar --- */
  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;

    const el = e.target;
    const tag = el.tagName;

    // En textarea, Shift+Enter permite nueva línea
    if (tag === "TEXTAREA" && e.shiftKey) return;

    // Evitar submit nativo del navegador
    e.preventDefault();

    const fields = getFocusable();
    const idx    = fields.indexOf(el);

    if (idx === -1) {
      // Si no localizamos el campo, mejor enviar
      form.requestSubmit();
      return;
    }

    const isLast = idx === fields.length - 1;
    if (isLast) {
      // Último → guardar (dispara submit AJAX de abajo)
      form.requestSubmit();
    } else {
      const next = fields[idx + 1];
      if (next) {
        next.focus();
        if (typeof next.select === "function") {
          setTimeout(() => next.select(), 0);
        }
      }
    }
  });

  /* --- submit Ajax --- */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();

    // limpiar estado anterior
    hide(okBox); hide(errBox);
    form.querySelectorAll(".input-error").forEach(i => i.classList.remove("input-error"));
    form.querySelectorAll(".field-error").forEach(hide);

    try {
      const resp = await fetch(form.action, {
        method : "POST",
        headers: {
          "X-CSRFToken"      : csrftoken,
          "X-Requested-With" : "XMLHttpRequest",
          "Accept"           : "application/json"
        },
        body : new FormData(form)
      });

      const data = await resp.json();

      if (data.success) {
        if (data.redirect_url) {
          sessionStorage.setItem("flash-sucursal", "Sucursal actualizada correctamente.");
          window.location.href = data.redirect_url;
        } else {
          show(okBox, `<i class="fas fa-check-circle"></i> ${data.message || "Cambios guardados."}`);
        }
      } else {
        const errs = typeof data.errors === "string" ? JSON.parse(data.errors) : (data.errors || {});
        renderErrors(errs);
      }

    } catch (err) {
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

  /* --- pintar errores de validación --- */
  function renderErrors(errors){
    if (errors.__all__) {
      show(errBox, errors.__all__.map(e => icon(e.message)).join("<br>"));
    }
    for (const [field, msgs] of Object.entries(errors)) {
      if (field === "__all__") continue;
      const input = document.getElementById(`id_${field}`);
      const div   = document.getElementById(`error-id_${field}`);
      if (input) input.classList.add("input-error");
      if (div)   show(div, msgs.map(e => icon(e.message)).join("<br>"));
    }
  }

  /* --- limpiar error de un campo al escribir --- */
  getFocusable().forEach(inp => {
    inp.addEventListener("input", () => {
      if (inp.classList.contains("input-error")) {
        inp.classList.remove("input-error");
        const field = inp.id.replace(/^id_/, "");
        const div   = document.getElementById(`error-id_${field}`);
        hide(div);
        hide(errBox);
      }
    });
  });

})();

/* static/javascript/agregar_sucursal.js
   — Enter para avanzar / enviar + mismo manejo de errores/éxito — */
(() => {
  "use strict";

  /* -----------------  helpers ----------------- */
  const $ = s => document.querySelector(s);

  const form         = $("#sucursalForm");
  const errorDiv     = $("#error-message");
  const successDiv   = $("#success-message");
  const successText  = $("#success-text");
  const fieldErrors  = document.querySelectorAll(".field-error");
  const inputs       = form.querySelectorAll("input, textarea");

  const icon   = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const okIcon = txt => `<i class="fas fa-check-circle success-icon"></i> ${txt}`;

  const show = (div, html, flex = false) => {
    div.innerHTML     = html;
    div.style.display = flex ? "flex" : "block";
    div.classList.add("visible");
  };
  const hide = div => {
    div.style.display = "none";
    div.innerHTML     = "";
    div.classList.remove("visible");
  };

  /* -----------------  limpieza ----------------- */
  function clearAll() {
    hide(errorDiv);
    hide(successDiv);
    if (successText) successText.textContent = "";
    fieldErrors.forEach(hide);
    inputs.forEach(i => i.classList.remove("input-error"));
  }

  /* -----------------  errores de campo ----------------- */
  function showFieldError(field, html) {
    const div   = document.getElementById(`error-${field}`);
    const input = document.getElementById(`id_${field}`); // Django: id_{field}
    if (div)  show(div, html);
    if (input) input.classList.add("input-error");
  }

  /* -----------------  submit AJAX ----------------- */
  form.addEventListener("submit", async e => {
    e.preventDefault();
    clearAll();

    try {
      const resp  = await fetch(form.action, {
        method : "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body   : new FormData(form)
      });
      const data = await resp.json();

      if (resp.ok && data.success) {
        show(successDiv, okIcon(data.message || "Sucursal agregada correctamente."), true);
        form.reset();
      } else {
        const errs = data.errors || {};
        if (errs.__all__) {
          show(errorDiv, errs.__all__.map(e => icon(e.message)).join("<br>"));
        }
        Object.keys(errs).forEach(field => {
          if (field === "__all__") return;
          const html = errs[field].map(e => icon(e.message)).join("<br>");
          showFieldError(field, html);
        });
      }
    } catch (err) {
      console.error(err);
      show(errorDiv, icon("Error de red. Inténtalo de nuevo."));
    }
  });

  /* -----------------  quitar resaltado al teclear ----------------- */
  inputs.forEach(input => {
    input.addEventListener("input", () => {
      if (input.classList.contains("input-error")) {
        input.classList.remove("input-error");
        const field        = input.id.replace("id_", "");
        const errContainer = document.getElementById(`error-${field}`);
        if (errContainer) hide(errContainer);
        hide(errorDiv);
      }
    });
  });

  /* -----------------  Enter = siguiente / enviar ----------------- */
  function getFocusable() {
    // Orden natural del DOM; excluye botones/ocultos/deshabilitados
    return Array.from(
      form.querySelectorAll(
        'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
      )
    );
  }

  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;

    const el = e.target;
    const tag = el.tagName;

    // En textarea, Shift+Enter = salto de línea; Enter solo avanza/enviar
    if (tag === "TEXTAREA" && e.shiftKey) return;

    // Previene envío por defecto del navegador
    e.preventDefault();

    const fields = getFocusable();
    const idx = fields.indexOf(el);

    // Si no ubicamos el campo, hacemos submit por seguridad
    if (idx === -1) {
      form.requestSubmit();
      return;
    }

    const isLast = idx === fields.length - 1;

    if (isLast) {
      // Último campo → enviar (dispara nuestro listener AJAX)
      form.requestSubmit();
    } else {
      // Siguiente campo en cadena de foco
      const next = fields[idx + 1];
      if (next) {
        next.focus();
        // Select all cuando es input/textarea
        if (typeof next.select === "function") {
          // retrasa un tick para asegurar foco previo
          setTimeout(() => next.select(), 0);
        }
      }
    }
  });
})();

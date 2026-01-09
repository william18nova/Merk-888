/* editar_proveedor.js — Enter navega por campos; último = enviar; oculta éxito vacío */
(() => {
  "use strict";

  /* ---------- helpers DOM ---------- */
  const $id  = id  => document.getElementById(id);
  const $qsa = sel => document.querySelectorAll(sel);

  /* ---------- refs ---------- */
  const form   = $id("form-editar-proveedor");
  if (!form) return; // si no existe el formulario, salimos

  const boxErr = $id("error-message");
  const boxOk  = $id("success-message");
  const okTxt  = $id("success-text");

  /* Oculta el alert de éxito si viene vacío desde el template */
  const hideEmptySuccess = () => {
    if (!boxOk) return;
    const hasText = (okTxt?.textContent || "").trim().length > 0;
    if (!hasText) boxOk.style.display = "none";
  };
  hideEmptySuccess();

  /* ---------- UI helpers ---------- */
  const UI = {
    reset() {
      // No ocultes el éxito “válido” aquí (solo errores); el éxito lo controla UI.ok()
      boxErr.style.display = "none";
      boxErr.innerHTML     = "";

      $qsa(".field-error").forEach(div => {
        div.innerHTML = "";
        div.classList.remove("visible");
      });
      $qsa(".input-error").forEach(inp => inp.classList.remove("input-error"));

      // si el éxito quedó visible sin texto, lo ocultamos
      hideEmptySuccess();
    },
    ok(msg) {
      const txt = (msg || "").trim();
      if (!txt) {
        // si por alguna razón llaman sin msg, asegúrate de ocultarlo
        if (boxOk) boxOk.style.display = "none";
        return;
      }
      if (okTxt) okTxt.textContent = txt;
      if (boxOk) boxOk.style.display = "flex";
    },
    errGlobal(msg) {
      if (!boxErr) return;
      boxErr.innerHTML    = msg;
      boxErr.style.display = "block";
    },
    errFields(errors) {
      let focused = false;
      Object.entries(errors || {}).forEach(([field, arr]) => {
        const div = $id(`error-id_${field}`);
        if (div) {
          div.innerHTML = arr
            .map(e => `<i class="fas fa-exclamation-circle"></i> ${e.message}`)
            .join("<br>");
          div.classList.add("visible");
        }
        const input = $id(`id_${field}`);
        if (input){
          input.classList.add("input-error");
          if (!focused){ input.focus(); focused = true; }
        }
      });
    },
  };

  /* ---------- CSRF ---------- */
  const getCSRF = () =>
    document.cookie
      .split("; ")
      .find(c => c.startsWith("csrftoken="))
      ?.split("=")[1] || "";

  /* ---------- Navegación con Enter ---------- */
  // Campos “navegables” (sin hidden/submit/disabled/readonly)
  const fields = Array.from(
    form.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([disabled]), textarea, select'
    )
  ).filter(el => !el.readOnly);

  const focusNextOrSubmit = (current) => {
    const idx = fields.indexOf(current);
    if (idx < 0) return;

    const next = fields[idx + 1];
    if (next){
      next.focus();
      if (typeof next.select === "function") {
        try { next.select(); } catch {}
      }
    } else {
      // último campo → enviar
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }
  };

  fields.forEach(el => {
    el.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;

      // En textarea, Shift+Enter = salto de línea
      if (el.tagName === "TEXTAREA" && e.shiftKey) return;

      e.preventDefault();
      focusNextOrSubmit(el);
    });
  });

  /* ---------- submit ---------- */
  form.addEventListener("submit", async e => {
    e.preventDefault();
    UI.reset();

    try {
      const resp = await fetch(form.action, {
        method : "POST",
        headers: { "X-CSRFToken": getCSRF(), Accept: "application/json" },
        body   : new FormData(form),
      });
      const data = await resp.json();

      if (data.success) {
        // flash en sessionStorage y redirección
        sessionStorage.setItem(
          "flash-prov",
          "Proveedor actualizado exitosamente."
        );
        window.location.href = data.redirect_url;
      } else {
        UI.errFields(data.errors);
        if (data.errors?.__all__) {
          UI.errGlobal(
            data.errors.__all__.map(e => e.message).join("<br>")
          );
        }
      }
    } catch (err) {
      console.error(err);
      UI.errGlobal("Ocurrió un error inesperado.");
    }
  });
})();

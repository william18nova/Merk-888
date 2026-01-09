/* agregar_proveedor.js — Enter navega campos; último = enviar */
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  /* ---------- Helpers ---------- */
  const $id  = id  => document.getElementById(id);
  const $qsa = sel => document.querySelectorAll(sel);

  /* ---------- refs ---------- */
  const form   = $id("form-agregar-proveedor");
  if (!form) return;

  const boxErr = $id("error-message");
  const boxOk  = $id("success-message");
  const okText = $id("success-text");

  /* ---------- UI helpers ---------- */
  const UI = {
    reset() {
      boxErr.style.display = boxOk.style.display = "none";
      boxErr.innerHTML = okText.textContent = "";

      $qsa(".field-error").forEach(d => {
        d.textContent = "";
        d.classList.remove("visible");
      });
      $qsa(".input-error").forEach(i => i.classList.remove("input-error"));
    },
    ok(msg) {
      okText.textContent  = msg;
      boxOk.style.display = "flex";
      form.reset();
      // vuelve al primer campo tras agregar
      const first = form.querySelector("input:not([type=hidden]):not([disabled]), textarea, select");
      first?.focus();
    },
    errGlobal(msg) {
      boxErr.innerHTML     = msg;
      boxErr.style.display = "block";
    },
    errFields(errObj = {}) {
      let focused = false;
      Object.entries(errObj).forEach(([field, list]) => {
        const div = $id(`error-id_${field}`);
        if (!div) return;

        div.innerHTML = list
          .map(e => `<i class="fas fa-exclamation-circle"></i> ${e.message}`)
          .join("<br>");
        div.classList.add("visible");

        const inp = $id(`id_${field}`);
        if (inp){
          inp.classList.add("input-error");
          if (!focused){ inp.focus(); focused = true; }
        }
      });
    }
  };

  /* ---------- CSRF ---------- */
  const getCookie = name =>
    document.cookie.split("; ")
      .find(c => c.startsWith(name + "="))
      ?.split("=")[1] || "";

  /* ---------- Navegación con Enter ---------- */
  // Campos navegables en orden DOM
  const fields = Array.from(
    form.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([disabled]), textarea, select'
    )
  ).filter(el => !el.readOnly);

  // Mueve el foco al siguiente; si es el último, envía
  const focusNextOrSubmit = (current) => {
    const idx = fields.indexOf(current);
    if (idx === -1) return;

    const next = fields[idx + 1];
    if (next){
      next.focus();
      // Selecciona texto si es input/textarea para edición rápida
      if (next.select && typeof next.select === "function") {
        try { next.select(); } catch {}
      }
    } else {
      // último campo → enviar como “Agregar Proveedor”
      // requestSubmit respeta el botón por defecto si lo hubiera
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }
  };

  // Intercepta Enter en todos los campos
  fields.forEach(el => {
    el.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;

      // Permite Enter+Shift en textarea para salto de línea
      if (el.tagName === "TEXTAREA" && e.shiftKey) return;

      e.preventDefault();
      focusNextOrSubmit(el);
    });
  });

  /* ---------- submit ---------- */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    UI.reset();

    try {
      const resp = await fetch(form.action, {
        method : "POST",
        headers: {
          "X-CSRFToken": getCookie("csrftoken"),
          Accept       : "application/json"
        },
        body: new FormData(form)
      });
      const data = await resp.json();

      if (resp.ok && data.success) {
        UI.ok(data.message || "Proveedor agregado.");
      } else {
        UI.errFields(data.errors || {});
        if (data.errors?.__all__)
          UI.errGlobal(data.errors.__all__.map(e => e.message).join("<br>"));
      }
    } catch (err) {
      console.error(err);
      UI.errGlobal("Ocurrió un error inesperado.");
    }
  });
});

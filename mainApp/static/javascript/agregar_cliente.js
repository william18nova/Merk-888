/*  static/javascript/agregar_cliente.js
    ─────────────────────────────────────────────────────────
    · Envío AJAX
    · Errores de campo + globales
    · Flashes de éxito / error
    · Misma UX que agregar_rol.js
    · NUEVO: Enter = enfocar siguiente input; en el último => enviar
----------------------------------------------------------*/
(() => {
  "use strict";

  const $          = s => document.querySelector(s);
  const form       = $("#form-agregar-cliente");
  const divError   = $("#error-message");
  const divSuccess = $("#success-message");
  const inputs     = form.querySelectorAll("input, select, textarea");

  const iconErr = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;
  const iconOk  = t => `<i class="fas fa-check-circle"></i> ${t}`;
  const hide    = el => { el.style.display="none"; el.innerHTML=""; };
  const show    = (el,html)=>{ el.innerHTML=html; el.style.display="block"; };

  function clearAll(){
    hide(divError); hide(divSuccess);
    form.querySelectorAll(".field-error").forEach(hide);
    form.querySelectorAll(".input-error").forEach(el => el.classList.remove("input-error"));
  }
  function showFieldErr(name, html){
    const errBox = $(`#error-${name}`);
    const inp    = $(`#id_${name}`);
    if(errBox) show(errBox, html);
    if(inp)    inp.classList.add("input-error");
  }

  // ===== Enter navega al siguiente campo; si es el último => submit AJAX =====
  function isVisible(el){
    // visible en el flujo (no hidden/disabled/readonly y ocupa espacio)
    if (!el || el.disabled || el.readOnly) return false;
    if (el.type === "hidden") return false;
    const rect = el.getBoundingClientRect();
    const visible = !!(rect.width || rect.height);
    return visible && window.getComputedStyle(el).visibility !== "hidden";
  }

  function focusNextOrSubmit(current){
    // Lista de campos focuseables en el orden DOM
    const focusables = Array.from(form.querySelectorAll("input, select, textarea"))
      .filter(isVisible);
    const idx = focusables.indexOf(current);
    if (idx > -1 && idx < focusables.length - 1) {
      const next = focusables[idx + 1];
      next.focus();
      // Selecciona el contenido en inputs de texto/número/email/tel
      if (typeof next.select === "function" &&
          /^(text|search|tel|url|email|number|date|time|datetime-local|month|week|password)$/.test(next.type || "")) {
        next.select();
      }
    } else {
      // Último campo: enviar formulario (dispara nuestro handler AJAX)
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        // Fallback que dispara el listener de "submit" (sin recargar página)
        const evt = new Event("submit", { cancelable: true, bubbles: true });
        form.dispatchEvent(evt);
      }
    }
  }

  // Captura Enter en inputs/selecciones (excepto textarea, donde Enter suele ser útil)
  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;

    // Permitir Enter en textarea (salto de línea)
    if (target.tagName === "TEXTAREA") return;

    // Evita submit nativo y navega/manda
    e.preventDefault();
    e.stopPropagation();
    focusNextOrSubmit(target);
  });

  // ===== Envío AJAX =====
  form.addEventListener("submit", async ev=>{
    ev.preventDefault(); clearAll();

    try{
      const resp = await fetch(form.action,{
        method : "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body   : new FormData(form)
      });
      const data = await resp.json();

      if(resp.ok && data.success){
        show(divSuccess, iconOk("Cliente agregado exitosamente."));
        form.reset();
        // Lleva el foco al primer campo tras éxito
        const first = Array.from(form.querySelectorAll("input, select, textarea"))
          .find(isVisible);
        first?.focus();
      }else{
        const errs = data.errors || {};
        if(errs.__all__)
          show(divError, errs.__all__.map(e=>iconErr(e.message)).join("<br>"));
        Object.entries(errs).forEach(([f,arr])=>{
          if(f==="__all__") return;
          showFieldErr(f, arr.map(e=>iconErr(e.message)).join("<br>"));
        });
      }
    }catch(ex){
      console.error(ex);
      show(divError, iconErr("Error de red. Inténtalo nuevamente."));
    }
  });

  // Quita resaltado de error “on-the-fly”
  inputs.forEach(inp=>{
    inp.addEventListener("input", ()=>{
      if(inp.classList.contains("input-error")){
        inp.classList.remove("input-error");
        const box = $(`#error-${inp.name}`);
        if (box) hide(box);
        hide(divError);
      }
    });
  });

})();

/*  editar_rol.js
    ──────────────────────────────────────────────────────────
    • AJAX Submit (igual a “Editar Sucursal”)
    • Resaltado de .input-error
    • Enter → siguiente campo; último campo → submit (guardar)
----------------------------------------------------------------*/
(() => {
  "use strict";

  const $   = s => document.querySelector(s);
  const $$  = s => document.querySelectorAll(s);

  const form      = $("#rolForm");
  const okBox     = $("#success-message");
  const errBox    = $("#error-message");
  const submitBtn = form.querySelector('button[type="submit"], .btn-agregar-rol, .btn-guardar');

  const csrftoken =
    document.cookie.split(";").map(c => c.trim())
           .find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";

  /* ---------- helpers ---------- */
  const icon = t => `<i class="fas fa-exclamation-circle"></i> ${t}`;

  const show = (el, html) => {
    el.innerHTML = html;
    el.style.display = "block";
    el.classList.add("visible");
  };
  const hide = el => {
    el.style.display = "none";
    el.innerHTML     = "";
    el.classList.remove("visible");
  };
  const clear = () => {
    [okBox, errBox].forEach(hide);
    $$(".field-error").forEach(hide);
    $$(".input-error").forEach(i => i.classList.remove("input-error"));
  };

  /* ---------- submit (AJAX) ---------- */
  form.addEventListener("submit", async e => {
    e.preventDefault();
    clear();

    try{
      const resp = await fetch(form.action, {
        method : "POST",
        headers: {
          "X-CSRFToken"      : csrftoken,
          "X-Requested-With" : "XMLHttpRequest",
          "Accept"           : "application/json",
        },
        body : new FormData(form)
      });
      const data = await resp.json();

      if(data.success){
        if(data.redirect_url){
          sessionStorage.setItem("flash-rol", "Rol actualizado correctamente.");
          window.location.href = data.redirect_url;
        }else{
          show(okBox, `<i class="fas fa-check-circle"></i> ${data.message || "Guardado correctamente."}`);
        }
      }else{
        renderErrors(typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors);
      }
    }catch(err){
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

  /* ---------- pintar errores ---------- */
  function renderErrors(errors){
    if(errors.__all__){
      show(errBox, errors.__all__.map(e=>icon(e.message)).join("<br>"));
    }
    Object.entries(errors || {}).forEach(([field,msgs])=>{
      if(field==="__all__") return;
      const input = document.getElementById(`id_${field}`);
      const div   = document.getElementById(`error-id_${field}`);
      if(input) input.classList.add("input-error");
      if(div)   show(div, (msgs||[]).map(e=>icon(e.message || e)).join("<br>"));
    });
  }

  /* ---------- limpiar error al teclear ---------- */
  $$("#rolForm input, #rolForm textarea, #rolForm select").forEach(inp=>{
    inp.addEventListener("input", ()=>{
      if(inp.classList.contains("input-error")){
        inp.classList.remove("input-error");
        const div = document.getElementById(`error-id_${inp.id.replace("id_","")}`);
        if(div) hide(div);
        hide(errBox);
      }
    });
  });

  /* ---------- Enter → siguiente / submit ---------- */
  function isVisible(el){
    return !!(el && el.offsetParent !== null);
  }
  function tabbables(){
    // Campos que participan en la navegación (excluye hidden/disabled y botones salvo el submit final)
    return Array.from(
      form.querySelectorAll(
        'input:not([type="hidden"]):not([disabled]):not([type="button"]):not([type="submit"]), textarea:not([disabled]), select:not([disabled])'
      )
    ).filter(isVisible);
  }
  function focusNextOrSubmit(current){
    const fields = tabbables();
    const idx    = fields.indexOf(current);
    const next   = fields[idx + 1];

    if(next){
      next.focus();
      if(/^(INPUT|TEXTAREA)$/.test(next.tagName) && typeof next.select === "function"){
        try{ next.select(); }catch(_){}
      }
      return;
    }
    // Último campo → enviar (equivalente a pulsar el botón)
    if(typeof form.requestSubmit === "function"){
      form.requestSubmit(submitBtn || undefined);
    }else if(submitBtn){
      submitBtn.click();
    }else{
      form.dispatchEvent(new Event("submit", { cancelable:true }));
    }
  }

  form.addEventListener("keydown", (e)=>{
    if(e.key !== "Enter") return;
    const t = e.target;

    // textarea: Shift+Enter = salto de línea; Enter “solo” = siguiente/enviar
    if(t.tagName === "TEXTAREA"){
      if(e.shiftKey) return;
      e.preventDefault();
      focusNextOrSubmit(t);
      return;
    }

    // inputs/selects: Enter navega o envía (no duplicar submit del navegador)
    if(t.tagName === "INPUT" || t.tagName === "SELECT"){
      // Permite que Enter en inputs type=submit haga su trabajo normal (por si hay uno en medio)
      if(t.type && t.type.toLowerCase() === "submit") return;
      e.preventDefault();
      focusNextOrSubmit(t);
    }
  });
})();

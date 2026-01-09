/*  editar_permiso.js
    ──────────────────────────────────────────────────────────
    • AJAX Submit (igual a “Editar Rol”)
    • Errores bajo el input + .input-error
    • Enter → siguiente campo; último campo → submit
----------------------------------------------------------------*/
(() => {
  "use strict";

  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form      = $("#permisoForm");
  const okBox     = $("#success-message");
  const errBox    = $("#error-message");
  const submitBtn = form.querySelector('button[type="submit"], .btn-agregar-rol, .btn-guardar');

  const csrftoken =
    document.cookie.split(";").map(c => c.trim())
      .find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";

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
          sessionStorage.setItem("flash-permiso", "Permiso actualizado correctamente.");
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

  function renderErrors(errors){
    if(errors?.__all__){
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

  $$("#permisoForm input, #permisoForm textarea, #permisoForm select").forEach(inp=>{
    inp.addEventListener("input", ()=>{
      if(inp.classList.contains("input-error")){
        inp.classList.remove("input-error");
        const div = document.getElementById(`error-id_${inp.id.replace("id_","")}`);
        if(div) hide(div);
        hide(errBox);
      }
    });
  });

  function isVisible(el){ return !!(el && el.offsetParent !== null); }
  function tabbables(){
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

    if(t.tagName === "TEXTAREA"){
      if(e.shiftKey) return;  // Shift+Enter => salto de línea
      e.preventDefault();
      focusNextOrSubmit(t);
      return;
    }

    if(t.tagName === "INPUT" || t.tagName === "SELECT"){
      if(t.type && t.type.toLowerCase() === "submit") return;
      e.preventDefault();
      focusNextOrSubmit(t);
    }
  });
})();

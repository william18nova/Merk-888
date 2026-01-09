/* static/javascript/agregar_categorias.js */
(() => {
  "use strict";

  const $   = sel => document.querySelector(sel);
  const $$  = sel => document.querySelectorAll(sel);

  const form    = $("#categoriaForm");
  const errBox  = $("#error-message");
  const okBox   = $("#success-message");
  const okText  = $("#success-text");
  const btn     = $(".btn-agregar-categoria"); // por si hace falta como fallback

  const icon = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const show = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide = el         => { el.style.display = "none"; el.innerHTML = ""; };

  /* ========= UI reset ========= */
  function resetUI () {
    hide(errBox); hide(okBox); okText && (okText.textContent = "");
    form.querySelectorAll(".field-error").forEach(div => {
      div.classList.remove("visible");
      div.innerHTML = "";
      div.style.display = "none";
    });
    form.querySelectorAll(".input-error").forEach(i => i.classList.remove("input-error"));
  }

  /* ========= pinta errores del backend ========= */
  function renderErrors (errors){
    if (errors.__all__)
      show(errBox, errors.__all__.map(e => icon(e.message)).join("<br>"));

    for (const [field,msgArr] of Object.entries(errors)){
      if (field === "__all__") continue;
      const div   = document.getElementById(`error-id_${field}`);
      const input = document.getElementById(`id_${field}`);
      if (div){
        div.innerHTML = msgArr.map(e => icon(e.message)).join("<br>");
        div.classList.add("visible"); div.style.display="block";
      }
      if (input) input.classList.add("input-error");
    }
  }

  const csrftoken = document.cookie.split(";").map(c=>c.trim())
                     .find(c=>c.startsWith("csrftoken="))?.split("=")[1] || "";

  /* ========= Navegación con Enter (sin “quedarse” en el botón) ========= */
  // Solo campos de entrada reales (sin botones)
  function getFormControls(){
    return Array.from(
      form.querySelectorAll("input, select, textarea")
    ).filter(el=>{
      if (el.disabled) return false;
      const t = (el.type||"").toLowerCase();
      return t !== "hidden";
    });
  }

  function requestSafeSubmit() {
    // Usa requestSubmit si está disponible (respeta validaciones nativas y botones)
    if (typeof form.requestSubmit === "function") {
      const submitter = form.querySelector('button[type="submit"], input[type="submit"]');
      try { form.requestSubmit(submitter || undefined); return; } catch(_){}
    }
    // Fallback: click al botón o submit directo
    if (btn && typeof btn.click === "function") { btn.click(); return; }
    form.submit();
  }

  function focusNextOrSubmit(current){
    const fields = getFormControls();
    const idx = fields.indexOf(current);
    if (idx === -1) return;

    const next = fields[idx+1];
    if (next){
      next.focus();
      // si es texto/número, seleccionar contenido ayuda a editar rápido
      if (typeof next.select === "function" && (next.tagName === "INPUT" || next.tagName === "TEXTAREA")) {
        try { next.select(); } catch(_){}
      }
    } else {
      // último campo → enviar formulario de inmediato
      requestSafeSubmit();
    }
  }

  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;

    const el = e.target;
    // Solo actuamos en inputs/selects/textarea. No interferimos con botones.
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA") {
      // Si el campo es textarea y quieres salto de línea, comenta las 2 líneas siguientes
      e.preventDefault();
      e.stopPropagation();

      // Si es el último campo -> submit; si no, foco al siguiente
      const fields = getFormControls();
      const idx = fields.indexOf(el);
      if (idx === fields.length - 1) {
        requestSafeSubmit();
      } else {
        focusNextOrSubmit(el);
      }
    }
  });

  /* ========= Submit (AJAX) ========= */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    try{
      const resp = await fetch(form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken":csrftoken,
          "X-Requested-With":"XMLHttpRequest",
          "Accept":"application/json"
        },
        body:new FormData(form)
      });
      const data = await resp.json();

      if (data.success){
        okBox.innerHTML     = '<i class="fas fa-check-circle"></i> Categoría agregada exitosamente.';
        okBox.style.display = "flex";
        form.reset();
      }else{
        renderErrors(
          typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors
        );
      }
    }catch(err){
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

})();

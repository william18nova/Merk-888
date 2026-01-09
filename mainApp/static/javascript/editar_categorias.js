/* static/javascript/editar_categorias.js
   ⇢ Versión alineada con “agregar” + Enter navega/submit directo */
(() => {
  "use strict";

  /* ----------- utilidades DOM ----------- */
  const $  = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  const form      = $("#categoriaEditForm");
  const errBox    = $("#error-message");
  const okBox     = $("#success-message");   /* (no se usa, pero lo limpiamos) */
  const csrftoken = document.cookie
                      .split(";")
                      .map(c => c.trim())
                      .find(c => c.startsWith("csrftoken="))
                      ?.split("=")[1] || "";

  const icon = txt => `<i class="fas fa-exclamation-circle"></i> ${txt}`;
  const show = (el, html) => { el.innerHTML = html; el.style.display = "block"; };
  const hide = el         => { el.style.display = "none"; el.innerHTML = "";   };

  /* ----------- limpiar interfaz antes de cada submit ----------- */
  function resetUI () {
    hide(errBox); hide(okBox);

    form.querySelectorAll(".field-error").forEach(div => {
      div.classList.remove("visible");
      div.innerHTML = "";
      div.style.display = "none";
    });
    form.querySelectorAll(".input-error").forEach(i => i.classList.remove("input-error"));
  }

  /* ----------- pintar errores de validación ----------- */
  function renderErrors (errors){
    /* globales */
    if (errors.__all__)
      show(errBox, errors.__all__.map(e => icon(e.message)).join("<br>"));

    /* por-campo */
    for (const [field,msgArr] of Object.entries(errors)){
      if (field === "__all__") continue;
      const div   = document.getElementById(`error-id_${field}`);
      const input = document.getElementById(`id_${field}`);

      if (div){
        div.innerHTML = msgArr.map(e => icon(e.message)).join("<br>");
        div.classList.add("visible");
        div.style.display = "block";
      }
      if (input) input.classList.add("input-error");
    }
  }

  /* =================== Navegación con Enter =================== */
  function getFormControls(){
    // Solo campos de entrada reales (no botones)
    return Array.from(
      form.querySelectorAll("input, select, textarea")
    ).filter(el=>{
      if (el.disabled) return false;
      const t = (el.type||"").toLowerCase();
      return t !== "hidden";
    });
  }

  function requestSafeSubmit() {
    // Prefiere requestSubmit (respeta validaciones nativas y el botón submit si existe)
    if (typeof form.requestSubmit === "function") {
      const submitter = form.querySelector('button[type="submit"], input[type="submit"]');
      try { form.requestSubmit(submitter || undefined); return; } catch(_){}
    }
    // Fallback viejo
    const btn = form.querySelector(".btn-guardar, .btn-guardar-categoria, button[type='submit'], input[type='submit']");
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
      if (typeof next.select === "function" && (next.tagName === "INPUT" || next.tagName === "TEXTAREA")) {
        try { next.select(); } catch(_){}
      }
    } else {
      // último campo → enviar
      requestSafeSubmit();
    }
  }

  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;

    const el = e.target;
    // Solo actuamos en inputs/selects/textarea. No tocamos botones.
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA") {
      // Si quisieras permitir saltos de línea en textarea, comenta las dos líneas siguientes:
      e.preventDefault();
      e.stopPropagation();

      const fields = getFormControls();
      const idx = fields.indexOf(el);
      if (idx === fields.length - 1) {
        requestSafeSubmit();    // último campo -> submit directo
      } else {
        focusNextOrSubmit(el);  // si no, pasa al siguiente
      }
    }
  });

  /* -------------------- envío AJAX -------------------- */
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    resetUI();

    try{
      const resp = await fetch(form.action,{
        method:"POST",
        headers:{
          "X-CSRFToken"      : csrftoken,
          "X-Requested-With" : "XMLHttpRequest",
          "Accept"           : "application/json"
        },
        body:new FormData(form)
      });
      const data = await resp.json();

      if (data.success){
        /* flash-message para la tabla de categorías */
        sessionStorage.setItem(
          "flash-categoria",
          `Categoría «${form.nombre?.value || ""}» actualizada correctamente.`
        );
        /* redirección al listado */
        window.location.href = data.redirect_url;
        return;
      }

      /* errores → asegurarse de que es objeto (el backend puede enviar string) */
      const errs = typeof data.errors === "string" ? JSON.parse(data.errors) : data.errors;
      renderErrors(errs);

    }catch(err){
      console.error(err);
      show(errBox, icon("Ocurrió un error inesperado."));
    }
  });

})();

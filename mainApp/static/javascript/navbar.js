// Menú móvil y dropdowns en tap/click (combinado y robusto)
(function () {
  function init() {
    const burger = document.getElementById("nvBurger");
    const links  = document.getElementById("nvLinks");
    const nav    = document.querySelector(".nv");

    if (!burger || !links || !nav) {
      // Si el HTML todavía no está, reintenta
      return setTimeout(init, 100);
    }

    const isMobile = () => window.matchMedia("(max-width: 720px)").matches;

    // Guard para evitar cierre inmediato por el click global
    let openGuardUntil = 0;

    function setOpen(open) {
      links.classList.toggle("is-open", open);
      // sincroniza aria-expanded
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) {
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(x => x.classList.remove("is-open"));
      } else {
        openGuardUntil = Date.now() + 140; // 140ms de protección
      }
    }

    // === Toggle: mantenemos tu handler que funcionaba en móvil ===
    burger.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setOpen(!links.classList.contains("is-open"));
    }, { passive: false });

    // === Submenús: tap en móvil sin navegar (tu patrón original) ===
    const parents = Array.from(document.querySelectorAll(".nv__item.has-dd > a"));
    parents.forEach(a => {
      a.addEventListener("click", (e) => {
        if (!isMobile()) return; // en desktop usa hover
        e.preventDefault();
        e.stopPropagation();
        const li = a.parentElement;
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(x => { if (x !== li) x.classList.remove("is-open"); });
        li.classList.toggle("is-open");
      }, { passive: false });
    });

    // === Cerrar si se hace click fuera (con guard) ===
    document.addEventListener("click", (e) => {
      if (Date.now() < openGuardUntil) return;  // evita cierre inmediato
      if (!nav.contains(e.target)) {
        setOpen(false);
      }
    }, { passive: true, capture: true });

    // === Reset de estado al cambiar tamaño ===
    window.addEventListener("resize", () => {
      if (!isMobile()) {
        setOpen(false);
      }
    });

    // Log de depuración
    console.log("Navbar listo ✅");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

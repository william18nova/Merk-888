// Menú móvil y dropdowns en tap/click
(function () {
  const burger = document.getElementById("nvBurger");
  const links  = document.getElementById("nvLinks");

  if (burger && links) {
    burger.addEventListener("click", () => {
      links.classList.toggle("is-open");
    });
  }

  // En móvil, permitir abrir/cerrar submenús con tap
  const isMobile = () => window.matchMedia("(max-width: 720px)").matches;
  const parents = Array.from(document.querySelectorAll(".nv__item.has-dd > a"));

  parents.forEach(a => {
    a.addEventListener("click", (e) => {
      if (!isMobile()) return;          // en desktop usa hover
      e.preventDefault();
      const li = a.parentElement;
      // Cierra otros
      document.querySelectorAll(".nv__item.has-dd.is-open")
        .forEach(x => { if (x !== li) x.classList.remove("is-open"); });
      li.classList.toggle("is-open");
    });
  });

  // Cerrar menú móvil si se hace click fuera
  document.addEventListener("click", (e) => {
    if (!links.contains(e.target) && !burger.contains(e.target)) {
      links.classList.remove("is-open");
      document.querySelectorAll(".nv__item.has-dd.is-open")
        .forEach(x => x.classList.remove("is-open"));
    }
  });

  // Cerrar menús en cambios de tamaño (evitar estados raros)
  window.addEventListener("resize", () => {
    if (!isMobile()) {
      links.classList.remove("is-open");
      document.querySelectorAll(".nv__item.has-dd.is-open")
        .forEach(x => x.classList.remove("is-open"));
    }
  });
})();

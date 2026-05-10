// Menú móvil y dropdowns en tap/click (combinado y robusto)
(function () {
  function init() {
    const burger = document.getElementById("nvBurger");
    const links  = document.getElementById("nvLinks");
    const nav    = document.querySelector(".nv");
    const moreItem = document.getElementById("nvMoreItem");
    const moreMenu = document.getElementById("nvMoreMenu");
    const moreToggle = document.getElementById("nvMoreToggle");

    if (!burger || !links || !nav) {
      // Si el HTML todavía no está, reintenta
      return setTimeout(init, 100);
    }

    const isMobile = () => window.matchMedia("(max-width: 1180px)").matches;
    const navItems = moreItem
      ? Array.from(links.children).filter(item => item !== moreItem)
      : [];

    // Guard para evitar cierre inmediato por el click global
    let openGuardUntil = 0;
    let positionRaf = null;
    let fitRaf = null;

    function positionDropdown(li) {
      if (!li || isMobile()) return;
      if (li.closest(".nv__more-menu")) return;
      const dd = li.querySelector(".nv__dd");
      if (!dd) return;

      const itemRect = li.getBoundingClientRect();
      const navRect = nav.getBoundingClientRect();
      const ddRect = dd.getBoundingClientRect();
      const fallbackWidth = Math.min(Math.max(itemRect.width + 60, 220), Math.min(window.innerWidth * 0.92, 360));
      const ddWidth = ddRect.width || fallbackWidth;
      const margin = 8;

      let left = itemRect.left;
      if (left + ddWidth > window.innerWidth - margin) {
        left = window.innerWidth - ddWidth - margin;
      }
      left = Math.max(margin, left);

      dd.style.setProperty("--nv-dd-left", `${Math.round(left)}px`);
      dd.style.setProperty("--nv-dd-top", `${Math.round(navRect.bottom + 2)}px`);
    }

    function positionOpenDropdowns() {
      if (isMobile()) return;
      document.querySelectorAll(".nv__item.has-dd:hover, .nv__item.has-dd:focus-within")
        .forEach(positionDropdown);
    }

    function scheduleDropdownPosition() {
      if (positionRaf) cancelAnimationFrame(positionRaf);
      positionRaf = requestAnimationFrame(() => {
        positionRaf = null;
        positionOpenDropdowns();
      });
    }

    function restoreMainItems() {
      if (!moreItem || !moreMenu) return;
      navItems.forEach(item => links.insertBefore(item, moreItem));
      moreItem.classList.remove("is-parent-active");
      moreItem.hidden = true;
    }

    function syncMoreState() {
      if (!moreItem || !moreMenu) return;
      const hasItems = moreMenu.children.length > 0;
      const hasActive = Boolean(moreMenu.querySelector(".is-active, .is-parent-active"));
      moreItem.hidden = !hasItems;
      moreItem.classList.toggle("is-parent-active", hasItems && hasActive);
    }

    function linksOverflow() {
      return links.scrollWidth > links.clientWidth + 1;
    }

    function fitNavItems() {
      if (!moreItem || !moreMenu) return;

      restoreMainItems();
      if (isMobile()) return;

      moreItem.hidden = true;
      if (!linksOverflow()) return;

      moreItem.hidden = false;
      for (let i = navItems.length - 1; i >= 0 && linksOverflow(); i -= 1) {
        moreMenu.insertBefore(navItems[i], moreMenu.firstChild);
      }
      syncMoreState();
      scheduleDropdownPosition();
    }

    function scheduleFitNavItems() {
      if (fitRaf) cancelAnimationFrame(fitRaf);
      fitRaf = requestAnimationFrame(() => {
        fitRaf = null;
        fitNavItems();
      });
    }

    function setOpen(open) {
      links.classList.toggle("is-open", open);
      document.body.classList.toggle("nv-menu-open", open);
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
    moreToggle?.addEventListener("click", (e) => {
      e.preventDefault();
      positionDropdown(moreItem);
    });

    // === Submenús: tap en móvil sin navegar (tu patrón original) ===
    const parents = Array.from(document.querySelectorAll(".nv__item.has-dd > a"));
    parents.forEach(a => {
      const li = a.parentElement;
      li?.addEventListener("mouseenter", () => positionDropdown(li), { passive: true });
      li?.addEventListener("focusin", () => positionDropdown(li), { passive: true });

      a.addEventListener("click", (e) => {
        if (!isMobile()) return; // en desktop usa hover
        e.preventDefault();
        e.stopPropagation();
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(x => { if (x !== li) x.classList.remove("is-open"); });
        li.classList.toggle("is-open");
      }, { passive: false });
    });

    links.addEventListener("click", (e) => {
      if (!isMobile()) return;
      const clickedLink = e.target.closest("a");
      if (!clickedLink || clickedLink.parentElement?.classList.contains("has-dd")) return;
      setOpen(false);
    });

    // === Cerrar si se hace click fuera (con guard) ===
    document.addEventListener("click", (e) => {
      if (Date.now() < openGuardUntil) return;  // evita cierre inmediato
      if (!nav.contains(e.target)) {
        setOpen(false);
      }
    }, { passive: true, capture: true });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && links.classList.contains("is-open")) {
        setOpen(false);
        burger.focus();
      }
    });

    // === Reset de estado al cambiar tamaño ===
    window.addEventListener("resize", () => {
      if (!isMobile()) {
        setOpen(false);
      }
      scheduleFitNavItems();
      scheduleDropdownPosition();
    });

    if ("ResizeObserver" in window) {
      const navResizeObserver = new ResizeObserver(scheduleFitNavItems);
      navResizeObserver.observe(nav);
    }
    window.addEventListener("load", scheduleFitNavItems, { once: true });
    scheduleFitNavItems();

    // Log de depuración
    console.log("Navbar listo ✅");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

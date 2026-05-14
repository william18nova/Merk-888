// Menu movil y dropdowns del navbar.
(function () {
  function init() {
    const burger = document.getElementById("nvBurger");
    const links = document.getElementById("nvLinks");
    const nav = document.querySelector(".nv");

    if (!burger || !links || !nav) {
      return setTimeout(init, 100);
    }

    const isMobile = () => window.matchMedia("(max-width: 760px)").matches;

    let openGuardUntil = 0;
    let heightRaf = null;
    let positionRaf = null;
    let touchStartX = null;
    let touchStartY = null;
    const hoverTimers = new WeakMap();

    function syncNavHeight() {
      if (heightRaf) cancelAnimationFrame(heightRaf);
      heightRaf = requestAnimationFrame(() => {
        heightRaf = null;
        const height = Math.max(56, Math.ceil(nav.getBoundingClientRect().height || 56));
        document.documentElement.style.setProperty("--nav-current-h", `${height}px`);
      });
    }

    function positionDropdown(li) {
      if (!li || isMobile()) return;
      const dd = li.querySelector(".nv__dd");
      if (!dd) return;

      const itemRect = li.getBoundingClientRect();
      const navRect = nav.getBoundingClientRect();
      const ddRect = dd.getBoundingClientRect();
      const fallbackWidth = Math.min(
        Math.max(itemRect.width + 60, 220),
        Math.min(window.innerWidth * 0.92, 360)
      );
      const ddWidth = ddRect.width || fallbackWidth;
      const margin = 8;

      let left = itemRect.left;
      if (left + ddWidth > window.innerWidth - margin) {
        left = window.innerWidth - ddWidth - margin;
      }
      left = Math.max(margin, left);

      dd.style.setProperty("--nv-dd-left", `${Math.round(left)}px`);
      dd.style.setProperty("--nv-dd-top", `${Math.round(navRect.bottom + 2)}px`);
      dd.style.setProperty("--nv-dd-trigger-w", `${Math.ceil(itemRect.width)}px`);
    }

    function closeOtherDesktopDropdowns(currentLi) {
      document.querySelectorAll(".nv__item.has-dd.is-hover-open")
        .forEach(item => {
          if (item !== currentLi) item.classList.remove("is-hover-open");
        });
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

    function setOpen(open) {
      links.classList.toggle("is-open", open);
      nav.classList.toggle("is-open", open);
      document.body.classList.toggle("nv-menu-open", open);
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Cerrar menu" : "Abrir menu");
      if (!open) {
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(item => item.classList.remove("is-open"));
        nav.scrollTop = 0;
        touchStartX = null;
        touchStartY = null;
      } else {
        openGuardUntil = Date.now() + 140;
        document.querySelectorAll(".nv__item.has-dd.is-parent-active")
          .forEach(item => item.classList.add("is-open"));
      }
      syncNavHeight();
    }

    function menuIsOpen() {
      return isMobile() && nav.classList.contains("is-open");
    }

    function scrollMenuBy(deltaY) {
      const maxScroll = nav.scrollHeight - nav.clientHeight;
      if (maxScroll <= 0 || !deltaY) return false;

      const current = nav.scrollTop;
      const next = Math.max(0, Math.min(maxScroll, current + deltaY));
      if (next === current) return false;

      nav.scrollTop = next;
      return true;
    }

    burger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(!links.classList.contains("is-open"));
    }, { passive: false });

    nav.addEventListener("wheel", (event) => {
      if (!menuIsOpen()) return;
      if (scrollMenuBy(event.deltaY)) {
        event.preventDefault();
      }
    }, { passive: false });

    nav.addEventListener("touchstart", (event) => {
      if (!menuIsOpen() || event.touches.length !== 1) return;
      touchStartX = event.touches[0].clientX;
      touchStartY = event.touches[0].clientY;
    }, { passive: true });

    nav.addEventListener("touchmove", (event) => {
      if (!menuIsOpen() || event.touches.length !== 1 || touchStartY === null) return;

      const touch = event.touches[0];
      const deltaY = touchStartY - touch.clientY;
      const deltaX = Math.abs(touchStartX - touch.clientX);

      touchStartX = touch.clientX;
      touchStartY = touch.clientY;

      if (Math.abs(deltaY) <= deltaX || Math.abs(deltaY) < 2) return;
      if (scrollMenuBy(deltaY)) {
        event.preventDefault();
      }
    }, { passive: false });

    nav.addEventListener("touchend", () => {
      touchStartX = null;
      touchStartY = null;
    }, { passive: true });

    document.querySelectorAll(".nv__item.has-dd > a").forEach(anchor => {
      const li = anchor.parentElement;
      const dd = li?.querySelector(".nv__dd");

      function openDesktopDropdown() {
        if (!li || isMobile()) return;
        const timer = hoverTimers.get(li);
        if (timer) clearTimeout(timer);
        closeOtherDesktopDropdowns(li);
        li.classList.add("is-hover-open");
        positionDropdown(li);
      }

      function closeDesktopDropdown() {
        if (!li || isMobile()) return;
        const timer = setTimeout(() => {
          li.classList.remove("is-hover-open");
        }, 220);
        hoverTimers.set(li, timer);
      }

      li?.addEventListener("mouseenter", openDesktopDropdown, { passive: true });
      li?.addEventListener("mouseleave", closeDesktopDropdown, { passive: true });
      li?.addEventListener("focusin", openDesktopDropdown, { passive: true });
      li?.addEventListener("focusout", closeDesktopDropdown, { passive: true });
      dd?.addEventListener("mouseenter", openDesktopDropdown, { passive: true });
      dd?.addEventListener("mouseleave", closeDesktopDropdown, { passive: true });

      anchor.addEventListener("click", (event) => {
        if (!isMobile()) return;
        event.preventDefault();
        event.stopPropagation();
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(item => { if (item !== li) item.classList.remove("is-open"); });
        li.classList.toggle("is-open");
      }, { passive: false });
    });

    links.addEventListener("click", (event) => {
      if (!isMobile()) return;
      const clickedLink = event.target.closest("a");
      if (!clickedLink || clickedLink.parentElement?.classList.contains("has-dd")) return;
      setOpen(false);
    });

    document.addEventListener("click", (event) => {
      if (Date.now() < openGuardUntil) return;
      if (!nav.contains(event.target)) setOpen(false);
    }, { passive: true, capture: true });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && links.classList.contains("is-open")) {
        setOpen(false);
        burger.focus();
      }
    });

    window.addEventListener("resize", () => {
      if (!isMobile()) setOpen(false);
      syncNavHeight();
      scheduleDropdownPosition();
    });

    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(() => {
        syncNavHeight();
        scheduleDropdownPosition();
      });
      observer.observe(nav);
    }

    window.addEventListener("load", syncNavHeight, { once: true });
    syncNavHeight();

    console.log("Navbar listo");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

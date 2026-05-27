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
    const canDesktopHover = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    const DESKTOP_HOVER_OPEN_DELAY = 180;
    const DESKTOP_HOVER_CLOSE_DELAY = 160;

    let openGuardUntil = 0;
    let heightRaf = null;
    let positionRaf = null;
    let touchStartX = null;
    let touchStartY = null;
    const hoverTimers = new WeakMap();

    function clearHoverTimer(li) {
      const timer = hoverTimers.get(li);
      if (timer) {
        clearTimeout(timer);
        hoverTimers.delete(li);
      }
    }

    function dropdownAnchor(li) {
      return li?.querySelector(":scope > a");
    }

    function setAriaExpanded(li, open) {
      dropdownAnchor(li)?.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function linksAreWrapped() {
      if (isMobile()) return false;
      const items = Array.from(links.children);
      if (items.length < 2) return false;

      const firstTop = items[0].offsetTop;
      return items.some(item => Math.abs(item.offsetTop - firstTop) > 3);
    }

    function syncWrappedState() {
      const wrapped = linksAreWrapped();
      nav.classList.toggle("is-links-wrapped", wrapped);
      return wrapped;
    }

    function desktopUsesHover() {
      return !isMobile() && canDesktopHover();
    }

    function desktopUsesClick() {
      return !isMobile() && (!canDesktopHover() || syncWrappedState());
    }

    function desktopHoverOpenDelay() {
      return syncWrappedState() ? 320 : DESKTOP_HOVER_OPEN_DELAY;
    }

    function syncNavHeight() {
      if (heightRaf) cancelAnimationFrame(heightRaf);
      heightRaf = requestAnimationFrame(() => {
        heightRaf = null;
        syncWrappedState();
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
          if (item !== currentLi) {
            clearHoverTimer(item);
            item.classList.remove("is-hover-open");
            setAriaExpanded(item, false);
          }
        });
    }

    function positionOpenDropdowns() {
      if (isMobile()) return;
      document.querySelectorAll(".nv__item.has-dd.is-hover-open")
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
          .forEach(item => {
            item.classList.remove("is-open");
            setAriaExpanded(item, false);
          });
        closeOtherDesktopDropdowns(null);
        nav.scrollTop = 0;
        touchStartX = null;
        touchStartY = null;
      } else {
        closeOtherDesktopDropdowns(null);
        openGuardUntil = Date.now() + 140;
        document.querySelectorAll(".nv__item.has-dd.is-parent-active")
          .forEach(item => {
            item.classList.add("is-open");
            setAriaExpanded(item, true);
          });
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
        clearHoverTimer(li);
        closeOtherDesktopDropdowns(li);
        li.classList.add("is-hover-open");
        setAriaExpanded(li, true);
        positionDropdown(li);
      }

      function scheduleDesktopDropdownOpen() {
        if (!li || isMobile()) return;
        clearHoverTimer(li);
        if (!desktopUsesHover()) return;

        const timer = setTimeout(() => {
          if (!desktopUsesHover()) return;
          openDesktopDropdown();
        }, desktopHoverOpenDelay());
        hoverTimers.set(li, timer);
      }

      function scheduleDesktopDropdownClose() {
        if (!li || isMobile()) return;
        clearHoverTimer(li);
        if (!desktopUsesHover()) return;

        const timer = setTimeout(() => {
          li.classList.remove("is-hover-open");
          setAriaExpanded(li, false);
        }, DESKTOP_HOVER_CLOSE_DELAY);
        hoverTimers.set(li, timer);
      }

      function openFocusedDesktopDropdown() {
        if (desktopUsesHover()) openDesktopDropdown();
      }

      function toggleClickDropdown() {
        if (!li) return;
        clearHoverTimer(li);
        const willOpen = !li.classList.contains("is-hover-open");
        closeOtherDesktopDropdowns(li);
        li.classList.toggle("is-hover-open", willOpen);
        setAriaExpanded(li, willOpen);
        if (willOpen) positionDropdown(li);
      }

      li?.addEventListener("mouseenter", scheduleDesktopDropdownOpen, { passive: true });
      li?.addEventListener("mouseleave", scheduleDesktopDropdownClose, { passive: true });
      li?.addEventListener("focusin", openFocusedDesktopDropdown, { passive: true });
      li?.addEventListener("focusout", scheduleDesktopDropdownClose, { passive: true });
      dd?.addEventListener("mouseenter", openDesktopDropdown, { passive: true });
      dd?.addEventListener("mouseleave", scheduleDesktopDropdownClose, { passive: true });

      anchor.addEventListener("click", (event) => {
        if (!isMobile()) {
          if (desktopUsesClick()) {
            event.preventDefault();
            event.stopPropagation();
            toggleClickDropdown();
          }
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        document.querySelectorAll(".nv__item.has-dd.is-open")
          .forEach(item => {
            if (item !== li) {
              item.classList.remove("is-open");
              setAriaExpanded(item, false);
            }
          });
        li.classList.toggle("is-open");
        setAriaExpanded(li, li.classList.contains("is-open"));
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
      if (event.key !== "Escape") return;

      if (links.classList.contains("is-open")) {
        setOpen(false);
        burger.focus();
        return;
      }

      const openDesktopItem = document.querySelector(".nv__item.has-dd.is-hover-open");
      if (openDesktopItem) {
        closeOtherDesktopDropdowns(null);
        dropdownAnchor(openDesktopItem)?.focus();
      }
    });

    window.addEventListener("resize", () => {
      if (isMobile()) {
        closeOtherDesktopDropdowns(null);
      } else {
        setOpen(false);
      }
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

  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

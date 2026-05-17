(function () {
  "use strict";

  const prefetched = new Set();
  const skipPath = /\/(api|autocomplete)\//i;
  const skipWords = /(logout|eliminar|delete|borrar|cerrar_sesion|cerrar-sesion)/i;
  const fileLike = /\.(pdf|zip|rar|7z|png|jpe?g|webp|gif|svg|xlsx?|csv|docx?|pptx?)$/i;

  function getUrl(anchor) {
    if (!anchor || !anchor.href) return null;
    try {
      return new URL(anchor.href, window.location.href);
    } catch (_) {
      return null;
    }
  }

  function isPlainNavigationEvent(event) {
    return !event.defaultPrevented
      && event.button === 0
      && !event.metaKey
      && !event.ctrlKey
      && !event.shiftKey
      && !event.altKey;
  }

  function isInternalPage(anchor) {
    const url = getUrl(anchor);
    if (!url) return false;
    if (url.origin !== window.location.origin) return false;
    if (!/^https?:$/.test(url.protocol)) return false;
    if (anchor.target && anchor.target !== "_self") return false;
    if (anchor.hasAttribute("download")) return false;
    if (anchor.dataset.noPrefetch || anchor.dataset.noTransition) return false;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return false;
    if (skipPath.test(url.pathname) || skipWords.test(url.pathname) || fileLike.test(url.pathname)) return false;
    return true;
  }

  function ensureProgressBar() {
    let bar = document.querySelector(".page-nav-progress");
    if (bar) return bar;
    bar = document.createElement("div");
    bar.className = "page-nav-progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    return bar;
  }

  function beginNavigationFeedback() {
    ensureProgressBar();
    document.body.classList.add("is-page-loading", "is-page-leaving");
  }

  function clearNavigationFeedback() {
    document.body.classList.remove("is-page-loading", "is-page-leaving");
  }

  function schedule(task) {
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(task, { timeout: 700 });
      return;
    }
    window.setTimeout(task, 120);
  }

  function prefetch(anchor) {
    if (!isInternalPage(anchor)) return;
    const url = getUrl(anchor);
    if (!url || prefetched.has(url.href)) return;

    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (connection?.saveData || /2g/i.test(connection?.effectiveType || "")) return;

    prefetched.add(url.href);
    schedule(() => {
      const link = document.createElement("link");
      link.rel = "prefetch";
      link.as = "document";
      link.href = url.href;
      document.head.appendChild(link);
    });
  }

  document.addEventListener("pointerover", (event) => {
    const anchor = event.target.closest?.(".nv a[href]");
    if (anchor) prefetch(anchor);
  }, { passive: true });

  document.addEventListener("focusin", (event) => {
    const anchor = event.target.closest?.(".nv a[href]");
    if (anchor) prefetch(anchor);
  });

  document.addEventListener("touchstart", (event) => {
    const anchor = event.target.closest?.(".nv a[href]");
    if (anchor) prefetch(anchor);
  }, { passive: true });

  document.addEventListener("click", (event) => {
    const anchor = event.target.closest?.("a[href]");
    if (!anchor || !isPlainNavigationEvent(event) || !isInternalPage(anchor)) return;
    beginNavigationFeedback();
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented) return;
    if (!form || form.dataset.noTransition || form.target === "_blank") return;
    beginNavigationFeedback();
  });

  window.addEventListener("pageshow", clearNavigationFeedback);
  window.addEventListener("DOMContentLoaded", clearNavigationFeedback, { once: true });
})();

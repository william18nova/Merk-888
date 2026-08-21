/*
 * Lector de codigos de barras reutilizable para Nova Advance.
 *
 * Uso: agrega data-barcode-camera="true" a cualquier <input>. El modulo
 * inserta el boton, reutiliza un unico dialogo y tambien observa inputs que
 * aparezcan despues (por ejemplo, filas de DataTables o resultados de fotos).
 *
 * Al detectar un codigo actualiza el input y emite:
 *   nova:barcode-scanned  detail: { code, source: "camera", targetId }
 */
(() => {
  "use strict";

  const INPUT_SELECTOR = 'input[data-barcode-camera="true"]';
  const EVENT_NAME = "nova:barcode-scanned";
  const ZXING_SCRIPT_ID = "zxing-cdn";
  const DEFAULT_ZXING_URL = "https://cdn.jsdelivr.net/npm/@zxing/library@0.20.0/umd/index.min.js";
  const ownScript = document.currentScript;
  const zxingUrl = ownScript?.dataset?.zxingUrl || DEFAULT_ZXING_URL;
  const wantedFormats = [
    "ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39",
    "code_93", "codabar", "itf", "data_matrix", "qr_code",
  ];

  const state = {
    target: null,
    trigger: null,
    stream: null,
    running: false,
    session: 0,
    nativeRaf: 0,
    nativeBusy: false,
    lastNativeDetectionAt: 0,
    fallbackTimer: 0,
    acceptTimer: 0,
    zxingReader: null,
    zxingErrorCount: 0,
    torchEnabled: false,
    devices: [],
    deviceIndex: 0,
  };

  let detectorPromise = null;
  let zxingLoadPromise = null;
  let observer = null;
  let generatedInputId = 0;

  const cameraIcon = `
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M14.5 5 13 3h-2L9.5 5H6a3 3 0 0 0-3 3v8a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3V8a3 3 0 0 0-3-3h-3.5Z"></path>
      <circle cx="12" cy="12" r="3.5"></circle>
    </svg>`;

  const isIOSLike = () => {
    const ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/i.test(ua)
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  };

  const isSecureForCamera = () => {
    const host = window.location.hostname;
    return Boolean(window.isSecureContext || host === "localhost" || host === "127.0.0.1");
  };

  function inputLabel(input) {
    const label = input?.labels?.[0]?.textContent?.replace(/\s+/g, " ").trim();
    return label || input?.getAttribute("aria-label") || "código de barras";
  }

  function syncButton(input) {
    const wrapper = input?.closest?.(".nova-barcode-field");
    const button = wrapper?.querySelector?.(".nova-barcode-camera-button");
    if (!button) return;
    button.disabled = Boolean(input.disabled || input.readOnly || state.running);
    button.setAttribute("aria-disabled", String(button.disabled));
  }

  function syncAllButtons() {
    document.querySelectorAll(INPUT_SELECTOR).forEach(syncButton);
  }

  function enhanceInput(input) {
    if (!(input instanceof HTMLInputElement)) return;

    const existingWrapper = input.closest(".nova-barcode-field");
    if (existingWrapper && existingWrapper.contains(input)) {
      syncButton(input);
      return;
    }

    if (!input.id) {
      generatedInputId += 1;
      input.id = `nova-barcode-input-${generatedInputId}`;
    }

    const parent = input.parentNode;
    if (!parent) return;

    const wrapper = document.createElement("span");
    wrapper.className = "nova-barcode-field";
    parent.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "nova-barcode-camera-button";
    button.dataset.novaBarcodeButton = "true";
    button.setAttribute("aria-label", `Escanear ${inputLabel(input)} con la cámara`);
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-controls", "nova-barcode-camera-dialog");
    button.title = "Escanear con la cámara";
    button.innerHTML = cameraIcon;
    button._novaBarcodeTarget = input;
    wrapper.appendChild(button);
    syncButton(input);
  }

  function unenhanceInput(input) {
    if (!(input instanceof HTMLInputElement)) return;
    const wrapper = input.closest(".nova-barcode-field");
    if (!wrapper || !wrapper.parentNode) return;
    if (state.target === input) closeScanner({ restoreFocus: false });
    wrapper.parentNode.insertBefore(input, wrapper);
    wrapper.remove();
  }

  function scanNode(node) {
    if (node === document) {
      document.querySelectorAll(INPUT_SELECTOR).forEach(enhanceInput);
      return;
    }
    if (!(node instanceof Element)) return;
    if (node.matches(INPUT_SELECTOR)) enhanceInput(node);
    node.querySelectorAll?.(INPUT_SELECTOR).forEach(enhanceInput);
  }

  function ensureDialog() {
    let overlay = document.getElementById("nova-barcode-camera-overlay");
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = "nova-barcode-camera-overlay";
    overlay.className = "nova-barcode-camera-overlay";
    overlay.hidden = true;
    overlay.innerHTML = `
      <section id="nova-barcode-camera-dialog"
               class="nova-barcode-camera-dialog"
               role="dialog"
               aria-modal="true"
               aria-labelledby="nova-barcode-camera-title"
               aria-describedby="nova-barcode-camera-status">
        <header class="nova-barcode-camera-head">
          <div class="nova-barcode-camera-heading">
            <span class="nova-barcode-camera-eyebrow">Lector de productos</span>
            <strong id="nova-barcode-camera-title" class="nova-barcode-camera-title">Escanear código de barras</strong>
          </div>
          <button type="button" class="nova-barcode-camera-close" data-nova-camera-close
                  aria-label="Cerrar lector de cámara" title="Cerrar">&times;</button>
        </header>
        <div class="nova-barcode-camera-stage">
          <video id="nova-barcode-camera-video" class="nova-barcode-camera-video"
                 playsinline muted autoplay webkit-playsinline="true"></video>
          <div class="nova-barcode-camera-guide" aria-hidden="true"></div>
          <div class="nova-barcode-camera-scanline" aria-hidden="true"></div>
        </div>
        <div class="nova-barcode-camera-copy">
          <p id="nova-barcode-camera-status" class="nova-barcode-camera-status" role="status" aria-live="polite">
            Preparando el lector…
          </p>
          <p class="nova-barcode-camera-help">Apunta la cámara al código y mantenlo dentro del recuadro. No necesitas tomar una foto.</p>
        </div>
        <div class="nova-barcode-camera-actions">
          <button type="button" id="nova-barcode-camera-switch" class="nova-barcode-camera-action" hidden>
            Cambiar cámara
          </button>
          <button type="button" id="nova-barcode-camera-torch" class="nova-barcode-camera-action" hidden>
            Encender luz
          </button>
          <button type="button" class="nova-barcode-camera-action" data-nova-camera-close>Cancelar</button>
        </div>
      </section>`;
    document.body.appendChild(overlay);

    if (!document.getElementById("nova-barcode-camera-announcer")) {
      const announcer = document.createElement("div");
      announcer.id = "nova-barcode-camera-announcer";
      announcer.className = "nova-barcode-camera-announcer";
      announcer.setAttribute("aria-live", "assertive");
      announcer.setAttribute("aria-atomic", "true");
      document.body.appendChild(announcer);
    }

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-nova-camera-close]")) {
        closeScanner();
      }
    });
    document.getElementById("nova-barcode-camera-switch")
      ?.addEventListener("click", switchCamera);
    document.getElementById("nova-barcode-camera-torch")
      ?.addEventListener("click", toggleTorch);
    return overlay;
  }

  function announce(message) {
    const announcer = document.getElementById("nova-barcode-camera-announcer");
    if (!announcer) return;
    announcer.textContent = "";
    window.setTimeout(() => { announcer.textContent = message; }, 20);
  }

  function setStatus(message, kind = "info") {
    const box = document.getElementById("nova-barcode-camera-status");
    if (!box) return;
    box.textContent = message;
    box.classList.toggle("is-error", kind === "error");
    box.classList.toggle("is-success", kind === "success");
  }

  function showDialog(target) {
    const overlay = ensureDialog();
    const title = document.getElementById("nova-barcode-camera-title");
    if (title) title.textContent = `Escanear: ${inputLabel(target)}`;
    overlay.hidden = false;
    document.body.classList.add("nova-barcode-camera-open");
    requestAnimationFrame(() => overlay.classList.add("is-open"));
    window.setTimeout(() => {
      overlay.querySelector("[data-nova-camera-close]")?.focus({ preventScroll: true });
    }, 30);
  }

  function hideDialog() {
    const overlay = document.getElementById("nova-barcode-camera-overlay");
    if (!overlay) return;
    overlay.classList.remove("is-open");
    overlay.hidden = true;
    document.body.classList.remove("nova-barcode-camera-open");
  }

  function clearTimersAndDecoders() {
    stopNativeDetector();
    if (state.fallbackTimer) window.clearTimeout(state.fallbackTimer);
    state.fallbackTimer = 0;
    if (state.acceptTimer) window.clearTimeout(state.acceptTimer);
    state.acceptTimer = 0;
    try { state.zxingReader?.reset?.(); } catch (_) {}
    state.zxingErrorCount = 0;
  }

  function stopNativeDetector() {
    if (state.nativeRaf) cancelAnimationFrame(state.nativeRaf);
    state.nativeRaf = 0;
    state.nativeBusy = false;
    state.lastNativeDetectionAt = 0;
  }

  function stopMedia() {
    clearTimersAndDecoders();
    const video = document.getElementById("nova-barcode-camera-video");
    if (video) {
      try { video.pause(); } catch (_) {}
      video.srcObject = null;
    }
    if (state.stream) {
      try { state.stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
      state.stream = null;
    }
    state.torchEnabled = false;
    const torch = document.getElementById("nova-barcode-camera-torch");
    if (torch) {
      torch.hidden = true;
      torch.textContent = "Encender luz";
    }
    const switchButton = document.getElementById("nova-barcode-camera-switch");
    if (switchButton) switchButton.hidden = true;
  }

  function closeScanner({ restoreFocus = true } = {}) {
    const trigger = state.trigger;
    state.session += 1;
    state.running = false;
    stopMedia();
    hideDialog();
    state.target = null;
    state.trigger = null;
    state.devices = [];
    syncAllButtons();
    if (restoreFocus && trigger?.isConnected) {
      window.setTimeout(() => trigger.focus({ preventScroll: true }), 0);
    }
  }

  function friendlyCameraError(error) {
    switch (error?.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "No se concedió permiso para usar la cámara. Habilítalo en el navegador e intenta de nuevo.";
      case "NotFoundError":
      case "DevicesNotFoundError":
        return "No se encontró una cámara disponible en este dispositivo.";
      case "NotReadableError":
      case "TrackStartError":
        return "La cámara está ocupada por otra aplicación o no pudo iniciarse.";
      case "OverconstrainedError":
        return "La cámara no admite la configuración solicitada. Intenta cambiar de cámara.";
      default:
        return "No se pudo abrir la cámara. Revisa el permiso del navegador e intenta de nuevo.";
    }
  }

  function staleSessionError() {
    const error = new Error("CameraSessionCancelled");
    error.name = "AbortError";
    return error;
  }

  async function openStream(preferredDeviceId = "", token) {
    const attempts = [];
    if (preferredDeviceId) {
      attempts.push({
        audio: false,
        video: {
          deviceId: { exact: preferredDeviceId },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      });
    }
    attempts.push(
      {
        audio: false,
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      },
      { audio: false, video: { facingMode: "environment" } },
      { audio: false, video: true },
    );

    let lastError = null;
    for (const constraints of attempts) {
      if (!state.running || token !== state.session) throw staleSessionError();
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (!state.running || token !== state.session) {
          stream.getTracks().forEach((track) => track.stop());
          throw staleSessionError();
        }
        return stream;
      } catch (error) {
        if (!state.running || token !== state.session || error?.name === "AbortError") {
          throw staleSessionError();
        }
        lastError = error;
      }
    }
    throw lastError || new Error("CameraUnavailable");
  }

  function waitForVideo(video) {
    const isReady = () => video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0;
    if (isReady()) return Promise.resolve(true);
    return new Promise((resolve) => {
      let settled = false;
      let timeout = 0;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        video.removeEventListener("loadedmetadata", finish);
        video.removeEventListener("canplay", finish);
        resolve(isReady());
      };
      video.addEventListener("loadedmetadata", finish, { once: true });
      video.addEventListener("canplay", finish, { once: true });
      timeout = window.setTimeout(finish, 2500);
    });
  }

  async function getDetector() {
    if (!("BarcodeDetector" in window)) return null;
    if (detectorPromise) return detectorPromise;
    detectorPromise = (async () => {
      try {
        const supported = await window.BarcodeDetector.getSupportedFormats?.();
        const formats = Array.isArray(supported) && supported.length
          ? wantedFormats.filter((format) => supported.includes(format))
          : wantedFormats;
        if (!formats.length) return null;
        return new window.BarcodeDetector({ formats });
      } catch (_) {
        try { return new window.BarcodeDetector(); } catch (_) { return null; }
      }
    })();
    return detectorPromise;
  }

  function loadZXing() {
    if (window.ZXing?.BrowserMultiFormatReader) return Promise.resolve(true);
    if (zxingLoadPromise) return zxingLoadPromise;

    zxingLoadPromise = new Promise((resolve) => {
      let script = document.getElementById(ZXING_SCRIPT_ID);
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        if (!ok) {
          zxingLoadPromise = null;
          if (!window.ZXing?.BrowserMultiFormatReader && script?.isConnected) {
            script.remove();
          }
        }
        resolve(Boolean(ok));
      };
      const timeout = window.setTimeout(
        () => finish(Boolean(window.ZXing?.BrowserMultiFormatReader)),
        8000,
      );

      if (!script) {
        script = document.createElement("script");
        script.id = ZXING_SCRIPT_ID;
        script.src = zxingUrl;
        script.async = true;
        document.head.appendChild(script);
      }
      script.addEventListener(
        "load",
        () => finish(Boolean(window.ZXing?.BrowserMultiFormatReader)),
        { once: true },
      );
      script.addEventListener("error", () => finish(false), { once: true });
    });
    return zxingLoadPromise;
  }

  async function getZXingReader() {
    if (state.zxingReader) return state.zxingReader;
    if (!(await loadZXing()) || !window.ZXing?.BrowserMultiFormatReader) return null;
    try {
      state.zxingReader = new window.ZXing.BrowserMultiFormatReader();
      return state.zxingReader;
    } catch (_) {
      return null;
    }
  }

  function resultText(result) {
    if (!result) return "";
    if (typeof result.getText === "function") return String(result.getText() || "").trim();
    return String(result.rawValue || result.text || "").trim();
  }

  function acceptCode(rawCode, token) {
    const code = String(rawCode || "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
    if (!code || code.length > 512 || !state.running || token !== state.session) return false;

    const target = state.target;
    if (!target?.isConnected || target.disabled || target.readOnly) {
      closeScanner({ restoreFocus: false });
      return false;
    }

    state.running = false;
    stopMedia();
    syncAllButtons();
    const successMessage = `Código detectado: ${code}`;
    setStatus(successMessage, "success");
    announce(successMessage);

    target.value = code;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    const scannedEvent = new CustomEvent(EVENT_NAME, {
      bubbles: true,
      cancelable: true,
      detail: {
        code,
        source: "camera",
        targetId: target.id || "",
      },
    });
    target.dispatchEvent(scannedEvent);
    const shouldRefocusTarget = !scannedEvent.defaultPrevented;

    state.acceptTimer = window.setTimeout(() => {
      const focusTarget = target?.isConnected ? target : null;
      closeScanner({ restoreFocus: false });
      if (shouldRefocusTarget) focusTarget?.focus?.({ preventScroll: true });
    }, 560);
    return true;
  }

  async function startNativeDetector(video, token) {
    if (isIOSLike()) return false;
    const detector = await getDetector();
    if (!detector || token !== state.session || !state.running) return false;

    state.lastNativeDetectionAt = 0;
    const tick = async (timestamp = 0) => {
      if (!state.running || token !== state.session) return;
      // Limita el trabajo a unas 10 lecturas por segundo para cuidar CPU y bateria.
      if (
        !state.nativeBusy
        && video.readyState >= 2
        && timestamp - state.lastNativeDetectionAt >= 100
      ) {
        state.lastNativeDetectionAt = timestamp;
        state.nativeBusy = true;
        try {
          const codes = await detector.detect(video);
          if (codes?.length && acceptCode(codes[0]?.rawValue || "", token)) return;
        } catch (_) {
          // Algunos navegadores anuncian BarcodeDetector pero fallan en cuadros
          // concretos; ZXing se inicia automaticamente como respaldo.
        } finally {
          state.nativeBusy = false;
        }
      }
      if (state.running && token === state.session) {
        state.nativeRaf = requestAnimationFrame(tick);
      }
    };
    state.nativeRaf = requestAnimationFrame(tick);
    return true;
  }

  function isRoutineZXingError(error) {
    const name = String(error?.name || error?.constructor?.name || "");
    return /NotFound|Checksum|Format/i.test(name);
  }

  function handleZXingResult(result, error, token) {
    if (!state.running || token !== state.session) return;
    if (result) {
      state.zxingErrorCount = 0;
      acceptCode(resultText(result), token);
      return;
    }
    if (!error || isRoutineZXingError(error)) return;

    state.zxingErrorCount += 1;
    if (state.zxingErrorCount < 3) return;
    state.running = false;
    stopMedia();
    syncAllButtons();
    setStatus("El lector perdió la imagen de la cámara. Ciérralo e intenta de nuevo.", "error");
  }

  async function startZXing(video, token, { replaceNative = false } = {}) {
    const reader = await getZXingReader();
    if (!reader || token !== state.session || !state.running) return false;

    // Solo queda un decodificador activo: al iniciar ZXing se detiene el
    // detector nativo para evitar trabajo duplicado continuo a alta resolucion.
    if (replaceNative) stopNativeDetector();
    state.zxingErrorCount = 0;

    if (typeof reader.decodeContinuously === "function") {
      try {
        reader.decodeContinuously(video, (result, error) => {
          handleZXingResult(result, error, token);
        });
        return true;
      } catch (_) {}
    }

    if (typeof reader.decodeFromVideoElementContinuously === "function") {
      try {
        // Esta API espera un nuevo evento "playing". Se pausa primero para
        // que ZXing pueda iniciar correctamente incluso si el video ya corria.
        try { video.pause(); } catch (_) {}
        const startPromise = reader.decodeFromVideoElementContinuously(
          video,
          (result, error) => handleZXingResult(result, error, token),
        );
        Promise.resolve(startPromise).catch((error) => {
          handleZXingResult(null, error, token);
        });
        return true;
      } catch (_) {}
    }

    if (typeof reader.decodeOnceFromVideoElement !== "function") return false;
    const loop = async () => {
      if (!state.running || token !== state.session) return;
      try {
        const result = await reader.decodeOnceFromVideoElement(video);
        if (acceptCode(resultText(result), token)) return;
      } catch (_) {}
      if (state.running && token === state.session) requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return true;
  }

  async function refreshDeviceControls() {
    const switchButton = document.getElementById("nova-barcode-camera-switch");
    try {
      state.devices = (await navigator.mediaDevices.enumerateDevices())
        .filter((device) => device.kind === "videoinput");
    } catch (_) {
      state.devices = [];
    }

    const currentId = state.stream?.getVideoTracks?.()[0]?.getSettings?.().deviceId || "";
    const currentIndex = state.devices.findIndex((device) => device.deviceId === currentId);
    state.deviceIndex = currentIndex >= 0 ? currentIndex : 0;
    if (switchButton) switchButton.hidden = state.devices.length < 2;

    const torchButton = document.getElementById("nova-barcode-camera-torch");
    const track = state.stream?.getVideoTracks?.()[0];
    const capabilities = track?.getCapabilities?.() || {};
    if (torchButton) torchButton.hidden = !capabilities.torch;
  }

  async function startPipeline(token, preferredDeviceId = "") {
    const video = document.getElementById("nova-barcode-camera-video");
    if (!video) return;
    setStatus("Solicitando permiso para usar la cámara…");

    try {
      const stream = await openStream(preferredDeviceId, token);
      if (token !== state.session || !state.running) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      state.stream = stream;
      video.srcObject = stream;
      video.muted = true;
      video.setAttribute("playsinline", "true");
      try { await video.play(); } catch (_) {}
      const videoReady = await waitForVideo(video);
      if (token !== state.session || !state.running) return;
      if (!videoReady) {
        const error = new Error("CameraVideoUnavailable");
        error.name = "NotReadableError";
        throw error;
      }
      await refreshDeviceControls();
    } catch (error) {
      if (token !== state.session) return;
      state.running = false;
      stopMedia();
      syncAllButtons();
      setStatus(friendlyCameraError(error), "error");
      return;
    }

    setStatus("Apunta al código de barras y mantén el dispositivo estable.");

    if (isIOSLike()) {
      const zxingStarted = await startZXing(video, token);
      if (!zxingStarted && token === state.session && state.running) {
        state.running = false;
        stopMedia();
        syncAllButtons();
        setStatus("No se pudo cargar el lector compatible con este navegador. Revisa la conexión e intenta de nuevo.", "error");
      }
      return;
    }

    const nativeStarted = await startNativeDetector(video, token);
    if (nativeStarted) {
      state.fallbackTimer = window.setTimeout(() => {
        if (state.running && token === state.session) {
          startZXing(video, token, { replaceNative: true })
            .then((started) => {
              if (!started && state.running && token === state.session && !state.nativeRaf) {
                startNativeDetector(video, token).catch(() => {});
              }
            })
            .catch(() => {
              if (state.running && token === state.session && !state.nativeRaf) {
                startNativeDetector(video, token).catch(() => {});
              }
            });
        }
      }, 1700);
      return;
    }

    const zxingStarted = await startZXing(video, token);
    if (!zxingStarted && token === state.session && state.running) {
      state.running = false;
      stopMedia();
      syncAllButtons();
      setStatus("Este navegador no pudo iniciar un lector de códigos compatible.", "error");
    }
  }

  async function openScanner(input, trigger) {
    if (!(input instanceof HTMLInputElement) || input.disabled || input.readOnly) return;
    if (state.target || state.running) closeScanner({ restoreFocus: false });

    state.target = input;
    state.trigger = trigger || null;
    showDialog(input);

    if (!isSecureForCamera()) {
      setStatus("La cámara solo funciona desde HTTPS o desde localhost. Abre la página con una conexión segura.", "error");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("Este navegador no permite acceder a la cámara. Prueba una versión reciente de Chrome, Edge o Safari.", "error");
      return;
    }

    state.running = true;
    const token = ++state.session;
    syncAllButtons();
    await startPipeline(token);
  }

  async function switchCamera() {
    if (!state.running || state.devices.length < 2) return;
    state.deviceIndex = (state.deviceIndex + 1) % state.devices.length;
    const nextId = state.devices[state.deviceIndex]?.deviceId || "";
    state.session += 1;
    const token = state.session;
    stopMedia();
    state.running = true;
    syncAllButtons();
    await startPipeline(token, nextId);
  }

  async function toggleTorch() {
    const track = state.stream?.getVideoTracks?.()[0];
    const torchButton = document.getElementById("nova-barcode-camera-torch");
    if (!track || !torchButton) return;
    try {
      state.torchEnabled = !state.torchEnabled;
      await track.applyConstraints({ advanced: [{ torch: state.torchEnabled }] });
      torchButton.textContent = state.torchEnabled ? "Apagar luz" : "Encender luz";
    } catch (_) {
      state.torchEnabled = false;
      torchButton.hidden = true;
      setStatus("La luz de esta cámara no se puede controlar desde el navegador.", "error");
    }
  }

  function handleClick(event) {
    const button = event.target.closest?.("[data-nova-barcode-button]");
    if (!button) return;
    event.preventDefault();
    const input = button._novaBarcodeTarget
      || button.closest(".nova-barcode-field")?.querySelector(INPUT_SELECTOR);
    openScanner(input, button);
  }

  function handleDialogKeys(event) {
    const overlay = document.getElementById("nova-barcode-camera-overlay");
    if (!overlay || overlay.hidden) return;

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeScanner();
      return;
    }

    if (event.key !== "Tab") return;
    const focusable = Array.from(overlay.querySelectorAll("button:not([hidden]):not(:disabled)"));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function init() {
    scanNode(document);
    observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "attributes") {
          if (!(mutation.target instanceof HTMLInputElement)) return;
          if (mutation.target.matches(INPUT_SELECTOR)) enhanceInput(mutation.target);
          else unenhanceInput(mutation.target);
          return;
        }
        mutation.addedNodes.forEach(scanNode);
        mutation.removedNodes.forEach((node) => {
          if (!state.target) return;
          if (node === state.target || (node instanceof Element && node.contains(state.target))) {
            closeScanner({ restoreFocus: false });
          }
        });
      });
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled", "readonly", "data-barcode-camera"],
    });

    document.addEventListener("click", handleClick);
    document.addEventListener("keydown", handleDialogKeys, true);
    window.addEventListener("pagehide", () => closeScanner({ restoreFocus: false }));
    window.addEventListener("beforeunload", () => closeScanner({ restoreFocus: false }));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && state.target) closeScanner({ restoreFocus: false });
    });

    window.NovaBarcodeCamera = Object.freeze({
      close: closeScanner,
      enhance: () => scanNode(document),
      openFor(input) {
        const target = typeof input === "string" ? document.querySelector(input) : input;
        return openScanner(target, null);
      },
      eventName: EVENT_NAME,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

(() => {
  "use strict";

  const form = document.querySelector("[data-print-config-form]");
  const pointSelect = document.querySelector("[data-point-select]");
  const versionInput = document.querySelector("[data-version-input]");
  const profileInputs = Array.from(document.querySelectorAll("[data-profile-input]"));
  const receiptPreview = document.querySelector("[data-receipt-preview]");
  const currentProfile = document.querySelector("[data-current-profile]");
  const currentTransport = document.querySelector("[data-current-transport]");
  const savedProfile = document.querySelector("[data-saved-profile]");
  const updatedAt = document.querySelector("[data-updated-at]");
  const updatedBy = document.querySelector("[data-updated-by]");
  const paperWidth = document.querySelector("[data-paper-width]");
  const columnCount = document.querySelector("[data-column-count]");
  const outputMethod = document.querySelector("[data-output-method]");
  const combinationCards = Array.from(document.querySelectorAll("[data-combination]"));
  const migrationReady = form?.dataset.migrationReady !== "0";

  const dialog = document.getElementById("pc-confirm-dialog");
  const dialogPoint = dialog?.querySelector("[data-dialog-point]");
  const dialogProfile = dialog?.querySelector("[data-dialog-profile]");
  const dialogConfirm = dialog?.querySelector("[data-dialog-confirm]");
  const dialogCancel = dialog?.querySelector("[data-dialog-cancel]");
  const dialogClose = dialog?.querySelector("[data-dialog-close]");

  const VALID_SYSTEMS = new Set(["windows", "linux"]);
  const VALID_SIZES = new Set(["grande", "pequena"]);

  const normalizeSystem = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    return VALID_SYSTEMS.has(normalized) ? normalized : "windows";
  };

  const normalizeSize = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    return VALID_SIZES.has(normalized) ? normalized : "grande";
  };

  const selectedValue = (name, fallback) => {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || fallback;
  };

  const selectedSystem = () => normalizeSystem(selectedValue("operating_system", "windows"));
  const selectedSize = () => normalizeSize(selectedValue("paper_size", "grande"));

  const systemLabel = (system) => system === "linux" ? "Linux" : "Windows";
  const sizeLabel = (size) => size === "pequena" ? "pequeña" : "grande";

  const setRadioValue = (name, value) => {
    const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (input) input.checked = true;
  };

  const center = (text, width) => {
    const clipped = String(text || "").slice(0, width);
    const left = Math.max(0, Math.floor((width - clipped.length) / 2));
    return `${" ".repeat(left)}${clipped}`.padEnd(width, " ");
  };

  const leftRight = (left, right, width) => {
    const rightText = String(right || "").slice(0, width);
    const available = Math.max(0, width - rightText.length - 1);
    const leftText = String(left || "").slice(0, available);
    return `${leftText}${" ".repeat(Math.max(1, width - leftText.length - rightText.length))}${rightText}`.slice(0, width);
  };

  const wrapWords = (text, width) => {
    const words = String(text || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return [""];

    const lines = [];
    let current = "";
    words.forEach((word) => {
      let pending = word;
      while (pending.length > width) {
        if (current) {
          lines.push(current);
          current = "";
        }
        lines.push(pending.slice(0, width));
        pending = pending.slice(width);
      }

      if (!pending) return;
      if (!current) {
        current = pending;
      } else if (`${current} ${pending}`.length <= width) {
        current = `${current} ${pending}`;
      } else {
        lines.push(current);
        current = pending;
      }
    });
    if (current) lines.push(current);
    return lines;
  };

  const buildReceiptPreview = (size) => {
    const width = size === "pequena" ? 32 : 48;
    const divider = "-".repeat(width);
    const product = size === "pequena"
      ? "CAFE PREMIUM TOSTADO 500 G"
      : "CAFE PREMIUM TOSTADO Y MOLIDO 500 G";
    const lines = [
      center("NOVA POS", width),
      center("MERK2888", width),
      center("FACTURA #142266", width),
      divider,
      `Fecha: 02/08/2026  09:00`.slice(0, width),
      `Caja: Punto de pago principal`.slice(0, width),
      divider,
      ...wrapWords(product, width),
      leftRight("2 x $12.500", "$25.000", width),
      divider,
      leftRight("TOTAL", "$25.000", width),
      leftRight("EFECTIVO", "$25.000", width),
      divider,
      center("¡Gracias por su compra!", width),
    ];
    return { width, text: lines.map((line) => line.slice(0, width)).join("\n") };
  };

  const animateReceipt = () => {
    if (!receiptPreview) return;
    receiptPreview.style.animation = "none";
    void receiptPreview.offsetHeight;
    receiptPreview.style.animation = "";
  };

  const updateProfileView = ({ animate = true } = {}) => {
    const system = selectedSystem();
    const size = selectedSize();
    const isSmall = size === "pequena";
    const profileLabel = `${systemLabel(system)} · Factura ${sizeLabel(size)}`;
    const transportLabel = system === "linux" ? "USB / CUPS" : "Agente POS local";
    const widthLabel = isSmall ? "58 mm" : "80 mm";

    if (currentProfile) currentProfile.textContent = profileLabel;
    if (currentTransport) currentTransport.textContent = `${transportLabel} · ${widthLabel}`;
    if (paperWidth) paperWidth.textContent = widthLabel;
    if (columnCount) columnCount.textContent = isSmall ? "32" : "48";
    if (outputMethod) outputMethod.textContent = system === "linux" ? "USB / CUPS" : "Agente POS";

    if (receiptPreview) {
      const preview = buildReceiptPreview(size);
      receiptPreview.textContent = preview.text;
      receiptPreview.style.setProperty("--receipt-cols", String(preview.width));
      receiptPreview.setAttribute(
        "aria-label",
        `Vista previa de factura ${sizeLabel(size)} de ${preview.width} columnas`,
      );
      if (animate) animateReceipt();
    }

    const activeCombination = `${system}-${size}`;
    combinationCards.forEach((card) => {
      const active = card.dataset.combination === activeCombination;
      card.classList.toggle("is-active", active);
      card.setAttribute("aria-current", active ? "true" : "false");
    });
  };

  const applySelectedPoint = () => {
    const option = pointSelect?.selectedOptions?.[0];
    if (!option) {
      updateProfileView({ animate: false });
      return;
    }

    const operatingSystem = normalizeSystem(option.dataset.operatingSystem);
    const paperSize = normalizeSize(option.dataset.paperSize);
    setRadioValue("operating_system", operatingSystem);
    setRadioValue("paper_size", paperSize);

    if (versionInput) versionInput.value = String(option.dataset.version || "0");
    if (savedProfile) {
      savedProfile.textContent = `${systemLabel(operatingSystem)} · ${sizeLabel(paperSize)}`;
    }
    if (updatedAt) updatedAt.textContent = option.dataset.updatedAt || "Configuración inicial";
    if (updatedBy) updatedBy.textContent = option.dataset.updatedBy || "Configuración inicial";

    if (form) delete form.dataset.confirmed;
    updateProfileView();
  };

  const currentPointLabel = () => {
    const option = pointSelect?.selectedOptions?.[0];
    return String(option?.dataset.label || option?.textContent || "seleccionado").trim();
  };

  const closeDialog = () => {
    if (dialog?.open) dialog.close();
  };

  const requestConfirmation = () => {
    const system = selectedSystem();
    const size = selectedSize();
    const point = currentPointLabel();

    if (dialogPoint) dialogPoint.textContent = point;
    if (dialogProfile) {
      dialogProfile.textContent = `${systemLabel(system)} con factura ${sizeLabel(size)}`;
    }

    if (dialog && typeof dialog.showModal === "function") {
      dialog.showModal();
      window.setTimeout(() => dialogCancel?.focus(), 0);
      return;
    }

    const accepted = window.confirm(
      `¿Guardar ${systemLabel(system)} con factura ${sizeLabel(size)} para ${point}?`,
    );
    if (!accepted || !form) return;
    form.dataset.confirmed = "1";
    form.requestSubmit();
  };

  const lockForm = () => {
    if (!form || form.dataset.submitted === "1") return false;
    form.dataset.submitted = "1";
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });

    const submit = form.querySelector("[data-submit-button]");
    const label = submit?.querySelector("[data-submit-label]");
    if (submit) submit.setAttribute("aria-busy", "true");
    if (label) label.textContent = "Guardando configuración…";
    return true;
  };

  pointSelect?.addEventListener("change", applySelectedPoint);

  profileInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (form) delete form.dataset.confirmed;
      updateProfileView();
    });
  });

  form?.addEventListener("submit", (event) => {
    if (!migrationReady) {
      event.preventDefault();
      return;
    }
    if (form.dataset.submitted === "1") {
      event.preventDefault();
      return;
    }

    if (form.dataset.confirmed !== "1") {
      event.preventDefault();
      if (!form.reportValidity()) return;
      requestConfirmation();
      return;
    }

    if (!lockForm()) event.preventDefault();
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const inputId = button.getAttribute("aria-controls");
      const input = inputId ? document.getElementById(inputId) : null;
      if (!input) return;

      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.classList.toggle("is-visible", !showing);
      button.setAttribute("aria-label", showing ? "Mostrar contraseña" : "Ocultar contraseña");
      input.focus({ preventScroll: true });
      const end = input.value.length;
      input.setSelectionRange?.(end, end);
    });
  });

  dialogConfirm?.addEventListener("click", () => {
    if (!form) return closeDialog();
    form.dataset.confirmed = "1";
    closeDialog();
    form.requestSubmit();
  });

  dialogCancel?.addEventListener("click", closeDialog);
  dialogClose?.addEventListener("click", closeDialog);

  dialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });

  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });

  if (pointSelect?.options?.length) {
    const requestedPoint = String(pointSelect.dataset.selectedPointId || "").trim();
    if (requestedPoint) {
      const matchingOption = Array.from(pointSelect.options).find(
        (option) => String(option.value) === requestedPoint,
      );
      if (matchingOption) pointSelect.value = matchingOption.value;
    }
    applySelectedPoint();
  } else {
    setRadioValue("operating_system", "windows");
    setRadioValue("paper_size", "grande");
    updateProfileView({ animate: false });
  }
})();

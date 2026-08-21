(function () {
  "use strict";

  const forms = Array.from(document.querySelectorAll("[data-payment-method-form]"));
  const passwordToggles = document.querySelectorAll("[data-password-toggle]");
  const filter = document.querySelector("[data-method-filter]");
  const methodCards = Array.from(document.querySelectorAll("[data-method-card]"));
  const filterEmpty = document.querySelector("[data-filter-empty]");
  const dialog = document.querySelector("#pmc-toggle-dialog");
  const dialogMethod = dialog?.querySelector("[data-dialog-method]");
  const dialogConfirm = dialog?.querySelector("[data-dialog-confirm]");
  const dialogCloseButtons = dialog?.querySelectorAll(
    "[data-dialog-close], [data-dialog-cancel]"
  );

  let pendingForm = null;
  let pendingSubmitter = null;
  let returnFocus = null;
  const confirmedForms = new WeakSet();

  function createRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }

    const randomPart = Math.random().toString(36).slice(2);
    return `pm-${Date.now().toString(36)}-${randomPart}`;
  }

  function ensureRequestId(form) {
    const input = form.querySelector("[data-request-id]");
    if (input && !input.value) {
      input.value = createRequestId();
    }
  }

  function normaliseSearch(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("es-CO")
      .trim();
  }

  function setBusy(form, submitter) {
    form.dataset.submitting = "1";
    form.classList.add("is-submitting");

    form.querySelectorAll("[data-submit-button]").forEach((button) => {
      button.setAttribute("aria-disabled", "true");
    });

    const label = submitter?.querySelector("[data-submit-label]");
    const busyLabel = submitter?.dataset.busyLabel;
    if (label && busyLabel) {
      label.textContent = busyLabel;
    }
  }

  function closeDialog() {
    if (dialog?.open) {
      dialog.close();
    }
    pendingForm = null;
    pendingSubmitter = null;
    returnFocus?.focus();
    returnFocus = null;
  }

  function openDisableDialog(form, submitter) {
    const labelInput = form.querySelector("input[name='label']");
    const methodLabel = labelInput?.value.trim() || form.dataset.methodLabel || "Este método";

    pendingForm = form;
    pendingSubmitter = submitter;
    returnFocus = submitter;

    if (dialogMethod) {
      dialogMethod.textContent = methodLabel;
    }

    if (dialog && typeof dialog.showModal === "function") {
      dialog.showModal();
      dialogConfirm?.focus();
      return;
    }

    if (window.confirm(`¿Desactivar ${methodLabel}?`)) {
      confirmedForms.add(form);
      form.requestSubmit(submitter);
    } else {
      pendingForm = null;
      pendingSubmitter = null;
      returnFocus = null;
    }
  }

  forms.forEach((form) => {
    ensureRequestId(form);

    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;

      if (form.dataset.submitting === "1") {
        event.preventDefault();
        return;
      }

      const isDisableAction =
        submitter?.matches("[data-toggle-button]") &&
        form.querySelector("[data-active-target]")?.value === "0";

      if (isDisableAction && !confirmedForms.has(form)) {
        event.preventDefault();
        openDisableDialog(form, submitter);
        return;
      }

      confirmedForms.delete(form);
      ensureRequestId(form);
      setBusy(form, submitter);
    });
  });

  passwordToggles.forEach((button) => {
    button.addEventListener("click", () => {
      const inputId = button.getAttribute("aria-controls");
      const input = inputId ? document.getElementById(inputId) : null;
      if (!input) return;

      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", show ? "true" : "false");
      button.setAttribute(
        "aria-label",
        show ? "Ocultar contraseña" : "Mostrar contraseña"
      );
      input.focus({ preventScroll: true });
    });
  });

  filter?.addEventListener("input", () => {
    const term = normaliseSearch(filter.value);
    let visible = 0;

    methodCards.forEach((card) => {
      const matches = normaliseSearch(card.dataset.methodSearch).includes(term);
      card.hidden = !matches;
      if (matches) visible += 1;
    });

    if (filterEmpty) {
      filterEmpty.hidden = visible > 0 || methodCards.length === 0;
    }
  });

  dialogCloseButtons?.forEach((button) => {
    button.addEventListener("click", closeDialog);
  });

  dialogConfirm?.addEventListener("click", () => {
    const form = pendingForm;
    const submitter = pendingSubmitter;
    if (!form || !submitter) {
      closeDialog();
      return;
    }

    confirmedForms.add(form);
    if (dialog?.open) dialog.close();
    pendingForm = null;
    pendingSubmitter = null;
    returnFocus = null;
    form.requestSubmit(submitter);
  });

  dialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });

  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });
})();

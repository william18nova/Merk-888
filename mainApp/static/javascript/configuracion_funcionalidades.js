(() => {
  "use strict";

  const forms = Array.from(document.querySelectorAll("[data-feature-form]"));
  const dialog = document.getElementById("ff-disable-dialog");
  const dialogFeature = dialog?.querySelector("[data-dialog-feature]");
  const dialogImpact = dialog?.querySelector("[data-dialog-impact]");
  const dialogConfirm = dialog?.querySelector("[data-dialog-confirm]");
  const dialogCancel = dialog?.querySelector("[data-dialog-cancel]");
  const dialogClose = dialog?.querySelector("[data-dialog-close]");
  let pendingForm = null;

  const isDisabling = (form) => {
    const value = String(form.querySelector("[name='enabled']")?.value || "")
      .trim()
      .toLowerCase();
    return ["0", "false", "off", "no"].includes(value);
  };

  const resetPending = () => {
    if (pendingForm) {
      delete pendingForm.dataset.confirmed;
    }
    pendingForm = null;
  };

  const closeDialog = () => {
    if (dialog?.open) dialog.close();
    resetPending();
  };

  const lockSubmittedForm = (form) => {
    if (form.dataset.submitted === "1") return false;
    form.dataset.submitted = "1";

    form.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });

    const submit = form.querySelector("[data-submit-button]");
    const label = submit?.querySelector("[data-submit-label]");
    if (submit) submit.setAttribute("aria-busy", "true");
    if (label) label.textContent = "Guardando cambio…";
    return true;
  };

  const requestDisableConfirmation = (form) => {
    pendingForm = form;
    if (dialogFeature) {
      dialogFeature.textContent =
        form.dataset.featureLabel || "Esta funcionalidad";
    }
    if (dialogImpact) {
      dialogImpact.textContent =
        form.dataset.featureDisableMessage ||
        "Esta función dejará de estar disponible para todos los usuarios.";
    }

    if (dialog && typeof dialog.showModal === "function") {
      dialog.showModal();
      window.setTimeout(() => dialogCancel?.focus(), 0);
      return;
    }

    const accepted = window.confirm(
      `Este cambio será global. ${
        form.dataset.featureDisableMessage ||
        "Esta función dejará de estar disponible para todos los usuarios."
      } ¿Deseas continuar?`
    );
    if (!accepted) {
      resetPending();
      return;
    }

    form.dataset.confirmed = "1";
    form.requestSubmit(form.querySelector("[data-submit-button]"));
  };

  forms.forEach((form) => {
    const reason = form.querySelector("[data-feature-reason]");

    if (reason) reason.required = isDisabling(form);

    form.addEventListener("submit", (event) => {
      if (form.dataset.submitted === "1") {
        event.preventDefault();
        return;
      }

      if (isDisabling(form) && form.dataset.confirmed !== "1") {
        event.preventDefault();
        if (!form.reportValidity()) return;
        requestDisableConfirmation(form);
        return;
      }

      if (!lockSubmittedForm(form)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const inputId = button.getAttribute("aria-controls");
      const input = inputId ? document.getElementById(inputId) : null;
      if (!input) return;

      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.classList.toggle("is-visible", !showing);
      button.setAttribute(
        "aria-label",
        showing ? "Mostrar contraseña" : "Ocultar contraseña"
      );
      input.focus({ preventScroll: true });
      const end = input.value.length;
      input.setSelectionRange?.(end, end);
    });
  });

  document.querySelectorAll("[data-feature-change]").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (!details.open) return;
      const password = details.querySelector("[data-feature-password]");
      window.setTimeout(() => password?.focus({ preventScroll: true }), 80);
    });
  });

  dialogConfirm?.addEventListener("click", () => {
    const form = pendingForm;
    if (!form) {
      closeDialog();
      return;
    }

    form.dataset.confirmed = "1";
    pendingForm = null;
    dialog?.close();
    form.requestSubmit(form.querySelector("[data-submit-button]"));
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
})();

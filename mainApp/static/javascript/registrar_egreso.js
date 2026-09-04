(function () {
  "use strict";

  const money = new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    maximumFractionDigits: 0,
  });

  document.querySelectorAll("[data-money]").forEach((element) => {
    const raw = String(element.textContent || "").trim().replace(",", ".");
    const value = Number(raw);
    if (Number.isFinite(value)) element.textContent = money.format(value);
  });

  const conceptInput = document.getElementById("id_concepto");
  const normalizeConcept = (value, trim) => {
    let normalized = String(value || "").toLocaleUpperCase("es-CO");
    normalized = normalized.replace(/\s+/g, " ");
    return trim ? normalized.trim() : normalized;
  };

  conceptInput?.addEventListener("input", () => {
    const start = conceptInput.selectionStart;
    const end = conceptInput.selectionEnd;
    conceptInput.value = normalizeConcept(conceptInput.value, false);
    try {
      conceptInput.setSelectionRange(start, end);
    } catch (_error) {
      // Algunos navegadores no permiten restaurar selección en este tipo de input.
    }
  });
  conceptInput?.addEventListener("blur", () => {
    conceptInput.value = normalizeConcept(conceptInput.value, true);
  });

  const form = document.querySelector("[data-expense-form]");
  form?.addEventListener("submit", (event) => {
    if (conceptInput) conceptInput.value = normalizeConcept(conceptInput.value, true);
    if (!form.checkValidity()) return;

    const submit = form.querySelector("button[type='submit']");
    if (!submit || submit.disabled) {
      event.preventDefault();
      return;
    }
    submit.disabled = true;
    const label = submit.querySelector("span");
    if (label) label.textContent = "Registrando…";
  });
})();

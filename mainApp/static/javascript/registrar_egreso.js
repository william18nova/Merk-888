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

  function formatExpenseAmount(value) {
    const raw = String(value || "");
    // No convertir signos o texto inválido en otro importe silenciosamente.
    if (!/^[0-9.]*,?[0-9]*$/.test(raw)) return raw;
    const [whole, cents] = raw.split(",");
    const digits = whole.replace(/\./g, "").replace(/^0+(?=[0-9])/, "");
    const grouped = (digits || (cents !== undefined ? "0" : "")).replace(/\B(?=([0-9]{3})+(?![0-9]))/g, ".");
    return grouped + (cents !== undefined ? `,${cents}` : "");
  }

  function normalizeExpenseAmount(raw) {
    const value = String(raw || "").trim();
    // Valores iniciales de Django y cantidades pegadas con punto decimal.
    if (/^[0-9]+\.[0-9]{1,2}$/.test(value)) return value.replace(".", ",");
    return value;
  }

  const amountInput = document.getElementById("id_monto");
  function updateExpenseAmount() {
    if (!amountInput) return;
    const raw = amountInput.value;
    const start = amountInput.selectionStart ?? raw.length;
    const end = amountInput.selectionEnd ?? start;
    const meaningful = text => text.replace(/\./g, "").length;
    const newValue = formatExpenseAmount(raw);
    const position = oldPosition => {
      if (oldPosition === raw.length) return newValue.length;
      const wanted = meaningful(raw.slice(0, oldPosition));
      let seen = 0;
      let index = 0;
      while (index < newValue.length && seen < wanted) {
        if (newValue[index] !== ".") seen++;
        index++;
      }
      return index;
    };
    amountInput.value = newValue;
    amountInput.setSelectionRange(position(start), position(end));
    const valid = /^(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]{1,2})?$/.test(newValue);
    amountInput.setCustomValidity(newValue && !valid ? "Escribe un valor como 1.000 o 1.000,50." : "");
  }

  if (amountInput) {
    // No reformatear una respuesta inválida del servidor como si fuera otro valor.
    const initial = normalizeExpenseAmount(amountInput.value);
    if (/^(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]{1,2})?$/.test(initial)) {
      amountInput.value = formatExpenseAmount(initial);
    }
    amountInput.addEventListener("input", updateExpenseAmount);
    amountInput.addEventListener("paste", event => {
      const pasted = event.clipboardData?.getData("text");
      if (pasted === undefined) return;
      const normalized = normalizeExpenseAmount(pasted);
      if (!/^[0-9]+(?:\.[0-9]{3})*(?:,[0-9]{1,2})?$/.test(normalized)) return;
      event.preventDefault();
      amountInput.setRangeText(normalized, amountInput.selectionStart, amountInput.selectionEnd, "end");
      updateExpenseAmount();
    });
    amountInput.addEventListener("blur", () => {
      if (amountInput.value.endsWith(",")) {
        amountInput.value = amountInput.value.slice(0, -1);
        updateExpenseAmount();
      }
    });
  }

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

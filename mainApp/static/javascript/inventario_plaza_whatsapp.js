(function () {
  "use strict";

  const page = document.querySelector(".plaza-page");
  if (!page) return;

  const phone = page.dataset.whatsappPhone || "573189092347";
  const preview = document.getElementById("plazaMessagePreview");
  const sendBtn = document.getElementById("plazaSendWhatsapp");
  const copyBtn = document.getElementById("plazaCopy");
  const fillBtn = document.getElementById("plazaFillNoHay");
  const clearBtn = document.getElementById("plazaClear");
  const manualName = document.getElementById("plazaManualName");
  const manualQuantity = document.getElementById("plazaManualQuantity");
  const manualAddBtn = document.getElementById("plazaAddManual");
  const manualItems = document.getElementById("plazaManualItems");
  const manualCount = document.getElementById("plazaManualCount");

  const HEADER = "Hola don william este es el inventario de plaza de yerbabuena:";

  function getInputs() {
    return Array.from(document.querySelectorAll("[data-plaza-input]"));
  }

  function normalizeQuantity(value) {
    const text = String(value || "").trim();
    if (!text) return "No hay";
    if (/^0+([,.]0+)?$/.test(text)) return "No hay";
    if (/^no\s*hay$/i.test(text)) return "No hay";
    return text;
  }

  function productNameForInput(input) {
    const row = input.closest(".plaza-row");
    const manualNameInput = row ? row.querySelector("[data-plaza-manual-name]") : null;
    return String(input.dataset.product || manualNameInput?.value || "").trim();
  }

  function groupedInputs() {
    const groups = [];
    const byName = new Map();

    for (const input of getInputs()) {
      const section = input.dataset.section || "Inventario";
      const product = productNameForInput(input);
      if (!product) continue;
      if (!byName.has(section)) {
        const group = { title: section, rows: [] };
        byName.set(section, group);
        groups.push(group);
      }
      byName.get(section).rows.push({
        product,
        quantity: normalizeQuantity(input.value),
      });
    }

    return groups;
  }

  function buildMessage() {
    const lines = [HEADER, ""];

    for (const group of groupedInputs()) {
      lines.push(`*${group.title}*`);
      for (const row of group.rows) {
        lines.push(`${row.product}: ${row.quantity}`);
      }
      lines.push("");
    }

    return lines.join("\n").trim();
  }

  function updatePreview() {
    if (preview) preview.value = buildMessage();
  }

  function markInputNoHay(input) {
    input.value = "No hay";
    updatePreview();
  }

  function updateManualCount() {
    if (!manualCount || !manualItems) return;
    manualCount.textContent = String(manualItems.querySelectorAll(".plaza-row--manual").length);
  }

  function addManualItem() {
    const name = String(manualName?.value || "").trim();
    const quantity = String(manualQuantity?.value || "").trim();
    if (!name || !manualItems) {
      manualName?.focus();
      return;
    }

    const row = document.createElement("div");
    row.className = "plaza-row plaza-row--manual";
    row.innerHTML = `
      <input class="plaza-manual-name" type="text" data-plaza-manual-name aria-label="Nombre del adicional">
      <input type="text" inputmode="text" placeholder="Cantidad" data-plaza-input data-section="Adicionales">
      <button type="button" class="plaza-nohay" data-plaza-nohay>No hay</button>
      <button type="button" class="plaza-remove" data-plaza-remove aria-label="Eliminar adicional">
        <i class="fa-solid fa-trash"></i>
      </button>
    `;

    const nameInput = row.querySelector("[data-plaza-manual-name]");
    const quantityInput = row.querySelector("[data-plaza-input]");
    nameInput.value = name;
    quantityInput.value = quantity;
    manualItems.appendChild(row);

    manualName.value = "";
    manualQuantity.value = "";
    manualName.focus();
    updateManualCount();
    updatePreview();
  }

  function openWhatsapp() {
    const text = buildMessage();
    const url = `https://wa.me/${phone}?text=${encodeURIComponent(text)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  async function copyMessage() {
    const text = buildMessage();
    try {
      await navigator.clipboard.writeText(text);
      if (copyBtn) {
        const old = copyBtn.innerHTML;
        copyBtn.innerHTML = '<i class="fa-solid fa-check"></i> Copiado';
        setTimeout(() => { copyBtn.innerHTML = old; }, 1300);
      }
    } catch (_) {
      if (preview) {
        preview.focus();
        preview.select();
      }
    }
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-plaza-input], [data-plaza-manual-name]")) {
      updatePreview();
    }
  });

  document.addEventListener("blur", (event) => {
    const input = event.target;
    if (!input.matches("[data-plaza-input]")) return;
    if (/^0+([,.]0+)?$/.test(String(input.value || "").trim())) {
      markInputNoHay(input);
    }
  }, true);

  document.addEventListener("click", (event) => {
    const noHayBtn = event.target.closest("[data-plaza-nohay]");
    const removeBtn = event.target.closest("[data-plaza-remove]");

    if (noHayBtn) {
      const row = noHayBtn.closest(".plaza-row");
      const input = row ? row.querySelector("[data-plaza-input]") : null;
      if (input) {
        markInputNoHay(input);
        input.focus();
      }
      return;
    }

    if (removeBtn) {
      removeBtn.closest(".plaza-row")?.remove();
      updateManualCount();
      updatePreview();
    }
  });

  fillBtn?.addEventListener("click", () => {
    getInputs().forEach((input) => {
      if (!String(input.value || "").trim()) input.value = "No hay";
    });
    updatePreview();
  });

  clearBtn?.addEventListener("click", () => {
    getInputs().forEach((input) => { input.value = ""; });
    if (manualItems) manualItems.innerHTML = "";
    if (manualName) manualName.value = "";
    if (manualQuantity) manualQuantity.value = "";
    updateManualCount();
    updatePreview();
    getInputs()[0]?.focus();
  });

  [manualName, manualQuantity].forEach((input) => {
    input?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addManualItem();
    });
  });

  manualAddBtn?.addEventListener("click", addManualItem);
  copyBtn?.addEventListener("click", copyMessage);
  sendBtn?.addEventListener("click", openWhatsapp);

  updateManualCount();
  updatePreview();
})();

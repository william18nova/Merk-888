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

  const HEADER = "Hola don william, esto es lo que toca traer para plaza de yerbabuena:";

  function getRows() {
    return Array.from(document.querySelectorAll("[data-plaza-row]"));
  }

  function getInputs() {
    return Array.from(document.querySelectorAll("[data-plaza-input]"));
  }

  function rowFor(element) {
    return element ? element.closest("[data-plaza-row]") : null;
  }

  function normalizeCurrentAmount(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (/^0+([,.]0+)?$/.test(text)) return "no hay";
    if (/^no\s*hay$/i.test(text)) return "no hay";
    if (/^traer$/i.test(text)) return "";
    return text;
  }

  function productNameForInput(input) {
    const row = rowFor(input);
    const manualNameInput = row ? row.querySelector("[data-plaza-manual-name]") : null;
    return String(input.dataset.product || manualNameInput?.value || "").trim();
  }

  function setRowStatus(row, status, { focus = false } = {}) {
    if (!row) return;
    const isBring = status === "traer";
    row.dataset.status = isBring ? "traer" : "suficiente";
    row.classList.toggle("is-bring", isBring);
    row.classList.toggle("is-sufficient", !isBring);
    row.querySelector("[data-plaza-traer]")?.classList.toggle("is-active", isBring);
    row.querySelector("[data-plaza-suficiente]")?.classList.toggle("is-active", !isBring);
    if (focus) row.querySelector("[data-plaza-input]")?.focus();
    updatePreview();
  }

  function groupedInputs() {
    const groups = [];
    const byName = new Map();

    for (const row of getRows()) {
      if (row.dataset.status !== "traer") continue;
      const input = row.querySelector("[data-plaza-input]");
      if (!input) continue;

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
        quantity: normalizeCurrentAmount(input.value),
      });
    }

    return groups;
  }

  function countBringItems() {
    return groupedInputs().reduce((total, group) => total + group.rows.length, 0);
  }

  function buildMessage() {
    const groups = groupedInputs();
    const lines = [HEADER, ""];

    if (!groups.length) {
      lines.push("No hay productos marcados para traer.");
      return lines.join("\n").trim();
    }

    for (const group of groups) {
      lines.push(`*${group.title}*`);
      for (const row of group.rows) {
        if (row.quantity === "no hay") {
          lines.push(`${row.product}: no hay`);
        } else {
          lines.push(row.quantity ? `${row.product}: hay ${row.quantity}` : `${row.product}: traer`);
        }
      }
      lines.push("");
    }

    return lines.join("\n").trim();
  }

  function updatePreview() {
    if (preview) preview.value = buildMessage();
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
    row.className = "plaza-row plaza-row--manual is-bring";
    row.dataset.status = "traer";
    row.setAttribute("data-plaza-row", "");
    row.innerHTML = `
      <input class="plaza-manual-name" type="text" data-plaza-manual-name aria-label="Nombre del adicional">
      <input type="text" inputmode="text" placeholder="Hay ahora" data-plaza-input data-section="Adicionales">
      <div class="plaza-status-toggle" role="group" aria-label="Estado del adicional">
        <button type="button" class="plaza-status-btn plaza-status-btn--enough" data-plaza-suficiente>
          Hay suficiente
        </button>
        <button type="button" class="plaza-status-btn plaza-status-btn--bring is-active" data-plaza-traer>
          Traer
        </button>
      </div>
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
    if (countBringItems() <= 0) {
      alert("Marca al menos un producto como Traer antes de enviar.");
      return;
    }
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
    const target = event.target;
    if (!target) return;

    if (target.matches("[data-plaza-input]")) {
      const row = rowFor(target);
      if (String(target.value || "").trim()) {
        setRowStatus(row, "traer");
      } else {
        updatePreview();
      }
      return;
    }

    if (target.matches("[data-plaza-manual-name]")) {
      updatePreview();
    }
  });

  document.addEventListener("blur", (event) => {
    const input = event.target;
    if (!input.matches("[data-plaza-input]")) return;
    const value = String(input.value || "").trim();
    const row = rowFor(input);

    if (/^(hay\s*suficiente|suficiente|ok)$/i.test(value)) {
      input.value = "";
      setRowStatus(row, "suficiente");
    } else if (/^traer$/i.test(value) || /^0+([,.]0+)?$/.test(value)) {
      input.value = "";
      setRowStatus(row, "traer");
    }
  }, true);

  document.addEventListener("click", (event) => {
    const bringBtn = event.target.closest("[data-plaza-traer]");
    const sufficientBtn = event.target.closest("[data-plaza-suficiente]");
    const removeBtn = event.target.closest("[data-plaza-remove]");

    if (bringBtn) {
      setRowStatus(rowFor(bringBtn), "traer", { focus: true });
      return;
    }

    if (sufficientBtn) {
      setRowStatus(rowFor(sufficientBtn), "suficiente");
      return;
    }

    if (removeBtn) {
      removeBtn.closest("[data-plaza-row]")?.remove();
      updateManualCount();
      updatePreview();
    }
  });

  fillBtn?.addEventListener("click", () => {
    getRows().forEach((row) => {
      const input = row.querySelector("[data-plaza-input]");
      if (!String(input?.value || "").trim()) setRowStatus(row, "suficiente");
    });
    updatePreview();
  });

  clearBtn?.addEventListener("click", () => {
    getInputs().forEach((input) => { input.value = ""; });
    getRows().forEach((row) => setRowStatus(row, "suficiente"));
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

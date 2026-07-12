/*
 * Alta de inventario
 * - Sin dependencia de jQuery/DataTables: el formulario sigue funcionando
 *   aunque un CDN externo no esté disponible.
 * - Autocompletes accesibles con teclado, caché, abort, paginación y reintento.
 * - Validación completa del lote y protección contra doble envío.
 */
(() => {
  "use strict";

  const config = window.inventoryCreateConfig || {};
  const byId = (id) => document.getElementById(id);
  const form = byId("inventarioForm");
  if (!form) return;

  const dom = {
    form,
    branchInput: byId("id_sucursal_autocomplete"),
    branchHidden: byId("id_sucursal"),
    branchResults: byId("sucursal-autocomplete-results"),
    branchFeedback: byId("sucursal-selection-feedback"),
    productInput: byId("id_producto_autocomplete"),
    productHidden: byId("id_productoid"),
    productResults: byId("producto-autocomplete-results"),
    productFeedback: byId("producto-selection-feedback"),
    quantityInput: byId("id_cantidad"),
    addButton: byId("agregarProductoBtn"),
    resetButton: byId("resetInventoryBtn"),
    saveButton: byId("saveInventoryBtn"),
    saveLabel: document.querySelector("#saveInventoryBtn .button-label"),
    itemsBody: byId("productos-body"),
    itemsEmpty: byId("items-empty"),
    itemsTableWrap: byId("items-table-wrap"),
    itemsToolbar: byId("items-toolbar"),
    itemsFilter: byId("items-filter"),
    itemsCountBadge: byId("items-count-badge"),
    summaryProducts: byId("summary-products"),
    summaryUnits: byId("summary-units"),
    payloadInput: byId("id_inventarios_temp"),
    errorAlert: byId("error-message"),
    successAlert: byId("success-message"),
    infoAlert: byId("info-message"),
    announcements: byId("inventory-announcements"),
    branchPanel: document.querySelector(".inventory-panel--branch"),
    productsPanel: document.querySelector(".inventory-panel--products"),
    reviewPanel: document.querySelector(".inventory-panel--review"),
    progressSteps: Array.from(document.querySelectorAll("[data-progress-step]")),
  };

  if (
    !dom.branchInput || !dom.branchHidden || !dom.branchResults ||
    !dom.productInput || !dom.productHidden || !dom.productResults ||
    !dom.quantityInput || !dom.addButton || !dom.itemsBody
  ) {
    console.error("No se pudo iniciar la página de inventario: faltan controles requeridos.");
    return;
  }

  const state = {
    items: [],
    branch: null,
    saving: false,
    noticeTimer: null,
  };

  const normalize = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();

  // Codificación delta/base36: mantiene corta la URL incluso con cientos de
  // productos ya agregados (y el backend conserva soporte para el formato
  // CSV anterior).
  const compactProductIds = (items) => {
    const ids = [...new Set(items
      .map((item) => Number(item.productId))
      .filter((id) => Number.isInteger(id) && id > 0 && id <= 2147483647))]
      .sort((a, b) => a - b);
    let previous = 0;
    return ids.map((id) => {
      const delta = id - previous;
      previous = id;
      return delta.toString(36);
    }).join(".");
  };

  const plural = (count, singular, pluralWord = `${singular}s`) =>
    `${count} ${count === 1 ? singular : pluralWord}`;

  const createIcon = (...classes) => {
    const icon = document.createElement("i");
    icon.classList.add(...classes);
    icon.setAttribute("aria-hidden", "true");
    return icon;
  };

  const announce = (message) => {
    if (!dom.announcements) return;
    dom.announcements.textContent = "";
    window.setTimeout(() => { dom.announcements.textContent = message; }, 20);
  };

  const UI = {
    alertIcon: {
      error: ["fa-solid", "fa-circle-exclamation"],
      success: ["fa-solid", "fa-circle-check"],
      info: ["fa-solid", "fa-circle-info"],
    },

    box(type) {
      return type === "error"
        ? dom.errorAlert
        : type === "success"
          ? dom.successAlert
          : dom.infoAlert;
    },

    hide(type) {
      const box = this.box(type);
      if (!box) return;
      box.hidden = true;
      box.replaceChildren();
      if (type === "info" && state.noticeTimer) {
        window.clearTimeout(state.noticeTimer);
        state.noticeTimer = null;
      }
    },

    clearAlerts() {
      this.hide("error");
      this.hide("success");
      this.hide("info");
    },

    show(type, message, action = null) {
      const box = this.box(type);
      if (!box) return;

      if (type !== "info") this.hide("info");
      box.replaceChildren();
      box.appendChild(createIcon(...this.alertIcon[type]));

      const text = document.createElement("span");
      text.textContent = message;
      box.appendChild(text);

      if (action && action.label && typeof action.callback === "function") {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "alert-action";
        button.textContent = action.label;
        button.addEventListener("click", () => {
          action.callback();
          this.hide(type);
        }, { once: true });
        box.appendChild(button);
      }

      box.hidden = false;
      announce(message);

      if (type === "error") {
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } else if (type === "info") {
        if (state.noticeTimer) window.clearTimeout(state.noticeTimer);
        state.noticeTimer = window.setTimeout(() => this.hide("info"), 7000);
      }
    },

    clearFieldErrors() {
      document.querySelectorAll(".inventory-create-page .field-error").forEach((box) => {
        box.classList.remove("visible");
        box.replaceChildren();
      });
      document.querySelectorAll(".inventory-create-page .inventory-field .input-error").forEach((input) => {
        input.classList.remove("input-error");
        input.removeAttribute("aria-invalid");
      });
    },

    clearFieldError(field) {
      const aliases = {
        sucursal_autocomplete: "sucursal",
        producto_autocomplete: "productoid",
      };
      const key = aliases[field] || field;
      const box = byId(`error-id_${key}`);
      if (box) {
        box.classList.remove("visible");
        box.replaceChildren();
      }
      const input = {
        sucursal: dom.branchInput,
        productoid: dom.productInput,
        cantidad: dom.quantityInput,
      }[key];
      if (input) {
        input.classList.remove("input-error");
        input.removeAttribute("aria-invalid");
      }
    },

    fieldError(field, messages) {
      const aliases = {
        sucursal_autocomplete: "sucursal",
        producto_autocomplete: "productoid",
      };
      const key = aliases[field] || field;
      const list = Array.isArray(messages) ? messages : [messages];
      const cleanMessages = list.filter(Boolean).map(String);
      const box = byId(`error-id_${key}`);
      const input = {
        sucursal: dom.branchInput,
        productoid: dom.productInput,
        cantidad: dom.quantityInput,
      }[key];

      if (box && cleanMessages.length) {
        box.replaceChildren();
        box.appendChild(createIcon("fa-solid", "fa-circle-exclamation"));
        const text = document.createElement("span");
        text.textContent = cleanMessages.join(" ");
        box.appendChild(text);
        box.classList.add("visible");
      }
      if (input) {
        input.classList.add("input-error");
        input.setAttribute("aria-invalid", "true");
        if (box) input.setAttribute("aria-describedby", box.id);
      }
    },

    serverErrors(rawErrors) {
      let errors = rawErrors;
      if (typeof errors === "string") {
        try { errors = JSON.parse(errors); } catch { errors = {}; }
      }
      if (!errors || typeof errors !== "object") {
        this.show("error", "No se pudo guardar el inventario. Inténtalo de nuevo.");
        return;
      }

      const globalMessages = [];
      Object.entries(errors).forEach(([field, entries]) => {
        const messages = (Array.isArray(entries) ? entries : [entries])
          .map((entry) => typeof entry === "string" ? entry : entry?.message)
          .filter(Boolean);

        if (["sucursal", "sucursal_autocomplete", "productoid", "producto_autocomplete", "cantidad", "inventarios_temp"].includes(field)) {
          this.fieldError(field, messages);
        } else {
          globalMessages.push(...messages);
        }
      });

      const payloadError = byId("error-id_inventarios_temp");
      if (payloadError?.classList.contains("visible")) {
        globalMessages.push(payloadError.textContent.trim());
      }
      if (globalMessages.length) this.show("error", globalMessages.join(" "));
    },
  };

  class Autocomplete {
    constructor(options) {
      this.input = options.input;
      this.hidden = options.hidden;
      this.box = options.box;
      this.url = options.url;
      this.kind = options.kind;
      this.getParams = options.getParams || (() => ({}));
      this.canSearch = options.canSearch || (() => true);
      this.onSelect = options.onSelect || (() => {});
      this.beforeSelect = options.beforeSelect || (() => true);
      this.onInvalidate = options.onInvalidate || (() => {});
      this.onClear = options.onClear || (() => {});
      this.beforeClear = options.beforeClear || (() => true);
      this.feedback = options.feedback || null;
      this.clearButton = document.querySelector(`[data-clear-autocomplete="${this.kind}"]`);
      this.control = this.input.closest(".inventory-control");
      this.cache = new Map();
      this.cacheTtl = 60000;
      this.controller = null;
      this.requestNumber = 0;
      this.timer = null;
      this.rows = [];
      this.activeIndex = -1;
      this.page = 1;
      this.hasMore = false;
      this.loading = false;
      this.selection = null;
      this.pointerInside = false;
      this.disabled = false;

      this.prepareAccessibility();
      this.bind();
      this.syncClearButton();
    }

    prepareAccessibility() {
      this.input.setAttribute("role", "combobox");
      this.input.setAttribute("aria-autocomplete", "list");
      this.input.setAttribute("aria-haspopup", "listbox");
      this.input.setAttribute("aria-controls", this.box.id);
      this.input.setAttribute("aria-expanded", "false");
      this.input.setAttribute("aria-required", "true");
      this.input.setAttribute("spellcheck", "false");
    }

    bind() {
      this.input.addEventListener("focus", () => {
        if (this.disabled) return;
        this.search(1, false);
      });

      this.input.addEventListener("input", () => {
        if (this.hidden.value || this.selection) {
          this.hidden.value = "";
          this.selection = null;
          this.input.classList.remove("has-selection");
          if (this.feedback) this.feedback.hidden = true;
          this.onInvalidate();
        }
        UI.clearFieldError(this.kind === "sucursal" ? "sucursal" : "productoid");
        this.syncClearButton();
        window.clearTimeout(this.timer);
        this.timer = window.setTimeout(() => this.search(1, false), 150);
      });

      this.input.addEventListener("keydown", (event) => this.onKeydown(event));
      this.input.addEventListener("blur", () => {
        window.setTimeout(() => {
          if (!this.pointerInside && !this.box.contains(document.activeElement)) this.close();
        }, 120);
      });

      this.box.addEventListener("pointerenter", () => { this.pointerInside = true; });
      this.box.addEventListener("pointerleave", () => { this.pointerInside = false; });
      this.box.addEventListener("focusout", () => {
        window.setTimeout(() => {
          if (document.activeElement !== this.input && !this.box.contains(document.activeElement)) {
            this.close();
          }
        }, 0);
      });
      this.box.addEventListener("mousedown", (event) => event.preventDefault());
      this.box.addEventListener("click", (event) => {
        const option = event.target.closest("[data-option-index]");
        if (option) {
          this.select(Number(option.dataset.optionIndex));
          return;
        }
        if (event.target.closest(".autocomplete-retry")) this.search(1, false, true);
      });
      this.box.addEventListener("scroll", () => {
        const nearBottom = this.box.scrollTop + this.box.clientHeight >= this.box.scrollHeight - 32;
        if (nearBottom && this.hasMore && !this.loading) this.search(this.page + 1, true);
      });

      this.clearButton?.addEventListener("click", () => this.clear({ focus: true }));
      document.addEventListener("pointerdown", (event) => {
        if (!this.input.contains(event.target) && !this.box.contains(event.target)) this.close();
      });
    }

    setDisabled(disabled) {
      this.disabled = Boolean(disabled);
      this.input.disabled = this.disabled;
      this.input.setAttribute("aria-disabled", String(this.disabled));
      if (this.disabled) {
        this.controller?.abort();
        this.controller = null;
        this.requestNumber += 1;
        this.setLoading(false);
        this.close();
      }
      this.syncClearButton();
    }

    syncClearButton() {
      if (!this.clearButton) return;
      this.clearButton.hidden = this.disabled || !this.input.value;
    }

    setLoading(loading) {
      this.loading = loading;
      this.control?.classList.toggle("is-loading", loading);
      this.input.setAttribute("aria-busy", String(loading));
    }

    buildRequest(page) {
      const params = new URLSearchParams({
        term: this.input.value.trim(),
        page: String(page),
      });
      Object.entries(this.getParams() || {}).forEach(([key, value]) => {
        if (value !== undefined && value !== null && String(value) !== "") {
          params.set(key, String(value));
        }
      });
      return `${this.url}?${params.toString()}`;
    }

    readCache(key) {
      const cached = this.cache.get(key);
      if (!cached) return null;
      if (Date.now() - cached.time > this.cacheTtl) {
        this.cache.delete(key);
        return null;
      }
      return cached.data;
    }

    invalidateCache() {
      this.controller?.abort();
      this.controller = null;
      this.requestNumber += 1;
      this.setLoading(false);
      this.cache.clear();
      this.rows = [];
      this.hasMore = false;
      this.page = 1;
      this.close();
    }

    async search(page = 1, append = false, force = false) {
      if (this.disabled || !this.canSearch()) {
        this.renderState(
          this.kind === "producto"
            ? "Selecciona primero una sucursal."
            : "No hay opciones disponibles.",
          "fa-solid fa-circle-info"
        );
        return;
      }
      if (!this.url) {
        this.renderError("El buscador no está configurado.");
        return;
      }
      if (append && (this.loading || !this.hasMore)) return;

      if (!append) {
        this.controller?.abort();
        this.controller = null;
        this.setLoading(false);
        this.rows = [];
        this.activeIndex = -1;
      }

      // Cada búsqueda, incluso una resuelta desde caché, invalida cualquier
      // respuesta anterior para impedir que un resultado obsoleto reaparezca.
      const requestNumber = ++this.requestNumber;
      const requestUrl = this.buildRequest(page);
      const cached = !force ? this.readCache(requestUrl) : null;
      if (cached) {
        this.applyResults(cached, page, append);
        return;
      }

      if (!append) {
        this.controller = typeof AbortController === "function" ? new AbortController() : null;
        this.renderState("Buscando…", "fa-solid fa-magnifying-glass");
      }

      this.setLoading(true);

      try {
        const response = await fetch(requestUrl, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          signal: this.controller?.signal,
        });

        const contentType = response.headers.get("content-type") || "";
        if (response.redirected || !contentType.includes("application/json")) {
          throw new Error("Tu sesión expiró o no tienes permiso para usar este buscador.");
        }
        if (!response.ok) {
          throw new Error(
            response.status === 401
              ? "Tu sesión expiró. Recarga la página e inicia sesión nuevamente."
              : response.status === 403
                ? "No tienes permiso para consultar estas opciones."
                : "No se pudieron cargar las opciones."
          );
        }

        const data = await response.json();
        if (requestNumber !== this.requestNumber) return;
        this.cache.set(requestUrl, { time: Date.now(), data });
        this.applyResults(data, page, append);
      } catch (error) {
        if (error?.name === "AbortError") return;
        if (requestNumber !== this.requestNumber) return;
        this.renderError(error?.message || "No se pudieron cargar las opciones.");
      } finally {
        if (requestNumber === this.requestNumber) this.setLoading(false);
      }
    }

    applyResults(data, page, append) {
      const incoming = Array.isArray(data?.results) ? data.results : [];
      if (append) {
        const ids = new Set(this.rows.map((row) => String(row.id)));
        incoming.forEach((row) => {
          if (!ids.has(String(row.id))) {
            this.rows.push(row);
            ids.add(String(row.id));
          }
        });
      } else {
        this.rows = incoming;
      }

      this.page = page;
      this.hasMore = Boolean(data?.has_more ?? data?.pagination?.more);
      this.activeIndex = this.rows.length ? 0 : -1;
      this.renderOptions();
    }

    renderState(message, iconClasses) {
      this.box.replaceChildren();
      const row = document.createElement("div");
      row.className = "autocomplete-state";
      const [style, icon] = iconClasses.split(" ");
      row.appendChild(createIcon(style, icon));
      const text = document.createElement("span");
      text.textContent = message;
      row.appendChild(text);
      this.box.appendChild(row);
      this.open();
    }

    renderError(message) {
      this.box.replaceChildren();
      const row = document.createElement("div");
      row.className = "autocomplete-state autocomplete-state--error";
      row.appendChild(createIcon("fa-solid", "fa-triangle-exclamation"));
      const text = document.createElement("span");
      text.textContent = message;
      row.appendChild(text);
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "autocomplete-retry";
      retry.textContent = "Reintentar";
      row.appendChild(retry);
      this.box.appendChild(row);
      this.open();
    }

    optionMeta(row) {
      if (this.kind === "sucursal") {
        const meta = [];
        if (row.address) meta.push(row.address);
        const count = Number(row.inventory_count || 0);
        meta.push(`${plural(count, "producto")} registrado${count === 1 ? "" : "s"}`);
        return meta;
      }

      const meta = [`#${row.id}`];
      if (row.category) meta.push(row.category);
      if (row.barcode) meta.push(`Barras: ${row.barcode}`);
      return meta;
    }

    renderOptions() {
      this.box.replaceChildren();
      if (!this.rows.length) {
        const message = this.kind === "producto"
          ? (this.input.value.trim()
              ? "No encontramos productos disponibles con esa búsqueda."
              : "Esta sucursal ya tiene todos los productos disponibles.")
          : "No encontramos sucursales con esa búsqueda.";
        this.renderState(message, "fa-regular fa-face-meh");
        return;
      }

      const fragment = document.createDocumentFragment();
      this.rows.forEach((row, index) => {
        const option = document.createElement("div");
        option.id = `${this.input.id}-option-${index}`;
        option.className = `autocomplete-option${index === this.activeIndex ? " is-active" : ""}`;
        option.dataset.optionIndex = String(index);
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === this.activeIndex));

        const optionIcon = document.createElement("span");
        optionIcon.className = "autocomplete-option__icon";
        optionIcon.appendChild(createIcon(
          "fa-solid",
          this.kind === "sucursal" ? "fa-store" : "fa-box"
        ));
        option.appendChild(optionIcon);

        const copy = document.createElement("span");
        copy.className = "autocomplete-option__copy";
        const title = document.createElement("strong");
        title.className = "autocomplete-option__title";
        title.textContent = row.text || `Opción ${row.id}`;
        copy.appendChild(title);

        const meta = this.optionMeta(row);
        if (meta.length) {
          const metaBox = document.createElement("span");
          metaBox.className = "autocomplete-option__meta";
          meta.forEach((value) => {
            const bit = document.createElement("span");
            bit.textContent = value;
            metaBox.appendChild(bit);
          });
          copy.appendChild(metaBox);
        }
        option.appendChild(copy);

        const hint = document.createElement("span");
        hint.className = "autocomplete-option__hint";
        hint.textContent = "Enter ↵";
        option.appendChild(hint);
        fragment.appendChild(option);
      });

      if (this.hasMore) {
        const more = document.createElement("div");
        more.className = "autocomplete-load-more";
        more.textContent = "Desplázate para ver más resultados";
        fragment.appendChild(more);
      }

      this.box.appendChild(fragment);
      this.open();
      this.syncActiveDescendant();
    }

    open() {
      if (document.activeElement !== this.input && !this.pointerInside) return;
      this.box.hidden = false;
      this.input.setAttribute("aria-expanded", "true");
    }

    close() {
      this.box.hidden = true;
      this.input.setAttribute("aria-expanded", "false");
      this.input.removeAttribute("aria-activedescendant");
    }

    setActive(index) {
      if (!this.rows.length) return;
      this.activeIndex = (index + this.rows.length) % this.rows.length;
      this.box.querySelectorAll("[data-option-index]").forEach((option, optionIndex) => {
        const active = optionIndex === this.activeIndex;
        option.classList.toggle("is-active", active);
        option.setAttribute("aria-selected", String(active));
        if (active) option.scrollIntoView({ block: "nearest" });
      });
      this.syncActiveDescendant();
    }

    syncActiveDescendant() {
      if (this.activeIndex < 0) {
        this.input.removeAttribute("aria-activedescendant");
        return;
      }
      this.input.setAttribute("aria-activedescendant", `${this.input.id}-option-${this.activeIndex}`);
    }

    onKeydown(event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (this.box.hidden) this.search(1, false);
        else this.setActive(this.activeIndex + 1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (this.box.hidden) this.search(1, false);
        else this.setActive(this.activeIndex - 1);
        return;
      }
      if (event.key === "Home" && !this.box.hidden && this.rows.length) {
        event.preventDefault();
        this.setActive(0);
        return;
      }
      if (event.key === "End" && !this.box.hidden && this.rows.length) {
        event.preventDefault();
        this.setActive(this.rows.length - 1);
        return;
      }
      if (event.key === "Enter" && !this.box.hidden) {
        event.preventDefault();
        if (this.rows.length) {
          this.select(this.activeIndex >= 0 ? this.activeIndex : 0);
        } else if (this.box.querySelector(".autocomplete-retry")) {
          this.search(1, false, true);
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        this.close();
        return;
      }
      // Tab puede mover el foco al botón "Reintentar" del estado de error.
    }

    select(index) {
      if (this.disabled) return;
      const row = this.rows[index];
      if (!row || !this.beforeSelect(row)) return;

      this.selection = row;
      this.input.value = row.text || "";
      this.hidden.value = String(row.id);
      this.input.classList.add("has-selection");
      this.syncClearButton();
      this.close();
      this.showFeedback(row);
      this.onSelect(row);
      UI.clearFieldError(this.kind === "sucursal" ? "sucursal" : "productoid");
      announce(`${row.text} seleccionado.`);
    }

    showFeedback(row) {
      if (!this.feedback) return;
      this.feedback.replaceChildren();
      this.feedback.appendChild(createIcon("fa-solid", "fa-circle-check"));
      const text = document.createElement("span");
      const meta = this.optionMeta(row);
      text.textContent = meta.length ? `${row.text} · ${meta.join(" · ")}` : row.text;
      this.feedback.appendChild(text);
      this.feedback.hidden = false;
    }

    clear({ focus = false, silent = false } = {}) {
      if (!silent && !this.beforeClear()) return false;
      this.controller?.abort();
      this.controller = null;
      this.requestNumber += 1;
      this.setLoading(false);
      this.selection = null;
      this.input.value = "";
      this.hidden.value = "";
      this.input.classList.remove("has-selection", "input-error");
      this.input.removeAttribute("aria-invalid");
      if (this.feedback) this.feedback.hidden = true;
      this.rows = [];
      this.activeIndex = -1;
      this.close();
      this.syncClearButton();
      if (!silent) this.onClear();
      if (focus && !this.disabled) this.input.focus();
      return true;
    }
  }

  function clearItems({ resetFilter = true } = {}) {
    UI.hide("info");
    state.items = [];
    if (resetFilter && dom.itemsFilter) dom.itemsFilter.value = "";
    renderItems();
  }

  let branchAutocomplete;
  let productAutocomplete;

  function restoreBranchSelection() {
    if (!state.branch || !branchAutocomplete) return;
    branchAutocomplete.selection = state.branch;
    dom.branchInput.value = state.branch.text || "";
    dom.branchHidden.value = String(state.branch.id);
    dom.branchInput.classList.add("has-selection");
    branchAutocomplete.syncClearButton();
    branchAutocomplete.showFeedback(state.branch);
    branchAutocomplete.close();
    setComposerState();
    updatePageState();
    announce(`${state.branch.text} continúa seleccionada.`);
  }

  branchAutocomplete = new Autocomplete({
    kind: "sucursal",
    input: dom.branchInput,
    hidden: dom.branchHidden,
    box: dom.branchResults,
    feedback: dom.branchFeedback,
    url: config.sucursalUrl,
    beforeSelect: (branch) => {
      if (
        state.branch && String(state.branch.id) !== String(branch.id) &&
        state.items.length &&
        !window.confirm("Cambiar de sucursal eliminará los productos de la lista actual. ¿Deseas continuar?")
      ) {
        restoreBranchSelection();
        return false;
      }
      if (state.branch && String(state.branch.id) !== String(branch.id)) clearItems();
      return true;
    },
    onSelect: (branch) => {
      state.branch = branch;
      productAutocomplete.clear({ silent: true });
      productAutocomplete.invalidateCache();
      setComposerState();
      updatePageState();
      window.setTimeout(() => dom.productInput.focus(), 0);
    },
    onInvalidate: () => {
      productAutocomplete.clear({ silent: true });
      setComposerState();
      updatePageState();
    },
    beforeClear: () => !state.items.length || window.confirm(
      "Quitar la sucursal eliminará los productos de la lista actual. ¿Deseas continuar?"
    ),
    onClear: () => {
      state.branch = null;
      clearItems();
      productAutocomplete.clear({ silent: true });
      productAutocomplete.invalidateCache();
      setComposerState();
      updatePageState();
    },
  });

  productAutocomplete = new Autocomplete({
    kind: "producto",
    input: dom.productInput,
    hidden: dom.productHidden,
    box: dom.productResults,
    feedback: dom.productFeedback,
    url: config.productoUrl,
    canSearch: () => Boolean(dom.branchHidden.value),
    getParams: () => ({
      sucursal_id: dom.branchHidden.value,
      excluded_compact: compactProductIds(state.items),
    }),
    onSelect: () => {
      updatePageState();
      window.setTimeout(() => {
        dom.quantityInput.focus();
        dom.quantityInput.select?.();
      }, 0);
    },
    onInvalidate: updatePageState,
    onClear: updatePageState,
  });

  function positiveInteger(value) {
    const text = String(value ?? "").trim();
    if (!/^\d+$/.test(text)) return null;
    const number = Number(text);
    return Number.isSafeInteger(number) && number >= 1 && number <= 2147483647
      ? number
      : null;
  }

  function itemMeta(item) {
    return [item.category, item.barcode ? `Barras: ${item.barcode}` : ""]
      .filter(Boolean)
      .join(" · ");
  }

  function renderItems() {
    if (state.items.length < 5 && dom.itemsFilter?.value) {
      dom.itemsFilter.value = "";
    }
    const query = normalize(dom.itemsFilter?.value);
    const visibleItems = query
      ? state.items.filter((item) => normalize(`${item.productName} ${item.productId} ${item.barcode} ${item.category}`).includes(query))
      : state.items;

    dom.itemsBody.replaceChildren();
    const fragment = document.createDocumentFragment();

    visibleItems.forEach((item) => {
      const row = document.createElement("tr");
      row.dataset.productId = item.productId;

      const productCell = document.createElement("td");
      productCell.dataset.label = "Producto";
      const productBox = document.createElement("div");
      productBox.className = "inventory-product-cell";
      const iconBox = document.createElement("span");
      iconBox.className = "inventory-product-cell__icon";
      iconBox.appendChild(createIcon("fa-solid", "fa-box"));
      productBox.appendChild(iconBox);

      const copy = document.createElement("div");
      copy.className = "inventory-product-cell__copy";
      const name = document.createElement("strong");
      name.textContent = item.productName;
      copy.appendChild(name);
      const meta = document.createElement("small");
      meta.textContent = `#${item.productId}${itemMeta(item) ? ` · ${itemMeta(item)}` : ""}`;
      copy.appendChild(meta);
      productBox.appendChild(copy);
      productCell.appendChild(productBox);
      row.appendChild(productCell);

      const quantityCell = document.createElement("td");
      quantityCell.dataset.label = "Cantidad";
      const quantity = document.createElement("input");
      quantity.type = "number";
      quantity.className = "inventory-row-quantity";
      quantity.min = "1";
      quantity.max = "2147483647";
      quantity.step = "1";
      quantity.inputMode = "numeric";
      quantity.value = String(item.quantity);
      quantity.dataset.productId = item.productId;
      quantity.setAttribute("aria-label", `Cantidad de ${item.productName}`);
      if (positiveInteger(item.quantity) === null) {
        quantity.classList.add("input-error");
        quantity.setAttribute("aria-invalid", "true");
      }
      quantityCell.appendChild(quantity);
      row.appendChild(quantityCell);

      const actionCell = document.createElement("td");
      actionCell.dataset.label = "Acciones";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "inventory-remove-button";
      remove.dataset.removeProduct = item.productId;
      remove.setAttribute("aria-label", `Quitar ${item.productName}`);
      remove.title = "Quitar producto";
      remove.appendChild(createIcon("fa-solid", "fa-trash-can"));
      actionCell.appendChild(remove);
      row.appendChild(actionCell);

      fragment.appendChild(row);
    });

    if (state.items.length && !visibleItems.length) {
      const row = document.createElement("tr");
      row.className = "inventory-no-match";
      const cell = document.createElement("td");
      cell.colSpan = 3;
      cell.textContent = "Ningún producto agregado coincide con el filtro.";
      row.appendChild(cell);
      fragment.appendChild(row);
    } else if (!state.items.length) {
      const row = document.createElement("tr");
      row.className = "inventory-empty-row";
      const cell = document.createElement("td");
      cell.colSpan = 3;
      cell.textContent = "No hay productos para mostrar";
      row.appendChild(cell);
      fragment.appendChild(row);
    }

    dom.itemsBody.appendChild(fragment);
    const hasItems = state.items.length > 0;
    if (dom.itemsEmpty) dom.itemsEmpty.hidden = true;
    if (dom.itemsTableWrap) dom.itemsTableWrap.hidden = false;
    if (dom.itemsToolbar) dom.itemsToolbar.hidden = state.items.length < 5;

    const totalUnits = state.items.reduce((total, item) => total + (positiveInteger(item.quantity) || 0), 0);
    if (dom.itemsCountBadge) dom.itemsCountBadge.textContent = plural(state.items.length, "producto");
    if (dom.summaryProducts) dom.summaryProducts.textContent = String(state.items.length);
    if (dom.summaryUnits) {
      dom.summaryUnits.textContent = new Intl.NumberFormat("es-CO").format(totalUnits);
    }
    if (dom.saveLabel) {
      dom.saveLabel.textContent = state.items.length
        ? `Guardar ${plural(state.items.length, "producto")}`
        : "Guardar inventario";
    }

    productAutocomplete?.invalidateCache();
    updatePageState();
  }

  function setComposerState() {
    const branchSelected = Boolean(dom.branchHidden.value);
    const noProducts = Number(config.productCount || 0) < 1;
    const disabled = state.saving || !branchSelected || noProducts;
    productAutocomplete.setDisabled(disabled);
    dom.quantityInput.disabled = disabled;
    document.querySelectorAll("[data-quantity-action]").forEach((button) => {
      button.disabled = disabled;
    });
    dom.addButton.disabled = disabled;
  }

  function updatePageState() {
    const branchSelected = Boolean(dom.branchHidden.value);
    const hasItems = state.items.length > 0;
    const validItems = hasItems && state.items.every((item) => positiveInteger(item.quantity) !== null);

    dom.branchPanel?.classList.toggle("is-complete", branchSelected);
    dom.productsPanel?.classList.toggle("is-complete", hasItems);
    dom.reviewPanel?.classList.toggle("is-complete", false);
    dom.branchPanel?.querySelector(".inventory-complete-mark")
      ?.setAttribute("aria-hidden", String(!branchSelected));
    dom.productsPanel?.querySelector(".inventory-complete-mark")
      ?.setAttribute("aria-hidden", String(!hasItems));

    dom.progressSteps.forEach((step) => {
      const name = step.dataset.progressStep;
      let complete = false;
      let current = false;
      if (name === "branch") {
        complete = branchSelected;
        current = !branchSelected;
      } else if (name === "products") {
        complete = hasItems;
        current = branchSelected && !hasItems;
      } else if (name === "review") {
        current = hasItems;
      }
      step.classList.toggle("is-complete", complete);
      step.classList.toggle("is-current", current);
      if (current) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
    });

    if (dom.saveButton) {
      dom.saveButton.disabled = state.saving || !branchSelected || !validItems;
    }
  }

  function addCurrentProduct() {
    UI.hide("error");
    UI.hide("success");
    UI.hide("info");
    UI.clearFieldErrors();

    let valid = true;
    if (!dom.branchHidden.value) {
      UI.fieldError("sucursal", "Selecciona una sucursal de la lista.");
      valid = false;
    }
    if (!dom.productHidden.value || !productAutocomplete.selection) {
      UI.fieldError("productoid", "Selecciona un producto de la lista.");
      valid = false;
    }

    const quantity = positiveInteger(dom.quantityInput.value);
    if (quantity === null) {
      UI.fieldError("cantidad", "Ingresa un número entero mayor que cero.");
      valid = false;
    }
    if (!valid) {
      const firstInvalid = form.querySelector("[aria-invalid='true']");
      firstInvalid?.focus();
      return false;
    }

    const product = productAutocomplete.selection;
    const productId = String(product.id);
    const existing = state.items.find((item) => item.productId === productId);
    if (existing) {
      existing.quantity = (positiveInteger(existing.quantity) || 0) + quantity;
      UI.show("info", `Actualizamos la cantidad de ${existing.productName}.`);
    } else {
      state.items.push({
        productId,
        productName: product.text,
        quantity,
        barcode: product.barcode || "",
        category: product.category || "",
      });
      announce(`${product.text} agregado con cantidad ${quantity}.`);
    }

    productAutocomplete.clear({ silent: true });
    dom.quantityInput.value = "1";
    renderItems();
    window.setTimeout(() => dom.productInput.focus(), 0);
    return true;
  }

  dom.addButton.addEventListener("click", addCurrentProduct);
  dom.quantityInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addCurrentProduct();
    }
  });
  dom.quantityInput.addEventListener("input", () => UI.clearFieldError("cantidad"));

  document.querySelectorAll("[data-quantity-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const delta = button.dataset.quantityAction === "increase" ? 1 : -1;
      const current = positiveInteger(dom.quantityInput.value) || 1;
      dom.quantityInput.value = String(Math.max(1, Math.min(2147483647, current + delta)));
      UI.clearFieldError("cantidad");
    });
  });

  dom.itemsBody.addEventListener("input", (event) => {
    const input = event.target.closest(".inventory-row-quantity");
    if (!input) return;
    const item = state.items.find((row) => row.productId === input.dataset.productId);
    if (!item) return;
    item.quantity = input.value;
    const valid = positiveInteger(input.value) !== null;
    input.classList.toggle("input-error", !valid);
    input.setAttribute("aria-invalid", String(!valid));
    const totalUnits = state.items.reduce((total, row) => total + (positiveInteger(row.quantity) || 0), 0);
    if (dom.summaryUnits) {
      dom.summaryUnits.textContent = new Intl.NumberFormat("es-CO").format(totalUnits);
    }
    updatePageState();
  });

  dom.itemsBody.addEventListener("keydown", (event) => {
    if (event.target.matches(".inventory-row-quantity") && event.key === "Enter") {
      event.preventDefault();
      event.target.blur();
    }
  });

  dom.itemsBody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-product]");
    if (!button) return;
    const index = state.items.findIndex((item) => item.productId === button.dataset.removeProduct);
    if (index < 0) return;

    const [removed] = state.items.splice(index, 1);
    renderItems();
    UI.show("info", `${removed.productName} se quitó de la lista.`, {
      label: "Deshacer",
      callback: () => {
        const existing = state.items.find((item) => item.productId === removed.productId);
        if (existing) {
          existing.quantity = (positiveInteger(existing.quantity) || 0)
            + (positiveInteger(removed.quantity) || 0);
        } else {
          state.items.splice(Math.min(index, state.items.length), 0, removed);
        }
        renderItems();
        announce(`${removed.productName} volvió a la lista.`);
      },
    });
  });

  dom.itemsFilter?.addEventListener("input", renderItems);

  dom.resetButton?.addEventListener("click", () => {
    const dirty = state.items.length || dom.branchHidden.value;
    if (dirty && !window.confirm("¿Limpiar la sucursal y todos los productos agregados?")) return;

    UI.clearAlerts();
    UI.clearFieldErrors();
    state.branch = null;
    clearItems();
    branchAutocomplete.clear({ silent: true });
    productAutocomplete.clear({ silent: true });
    productAutocomplete.invalidateCache();
    dom.quantityInput.value = "1";
    dom.payloadInput.value = "";
    setComposerState();
    updatePageState();
    if (!branchAutocomplete.disabled) dom.branchInput.focus();
  });

  function csrfToken() {
    return form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  }

  function setSaving(saving) {
    state.saving = saving;
    dom.saveButton?.classList.toggle("is-loading", saving);
    dom.saveButton?.setAttribute("aria-busy", String(saving));
    if (dom.resetButton) dom.resetButton.disabled = saving;
    branchAutocomplete.setDisabled(
      saving || Number(config.availableSucursalCount || 0) < 1
    );
    setComposerState();
    if (dom.itemsFilter) dom.itemsFilter.disabled = saving;
    dom.itemsBody.querySelectorAll("input, button").forEach((control) => {
      control.disabled = saving;
    });
    updatePageState();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.saving) return;

    UI.clearAlerts();
    UI.clearFieldErrors();

    // Si el usuario eligió el siguiente producto y pulsó Guardar sin pasar
    // por el botón intermedio, lo incorporamos al lote en vez de perderlo.
    if (dom.productInput.value.trim() || dom.productHidden.value) {
      if (!addCurrentProduct()) return;
    }

    let valid = true;

    if (!dom.branchHidden.value) {
      UI.fieldError("sucursal", "Selecciona una sucursal de la lista.");
      valid = false;
    }
    if (!state.items.length) {
      UI.fieldError("inventarios_temp", "Agrega al menos un producto antes de guardar.");
      valid = false;
    }

    const invalidItems = state.items.filter((item) => positiveInteger(item.quantity) === null);
    if (invalidItems.length) {
      UI.show("error", "Corrige las cantidades marcadas antes de guardar.");
      renderItems();
      valid = false;
    }
    if (!valid) {
      form.querySelector("[aria-invalid='true']")?.focus();
      return;
    }

    dom.payloadInput.value = JSON.stringify(state.items.map((item) => ({
      productId: item.productId,
      productName: item.productName,
      cantidad: positiveInteger(item.quantity),
    })));

    const submissionData = new FormData(form);
    // El editor superior es solo un "compositor" de filas. Una selección que
    // todavía no se agregó no debe bloquear el guardado del lote ya revisado.
    submissionData.delete("producto_autocomplete");
    submissionData.delete("productoid");
    submissionData.delete("cantidad");
    setSaving(true);
    try {
      const response = await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
        },
        body: submissionData,
      });

      const contentType = response.headers.get("content-type") || "";
      if (response.redirected || !contentType.includes("application/json")) {
        throw new Error("Tu sesión expiró. Recarga la página e inicia sesión nuevamente.");
      }
      const data = await response.json();

      if (!response.ok || !data.success) {
        if (data.errors) UI.serverErrors(data.errors);
        else UI.show("error", data.error || data.message || "No se pudo guardar el inventario.");
        return;
      }

      clearItems();
      productAutocomplete.clear({ silent: true });
      productAutocomplete.invalidateCache();
      dom.quantityInput.value = "1";
      dom.payloadInput.value = "";
      UI.show("success", data.message || "Inventario guardado correctamente.");
      updatePageState();
      window.setTimeout(() => {
        if (!dom.productInput.disabled) dom.productInput.focus();
      }, 0);
    } catch (error) {
      UI.show("error", error?.message || "Ocurrió un error de red. Revisa tu conexión e inténtalo de nuevo.");
    } finally {
      setSaving(false);
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!state.items.length) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // Estado inicial
  dom.quantityInput.value = positiveInteger(dom.quantityInput.value) || "1";
  branchAutocomplete.setDisabled(Number(config.availableSucursalCount || 0) < 1);
  setComposerState();
  renderItems();
})();

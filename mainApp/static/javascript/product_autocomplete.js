/* Motor compartido del POS y visor: normalización, ranking y teclado. */
(function (root) {
  "use strict";
  const onlyDigits = (s) => String(s||"").replace(/\D+/g, "");
  const norm = (s)=> (s||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();
  const normalizeUnits = (s) => {
    let x = norm(s);
    x = x.replace(/\bx\s*(\d+)\b/g, "x$1");
    x = x.replace(/(\d+(?:[.,]\d+)?)\s*(ml|g|gr|kg|l|lt|oz)\b/g, (m, a, u) => `${a.replace(",", ".")}${u}`);
    x = x.replace(/\s+/g, " ").trim();
    return x;
  };

  function classifyQuery(term){
    const raw = String(term || "").trim();
    const digits = onlyDigits(raw);
    const hasLetters = /[a-záéíóúñ]/i.test(raw);
    const compact = raw.replace(/\s+/g,"");
    const isPureDigits = digits.length > 0 && digits.length === compact.length;
    const isBarcodeLike = isPureDigits && digits.length >= 6;
    return { raw, digits, hasLetters, isPureDigits, isBarcodeLike };
  }

  function createIndex(items){
    const idx = { names: [], codes: [], ids: [], map:new Map() };
    for (const p of items) {
      const id = p.id;
      const idStr = String(id);

      const rawName = (p.name || "").toString();
      const nnameU = normalizeUnits(rawName);
      const toks = nnameU ? nnameU.split(/\s+/).filter(Boolean) : [];

      const barcodeRaw = (p.barcode || "").toString();
      const nbarcode = barcodeRaw ? onlyDigits(barcodeRaw) : "";

      idx.names.push({ id, nnameU, toks, label: rawName || "", price: p.price, stock: p.stock, barcode: barcodeRaw || "" });
      idx.codes.push({ id, nbarcode, label: barcodeRaw || rawName || "", price: p.price, stock: p.stock });
      idx.ids.push({ id, idStr, label: idStr, name: rawName || "", barcode: barcodeRaw || "", price: p.price, stock: p.stock });

      idx.map.set(String(id), { id, name: rawName || "", barcode: barcodeRaw || "", price: p.price, stock: p.stock });
    }
    idx.names.sort((a,b) => (a.nnameU < b.nnameU ? -1 : a.nnameU > b.nnameU ? 1 : 0));
    idx.codes.sort((a,b) => (a.nbarcode < b.nbarcode ? -1 : a.nbarcode > b.nbarcode ? 1 : 0));
    idx.ids.sort((a,b)=> (a.idStr < b.idStr ? -1 : a.idStr > b.idStr ? 1 : 0));
    return idx;
  }


  function createSearch({getBoost = () => ({})} = {}) {
    let pickBoost = {};
  function pushTopK(arr, item, score, K=40){
    if (score <= 0) return;
    const rec = { item, score };
    if (arr.length < K) { arr.push(rec); return; }
    let minI = 0, minS = arr[0].score;
    for (let i=1;i<arr.length;i++){
      if (arr[i].score < minS) { minS = arr[i].score; minI = i; }
    }
    if (score <= minS) return;
    arr[minI] = rec;
  }

  function isStrongToken(t){
    if (!t) return false;
    return /\d/.test(t) || /^x\d+/.test(t) || /\d+(ml|g|gr|kg|l|lt|oz)$/.test(t);
  }

  function scoreName(qU, qTokens, strongTokens, cand){
    const s = cand.nnameU;
    if (!s) return 0;
    if (s === qU) return 2600 + (pickBoost[String(cand.id)] || 0) * 7;

    let score = 0;
    const pos = s.indexOf(qU);
    if (pos === 0) score += 1500;
    else if (pos > 0) score += 850;
    if (pos >= 0) score += Math.max(0, 160 - pos * 6);

    if (qTokens.length) {
      let hits = 0, strongHits = 0;
      for (let i=0;i<qTokens.length;i++){
        const t = qTokens[i];
        if (!t) continue;
        const strong = isStrongToken(t);
        let found = false;

        for (let j=0;j<cand.toks.length;j++){
          const ct = cand.toks[j];
          if (ct === t) { score += strong ? 240 : 170; hits++; if (strong) strongHits++; found=true; break; }
          if (ct.startsWith(t)) { score += strong ? 170 : 120; hits++; if (strong) strongHits++; found=true; break; }
        }
        if (!found) score -= (strong ? 190 : 70);
      }
      if (hits) score += hits * 45;
      if (hits === qTokens.length) score += 260;
      if (strongTokens.length && strongHits === strongTokens.length) score += 360;
    }

    const diff = Math.abs((s.length || 0) - (qU.length || 0));
    score += Math.max(0, 90 - diff);

    score += (pickBoost[String(cand.id)] || 0) * 7;

    const st = Number(cand.stock) || 0;
    if (st > 0) score += Math.min(80, st / 2);

    return score;
  }

  function scoreCode(qDigits, candCode){
    const s = candCode.nbarcode || "";
    if (!s || !qDigits) return 0;
    if (s === qDigits) return 2400 + (pickBoost[String(candCode.id)] || 0) * 7;
    const pos = s.indexOf(qDigits);
    if (pos === 0) return 1700 + Math.max(0, 130 - qDigits.length * 2) + (pickBoost[String(candCode.id)] || 0) * 7;
    if (pos > 0) return 1000 + Math.max(0, 70 - pos * 5) + (pickBoost[String(candCode.id)] || 0) * 7;
    return 0;
  }

  function rankNameLocal(term, idx, limit=40){
    const qU = normalizeUnits(term);
    if (!qU) return [];
    const qTokens = qU.split(/\s+/).filter(Boolean).slice(0, 6);
    const strongTokens = qTokens.filter(isStrongToken);

    const top = [];
    for (let i=0;i<idx.names.length;i++){
      const c = idx.names[i];
      if (qTokens.length) {
        const t0 = qTokens[0];
        if (t0 && c.nnameU.indexOf(t0) === -1) continue;
      } else {
        if (c.nnameU.indexOf(qU) === -1) continue;
      }

      if (strongTokens.length) {
        let ok = false;
        for (let k=0;k<strongTokens.length;k++){
          const st = strongTokens[k];
          if (st && c.nnameU.indexOf(st) !== -1) { ok = true; break; }
        }
        if (!ok) continue;
      }

      const sc = scoreName(qU, qTokens, strongTokens, c);
      pushTopK(top, c, sc, limit);
    }

    top.sort((a,b) => b.score - a.score);
    return top.map(({item:c}) => ({ id:c.id, name:c.label, barcode:c.barcode, price:c.price, stock:c.stock }));
  }

  function rankCodeLocal(term, idx, limit=40){
    const info = classifyQuery(term);
    if (!info.isBarcodeLike) return rankNameLocal(term, idx, limit);

    const qDigits = info.digits;
    const top = [];
    for (let i=0;i<idx.codes.length;i++){
      const c = idx.codes[i];
      if (!c.nbarcode) continue;
      if (c.nbarcode.indexOf(qDigits) === -1) continue;
      const sc = scoreCode(qDigits, c);
      pushTopK(top, c, sc, limit);
    }

    top.sort((a,b) => b.score - a.score);
    return top.map(({item:c}) => {
      const ref = idx.map.get(String(c.id));
      const barcode = ref?.barcode || c.label || "";
      const name = ref?.name || "";
      return { id:c.id, name, barcode, price: ref?.price ?? c.price, stock: ref?.stock ?? c.stock };
    });
  }

  function rankIdLocal(term, idx, limit=40){
    const q = onlyDigits(term);
    if (!q) return [];
    const top = [];
    for (let i=0;i<idx.ids.length;i++){
      const c = idx.ids[i];
      if (!c.idStr) continue;
      if (c.idStr === q) {
        top.push({ item:c, score: 3000 + (pickBoost[String(c.id)] || 0) * 7 });
        continue;
      }
      if (c.idStr.startsWith(q)) {
        const sc = 1800 + Math.max(0, 120 - (c.idStr.length - q.length) * 10) + (pickBoost[String(c.id)] || 0) * 7;
        pushTopK(top, c, sc, limit);
      }
    }
    top.sort((a,b)=> b.score - a.score);
    return top.map(({item:c}) => ({ id:c.id, name:c.name, barcode:c.barcode, price:c.price, stock:c.stock }));
  }

  function buildLocalSmart(term, idx, limit=40){
    const t = (term || "").trim();
    const info = classifyQuery(t);
    const locals = [];
    const seen = new Set();

    if (info.isPureDigits && idx) {
      const ref = idx.map.get(String(info.digits));
      if (ref) { locals.push({ id: ref.id, name: ref.name, barcode: ref.barcode, price: ref.price, stock: ref.stock }); seen.add(String(ref.id)); }
    }

    if (idx && info.isBarcodeLike) {
      const byCode = rankCodeLocal(t, idx, limit);
      for (const it of byCode) { const k=String(it.id); if(seen.has(k)) continue; locals.push(it); seen.add(k); if(locals.length>=limit) break; }
    }

    if (idx && locals.length < limit) {
      const byName = rankNameLocal(t, idx, limit);
      for (const it of byName) { const k=String(it.id); if(seen.has(k)) continue; locals.push(it); seen.add(k); if(locals.length>=limit) break; }
    }

    return locals.slice(0, limit);
  }


    const methods = {rankNameLocal, rankCodeLocal, rankIdLocal, buildLocalSmart};
    return Object.fromEntries(Object.entries(methods).map(([name, fn]) => [name, (...args) => {
      pickBoost = getBoost() || {};
      return fn(...args);
    }]));
  }
  function createAutocomplete($, options, {lastUserInputTS = new WeakMap(), now = Date.now} = {}) {
  function attachAltEnterBypass(inputEl) {
    if (!inputEl) return;
    inputEl.addEventListener("keydown", function(e){
      if (e.key === "Enter" && e.altKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        $(inputEl).data("skipAcSelectOnce", true);
        try { $(inputEl).autocomplete("close"); } catch (_){}
      }
    }, true);
  }

  function blockNavOpenWhenEmpty($inp, minChars) {
    $inp.on("keydown", function(e){
      const navKeys = ["ArrowDown","ArrowUp","PageDown","PageUp","Home","End"];
      if (!navKeys.includes(e.key)) return;
      const v = this.value || "";
      if (v.length < minChars) {
        try { $inp.autocomplete("close"); } catch {}
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      }
    });
  }

  function createAC({
    $inp,
    sourceFn,
    onSelect,
    openIfEmpty=false,
    enableInstantSearch=true,
    minChars=1,
    onEnterFallback=null,
    enterTermKey=null,
    preferEnterFallback=null
  }) {
    attachAltEnterBypass($inp[0]);

    const getEnterTermKey = (value) => {
      if (typeof enterTermKey === "function") return String(enterTermKey(value) || "");
      return String(value || "").trim();
    };

    function consumeEnter(evt){
      evt.preventDefault();
      evt.stopPropagation();
      evt.stopImmediatePropagation();
    }

    function commitActiveAutocompleteItem(evt){
      const inst = $inp.autocomplete("instance");
      if (!inst) return false;

      const $menu = inst.menu && inst.menu.element ? inst.menu.element : $();
      const menuVisible = !!($menu.length && $menu.is(":visible"));
      if (!menuVisible) return false;

      let $active = inst.menu && inst.menu.active && inst.menu.active.length
        ? inst.menu.active
        : $menu.find(".ui-state-active, .ui-menu-item-wrapper.ui-state-active").first();

      if (!$active || !$active.length) return false;

      const $activeLi = $active.is("li") ? $active : $active.closest("li");
      const $activeWrapper = $active.hasClass("ui-menu-item-wrapper")
        ? $active
        : $active.find(".ui-menu-item-wrapper").first();

      let item = null;

      try { item = $activeLi.data("ui-autocomplete-item"); } catch (_) {}
      if (!item) {
        try { item = $activeWrapper.data("ui-autocomplete-item"); } catch (_) {}
      }

      // respaldo: toma el primer item visible si por alguna razón no quedó activo
      if (!item) {
        const $firstLi = $menu.find("li").has(".ui-menu-item-wrapper").first();
        try { item = $firstLi.data("ui-autocomplete-item"); } catch (_) {}
      }

      if (!item) return false;

      consumeEnter(evt);

      try { $inp.autocomplete("close"); } catch (_) {}
      onSelect?.(item);
      return true;
    }

    function commitFallbackAutocompleteItem(evt){
      if (typeof onEnterFallback !== "function") return false;

      const term = String($inp.val() || "").trim();
      if (!term || (term.length < minChars && !openIfEmpty)) return false;

      const termKey = getEnterTermKey(term);
      if (!termKey) return false;

      if ($inp.data("enterFallbackPendingKey") === termKey) {
        consumeEnter(evt);
        return true;
      }

      let result = null;
      try { result = onEnterFallback(term); } catch (_) { return false; }
      if (!result) return false;

      consumeEnter(evt);
      $inp.data("enterFallbackPendingKey", termKey);

      Promise.resolve(result)
        .then((item) => {
          if (getEnterTermKey($inp.val() || "") !== termKey) return;

          if (!item) {
            try { $inp.autocomplete("search", term); } catch (_) {}
            return;
          }

          try { $inp.autocomplete("close"); } catch (_) {}
          onSelect?.(item);
        })
        .catch(() => {
          if (getEnterTermKey($inp.val() || "") === termKey) {
            try { $inp.autocomplete("search", term); } catch (_) {}
          }
        })
        .finally(() => {
          if ($inp.data("enterFallbackPendingKey") === termKey) {
            $inp.removeData("enterFallbackPendingKey");
          }
        });

      return true;
    }

    $inp.on("keydown.autocompleteEnterFix", function(e){
      if (e.key !== "Enter" || e.altKey || e.ctrlKey || e.metaKey) return;
      if ($inp.data("skipAcSelectOnce")) { $inp.data("skipAcSelectOnce", false); return; }
      if (typeof preferEnterFallback === "function" && preferEnterFallback($inp.val() || "")) {
        if (commitFallbackAutocompleteItem(e)) return;
      }
      if (commitActiveAutocompleteItem(e)) return;
      commitFallbackAutocompleteItem(e);
    });

    $inp.autocomplete({
      minLength: minChars,
      delay: 0,
      autoFocus: true,
      appendTo: "body",
      position:{ my:"left top+6", at:"left bottom", collision:"flipfit" },
      source: sourceFn,
      open(){ $inp.autocomplete("widget").css("z-index", 3000); },
      select(_e, ui){
        if ($inp.data("skipAcSelectOnce")) { $inp.data("skipAcSelectOnce", false); return false; }
        if (!ui || !ui.item) return false;
        onSelect?.(ui.item);
        return false;
      }
    });

    $inp.on("focus", function(){
      const v = this.value || "";
      if (v.length < minChars && !openIfEmpty) { try { $inp.autocomplete("close"); } catch {} return; }
      $inp.autocomplete("search", v);
    });

    if (enableInstantSearch) {
      let raf = null;
      $inp.on("input", function(){
        // ✅ marcar tipeo real del usuario para gating del autopick
        lastUserInputTS.set($inp[0], now());
        const v = this.value || "";
        if (v.length < minChars && !openIfEmpty) { try { $inp.autocomplete("close"); } catch {} return; }
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(()=> $inp.autocomplete("search", v));
      });
    }

    blockNavOpenWhenEmpty($inp, openIfEmpty ? 0 : minChars);
  }


    return createAC(options);
  }
  function createCatalog({url, storageKey, storage = null, fetcher = root.fetch.bind(root),
    now = Date.now, ttl = 5 * 60 * 1000, getBoost = () => ({})}) {
    let items = [], index = null, updated = 0, pending = null, byId = new Map();
    const search = createSearch({getBoost});
    function install(rows, timestamp) {
      if (!Array.isArray(rows)) throw new Error("El catálogo de productos no es válido.");
      items = rows.filter(p => p && /^[1-9]\d*$/.test(String(p.id)) && typeof p.name === "string");
      byId = new Map(items.map(item => [String(item.id), item]));
      index = createIndex(items);
      updated = timestamp;
    }
    try {
      const saved = JSON.parse(storage?.getItem(storageKey) || "null");
      if (saved && now() >= saved.at && now() - saved.at < ttl) install(saved.items, saved.at);
    } catch (_) {}
    function ensure(force = false) {
      if (pending) return pending;
      if (!force && index && now() - updated < ttl) return Promise.resolve(items);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      pending = (async () => {
        try {
          const response = await fetcher(url, {cache: "no-store", signal: controller.signal});
          if (!response.ok) throw new Error("No pudimos actualizar los productos. Revisa tu conexión o sesión.");
          const data = await response.json();
          install(data.results, now());
          try { storage?.setItem(storageKey, JSON.stringify({at: updated, items})); } catch (_) {}
          return items;
        } finally { clearTimeout(timer); pending = null; }
      })();
      return pending;
    }
    return {
      ensure,
      get ready() {return index !== null;},
      get updated() {return updated;},
      get(id) {return byId.get(String(id));},
      search(term, limit = 40) {return index ? search.buildLocalSmart(term, index, limit) : [];},
    };
  }
  const api = {norm, normalizeUnits, classifyQuery, createIndex, createSearch, createAutocomplete, createCatalog};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NovaProductAutocomplete = api;
})(typeof globalThis === "object" ? globalThis : window);

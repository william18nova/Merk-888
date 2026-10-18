/* Pure parser shared by the content script and tests. Never captures contracts/IPs. */
(function (root) {
  const headers = ["Id TX", "Fecha Inicio", "Fecha Fin", "Tipo de servicio", "Producto", "Contrato", "Valor", "Estado", "Medio", "Terminal", "Id Operador", "Usuario", "Error"];
  function text(value) { return String(value || "").replace(/\s+/g, " ").trim(); }
  function money(value) {
    const s = text(value).replace(/^\$\s*/, "");
    if (!/^-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}$/.test(s)) throw Error("estructura");
    return s.replace(/\./g, "").replace(",", ".");
  }
  function parse(headerCells, bodyRows) {
    if (headerCells.length !== headers.length || headerCells.some((h, i) => text(h) !== headers[i])) throw Error("estructura");
    return bodyRows.filter(cells => !(cells.length === 1 && /ning[uú]n dato disponible/i.test(cells[0]))).map(c => {
      if (c.length !== headers.length) throw Error("estructura");
      return {id_tx: text(c[0]), inicio: text(c[1]), fin: text(c[2]), producto: text(c[4]),
        valor: money(c[6]), estado: text(c[7]), id_operador: text(c[10]), usuario: text(c[11])};
    });
  }
  function endpoint(value) {
    const url = new URL(value);
    if (url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) throw Error("Usa solo la dirección principal de NovaPOS.");
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) throw Error("Usa HTTPS o localhost para las pruebas.");
    if (url.hostname === "ptm.transacciones.com.co") throw Error("El destino debe ser NovaPOS, no PTM.");
    return url.origin;
  }
  const api = {parse, money, headers, endpoint};
  if (typeof module !== "undefined") module.exports = api;
  else root.NovaPTMParser = api;
})(globalThis);

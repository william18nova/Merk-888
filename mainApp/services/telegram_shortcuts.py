"""Frases inequívocas de lectura; las peticiones compuestas siguen pasando a la IA."""
import re
import unicodedata


def specific_read_request(text):
    from .telegram_assistant import _period_dates
    normalized = "".join(c for c in unicodedata.normalize("NFKD", str(text).lower()) if not unicodedata.combining(c))
    normalized = re.sub(r"\s+", " ", normalized).strip(" ¿?¡!.,")
    normalized = re.sub(r"^(?:(?:hola|oye|jarvis|por favor)[, ]+)+", "", normalized)
    normalized = re.sub(r"[, ]+por favor$", "", normalized)
    if len(normalized) > 300:
        return None
    prefix = r"(?:(?:me )?(?:puedes|podrias) )?(?:muestrame|muestra|dame|ver|consulta|quiero ver|necesito ver)"
    payment = re.fullmatch(r"(?:" + prefix + r" )?(?:el )?(?P<history>historial (?:del?|de cambios del?) )?(?:pago|egreso) (?:con )?(?:el )?(?:id |numero )?#?(?P<id>[1-9][0-9]{0,17})", normalized)
    if payment:
        return "consultar_pago", {"pago_id": int(payment["id"]), "historial": bool(payment["history"])}
    payment_history = re.fullmatch(r"(?:quien (?:edito|corrigio|modifico)|que cambios (?:tiene|tuvo)) (?:el )?pago #?([1-9][0-9]{0,17})", normalized)
    if payment_history:
        return "consultar_pago", {"pago_id": int(payment_history[1]), "historial": True}

    detail_patterns = {
        "total": r"(?:cuanto fue|cual es|dime|dame) (?:el )?total (?:de|de la)",
        "cajero": r"quien (?:hizo|registro|atendio|facturo)(?: la)?",
        "cliente": r"(?:quien es|cual es|dime) (?:el )?cliente (?:de|de la)",
        "pagos": r"(?:como (?:pagaron|se pago)(?: la)?|(?:muestrame|dame) (?:solo )?(?:los )?(?:pagos|medios de pago) (?:de|de la))",
        "productos": r"(?:muestrame|dame) solo (?:los )?productos (?:de|de la)",
        "reintegros": r"(?:muestrame|dame) (?:los )?(?:reintegros|reembolsos) (?:de|de la)",
    }
    for view, pattern in detail_patterns.items():
        match = re.fullmatch(pattern + r" (?:venta|factura) (?:id |numero )?#?([1-9][0-9]{0,17})", normalized)
        if match:
            return "consultar_detalle_operativo", {"tipo": "venta", "id": int(match[1]), "vista": view}
    nequi = re.fullmatch(r"(?:la )?venta #?([1-9][0-9]{0,17}) (?:tiene|esta) (?:un )?nequi vinculad[oa]", normalized)
    if nequi:
        return "consultar_detalle_operativo", {"tipo": "venta", "id": int(nequi[1]), "vista": "nequi"}
    # Nunca convertir una orden o dos peticiones en una búsqueda de producto.
    if re.search(r"\b(?:y|ademas|luego|despues|manana|anteayer|cambia|cambiar|registra|registrar|borra|elimina|crea|agrega|actualiza|corrige|devuelve)\b", normalized):
        return None
    lookup = re.fullmatch(r"(?:cuanto (?:cuesta|vale)|(?:dime|dame|muestrame) (?:el )?precio de) (?:el |la |un |una )?(.+)", normalized)
    if lookup:
        return "buscar_producto", {"consulta": lookup[1], "vista": "precio"}
    periods = r"hoy|ayer|esta semana|este mes|la semana pasada|el mes pasado"
    ptm = re.fullmatch(r"(?:(?P<list>muestrame|dame|lista|ver) (?:las |los )?|(?P<count>cuantas) )?(?:operaciones|transacciones) ptm(?: (?P<period>" + periods + r"))?", normalized)
    if ptm:
        start, end = _period_dates(ptm["period"] or "hoy")
        return "consultar_datos", {"fuente": "ptm", "operacion": "contar" if ptm["count"] else "listar", "desde": start.isoformat(), "hasta": end.isoformat()}
    ptm_sum = re.fullmatch(r"cuanto (?:se )?(?P<direction>retiro|recargo) (?:por |en )?ptm(?: (?P<period>" + periods + r"))?", normalized)
    if ptm_sum:
        start, end = _period_dates(ptm_sum["period"] or "hoy")
        return "consultar_datos", {"fuente": "ptm", "operacion": "sumar", "campo": "importe", "desde": start.isoformat(), "hasta": end.isoformat(), "filtros": [{"campo": "tipo", "operador": "igual", "valor": "retiro" if ptm_sum["direction"] == "retiro" else "recarga"}]}
    catalog = re.fullmatch(r"(?:" + prefix + r" )?(?:la lista de )?(?:los |las )?(?P<resource>proveedores|clientes|categorias|sucursales|usuarios|roles|productos agotados|productos sin ventas|productos nunca vendidos|metodos de pago)", normalized)
    if catalog:
        resource = catalog["resource"]
        args = {"recurso": resource.replace(" ", "_")}
        if resource == "productos agotados":
            args = {"recurso": "inventario", "stock_max": 0}
        elif resource in {"productos sin ventas", "productos nunca vendidos"}:
            args = {"recurso": "productos", "sin_ventas": True}
        elif resource == "metodos de pago":
            args = {"recurso": "metodos_pago"}
        return "consultar_registros", args
    return None

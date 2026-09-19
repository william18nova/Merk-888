"""Compatibilidad de formato de IA, sin ejecutar consultas ni corregir su significado."""
import json
import re


class AIOutputError(ValueError):
    def __init__(self, reason="format"):
        self.reason = reason
        super().__init__(reason)


def strict_json(raw):
    if not isinstance(raw, str) or len(raw) > 32000:
        raise AIOutputError("format")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text, re.IGNORECASE)
    if fenced:
        text = fenced[1].strip()

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AIOutputError("format")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise AIOutputError("format")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError):
        raise AIOutputError("format") from None


def normalize_read_arguments(value, schema, depth=0):
    """Solo equivalencias inequívocas. No cambia dinero, fechas, nombres ni códigos."""
    if depth > 12:
        raise AIOutputError("schema")
    kind = schema.get("type", "").upper()
    if kind == "OBJECT" and isinstance(value, dict):
        properties = schema.get("properties", {})
        # Nunca eliminar campos desconocidos: el validador debe rechazarlos.
        return {
            key: normalize_read_arguments(item, properties.get(key, {}), depth + 1)
            for key, item in value.items()
            if not (item is None and key in properties and key not in schema.get("required", []))
        }
    if kind == "ARRAY" and isinstance(value, list):
        if len(value) > 30:
            raise AIOutputError("schema")
        return [normalize_read_arguments(item, schema.get("items", {}), depth + 1) for item in value]
    if kind == "INTEGER":
        if isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]{0,17})", value):
            return int(value)
        if isinstance(value, float) and value.is_integer() and abs(value) < 2 ** 53:
            return int(value)
    if kind == "BOOLEAN" and value in ("true", "false"):
        return value == "true"
    if isinstance(value, str) and "enum" in schema:
        options = [item for item in schema["enum"] if isinstance(item, str) and item.casefold() == value.casefold()]
        if len(options) == 1:
            return options[0]
    return value


def read_envelope(text):
    """Solo una llamada JSON completa, nunca extraer órdenes de prosa o fragmentos."""
    if not isinstance(text, str) or not text.strip():
        raise AIOutputError("empty")
    text = text.strip()
    if not text.startswith(("{", "```")):
        return None
    value = strict_json(text)
    if not isinstance(value, dict) or set(value) != {"name", "arguments"}:
        raise AIOutputError("format")
    return value["name"], value["arguments"]


def repair_instruction(reason):
    detail = {
        "truncated": "La respuesta anterior quedó cortada. Produce una respuesta completa y concisa.",
        "schema": "La llamada anterior tenía campos o tipos no válidos. Revisa el esquema exacto.",
        "multiple_actions": "No se permiten varias acciones de escritura juntas. Pregunta cuál preparar primero; no ignores las demás.",
        "unknown_tool": "La función anterior no pertenece a las herramientas disponibles.",
        "empty": "La respuesta anterior no contenía texto ni una llamada utilizable.",
    }.get(reason, "La respuesta anterior no tenía un formato interpretable.")
    return (
        "RECUPERACIÓN DE FORMATO: " + detail + " No se ejecutó ninguna herramienta. "
        "Interpreta de nuevo la petición ORIGINAL y conserva TODOS sus filtros, importes e intenciones. "
        "Usa una llamada nativa con argumentos JSON según el esquema: omite opcionales ausentes, "
        "no envíes null, no añadas campos, SQL ni código. Varias lecturas independientes van en consultar_varias. "
        "Si faltan datos obligatorios, pregunta en español sin inventarlos. "
        "No afirmes resultados ni cambios ya realizados; la confirmación sigue siendo por botones."
    )

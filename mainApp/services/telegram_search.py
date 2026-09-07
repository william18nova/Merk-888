"""Búsqueda por similitud sobre campos públicos y conjuntos ya autorizados."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db.models import Case, IntegerField, When


MAX_CANDIDATES = 15000
MAX_RESULTS = 50


def name_key(value):
    value = "".join(c for c in unicodedata.normalize("NFKD", str(value or "").lower()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def similarity(query, label):
    left, right = name_key(query), name_key(label)
    compact_left, compact_right = left.replace(" ", ""), right.replace(" ", "")
    if not compact_left or not compact_right:
        return 0.0
    if compact_left == compact_right:
        return 1.0
    # Los códigos y las presentaciones numéricas nunca se corrigen por parecido.
    if compact_left.isdigit():
        return 0.0
    numbers = set(re.findall(r"\d+", left))
    if numbers and not numbers <= set(re.findall(r"\d+", right)):
        return 0.0
    if min(len(compact_left), len(compact_right)) < 3:
        return 0.0
    words_left, words_right = left.split(), right.split()
    if set(words_left) <= set(words_right):
        return 0.96 if sorted(words_left) == sorted(words_right) else 0.90
    score = max(
        SequenceMatcher(None, compact_left, compact_right, autojunk=False).ratio(),
        SequenceMatcher(None, " ".join(sorted(words_left)), " ".join(sorted(words_right)), autojunk=False).ratio(),
    )
    # Permite 'aroz diana' frente a 'ARROZ DIANA 500 G', sin mezclar presentaciones.
    if len(words_left) <= len(words_right):
        remaining = list(words_right)
        matches = []
        for word in sorted(words_left, key=len, reverse=True):
            ratios = [(SequenceMatcher(None, word, candidate, autojunk=False).ratio(), index) for index, candidate in enumerate(remaining)]
            best, index = max(ratios)
            matches.append(best)
            remaining.pop(index)
        if min(matches) >= 0.72:
            score = max(score, sum(matches) / len(matches) * 0.93)
    return score


@dataclass(frozen=True)
class Match:
    pk: object
    label: str
    score: float


def rank_candidates(query, candidates, *, limit=MAX_RESULTS):
    matches = []
    for index, (pk, label, aliases) in enumerate(candidates):
        if index >= MAX_CANDIDATES:
            from .telegram_bot import TelegramBotError
            raise TelegramBotError("Hay demasiados nombres para comparar. Dime un nombre más completo o añade una sucursal o categoría.")
        score = max([similarity(query, label), *(similarity(query, alias) for alias in aliases)])
        if str(query).strip().isdigit() and str(pk) == str(query).strip():
            score = 1.0
        if score >= 0.72:
            matches.append(Match(pk, label, score))
    matches.sort(key=lambda item: (-item.score, name_key(item.label), str(item.pk)))
    return matches[:limit]


def rank_queryset(rows, query, fields=("nombre",), *, limit=MAX_RESULTS):
    # Sin datos privados, objetos completos, SQL del usuario ni caché de resultados.
    candidates = (
        (row[0], " ".join(str(value) for value in row[1:] if value), tuple(str(value) for value in row[1:] if value))
        for row in rows.order_by("pk").values_list("pk", *fields).iterator(chunk_size=500)
    )
    return rank_candidates(query, candidates, limit=limit)


def ranked_queryset(rows, query, fields=("nombre",)):
    matches = rank_queryset(rows, query, fields, limit=MAX_CANDIDATES)
    if len(matches) > 200:
        from .telegram_bot import TelegramBotError
        raise TelegramBotError("Encontré más de 200 coincidencias. Dime un nombre más completo para afinar la búsqueda.")
    if not matches:
        return rows.none()
    order = Case(*(When(pk=item.pk, then=index) for index, item in enumerate(matches)), output_field=IntegerField())
    return rows.filter(pk__in=[item.pk for item in matches]).order_by(order, "pk")


def choose_match(query, matches, *, entity="registro"):
    from .telegram_bot import TelegramBotError, TelegramClarification
    if not matches:
        raise TelegramBotError(f"No encontré {entity} que coincida con «{str(query)[:100]}». Revisa el nombre o dime su ID.")
    first = matches[0]
    close = len(matches) > 1 and first.score - matches[1].score < 0.08
    if first.score < 0.80 or close:
        options = "; ".join(f"ID {item.pk}: {item.label[:120]}" for item in matches[:5])
        raise TelegramClarification(f"Encontré varias opciones parecidas. ¿Cuál quieres usar? {options}. Dime el ID para no elegir una por error.")
    return first


def resolve_name(rows, query, fields=("nombre",), *, entity="registro"):
    value = str(query or "").strip()
    if value.isascii() and value.isdigit():
        found = rows.filter(pk=value).first() if len(value) <= 19 else None
        if found is not None:
            return found
        from .telegram_bot import TelegramBotError
        raise TelegramBotError(f"No encontré {entity} con ID {value[:30]}. No lo sustituí por otro parecido.")
    selected = choose_match(value, rank_queryset(rows, value, fields, limit=5), entity=entity)
    return rows.get(pk=selected.pk)

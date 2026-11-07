"""Conservative comparison of closing declarations and expenses.

Only an explicit audited source link confirms a match. Similarity is a hint,
never authority to create a payment or reuse one expense for several closes.
"""
import re
import hashlib
import json
import unicodedata
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
from .close_payments import validate

CO = ZoneInfo('America/Bogota')
# Fixed activation date requested by the owner, not a moving "today" filter.
RECONCILIATION_START = date(2026, 11, 1)
STOP = {'PAGO', 'PAGAR', 'TURNO', 'SRA', 'SENORA', 'SENOR', 'FACTURA',
        'COMPLEMENTO', 'COMPLETO', 'SALDO', 'DE', 'LA', 'EL', 'A', 'LE',
        'ENTREGUE', 'TERMINAR', 'SE', 'DINERO', 'Y', 'DON'}


def source_version(source):
    """Identidad de la declaración completa; detecta cambios y reordenamientos."""
    turn = source.turno
    data = {'turn': turn.pk, 'state': turn.estado, 'start': turn.inicio.isoformat(),
            'register': turn.puntopago_id, 'cashier': turn.cajero_id,
            'total': str(source.total), 'payments': source.pagos,
            'updated': source.registrado_en.isoformat()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def name_key(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', str(value).upper()) if not unicodedata.combining(c))
    words = re.findall(r'[A-Z]+', value)
    aliases = {'DIEGO': 'FABIAN', 'WILLIAN': 'WILLIAM', 'EMPANADA': 'EMPANADAS', 'EMPANAS': 'EMPANADAS'}
    return ' '.join(aliases.get(w, w) for w in words if w not in STOP)


def similarity(a, b):
    a, b = name_key(a), name_key(b)
    if not a or not b:
        return 0
    if a == b:
        return 1
    sa, sb = set(a.split()), set(b.split())
    overlap = sa & sb
    # AREPAS ALBERTO must not be matched to AREPAS NACH by their generic word.
    if overlap and (sa <= sb or sb <= sa):
        return .85
    return SequenceMatcher(None, a, b).ratio() if not overlap else len(overlap) / len(sa | sb)


def reconcile(declarations, expenses, links):
    """Pure matcher. links maps (turn, row) to explicitly audited expense IDs."""
    declarations = [d for d in declarations if d['date'] >= RECONCILIATION_START]
    expenses = [e for e in expenses if e['date'] >= RECONCILIATION_START]
    by_id = {e['id']: e for e in expenses}
    reserved = {i for ids in links.values() for i in ids}
    rows = []
    for declaration in declarations:
        for index, payment in enumerate(declaration['payments']):
            row = dict(declaration, index=index, name=str(payment.get('nombre', '')), candidates=[])
            row.pop('payments')
            try:
                amount = Decimal(str(payment['monto']))
                if not amount.is_finite() or amount <= 0:
                    raise ValueError
            except (KeyError, ValueError, InvalidOperation):
                row.update(amount=Decimal(0), status='review', explanation='Importe inválido en el desglose; requiere revisión.')
                rows.append(row)
                continue
            row['amount'] = amount
            audited = [by_id[i] for i in links.get((row['turn'], index), []) if i in by_id]
            if audited:
                row['candidates'] = audited
                valid = (sum(e['amount'] for e in audited) == amount
                         and all(e['date'] == row['date'] and e['method'] == 'efectivo'
                                 and (similarity(row['name'], e['name']) >= .72 or
                                      (row['name'].strip() and ' '.join(row['name'].split()).casefold() ==
                                       ' '.join(e['name'].split()).casefold())) for e in audited))
                row.update(status='linked' if valid else 'review', explanation=(
                    'Vínculo auditado con este cierre.' if valid else 'Existe vínculo auditado, pero cambió el importe, la fecha o el medio.'))
            else:
                candidates = [dict(e, score=similarity(row['name'], e['name'])) for e in expenses
                    if e['id'] not in reserved and e['amount'] == amount
                    and abs((e['date'] - row['date']).days) <= 1
                    and similarity(row['name'], e['name']) >= .72]
                candidates.sort(key=lambda e: (e['date'] != row['date'], -e['score'], e['id']))
                row.update(candidates=candidates, status='possible' if candidates else 'missing', explanation=(
                    'Mismo importe base y nombre similar; verifica fecha, medio y comprobante.' if candidates else
                    'Sin coincidencia por importe base y nombre similar en el día o ±1 día. No es prueba definitiva de ausencia.'))
            rows.append(row)
    uses = Counter(e['id'] for r in rows for e in r['candidates'])
    for r in rows:
        if any(uses[e['id']] > 1 for e in r['candidates']):
            r.update(status='review', explanation='Un mismo pago coincide con varios desgloses; no debe contabilizarse varias veces.')
    return rows


def registered_payments(expenses, rows, start, end):
    """Show the actual ledger for the selected dates, independently of row filters."""
    references = {}
    for row in rows:
        for candidate in row['candidates']:
            references.setdefault(candidate['id'], []).append({
                'turn': row['turn'], 'name': row['name'], 'status': row['status'],
            })
    ledger = []
    for expense in expenses:
        if not max(start, RECONCILIATION_START) <= expense['date'] <= end:
            continue
        item = dict(expense, references=references.get(expense['id'], []))
        item['total'] = expense.get('total', expense['amount'])
        item['tax'] = expense.get('tax', Decimal(0))
        item['relation'] = ('Vinculado por auditoría' if item['references'] and
            all(r['status'] == 'linked' for r in item['references']) else
            'Posible coincidencia; verificar' if item['references'] else
            'Sin vínculo identificado con los cierres mostrados')
        ledger.append(item)
    ledger.sort(key=lambda e: (e['date'], e['id']), reverse=True)
    return dict(registered_payments=ledger, registered_count=len(ledger),
        registered_base_total=sum((e['amount'] for e in ledger), Decimal(0)),
        registered_total=sum((e['total'] for e in ledger), Decimal(0)))


def closing_report(start, end):
    from django.utils import timezone
    from mainApp.models import CambioEgreso, DesglosePagosCierre, Egreso
    start = max(start, RECONCILIATION_START)
    lo = timezone.make_aware(datetime.combine(start, time.min), CO)
    cutoff = timezone.make_aware(datetime.combine(RECONCILIATION_START, time.min), CO)
    hi = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), CO)
    sources = list(DesglosePagosCierre.objects.select_related('turno__cajero', 'turno__puntopago__sucursalid').filter(
        turno__estado='CERRADO', turno__inicio__gte=lo, turno__inicio__lt=hi).order_by('-turno__inicio', '-turno_id'))
    declarations=[]
    for s in sources:
        t=s.turno
        dt=timezone.localtime(t.inicio, CO)
        declarations.append(dict(turn=t.pk, date=dt.date(), started_at=dt,
            cashier=t.cajero.nombreusuario, register=t.puntopago.nombre,
            branch=str(t.puntopago.sucursalid), payments=s.pagos or [], total=s.total))
        declarations[-1]['source_version'] = source_version(s)
        try:
            validate(s.total, s.pagos)
            declarations[-1]['source_error'] = ''
        except (ValueError, TypeError):
            declarations[-1]['source_error'] = 'El desglose o su total requieren revisión antes de registrar pagos.'
    expenses=[dict(id=e.pk, date=timezone.localtime(e.creado_en, CO).date(),
        paid_at=timezone.localtime(e.creado_en, CO), registered_by=e.registrado_por_nombre,
        name=e.concepto.nombre, amount=e.monto_base, total=e.monto,
        tax=e.impuesto_4xmil, method=e.medio_pago)
        for e in Egreso.objects.select_related('concepto').filter(creado_en__gte=max(cutoff,lo-timedelta(days=1)), creado_en__lt=hi+timedelta(days=1))]
    links={}
    for audit in CambioEgreso.objects.filter(egreso_id__in=[e['id'] for e in expenses], motivo__startswith='CONCILIACION').values('egreso_id','motivo'):
        match=re.search(r'CIERRE (\d+)(?:/(\d+))?',audit['motivo'])
        if match:
            key=(int(match[1]),int(match[2] or 0))
            links.setdefault(key,set()).add(audit['egreso_id'])
    # All closed shifts participate in the same comparison, at any start time.
    rows=reconcile(declarations,expenses,links)
    labels={'linked':'Conciliado con auditoría','possible':'Puede estar registrado','missing':'Sin coincidencia','review':'Revisar'}
    for r in rows:
        r['label']=labels[r['status']]
    summary=[dict(code=code,label=label,count=sum(r['status']==code for r in rows),
        total=sum((r['amount'] for r in rows if r['status']==code),Decimal(0))) for code,label in labels.items()]
    return dict(rows=rows, summary=summary, source_count=sum(bool(d['payments']) for d in declarations),
        **registered_payments(expenses, rows, start, end))

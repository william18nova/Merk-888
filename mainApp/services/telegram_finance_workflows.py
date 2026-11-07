"""Conciliación y lotes: propuestas verificables, nunca transferencias de dinero."""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F
from django.utils import timezone

from .expense_reconciliation import CO, RECONCILIATION_START, closing_report, similarity
from .expense_reconciliation_registration import registration_previews, register_closing_expense
from .expense_editing import can_edit_expenses
from .telegram_expense_language import PaymentContext, extract_payment
from .telegram_ai_policy import normalized


def bot():
    from . import telegram_bot
    return telegram_bot


def require(profile, permission="registrar_egreso"):
    if not profile.activo or not profile.usuario.is_active or profile.telegram_chat_id != profile.telegram_user_id:
        raise PermissionDenied("Esta función requiere tu cuenta activa y su chat privado.")
    bot()._require_access(profile, permission)


def create_action(profile, kind, data, title, update):
    from mainApp.models import TelegramAccionPendiente
    if update is not None:
        prior = TelegramAccionPendiente.objects.filter(telegram_usuario=profile, actualizacion=update, accion=kind).first()
        if prior:
            return prior
    return TelegramAccionPendiente.objects.create(telegram_usuario=profile, actualizacion=update,
        accion=kind, argumentos=data, resumen=title[:500], vence_en=timezone.now()+timedelta(minutes=bot().ACTION_TTL_MINUTES))


def active_action(profile, key, kind):
    from mainApp.models import TelegramAccionPendiente
    require(profile)
    try:
        action = TelegramAccionPendiente.objects.select_for_update().get(pk=UUID(key), telegram_usuario=profile, accion=kind)
    except (ValueError, TelegramAccionPendiente.DoesNotExist):
        raise bot().TelegramClarification("La propuesta no existe o no pertenece a tu cuenta.") from None
    if action.estado != "PENDIENTE" or action.vence_en <= timezone.now():
        raise bot().TelegramClarification("Esta propuesta ya se resolvió o venció. Solicita una revisión nueva.")
    return action


def methods():
    return {row['code']: row for row in bot().payment_method_options(active_only=True)}


def duplicate_ids(concept, amount, day=None):
    from mainApp.models import Egreso
    day = day or timezone.localdate(timezone=CO)
    lo = timezone.make_aware(datetime.combine(day, time.min), CO)
    rows = Egreso.objects.select_related('concepto').filter(creado_en__gte=lo, creado_en__lt=lo+timedelta(days=1)).annotate(
        base=ExpressionWrapper(F('monto')-F('impuesto_4xmil'), output_field=DecimalField(max_digits=14,decimal_places=2))).filter(base=Decimal(str(amount)))
    return [row.pk for row in rows if similarity(concept,row.concepto.nombre) >= .72]


def report(profile, args):
    require(profile)
    if not can_edit_expenses(profile.usuario):
        raise PermissionDenied("No tienes permiso para conciliar pagos de cierres.")
    start,end = bot()._date_range(args)
    if start < RECONCILIATION_START or (end-start).days > 92:
        raise bot().TelegramClarification("Elige hasta 93 días desde el 1 de noviembre de 2026.")
    result = closing_report(start,end)
    rows = result['rows']
    if args.get('sucursal'):
        branch=bot()._find_branch(args['sucursal'])
        rows=[row for row in rows if row['branch_id']==branch.pk]
    if args.get('estado'):
        rows=[row for row in rows if row['status']==args['estado']]
    return rows,start,end


def tool_reconcile(profile,args):
    rows,start,end=report(profile,args)
    page,pages,offset=bot()._list_page(args,len(rows))
    shown=rows[offset:offset+bot().LIST_PAGE_SIZE]
    lines=[f"Pagos declarados en cierres · {start:%d/%m/%Y} a {end:%d/%m/%Y}",
           f"{len(rows)} declaraciones · página {page}/{pages} · más recientes primero."]
    keyboard=[]
    for row in shown:
        lines += [f"\nTurno #{row['turn']} · fila {row['index']+1} · {row['date']:%d/%m/%Y}",
                  f"{bot()._list_text(row['name'])}: {bot()._list_money(row['amount'])}",
                  f"{row['cashier']} · {row['branch']} · {row['label']}"]
        if row['candidates']:
            lines.append('Pagos para verificar: '+', '.join('#'+str(c['id']) for c in row['candidates']))
        if row['status']=='missing' and not row.get('source_error'):
            keyboard.append([{'text':f"Revisar turno {row['turn']} / fila {row['index']+1}", 'callback_data':f"recon:{row['turn']}:{row['index']}"}])
    lines.append('\nSin coincidencia no prueba que falte un pago. Revisa comprobantes y sucursal; no registro nada automáticamente.')
    return bot().BotReply('\n'.join(lines),'consultar_conciliacion_cierres',reply_markup={'inline_keyboard':keyboard} if keyboard else None,
        pagination={'page':page,'pages':pages,'arguments':dict(args,desde=start.isoformat(),hasta=end.isoformat(),pagina=page)})


def tool_prepare_closing(profile,args,update=None):
    from mainApp.models import TurnoCaja
    require(profile)
    if not can_edit_expenses(profile.usuario):
        raise PermissionDenied("No tienes permiso para registrar desde cierres.")
    turn=TurnoCaja.objects.filter(pk=args['turno_id']).first()
    if turn is None or not 1 <= args['fila'] <= 100:
        raise bot().TelegramClarification("Revisa el turno y el número de fila de la declaración.")
    day=timezone.localtime(turn.inicio,CO).date()
    rows,_,_=report(profile,{'desde':day.isoformat(),'hasta':day.isoformat()})
    row=next((r for r in rows if r['turn']==turn.pk and r['index']==args['fila']-1),None)
    if row is None:
        raise bot().TelegramClarification("No encontré esa fila en un cierre válido.")
    registration_previews([row],profile.usuario)
    if 'registration' not in row:
        raise bot().TelegramClarification("La declaración ya tiene vínculo, posibles coincidencias o datos que requieren revisión. No crearé otro pago.")
    preview=row['registration']
    data={'token':preview['token'],'turno':turn.pk,'fila':args['fila'],'concepto':row['name'],
          'fecha':day.isoformat(),'base':str(preview['base']),'impuesto':str(preview['tax']),'total':str(preview['total'])}
    return closing_reply(create_action(profile,'pago_cierre',data,f"Conciliar turno {turn.pk}, fila {args['fila']}",update))


def closing_reply(action):
    a=action.argumentos
    return bot().BotReply(f"Registrar declaración del turno #{a['turno']} / fila {a['fila']}\n{a['concepto']}\n"
        f"Fecha: {a['fecha']} · Efectivo\nBase: {bot()._list_money(a['base'])}\n4 × 1.000: {bot()._list_money(a['impuesto'])}\n"
        f"Total: {bot()._list_money(a['total'])}\n\nConfirma solo si comprobaste que se pagó y falta en Pagos. No volveré a descontar de caja.",
        'preparar_pago_cierre',proposal_id=str(action.pk),reply_markup={'inline_keyboard':[[
            {'text':'Confirmar pago faltante','callback_data':f'confirm:{action.pk}'},{'text':'Cancelar','callback_data':f'cancel:{action.pk}'}]]})


def confirm_closing(profile,action):
    require(profile)
    expense,created=register_closing_expense(user=profile.usuario,token=action.argumentos['token'],confirmed=True,origin='TELEGRAM')
    action.estado='CONFIRMADA'; action.resuelto_en=timezone.now(); action.save(update_fields=['estado','resuelto_en'])
    bot()._audit(profile,'confirmar_pago_cierre',{'pago_id':expense.pk,'turno':action.argumentos['turno'],'fila':action.argumentos['fila']})
    return f"{'Registré' if created else 'Ya estaba registrado'} el pago #{expense.pk} con la fecha del cierre y vínculo auditado. No se modificó la caja."


def tool_prepare_batch(profile,args,update=None):
    require(profile)
    text=str(update.transcripcion or update.texto or '') if update else ''
    if not 2 <= len(args['pagos']) <= 5:
        raise bot().TelegramClarification('Indica entre dos y cinco pagos por lote.')
    remaining=list(text); rows=[]; ranges=[]
    for item in args['pagos']:
        fragment=item['fragmento']
        positions=[m.span() for m in re.finditer(re.escape(fragment),text,re.IGNORECASE)] if fragment else []
        if len(positions)!=1 or any(a<positions[0][1] and positions[0][0]<b for a,b in ranges):
            raise bot().TelegramClarification('No pude separar todos los pagos sin mezclar datos. Dime cada concepto y valor con claridad.')
        a,b=positions[0]; ranges.append((a,b)); remaining[a:b]=' '*(b-a)
        fields={k:v for k,v in item.items() if k!='fragmento'}
        result=extract_payment(fields,PaymentContext(fragment))
        if result.question or not {'concepto','monto'} <= set(result.arguments):
            raise bot().TelegramClarification('En cada pago necesito un concepto y un monto inequívocos. No preparé el lote.')
        row=dict(result.arguments)
        if row.get('medio_pago'):
            code=bot()._exact_expense_method(row['medio_pago'],methods())
            if not code:
                raise bot().TelegramClarification('Indica un medio de pago activo para cada renglón.')
            row['medio_pago']=code
        rows.append(row)
    leftover=re.sub(r'[^\w\s]',' ',normalized(''.join(remaining)))
    if any(word not in {'y','ademas','tambien','por','favor','registra','anota','estos','pagos','pague','hice','dos','tres','cuatro','cinco',
                        'hola','oye','buenos','dias','buenas','tardes','noches','quiero','que','me','anotes','registres','anotame','agrega','agregalos','hoy'} for word in leftover.split()):
        raise bot().TelegramClarification('Quedó una parte de tu mensaje sin incluir. Repite el lote indicando cada pago; no omitiré ninguna operación.')
    if len({(normalized(r['concepto']),r['monto']) for r in rows}) != len(rows):
        raise bot().TelegramClarification('Hay dos pagos iguales dentro del lote. Confirma por separado si son operaciones distintas.')
    action=create_action(profile,'lote_pagos',{'filas':rows,'medios':list(methods()),'fecha':timezone.localdate(timezone=CO).isoformat()},f"Revisar lote de {len(rows)} pagos",update)
    return batch_reply(profile,action)


def batch_reply(profile,action):
    from mainApp.models import TelegramAccionPendiente
    lines=['Lote de pagos · revisa y confirma cada uno por separado.']; keyboard=[]
    for i,row in enumerate(action.argumentos['filas']):
        child=TelegramAccionPendiente.objects.filter(pk=row['propuesta'],telegram_usuario=profile).first() if row.get('propuesta') else None
        state='Confirmado' if child and child.estado=='CONFIRMADA' else 'Pendiente'
        lines.append(f"{i+1}. {row['concepto']} · {bot()._list_money(row['monto'])} · {row.get('medio_pago') or 'falta medio'} · {state}")
        if state!='Confirmado':
            keyboard.append([{'text':f'Revisar pago {i+1}','callback_data':f'bpay:o:{action.pk}:{i}'}])
    keyboard.append([{'text':'Descartar lo pendiente del lote','callback_data':f'cancel:{action.pk}'}])
    return bot().BotReply('\n'.join(lines)+f"\nFecha: {action.argumentos['fecha']}. Preparar el lote no registra pagos ni entrega dinero.",
        'preparar_lote_pagos',proposal_id=str(action.pk),reply_markup={'inline_keyboard':keyboard})


@transaction.atomic
def batch_callback(profile,key,index,method_index=None,update=None):
    from mainApp.models import TelegramAccionPendiente
    from .telegram_proposals import restore_confirmation
    action=active_action(profile,key,'lote_pagos'); data=dict(action.argumentos)
    if data['fecha']!=timezone.localdate(timezone=CO).isoformat():
        raise bot().TelegramClarification('Cambió el día desde que preparaste el lote. Revisa las fechas y solicita otro borrador.')
    if not 0<=index<len(data['filas']):
        raise bot().TelegramClarification('Esa fila no pertenece al lote.')
    row=data['filas'][index]
    if method_index is not None:
        if not 0<=method_index<len(data['medios']) or data['medios'][method_index] not in methods():
            raise bot().TelegramClarification('El medio cambió o ya no está activo. Revisa el lote.')
        if row.get('propuesta'):
            raise bot().TelegramClarification('Ese pago ya tiene propuesta. Cancélala antes de cambiar sus datos.')
        row['medio_pago']=data['medios'][method_index]
    if not row.get('medio_pago'):
        keys=[[{'text':bot().payment_method_label(code),'callback_data':f'bpay:m:{action.pk}:{index}:{j}'}]
              for j,code in enumerate(data['medios']) if code in methods()]
        return bot().BotReply(f"¿Con qué medio pagaste {row['concepto']} por {bot()._list_money(row['monto'])}?",'aclarar_lote',reply_markup={'inline_keyboard':keys})
    ids=duplicate_ids(row['concepto'],row['monto'])
    if ids and not row.get('propuesta'):
        return bot().BotReply('Este renglón puede estar registrado en los pagos '+', '.join('#'+str(k) for k in ids)+'. Verifica en la web; no crearé otro automáticamente.','lote_posible_duplicado')
    if row.get('propuesta'):
        child=TelegramAccionPendiente.objects.get(pk=row['propuesta'],telegram_usuario=profile)
    else:
        reply=bot()._execute_tool(profile,'preparar_registro_pago',row,update)
        child=TelegramAccionPendiente.objects.get(pk=reply.proposal_id,telegram_usuario=profile)
        child.argumentos=dict(child.argumentos,lote_origen=str(action.pk)); child.vence_en=min(child.vence_en,action.vence_en)
        child.save(update_fields=['argumentos','vence_en'])
        row['propuesta']=str(child.pk)
    action.argumentos=data; action.save(update_fields=['argumentos'])
    return restore_confirmation(profile,child)


def guard_batch_confirmation(profile,action):
    if not action.argumentos.get('lote_origen'):
        return
    from mainApp.models import MetodoPago, TelegramAccionPendiente
    parent=TelegramAccionPendiente.objects.select_for_update().filter(pk=action.argumentos['lote_origen'],telegram_usuario=profile,estado='PENDIENTE',vence_en__gt=timezone.now()).first()
    if parent is None:
        raise bot().TelegramClarification('El lote fue descartado o venció. No se registró el pago.')
    if parent.argumentos['fecha']!=timezone.localdate(timezone=CO).isoformat():
        raise bot().TelegramClarification('Cambió el día del lote. No se guardó un pago con otra fecha.')
    MetodoPago.objects.select_for_update().filter(pk=action.argumentos['medio_pago']).first()
    if duplicate_ids(action.argumentos['concepto'],action.argumentos['monto']):
        raise bot().TelegramClarification('Apareció un pago parecido con el mismo valor. Revisa los pagos antes de registrar otro.')


TOOL_DEFINITIONS=[
    {'name':'consultar_conciliacion_cierres','description':'Compara declaraciones de TODOS los cierres con Pagos: sin coincidencia, posibles, vinculados o revisar. Usa la misma conciliación conservadora de la web, no registra nada. Ofrece revisar pagos faltantes con fecha original.', 'parameters':{'type':'OBJECT','properties':{'desde':{'type':'STRING'},'hasta':{'type':'STRING'},'sucursal':{'type':'STRING'},'estado':{'type':'STRING','enum':['missing','possible','linked','review']},'pagina':{'type':'INTEGER'}}}},
    {'name':'preparar_pago_cierre','description':'Prepara registrar UNA declaración de cierre ya consultada. turno_id real y fila desde 1. Solo sin coincidencias válidas; exige botón, conserva fecha y no vuelve a descontar caja. Nunca inventar turno/fila.', 'parameters':{'type':'OBJECT','properties':{'turno_id':{'type':'INTEGER'},'fila':{'type':'INTEGER'}},'required':['turno_id','fila']}},
    {'name':'preparar_lote_pagos','description':'Separa entre DOS y CINCO pagos YA REALIZADOS hoy en propuestas individuales. Copia fragmentos literales disjuntos que incluyan todos los pagos; no inventar conceptos, importes ni medios. Si falta medio se pregunta por fila. Nunca confirmar ni transferir; cada pago requiere botón.', 'parameters':{'type':'OBJECT','properties':{'pagos':{'type':'ARRAY','items':{'type':'OBJECT','properties':{k:{'type':'STRING'} for k in ('fragmento','concepto','monto_texto','medio_texto')},'required':['fragmento','concepto','monto_texto']}}},'required':['pagos']}},
]
TOOL_FUNCTIONS={'consultar_conciliacion_cierres':tool_reconcile,'preparar_pago_cierre':tool_prepare_closing,'preparar_lote_pagos':tool_prepare_batch}

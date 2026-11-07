"""Recordatorios, informes y alertas consentidas; reserva persistente por entrega."""
import calendar
import hashlib
import json
from datetime import datetime, timedelta
import re

from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .expense_reconciliation import CO
from .telegram_finance_workflows import bot, require, create_action


def fingerprint(rule):
    return hashlib.sha256(json.dumps([rule.tipo,rule.frecuencia,rule.configuracion,rule.activo],sort_keys=True).encode()).hexdigest()


def access(profile,config):
    kind=config['tipo']
    permission='home' if kind=='recordatorio' else (
        {'cierre_diferencia':'turnos_caja_dashboard','caja_abierta':'turnos_caja_dashboard','lector_ptm':'conciliacion_ptm'}[config['alerta']]
        if kind=='alerta' else {'resumen':'metricas_negocio','pagos':'registrar_egreso','ventas':'visualizar_ventas','cierres':'turnos_caja_dashboard'}[config['informe']])
    require(profile,permission)
    if kind=='informe' and config['informe']=='ventas' and config.get('formato','texto')=='texto':
        bot()._require_access(profile,'metricas_negocio','ventas_diarias')


def validated(profile,args):
    config=dict(args)
    allowed={'tipo','frecuencia'}
    if args['tipo']=='alerta':
        allowed|={'alerta','umbral_minutos','sucursal'}
    else:
        allowed|=({'cuando'} if args['frecuencia']=='UNICA' else {'hora'})
        if args['frecuencia']=='SEMANAL': allowed.add('dia_semana')
        if args['frecuencia']=='MENSUAL': allowed.add('dia_mes')
        allowed|=({'texto'} if args['tipo']=='recordatorio' else {'informe','periodo','formato','sucursal'})
    if set(args)-allowed:
        raise bot().TelegramClarification('La solicitud mezcla opciones de calendario, informe o alerta. Indica una sola programación y sus datos correspondientes.')
    if args['tipo']=='alerta':
        if args['frecuencia']!='EVENTO' or not args.get('alerta'):
            raise bot().TelegramClarification('Indica qué alerta quieres: diferencia al cerrar, caja abierta o lector PTM sin reportar.')
        if args['alerta']!='cierre_diferencia' and not 1<=args.get('umbral_minutos',0)<=10080:
            raise bot().TelegramClarification('¿Cuántos minutos deben pasar antes de avisarte? Entre 1 y 10.080.')
        if args.get('sucursal') and args['alerta']=='lector_ptm':
            raise bot().TelegramClarification('La alerta del lector PTM se aplica a los lectores activos, sin atribuirlos a una sucursal por el nombre.')
    else:
        if args['frecuencia']=='EVENTO':
            raise bot().TelegramClarification('Un recordatorio o informe necesita una fecha o frecuencia de calendario.')
        if args['tipo']=='recordatorio' and not 1<=len(args.get('texto','').strip())<=500:
            raise bot().TelegramClarification('¿Qué texto quieres que te recuerde? Hasta 500 caracteres.')
        if args['tipo']=='informe' and (not args.get('informe') or not args.get('periodo')):
            raise bot().TelegramClarification('Dime qué informe y qué período quieres recibir: hoy, ayer, semana o mes actual/anterior.')
        if args['tipo']=='informe' and args.get('sucursal') and args['informe'] in {'pagos','resumen'}:
            raise bot().TelegramClarification('Los pagos y el balance no identifican sucursal. No puedo presentar un resumen por sucursal con esos datos.')
        if args['tipo']=='informe' and args.get('informe')=='resumen' and args.get('formato','texto')!='texto':
            raise bot().TelegramClarification('El balance programado se envía por texto. Para Excel o PDF elige ventas, pagos o cierres.')
        if args['frecuencia']=='UNICA':
            stamp=parse_datetime(args.get('cuando',''))
            if stamp is None:
                raise bot().TelegramClarification('Indica fecha y hora completas de Colombia para el recordatorio.')
            if timezone.is_naive(stamp): stamp=timezone.make_aware(stamp,CO)
            if stamp<=timezone.now() or stamp>timezone.now()+timedelta(days=366):
                raise bot().TelegramClarification('Elige una fecha futura dentro de los próximos 366 días.')
            config['cuando']=timezone.localtime(stamp,CO).isoformat()
        else:
            try:
                if not re.fullmatch(r'[0-9]{2}:[0-9]{2}',args.get('hora','')): raise ValueError
                datetime.strptime(args['hora'],'%H:%M')
            except ValueError: raise bot().TelegramClarification('¿A qué hora de Colombia? Por ejemplo 21:00.') from None
            if args['frecuencia']=='SEMANAL' and not 0<=args.get('dia_semana',-1)<=6:
                raise bot().TelegramClarification('¿Qué día de la semana? Lunes a domingo.')
            if args['frecuencia']=='MENSUAL' and not 0<=args.get('dia_mes',-1)<=31:
                raise bot().TelegramClarification('¿Qué día del mes? Usa 0 para el último día; un día 29, 30 o 31 no se adelanta en meses más cortos.')
    if args.get('sucursal'): config['sucursal']=str(bot()._find_branch(args['sucursal']).pk)
    access(profile,config)
    return config


def describe(config):
    lines=[f"{config['tipo'].capitalize()} · {config['frecuencia']} · solo tu chat privado (Colombia)"]
    for key,label in [('texto','Texto'),('informe','Informe'),('periodo','Período'),('formato','Formato'),('hora','Hora'),('cuando','Fecha y hora'),('dia_semana','Día (lunes=0)'),('dia_mes','Día del mes (0=último)'),('alerta','Alerta'),('umbral_minutos','Minutos sin novedad/abierta'),('sucursal','Sucursal ID')]:
        if key in config: lines.append(f'{label}: {config[key]}')
    if config['tipo']=='alerta':
        lines.append('Se revisa aproximadamente cada minuto cuando la cola lo permite. Un aviso por incidente, sin atribuir culpabilidad ni ejecutar cobros.')
    if config['tipo']=='informe' and config.get('formato','texto')=='texto':
        lines.append('Formato: resumen de todos los registros del período. Para el detalle descargable elige Excel o PDF.')
    lines.append('No se activa hasta confirmar. No registra pagos, ventas, inventario ni transferencias.')
    return '\n'.join(lines)


def program_reply(action):
    data=action.argumentos
    text=describe(data['config']) if 'config' in data else f"{'Activar' if data['activo'] else 'Pausar'} programación #{data['id']}. Solo afecta futuros avisos; no modifica registros del negocio."
    return bot().BotReply(text,'preparar_programacion',proposal_id=str(action.pk),reply_markup={'inline_keyboard':[[
        {'text':'Confirmar programación','callback_data':f'confirm:{action.pk}'},{'text':'Cancelar','callback_data':f'cancel:{action.pk}'}]]})


def tool_prepare(profile,args,update=None):
    from mainApp.models import TelegramProgramacion
    config=validated(profile,args)
    if TelegramProgramacion.objects.filter(telegram_usuario=profile,activo=True).count()>=50:
        raise bot().TelegramClarification('Tienes 50 programaciones activas. Pausa alguna antes de añadir otra.')
    return program_reply(create_action(profile,'programacion_bot',{'config':config},'Autorizar '+args['tipo'],update))


def tool_change(profile,args,update=None):
    from mainApp.models import TelegramProgramacion
    rule=TelegramProgramacion.objects.filter(pk=args['programacion_id'],telegram_usuario=profile).first()
    if rule is None: raise bot().TelegramClarification('Esa programación no pertenece a tu cuenta.')
    if args['activo']: access(profile,rule.configuracion)
    else: require(profile,'home')
    return program_reply(create_action(profile,'programacion_bot',{'id':rule.pk,'activo':args['activo'],'version':fingerprint(rule)},'Cambiar aviso #'+str(rule.pk),update))


def confirm_program(profile,action):
    from mainApp.models import TelegramProgramacion,TelegramUsuario
    # Serializa el límite por cuenta, incluso con dos confirmaciones simultáneas.
    TelegramUsuario.objects.select_for_update().get(pk=profile.pk)
    data=action.argumentos
    if 'config' in data:
        config=validated(profile,data['config'])
        if TelegramProgramacion.objects.filter(telegram_usuario=profile,activo=True).count()>=50:
            raise bot().TelegramClarification('Ya tienes 50 programaciones activas; pausa alguna antes de añadir otra.')
        rule=TelegramProgramacion.objects.create(telegram_usuario=profile,tipo=config['tipo'],frecuencia=config['frecuencia'],configuracion=config)
    else:
        rule=TelegramProgramacion.objects.select_for_update().filter(pk=data['id'],telegram_usuario=profile).first()
        if rule is None or fingerprint(rule)!=data['version']:
            raise bot().TelegramClarification('La programación cambió. Solicita una propuesta nueva.')
        if data['activo']:
            access(profile,rule.configuracion)
            if not rule.activo and TelegramProgramacion.objects.filter(telegram_usuario=profile,activo=True).count()>=50:
                raise bot().TelegramClarification('Ya tienes 50 programaciones activas; pausa alguna primero.')
        else: require(profile,'home')
        rule.activo=data['activo']; rule.save(update_fields=['activo'])
    action.estado='CONFIRMADA'; action.resuelto_en=timezone.now(); action.save(update_fields=['estado','resuelto_en'])
    bot()._audit(profile,'confirmar_programacion',{'programacion_id':rule.pk,'activo':rule.activo})
    return f"Programación #{rule.pk} {'activa' if rule.activo else 'pausada'}. Requiere el mismo trabajador del bot con --followups; no se ejecutarán operaciones contables."


def tool_list(profile,args):
    from mainApp.models import TelegramProgramacion
    require(profile,'home')
    rules=TelegramProgramacion.objects.filter(telegram_usuario=profile).order_by('-pk')
    total=rules.count(); page,pages,offset=bot()._list_page(args,total)
    lines=[f'Tus programaciones flexibles · {total} · página {page}/{pages}']
    for rule in rules[offset:offset+bot().LIST_PAGE_SIZE]:
        try: access(profile,rule.configuracion)
        except PermissionDenied:
            lines.append(f'\n#{rule.pk} · permiso o cuenta no disponible; no se enviarán avisos. Puedes pausarla.')
            continue
        lines.append(f"\n#{rule.pk} · {'activa' if rule.activo else 'pausada'}\n"+describe(rule.configuracion).replace('No se activa hasta confirmar. ',''))
        last=rule.entregas.order_by('-pk').first()
        if last: lines.append('Última entrega: '+last.estado+' (no se reenvía automáticamente si su recepción es incierta).')
    if not total: lines.append('No has autorizado programaciones flexibles. Los seguimientos diarios anteriores se consultan por separado en /seguimientos.')
    return bot().BotReply('\n'.join(lines),'consultar_programaciones',pagination={'page':page,'pages':pages,'arguments':dict(args,pagina=page)})


def period(config,now):
    day=now.date(); name=config['periodo']; start=end=day
    if name=='ayer': start=end=day-timedelta(days=1)
    elif name in {'semana_actual','semana_anterior'}:
        start=day-timedelta(days=day.weekday())
        if name=='semana_anterior': start-=timedelta(days=7); end=start+timedelta(days=6)
    elif name in {'mes_actual','mes_anterior'}:
        start=day.replace(day=1)
        if name=='mes_anterior': end=start-timedelta(days=1); start=end.replace(day=1)
    return {'desde':start.isoformat(),'hasta':end.isoformat(),**({'sucursal':config['sucursal']} if config.get('sucursal') else {})}


def slot(rule,now):
    c=rule.configuracion; freq=rule.frecuencia
    if freq=='UNICA':
        stamp=parse_datetime(c['cuando'])
        return 'unica' if now>=stamp else None
    if freq=='EVENTO': return None
    if now.strftime('%H:%M')<c['hora']: return None
    if freq=='SEMANAL' and now.weekday()!=c['dia_semana']: return None
    if freq=='MENSUAL' and now.day!=(calendar.monthrange(now.year,now.month)[1] if c['dia_mes']==0 else c['dia_mes']): return None
    return now.date().isoformat()


def events(rule,now):
    from mainApp.models import LectorPTM,TurnoCaja
    c=rule.configuracion; kind=c['alerta']; result=[]
    if kind=='lector_ptm':
        for row in LectorPTM.objects.filter(activo=True).select_related('cuenta')[:100]:
            stamp=row.ultima_lectura or row.creado_en
            if now-stamp>=timedelta(minutes=c['umbral_minutos']):
                result.append((f'lector:{row.pk}:{stamp.isoformat()}',f'El lector PTM {row.nombre} no tiene una lectura desde {timezone.localtime(stamp,CO):%d/%m/%Y %H:%M}. Revisa navegador, sesión y conexión; no prueba una pérdida de dinero.'))
    else:
        rows=TurnoCaja.objects.select_related('cajero','puntopago__sucursalid')
        if c.get('sucursal'): rows=rows.filter(puntopago__sucursalid_id=c['sucursal'])
        if kind=='cierre_diferencia':
            rows=rows.filter(estado='CERRADO',fin__gte=max(rule.creado_en,now-timedelta(days=1))).exclude(diferencia_total=0)
        else: rows=rows.filter(estado__in=['ABIERTO','CIERRE'],inicio__lte=now-timedelta(minutes=c['umbral_minutos']))
        for row in rows.order_by('pk')[:100]:
            detail=f'cierre con diferencia {bot()._list_money(row.diferencia_total)}' if kind=='cierre_diferencia' else 'caja abierta más tiempo del configurado'
            result.append((f'{kind}:{row.pk}',f'Turno #{row.pk} · {row.puntopago.nombre} · {row.cajero.nombreusuario}: {detail}. Requiere revisión; no atribuye culpa ni genera cobros.'))
    return result


def scheduled_reply(rule,now):
    c=rule.configuracion; profile=rule.telegram_usuario
    if rule.tipo=='recordatorio': return bot().BotReply('Recordatorio autorizado: '+c['texto'],'recordatorio')
    args=period(c,now); kind=c['informe']
    if c.get('formato','texto')!='texto':
        from .telegram_reports import tool_export
        return tool_export(profile,dict(args,reporte=kind,formato=c['formato']))
    if kind=='resumen': return bot().BotReply(str(bot().tool_balance(profile,dict(args,detalle=True))),'informe_programado')
    if kind=='pagos': return bot().tool_expenses(profile,dict(args,detalle=False,desglose_por_medio=True))
    if kind=='ventas': return bot().BotReply(str(bot().tool_sales(profile,args)),'informe_programado')
    from mainApp.models import TurnoCaja
    from django.db.models import Count,Sum
    rows=TurnoCaja.objects.filter(estado='CERRADO',fin__date__range=(args['desde'],args['hasta']))
    if args.get('sucursal'): rows=rows.filter(puntopago__sucursalid_id=args['sucursal'])
    values=rows.aggregate(cantidad=Count('pk'),diferencia=Sum('diferencia_total'),deuda=Sum('deuda_total'))
    return bot().BotReply(f"Cierres del {args['desde']} al {args['hasta']}\n{values['cantidad']} turnos cerrados.\n"
        f"Diferencia neta registrada: {bot()._list_money(values['diferencia'])}\nDeuda registrada: {bot()._list_money(values['deuda'])}\n"
        'Resumen de todos los cierres del período. Las diferencias positivas y negativas pueden compensarse; revisa el Excel/PDF para ver cada turno.','informe_programado')


def process_programmations(*,now=None,client=None,limit=5):
    from mainApp.models import TelegramProgramacion,TelegramEntregaProgramada
    from .feature_flags import TELEGRAM_BOT_FEATURE,is_feature_enabled
    if not is_feature_enabled(TELEGRAM_BOT_FEATURE,fresh=True): return 0
    now=timezone.localtime(now or timezone.now(),CO); sent=0
    ids=list(TelegramProgramacion.objects.filter(activo=True,telegram_usuario__activo=True,telegram_usuario__usuario__is_active=True).filter(
        ultimo_chequeo__isnull=True).values_list('pk',flat=True)[:limit])
    if len(ids)<limit:
        ids+=list(TelegramProgramacion.objects.filter(activo=True,ultimo_chequeo__lte=now-timedelta(seconds=60)).exclude(pk__in=ids).order_by('ultimo_chequeo','pk').values_list('pk',flat=True)[:limit-len(ids)])
    for key in ids:
        with transaction.atomic():
            rule=TelegramProgramacion.objects.select_for_update().select_related('telegram_usuario__usuario').get(pk=key)
            if not rule.activo or rule.ultimo_chequeo and rule.ultimo_chequeo>now-timedelta(seconds=60): continue
            rule.ultimo_chequeo=now; rule.save(update_fields=['ultimo_chequeo'])
        try:
            access(rule.telegram_usuario,rule.configuracion)
            due=events(rule,now) if rule.frecuencia=='EVENTO' else [(slot(rule,now),None)]
            for event,text in due:
                if event is None or sent>=limit: break
                digest=hashlib.sha256((fingerprint(rule)+':'+event).encode()).hexdigest()
                with transaction.atomic():
                    current=TelegramProgramacion.objects.select_for_update().get(pk=rule.pk)
                    if not current.activo or fingerprint(current)!=fingerprint(rule): break
                    delivery,created=TelegramEntregaProgramada.objects.get_or_create(programacion=rule,clave=digest)
                if not created: continue
                sent+=1
                try:
                    current.refresh_from_db()
                    if not current.activo or fingerprint(current)!=fingerprint(rule) or not is_feature_enabled(TELEGRAM_BOT_FEATURE,fresh=True):
                        delivery.estado='OMITIDO'; delivery.save(update_fields=['estado']); continue
                    profile=rule.telegram_usuario; profile.refresh_from_db(); profile.usuario.refresh_from_db(); access(profile,rule.configuracion)
                    reply=bot().BotReply(text,'alerta_operativa') if text else scheduled_reply(rule,now)
                    bot()._send_bot_reply(client or bot().TelegramApiClient(),profile.telegram_chat_id,reply)
                    delivery.estado='ENVIADO'
                except Exception:
                    delivery.estado='ERROR_INCIERTO'
                delivery.save(update_fields=['estado'])
                if rule.frecuencia=='UNICA': TelegramProgramacion.objects.filter(pk=rule.pk).update(activo=False)
        except Exception:
            # Una regla inválida o un permiso revocado no detiene el chat.
            bot().logger.warning('Telegram programacion=no_disponible id=%s',key)
    return sent


PROPERTIES={'tipo':{'type':'STRING','enum':['recordatorio','informe','alerta']},'frecuencia':{'type':'STRING','enum':['UNICA','DIARIA','SEMANAL','MENSUAL','EVENTO']},
    'texto':{'type':'STRING'},'cuando':{'type':'STRING','description':'Fecha y hora completas ISO en Colombia; solo UNICA.'},'hora':{'type':'STRING','description':'HH:MM de Colombia.'},
    'dia_semana':{'type':'INTEGER','description':'0 lunes a 6 domingo.'},'dia_mes':{'type':'INTEGER','description':'1 a 31; 0 último día. No adelantar días inexistentes.'},
    'informe':{'type':'STRING','enum':['resumen','pagos','ventas','cierres']},'periodo':{'type':'STRING','enum':['hoy','ayer','semana_actual','semana_anterior','mes_actual','mes_anterior']},
    'formato':{'type':'STRING','enum':['texto','xlsx','pdf'],'description':'Texto envía resumen de todos los registros; XLSX/PDF envía detalle descargable (hasta 1.000 filas).'},'alerta':{'type':'STRING','enum':['cierre_diferencia','caja_abierta','lector_ptm']},'umbral_minutos':{'type':'INTEGER'},'sucursal':{'type':'STRING'}}
TOOL_DEFINITIONS=[
    {'name':'preparar_programacion','description':'Propone un recordatorio puntual, diario, semanal o mensual; informes por período explícito o alertas por evento. Solo si lo solicita el usuario, para su propio chat privado y con Confirmar. Preguntar fecha/hora/día/período/umbral si faltan. No ejecuta acciones contables.', 'parameters':{'type':'OBJECT','properties':PROPERTIES,'required':['tipo','frecuencia']}},
    {'name':'consultar_programaciones','description':'Lista recordatorios, informes flexibles y alertas autorizados por esta cuenta, con estado y última entrega. No cambia reglas.', 'parameters':{'type':'OBJECT','properties':{'pagina':{'type':'INTEGER'}}}},
    {'name':'preparar_cambio_programacion','description':'Propone activar o pausar una programación flexible propia por su ID real; requiere botón. No cambia sus fechas ni registra pagos.', 'parameters':{'type':'OBJECT','properties':{'programacion_id':{'type':'INTEGER'},'activo':{'type':'BOOLEAN'}},'required':['programacion_id','activo']}},
]
TOOL_FUNCTIONS={'preparar_programacion':tool_prepare,'consultar_programaciones':tool_list,'preparar_cambio_programacion':tool_change}

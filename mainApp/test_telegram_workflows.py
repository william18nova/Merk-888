"""Pruebas sin Telegram/IA reales ni base productiva de las ampliaciones operativas."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase, SimpleTestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .models import (CambioEgreso, DesglosePagosCierre, Egreso, MetodoPago, PuntosPago, Rol, Sucursal,
                     TelegramAccionPendiente, TelegramActualizacion, TelegramEntregaProgramada,
                     TelegramProgramacion, TelegramUsuario, TurnoCaja, Usuario, CuentaLecturaPTM, LectorPTM)
from .services import telegram_bot as bot
from .services import telegram_finance_workflows as finance, telegram_receipts as receipts, telegram_scheduling as scheduling
from .services import telegram_reports as reports
from .services.expense_reconciliation import CO
from .services.operational_expenses import register_operational_expense
from .services.telegram_proposals import ensure_proposal_buttons, restore_confirmation


class WorkflowFixture(TestCase):
    def setUp(self):
        cache.clear(); self.addCleanup(cache.clear)
        self.user = Usuario.objects.create_user('Prueba ampliaciones', rolid=Rol.objects.create(nombre='Web Master'))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=88442, telegram_chat_id=88442)
        self.cash = MetodoPago.objects.create(codigo='efectivo', nombre='Efectivo', activo=True, es_efectivo=True)
        self.nequi = MetodoPago.objects.create(codigo='nequi', nombre='Nequi', activo=True, aplica_4xmil_egresos=True)
        self.branch = Sucursal.objects.create(nombre='Yerbabuena de prueba')
        self.point = PuntosPago.objects.create(nombre='Caja pruebas', sucursalid=self.branch, dinerocaja=123456)
        self.api = SimpleNamespace(answer_callback=MagicMock(), send_message=MagicMock(), send_document=MagicMock(), download_photo=MagicMock())

    def message(self, text='', **changes):
        return TelegramActualizacion.objects.create(**dict(dict(update_id=88000+TelegramActualizacion.objects.count(),
            telegram_user_id=88442, telegram_chat_id=88442, chat_type='private', tipo='TEXTO', texto=text), **changes))

    def callback(self, data):
        return bot.build_reply(self.message(data, tipo='CALLBACK', callback_query_id='fake'), self.api)

    def close(self):
        turn = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, estado='CERRADO', fin=timezone.now())
        TurnoCaja.objects.filter(pk=turn.pk).update(inicio=timezone.make_aware(datetime(2026,11,3,14), CO))
        turn.refresh_from_db()
        DesglosePagosCierre.objects.create(turno=turn, registrado_por=self.user, total=70000, pagos=[{'nombre':'TURNO CAMILA','monto':'70000'}])
        return turn


class ClosingWorkflowTests(WorkflowFixture):
    def test_read_review_and_two_confirms_keep_date_cash_and_audit(self):
        turn = self.close()
        query = bot._execute_tool(self.profile,'consultar_conciliacion_cierres',{'desde':'2026-11-03','hasta':'2026-11-03'})
        self.assertIn('Sin coincidencia',query.text); self.assertIn('CAMILA',query.text)
        self.assertFalse(Egreso.objects.exists())
        proposal=self.callback(f'recon:{turn.pk}:0')
        self.assertIn('2026-11-03',proposal.text)
        self.assertEqual(ensure_proposal_buttons(self.message(),proposal).proposal_id,proposal.proposal_id)
        self.callback('confirm:'+proposal.proposal_id); self.callback('confirm:'+proposal.proposal_id)
        expense=Egreso.objects.get()
        self.assertEqual(timezone.localtime(expense.creado_en,CO).date(),date(2026,11,3))
        self.assertIn('CONCILIACION TELEGRAM CIERRE',expense.cambios.get().motivo)
        self.point.refresh_from_db(); self.assertEqual(self.point.dinerocaja,123456)
        result=finance.tool_reconcile(self.profile,{'desde':'2026-11-03','hasta':'2026-11-03'})
        self.assertIn('Conciliado con auditoría',result.text)

    def test_possible_duplicate_after_preview_blocks_new_expense(self):
        turn=self.close()
        proposal=finance.tool_prepare_closing(self.profile,{'turno_id':turn.pk,'fila':1},self.message())
        expense=register_operational_expense(user=self.user,concept='CAMILA',amount=70000,payment_method='efectivo')
        Egreso.objects.filter(pk=expense.pk).update(creado_en=turn.inicio)
        with self.assertRaises(bot.OperationalExpenseError): self.callback('confirm:'+proposal.proposal_id)
        self.assertEqual(Egreso.objects.count(),1)

    def test_read_limits_permissions_and_invalid_source(self):
        turn=self.close()
        for args in ({'desde':'2026-10-01','hasta':'2026-11-03'}, {'desde':'2026-11-01','hasta':'2027-03-01'}):
            with self.assertRaises(bot.TelegramClarification): finance.tool_reconcile(self.profile,args)
        DesglosePagosCierre.objects.filter(turno=turn).update(total=1)
        with self.assertRaises(bot.TelegramClarification): finance.tool_prepare_closing(self.profile,{'turno_id':turn.pk,'fila':1})
        self.profile.telegram_chat_id=-20
        with self.assertRaises(PermissionDenied): finance.tool_reconcile(self.profile,{})


class BatchWorkflowTests(WorkflowFixture):
    def batch(self, missing_method=False):
        phrase='Pagué a Aurora 10000 en efectivo y pagué a Zeta 20000 en efectivo'
        fields=[{'fragmento':'Pagué a Aurora 10000 en efectivo','concepto':'Aurora','monto_texto':'10000','medio_texto':'efectivo'},
                {'fragmento':'pagué a Zeta 20000 en efectivo','concepto':'Zeta','monto_texto':'20000','medio_texto':'efectivo'}]
        if missing_method: fields[0].pop('medio_texto')
        return bot._execute_tool(self.profile,'preparar_lote_pagos',{'pagos':fields},self.message(phrase))

    def test_separate_review_confirmation_and_parent_has_no_bulk_confirm(self):
        result=self.batch(); action=TelegramAccionPendiente.objects.get(pk=result.proposal_id)
        self.assertFalse(Egreso.objects.exists())
        canonical=ensure_proposal_buttons(self.message(),result)
        self.assertNotIn('confirm:',str(canonical.reply_markup))
        for i in (0,1):
            child=self.callback(f'bpay:o:{action.pk}:{i}')
            self.callback('confirm:'+child.proposal_id)
            self.callback('confirm:'+child.proposal_id)
            self.assertEqual(Egreso.objects.count(),i+1)
        action.refresh_from_db()
        self.assertIn('Confirmado',restore_confirmation(self.profile,action).text)

    def test_select_missing_method_and_cancel_parent_blocks_children(self):
        parent=self.batch(True)
        choice=self.callback(f'bpay:o:{parent.proposal_id}:0')
        self.assertIn('¿Con qué medio',choice.text)
        action=TelegramAccionPendiente.objects.get(pk=parent.proposal_id)
        index=action.argumentos['medios'].index('nequi')
        child=self.callback(f'bpay:m:{action.pk}:0:{index}')
        self.assertIn('4',child.text)
        self.callback('cancel:'+str(action.pk))
        self.assertIn('descartado',self.callback('confirm:'+child.proposal_id).text)
        self.assertFalse(Egreso.objects.exists())

    def test_cannot_ignore_extra_payment_or_fabricate_literal(self):
        args={'pagos':[{'fragmento':'Pagué a Aurora 10000','concepto':'Aurora','monto_texto':'10000'},
                        {'fragmento':'pagué a Zeta 20000','concepto':'Zeta','monto_texto':'20000'}]}
        for phrase in ('Pagué a Aurora 10000 y pagué a Zeta 20000 y pagué a Delta 30000',
                       'Pagué a Aurora 99999 y pagué a Zeta 20000'):
            with self.assertRaises(bot.TelegramClarification): finance.tool_prepare_batch(self.profile,args,self.message(phrase))
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_similar_already_registered_warns_and_wrong_owner_is_blocked(self):
        parent=self.batch()
        register_operational_expense(user=self.user,concept='AURORA',amount=10000,payment_method='efectivo')
        result=self.callback(f'bpay:o:{parent.proposal_id}:0')
        self.assertEqual(result.intent,'lote_posible_duplicado')
        other=TelegramUsuario.objects.create(usuario=Usuario.objects.create_user('Otra',rolid=self.user.rolid),telegram_user_id=77,telegram_chat_id=77)
        with self.assertRaises(bot.TelegramClarification), transaction_for_test():
            finance.active_action(other,parent.proposal_id,'lote_pagos')


def transaction_for_test():
    from django.db import transaction
    return transaction.atomic()


@override_settings(GEMINI_API_KEY='fixture')
class ReceiptWorkflowTests(WorkflowFixture):
    def photo(self):
        return self.message('Un comprobante ficticio',tipo='FOTO',photo_file_id='telegram-fake',photo_file_size=200)

    def review(self, fields=None):
        result=bot.build_reply(self.photo(),self.api)
        extracted={'etapa':'revision','concepto':'AURORA','monto':'10000','fecha_impresa':'2026-11-03','requiere_base':False}
        with patch.object(receipts,'read_image',return_value=fields or extracted):
            self.callback('rcpt:l:'+result.proposal_id)
        return TelegramAccionPendiente.objects.get(pk=result.proposal_id)

    def test_photo_does_not_download_or_send_without_per_photo_consent(self):
        with patch.object(receipts,'read_image') as read:
            result=bot.build_reply(self.photo(),self.api)
            ensure_proposal_buttons(self.message(),result)
        self.api.download_photo.assert_not_called(); read.assert_not_called()
        self.assertIn('Gemini',result.text); self.assertIn('Autorizar',str(result.reply_markup))
        self.assertNotIn('confirm:',str(result.reply_markup)); self.assertFalse(Egreso.objects.exists())

    def test_second_photo_waits_until_the_first_is_reviewed_or_discarded(self):
        first=bot.build_reply(self.photo(),self.api)
        result=bot.build_reply(self.photo(),self.api)
        self.assertIn('Ya tienes un comprobante pendiente',result.text)
        self.assertEqual(TelegramAccionPendiente.objects.count(),1)
        self.api.download_photo.assert_not_called()
        self.callback('cancel:'+first.proposal_id)
        following=bot.build_reply(self.photo(),self.api)
        self.assertNotEqual(following.proposal_id,first.proposal_id)

    def test_photo_review_method_date_and_double_confirm(self):
        action=self.review()
        self.api.download_photo.assert_called_once_with('telegram-fake')
        self.assertFalse(Egreso.objects.exists())
        self.assertNotIn('archivo',action.argumentos)
        mid=action.argumentos['medios'].index('nequi')
        self.callback(f'rcpt:m:{action.pk}:{mid}')
        self.callback(f'rcpt:d:{action.pk}:1')
        proposal=restore_confirmation(self.profile,TelegramAccionPendiente.objects.get(pk=action.pk))
        self.assertIn('10.040',proposal.text)
        self.assertIn('confirm:',str(proposal.reply_markup))
        self.callback('confirm:'+str(action.pk)); self.callback('confirm:'+str(action.pk))
        expense=Egreso.objects.get()
        self.assertEqual(expense.monto,Decimal('10040'))
        self.assertEqual(timezone.localtime(expense.creado_en,CO).date(),date(2026,11,3))
        self.assertIn('COMPROBANTE TELEGRAM',CambioEgreso.objects.get().motivo)

    def test_ocr_tax_ambiguity_blocks_confirm_until_literal_base(self):
        action=self.review({'etapa':'revision','concepto':'AURORA','monto':'10040','requiere_base':True})
        self.assertIn('Completa y revisa',self.callback('confirm:'+str(action.pk)).text)
        for data,phrase in [({'monto_texto':'10000'},'El valor base es 10000'),({'medio_texto':'nequi'},'Lo pagué por nequi'),({'fecha_texto':'hoy'},'Lo pagué hoy')]:
            result=bot._execute_tool(self.profile,'completar_comprobante',data,self.message(phrase))
        self.assertIn('confirm:',str(result.reply_markup))
        self.callback('confirm:'+str(action.pk)); self.assertEqual(Egreso.objects.get().monto,10040)

    def test_invented_values_unconsented_read_and_duplicate_are_rejected(self):
        photo=bot.build_reply(self.photo(),self.api)
        with self.assertRaises(bot.TelegramClarification):
            receipts.tool_complete(self.profile,{'concepto':'OTRA'},self.message('cambia el concepto a OTRA'))
        self.callback('cancel:'+photo.proposal_id)
        action=self.review()
        with self.assertRaises(bot.TelegramClarification):
            receipts.tool_complete(self.profile,{'monto_texto':'99999'},self.message('el valor es 10000'))
        self.callback(f"rcpt:m:{action.pk}:{action.argumentos['medios'].index('efectivo')}")
        self.callback(f'rcpt:d:{action.pk}:0')
        register_operational_expense(user=self.user,concept='AURORA',amount=10000,payment_method='efectivo')
        self.assertIn('parecido',self.callback('confirm:'+str(action.pk)).text)
        self.assertEqual(Egreso.objects.count(),1)

    def test_read_failure_is_not_automatically_retried_and_expiry_is_enforced(self):
        result=bot.build_reply(self.photo(),self.api)
        with patch.object(receipts,'read_image',side_effect=bot.TelegramClarification('No legible')) as read:
            self.assertIn('No legible',self.callback('rcpt:l:'+result.proposal_id).text)
            self.callback('rcpt:l:'+result.proposal_id)
        read.assert_called_once()
        TelegramAccionPendiente.objects.filter(pk=result.proposal_id).update(vence_en=timezone.now()-timedelta(seconds=1))
        self.assertIn('venció',self.callback('rcpt:d:'+result.proposal_id+':0').text)

    def test_date_can_be_spoken_without_model_calculating_it(self):
        data={}; today=timezone.localdate(timezone=CO)
        receipts.select_date(self.profile,data,'ayer')
        self.assertEqual(data['fecha_pago'],(today-timedelta(days=1)).isoformat())
        receipts.select_date(self.profile,data,'tres de noviembre de dos mil veintiséis')
        self.assertEqual(data['fecha_pago'],'2026-11-03')
        receipts.select_date(self.profile,data,'03/11/2026')
        self.assertEqual(data['fecha_pago'],'2026-11-03')
        for value in ('2026-02-31','treinta y uno de febrero de 2026','mañana','2029-11-03'):
            with self.assertRaises(bot.TelegramClarification): receipts.select_date(self.profile,{},value)

    @override_settings(TELEGRAM_TEXT_PROVIDERS='gemini')
    def test_pending_photo_does_not_turn_a_correction_into_a_new_payment(self):
        from .test_telegram_ai_output import gemini_calls
        action=self.review()
        phrase='Pagué a Aurora 10000 en efectivo'
        with patch.object(bot.requests,'post',return_value=gemini_calls(('completar_comprobante',
            {'concepto':'Aurora','monto_texto':'10000','medio_texto':'efectivo'}))):
            result=bot.build_reply(self.message(phrase),self.api)
        self.assertEqual(result.proposal_id,str(action.pk))
        self.assertEqual(TelegramAccionPendiente.objects.count(),1)
        self.assertFalse(Egreso.objects.exists())


class ReportTests(WorkflowFixture):
    def test_real_xlsx_and_pdf_safe_strings_permission_date_limits_and_no_writes(self):
        expense=register_operational_expense(user=self.user,concept='=FAKE("formula")',amount=12000,payment_method='nequi')
        snapshot=list(Egreso.objects.values())
        for kind in ('xlsx','pdf'):
            result=bot._execute_tool(self.profile,'exportar_reporte',{'reporte':'pagos','formato':kind})
            self.assertTrue(result.document.data.startswith(b'PK' if kind=='xlsx' else b'%PDF'))
            bot._send_bot_reply(self.api,self.profile.telegram_chat_id,result)
            self.api.send_document.assert_called_with(88442,result.document)
            if kind=='xlsx':
                from openpyxl import load_workbook
                wb=load_workbook(BytesIO(result.document.data),data_only=False)
                self.assertTrue(wb.active['C2'].value.startswith("'="))
                self.assertEqual(wb.active['F2'].value,float(expense.monto))
        self.assertEqual(list(Egreso.objects.values()),snapshot)
        for args in ({'sucursal':str(self.branch.pk)}, {'desde':'2026-01-01','hasta':'2026-11-07'}):
            with self.assertRaises(bot.TelegramClarification): reports.tool_export(self.profile,dict(reporte='pagos',formato='xlsx',**args))
        self.profile.telegram_chat_id=-1
        with self.assertRaises(PermissionDenied): reports.tool_export(self.profile,{'reporte':'pagos','formato':'xlsx'})

    def test_document_delivery_uncertain_does_not_reinterpret_or_repeat(self):
        result=reports.tool_export(self.profile,{'reporte':'pagos','formato':'pdf'})
        self.api.send_document.side_effect=bot.TelegramRateLimit(30)
        with self.assertRaises(bot.TelegramDeliveryUncertain): bot._send_bot_reply(self.api,88442,result)
        self.api.send_document.assert_called_once()
        self.assertFalse(Egreso.objects.exists())

    def test_all_supported_report_queries_have_valid_model_fields(self):
        for kind in ('ventas','cierres','inventario','conciliacion'):
            with self.subTest(kind=kind):
                headers,rows,*_=reports.table(self.profile,{'reporte':kind})
                self.assertTrue(headers); self.assertEqual(rows,[])

    def test_report_refuses_to_silently_truncate(self):
        for i in (1,2): register_operational_expense(user=self.user,concept=f'PRUEBA {i}',amount=1000*i,payment_method='efectivo')
        with patch.object(reports,'MAX_ROWS',1), self.assertRaises(bot.TelegramClarification):
            reports.tool_export(self.profile,{'reporte':'pagos','formato':'xlsx'})


class SchedulingTests(WorkflowFixture):
    def activate(self,**changes):
        config=(dict(tipo='alerta',frecuencia='EVENTO') if changes.get('tipo')=='alerta'
                else dict(tipo='recordatorio',frecuencia='DIARIA',hora='00:00',texto='Revisar facturas'))
        config.update(changes)
        proposal=bot._execute_tool(self.profile,'preparar_programacion',config,self.message('Recordatorio ficticio solicitado'))
        self.assertFalse(TelegramProgramacion.objects.exists())
        self.callback('confirm:'+proposal.proposal_id)
        return TelegramProgramacion.objects.get()

    def run_due(self,now):
        with patch('mainApp.services.feature_flags.is_feature_enabled',return_value=True):
            return scheduling.process_programmations(now=now,client=self.api)

    def test_schedule_requires_confirmation_sends_once_and_can_pause(self):
        rule=self.activate()
        now=timezone.localtime(timezone.now(),CO)
        self.assertEqual(self.run_due(now),1)
        self.assertEqual(self.run_due(now+timedelta(minutes=2)),0)
        self.api.send_message.assert_called_once()
        proposal=bot._execute_tool(self.profile,'preparar_cambio_programacion',{'programacion_id':rule.pk,'activo':False},self.message())
        rule.refresh_from_db(); self.assertTrue(rule.activo)
        self.callback('confirm:'+proposal.proposal_id)
        self.assertEqual(self.run_due(now+timedelta(days=1)),0)
        self.assertIn('pausada',scheduling.tool_list(self.profile,{}).text)
        self.assertFalse(Egreso.objects.exists())

    def test_uncertain_delivery_is_reserved_no_automatic_retry(self):
        rule=self.activate(); self.api.send_message.side_effect=bot.TelegramDeliveryUncertain('red')
        now=timezone.now()
        self.assertEqual(self.run_due(now),1); self.assertEqual(self.run_due(now+timedelta(minutes=2)),0)
        self.assertEqual(rule.entregas.get().estado,'ERROR_INCIERTO')
        self.api.send_message.assert_called_once()

    def test_one_off_and_monthly_calendar_semantics(self):
        rule=SimpleNamespace(frecuencia='MENSUAL',configuracion={'hora':'08:00','dia_mes':31})
        local=lambda y,m,d,h=9: timezone.make_aware(datetime(y,m,d,h),CO)
        self.assertIsNone(scheduling.slot(rule,local(2027,2,28)))
        rule.configuracion['dia_mes']=0
        self.assertEqual(scheduling.slot(rule,local(2028,2,29)),'2028-02-29')
        rule.frecuencia='SEMANAL'; rule.configuracion['dia_semana']=0
        self.assertIsNone(scheduling.slot(rule,local(2026,11,8)))
        self.assertEqual(scheduling.slot(rule,local(2026,11,9)),'2026-11-09')
        rule.frecuencia='UNICA'; rule.configuracion={'cuando':local(2026,11,9).isoformat()}
        self.assertIsNone(scheduling.slot(rule,local(2026,11,8)))
        self.assertEqual(scheduling.slot(rule,local(2026,11,10)),'unica')
        p=scheduling.period({'periodo':'mes_anterior'},local(2027,1,5))
        self.assertEqual(p,{'desde':'2026-12-01','hasta':'2026-12-31'})

    def test_incomplete_time_report_period_and_unsupported_scope_are_not_activated(self):
        base=dict(tipo='informe',frecuencia='DIARIA',informe='pagos',periodo='hoy',hora='21:00')
        for changes in ({'hora':'7:0'}, {'periodo':''}, {'sucursal':str(self.branch.pk)}, {'informe':'resumen','formato':'pdf'}):
            with self.assertRaises(bot.TelegramClarification): scheduling.validated(self.profile,dict(base,**changes))
        self.assertFalse(TelegramProgramacion.objects.exists())

    def test_event_alerts_once_per_incident_and_recovery_new_incident(self):
        turn=TurnoCaja.objects.create(cajero=self.user,puntopago=self.point,estado='ABIERTO')
        now=timezone.now(); TurnoCaja.objects.filter(pk=turn.pk).update(inicio=now-timedelta(hours=12))
        rule=self.activate(tipo='alerta',frecuencia='EVENTO',alerta='caja_abierta',umbral_minutos=60)
        self.assertEqual(self.run_due(now),1); self.assertEqual(self.run_due(now+timedelta(minutes=2)),0)
        self.assertIn(str(turn.pk),self.api.send_message.call_args.args[1])
        self.assertEqual(rule.entregas.count(),1)

    def test_scheduler_integration_and_permissions_revoked(self):
        self.activate(); self.profile.activo=False; self.profile.save(update_fields=['activo'])
        self.assertEqual(self.run_due(timezone.now()),0)
        from .services.telegram_followups import process_followups
        with patch('mainApp.services.telegram_followups.is_feature_enabled',return_value=True), patch.object(scheduling,'process_programmations',return_value=2) as process:
            self.assertEqual(process_followups(client=self.api),2)
        process.assert_called_once()

    def test_ptm_outage_is_one_incident_until_another_reading(self):
        account=CuentaLecturaPTM.objects.create(nombre='Prueba',usuario_ptm='USUARIO_FICTICIO')
        now=timezone.now()
        reader=LectorPTM.objects.create(cuenta=account,nombre='Lector pruebas',token_hash='0'*64,creado_por=self.user,
            vence_en=now+timedelta(days=1),ultima_lectura=now-timedelta(hours=1))
        rule=self.activate(tipo='alerta',frecuencia='EVENTO',alerta='lector_ptm',umbral_minutos=15)
        self.assertEqual(self.run_due(now),1)
        self.assertEqual(self.run_due(now+timedelta(minutes=2)),0)
        LectorPTM.objects.filter(pk=reader.pk).update(ultima_lectura=now)
        self.assertEqual(self.run_due(now+timedelta(minutes=3)),0)
        self.assertEqual(self.run_due(now+timedelta(minutes=20)),1)
        self.assertEqual(rule.entregas.count(),2)

    def test_closing_alert_ignores_historical_and_zero_difference(self):
        now=timezone.now()
        rule=self.activate(tipo='alerta',frecuencia='EVENTO',alerta='cierre_diferencia')
        for delta,difference in ((-2,-1000),(1,0),(1,-2000)):
            TurnoCaja.objects.create(cajero=self.user,puntopago=self.point,estado='CERRADO',
                fin=now+timedelta(minutes=delta),diferencia_total=difference)
        self.assertEqual(self.run_due(now+timedelta(minutes=3)),1)
        self.assertEqual(rule.entregas.count(),1)
        self.assertIn('2.000',self.api.send_message.call_args.args[1])

    def test_monthly_file_and_text_reports_have_complete_summary_not_first_page(self):
        config=dict(tipo='informe',frecuencia='MENSUAL',hora='21:00',dia_mes=0,informe='pagos',periodo='mes_actual',formato='xlsx')
        config=scheduling.validated(self.profile,config)
        rule=TelegramProgramacion.objects.create(telegram_usuario=self.profile,tipo='informe',frecuencia='MENSUAL',configuracion=config)
        self.assertTrue(scheduling.scheduled_reply(rule,timezone.localtime(timezone.now(),CO)).document)
        rule.configuracion=dict(config,informe='cierres',formato='texto')
        result=scheduling.scheduled_reply(rule,timezone.localtime(timezone.now(),CO))
        self.assertIn('Resumen de todos los cierres',result.text)


class PhotoAndDocumentValidationTests(SimpleTestCase):
    def test_photo_webhook_fields_are_bounded_and_malformed_sizes_rejected(self):
        payload={'update_id':1,'message':{'from':{'id':2},'chat':{'id':2,'type':'private'},'photo':[{'file_id':'small','file_size':10},{'file_id':'large','file_size':20}],'caption':'Texto'}}
        value=bot.normalize_webhook_update(payload)
        self.assertEqual((value['tipo'],value['photo_file_id'],value['texto']),('FOTO','large','Texto'))
        payload['message']['photo'][-1]['file_size']=True
        with self.assertRaises(bot.TelegramBotError): bot.normalize_webhook_update(payload)

    def test_real_image_validation_and_invalid_bytes(self):
        from PIL import Image
        out=BytesIO(); Image.new('RGB',(2,2),'white').save(out,format='PNG')
        self.assertEqual(receipts.image_mime(out.getvalue()),'image/png')
        for content in (b'fake',b'',b'x'*(receipts.PHOTO_LIMIT+1)):
            with self.assertRaises(bot.TelegramClarification): receipts.image_mime(content)

    def test_ocr_extracts_only_whitelisted_fields_with_no_tools(self):
        from PIL import Image
        import json
        out=BytesIO(); Image.new('RGB',(2,2)).save(out,format='PNG')
        fields=dict(concepto='AURORA',monto_texto='10000',fecha_impresa='2026-11-03',moneda='COP',incluye_4xmil=False)
        response=MagicMock(status_code=200)
        response.json.return_value={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(fields)}]}}]}
        with override_settings(GEMINI_API_KEY='fixture',GEMINI_MODEL='fixture-model'), patch.object(receipts,'http_request',return_value=response) as request:
            value=receipts.read_image(out.getvalue())
        self.assertEqual(value['monto'],'10000')
        payload=request.call_args.kwargs['json']
        self.assertNotIn('tools',payload)
        self.assertEqual(payload['generationConfig']['responseMimeType'],'application/json')
        self.assertNotIn('medio_pago',value); self.assertNotIn('fecha_pago',value)
        response.close.assert_called_once()

    def test_document_schema_filename_and_repr_do_not_leak_bytes(self):
        doc=reports.BotDocument('../escape.pdf','application/pdf',b'privatefake')
        self.assertNotIn('privatefake',repr(doc))
        reply=bot.BotReply('Reporte',document=doc)
        with self.assertRaises(bot.TelegramBotError): bot._validate_bot_reply(reply)


class WorkflowConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_receipt_confirmed_by_two_workers_creates_only_one_expense(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        user=Usuario.objects.create_user('Concurrencia comprobante',rolid=Rol.objects.create(nombre='Web Master'))
        profile=TelegramUsuario.objects.create(usuario=user,telegram_user_id=9911,telegram_chat_id=9911)
        MetodoPago.objects.create(codigo='efectivo',nombre='Efectivo',activo=True)
        action=finance.create_action(profile,'pago_comprobante',dict(etapa='revision',concepto='AURORA',monto='10000',
            medio_pago='efectivo',medios=['efectivo'],fecha_pago=timezone.localdate(timezone=CO).isoformat(),aplica_4xmil=False), 'Prueba',None)
        updates=[TelegramActualizacion.objects.create(update_id=i,telegram_user_id=9911,telegram_chat_id=9911,
            chat_type='private',tipo='CALLBACK',texto='confirm:'+str(action.pk)) for i in (99111,99112)]
        barrier=Barrier(2)
        def worker(index):
            try:
                own=TelegramUsuario.objects.select_related('usuario').get(pk=profile.pk)
                barrier.wait(timeout=10)
                return bot._handle_callback(updates[index],own,SimpleNamespace(answer_callback=MagicMock())).text
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(worker,(0,1)))
        self.assertEqual(Egreso.objects.count(),1)
        self.assertEqual(CambioEgreso.objects.count(),1)
        self.assertTrue(any('registrado' in r for r in results))
        self.assertTrue(any('ya estaba' in r for r in results))

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_schedulers_reserve_one_delivery_before_the_network(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        user=Usuario.objects.create_user('Concurrencia avisos',rolid=Rol.objects.create(nombre='Web Master'))
        profile=TelegramUsuario.objects.create(usuario=user,telegram_user_id=9922,telegram_chat_id=9922)
        rule=TelegramProgramacion.objects.create(telegram_usuario=profile,tipo='recordatorio',frecuencia='DIARIA',
            configuracion=dict(tipo='recordatorio',frecuencia='DIARIA',hora='00:00',texto='Revisar'))
        barrier=Barrier(2); api=SimpleNamespace(send_message=MagicMock()); now=timezone.now()
        def worker(_):
            try:
                barrier.wait(timeout=10)
                return scheduling.process_programmations(now=now,client=api)
            finally: connections.close_all()
        with patch('mainApp.services.feature_flags.is_feature_enabled',return_value=True), ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,(0,1)))
        self.assertEqual(sum(results),1); api.send_message.assert_called_once()
        self.assertEqual(rule.entregas.count(),1)


class WorkflowMigrationTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_0054_reverses_and_applies_on_disposable_postgres_without_losing_updates(self):
        from importlib import import_module
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader
        migration=import_module('mainApp.migrations.0054_telegram_workflows').Migration('0054_telegram_workflows','mainApp')
        with override_settings(MIGRATION_MODULES={}):
            state=MigrationLoader(None).project_state([('mainApp','0053_avance_cierre_caja')])
        original=TelegramActualizacion.objects.create(update_id=99880,tipo='TEXTO',texto='Fila anterior a la ampliación')
        with connection.schema_editor() as editor: migration.unapply(state,editor)
        with connection.cursor() as cursor:
            cursor.execute('SELECT texto FROM telegram_actualizaciones WHERE update_id = %s',[original.pk])
            self.assertEqual(cursor.fetchone()[0],'Fila anterior a la ampliación')
        with connection.schema_editor() as editor: migration.apply(state,editor)
        original.refresh_from_db(); self.assertEqual(original.photo_file_id,'')
        self.assertEqual(original.texto,'Fila anterior a la ampliación')
        self.assertIn(TelegramProgramacion._meta.db_table,connection.introspection.table_names())

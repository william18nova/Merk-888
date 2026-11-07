"""Contratos de lenguaje libre: IA/voz simuladas, solo datos ficticios locales.

No miden la precisión real de un modelo. Verifican catálogo completo, argumentos,
contexto y barreras del servidor para TODAS las capacidades del bot.
"""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import (Egreso, MetodoPago, Rol, TelegramAccionPendiente, TelegramActualizacion,
                     TelegramUsuario, Usuario)
from .services import telegram_bot as bot
from .services.telegram_ai_output import AIOutputError, AIToolSelection, validate_plain_reply
from .services.telegram_conversation import conversation_history
from .services.telegram_expense_language import PaymentContext, payment_context
from .test_telegram_ai_output import chat_calls, gemini_calls, response


def chat_json(value):
    return response({'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]})


# Cada función debe seguir accesible aunque el usuario no nombre un comando.
CASES = [
    ('consultar_ventas', 'Oye, de lo que movimos ayer dime solamente cuánto entró en ventas.', {'desde': '2026-11-05', 'hasta': '2026-11-05'}),
    ('buscar_producto', 'Estoy mirando arroz Diana; necesito saber a cómo lo estamos vendiendo.', {'consulta': 'arroz Diana', 'vista': 'precio'}),
    ('consultar_inventario', 'Antes de surtir quisiera saber cuántos arroces Diana nos quedan en Yerbabuena.', {'consulta': 'arroz Diana', 'sucursal': 'Yerbabuena'}),
    ('consultar_pagos', 'Para comparar, tráeme lo que le hemos soltado a Papelería Sol en Nequi desde el lunes.', {'concepto': 'Papelería Sol', 'medio_pago': 'nequi', 'desde': '2026-11-02', 'hasta': '2026-11-06', 'detalle': True}),
    ('listar_empleados', 'Necesito mirar la gente que tenemos trabajando en Yerbabuena, con sus datos.', {'sucursal': 'Yerbabuena', 'detalle': True}),
    ('consultar_balance', 'De lo vendido hoy réstale lo pagado y cuéntame el resultado.', {}),
    ('consultar_turnos', 'Revisa quién sigue con una caja sin cerrar, porfa.', {'estado': 'ABIERTO'}),
    ('preparar_registro_pago', 'Por la mensajería ya salieron 25000 pesos en efectivo; déjame apuntado eso.', {'concepto': 'mensajería', 'monto_texto': '25000 pesos', 'medio_texto': 'efectivo'}),
    ('consultar_ayuda', 'Apenas estoy aprendiendo a usar esto, explícame con qué me puedes colaborar.', {}),
    ('consultar_cuenta', 'Oye, ¿bajo qué usuario del negocio te estoy hablando ahora?', {}),
    ('recuperar_botones', 'Quiero revisar lo que está sin guardar para poder descartarlo; tráeme esa solicitud.', {}),
    ('preparar_clave_merk2', 'Para el cliente Merk2 necesito la autorización especial que se usa una sola vez; prepara una.', {}),
    ('planificar_reabastecimiento', 'Ayúdame a mirar en Yerbabuena qué se acabó y con quién lo puedo surtir.', {'sucursal': 'Yerbabuena', 'stock_max': 0}),
    ('preparar_movimiento_inventario', 'Conté arroz Diana en Yerbabuena y hay doce; déjalo en doce, por ajuste del conteo físico.', {'producto': 'arroz Diana', 'sucursal': 'Yerbabuena', 'cantidad': 12, 'modo': 'fijar', 'motivo': 'ajuste del conteo físico'}),
    ('preparar_pedido_proveedor', 'Arma para Yerbabuena con Distribuidora Sol dos arroces Diana; por ahora solo déjalo esperando.', {'proveedor': 'Distribuidora Sol', 'sucursal': 'Yerbabuena', 'productos': [{'producto': 'arroz Diana', 'cantidad': 2}]}),
    ('preparar_alias', 'Cuando diga agüita quiero que recuerdes el producto número ocho con ese nombre.', {'entidad': 'producto', 'alias': 'agüita', 'registro_id': 8}),
    ('consultar_aliases', '¿Cuáles nombres distintos te pedí que recordaras para mí?', {}),
    ('preparar_seguimiento', 'Quisiera que todos los días a las nueve de la noche me cuentes cómo estuvo el negocio.', {'tipo': 'resumen_diario', 'hora': '21:00'}),
    ('consultar_seguimientos', 'Muéstrame qué reportes quedaron programados para mandarme y cuáles están pausados.', {}),
    ('consultar_resultado', 'De los que acabas de mostrarme quiero mirar el que está en segundo lugar.', {'posicion': 2}),
    ('analizar_balance', 'Explícame por qué lo que queda entre ventas y pagos se ve distinto frente al período anterior.', {}),
    ('resolver_tarea', 'Primero búscame al proveedor Sol y después déjame mirar ese resultado.', {'pasos': [{'herramienta': 'consultar_registros', 'argumentos_json': '{"recurso":"proveedores","consulta":"Sol"}'}]}),
    ('consultar_pago', 'Del egreso número cuarenta y dos quiero saber quién le cambió algo y qué fue.', {'pago_id': 42, 'historial': True}),
    ('preparar_edicion_pago', 'En el egreso cuarenta y dos me equivoqué digitando: eran treinta mil, corrige únicamente ese valor.', {'pago_id': 42, 'monto': 30000, 'motivo': 'me equivoqué digitando'}),
    ('consultar_datos', 'Quiero sumar los cobros de Nequi recibidos hoy por sucursal, sin inventar un saldo bancario.', {'fuente': 'cobros_ventas'}),
    ('consultar_informe', 'Entre la gente que atendió este mes, ¿quién fue el que más vendió?', {'fuente': 'ventas', 'agrupar': 'empleado', 'primeros': 1}),
    ('consultar_resumen_negocio', 'Dame una mirada general de cómo marcha el negocio en este momento.', {}),
    ('consultar_pendientes', 'Revisa qué cosas te pedí pero todavía están esperando mi decisión.', {}),
    ('consultar_varias', 'En una sola respuesta necesito lo vendido y lo pagado hoy, por separado.', {'consultas': [{'herramienta': 'consultar_ventas', 'argumentos_json': '{}'}, {'herramienta': 'consultar_pagos', 'argumentos_json': '{}'}]}),
    ('continuar_consulta', 'Con lo mismo que acabamos de revisar, ahora hazlo para ayer y no cambies los demás filtros.', {'periodo': 'ayer'}),
    ('consultar_horarios_empleados', '¿A qué hora le toca entrar mañana a Camila? No me mandes el resto de la jornada.', {'empleado': 'Camila', 'desde': '2026-11-07', 'hasta': '2026-11-07', 'vista': 'entrada'}),
    ('preparar_turno_empleado', 'A Camila ponle trabajo el siete de noviembre de 2026 desde las siete de la mañana hasta las dos de la tarde.', {'operacion': 'crear', 'empleado': 'Camila', 'inicio': '2026-11-07T07:00', 'fin': '2026-11-07T14:00'}),
    ('preparar_devolucion_venta', 'De la factura ciento veinte me devolvieron dos unidades del producto ocho; prepara esa devolución.', {'venta_id': 120, 'productos': [{'producto_id': 8, 'cantidad': 2}]}),
    ('consultar_registros', 'Necesito encontrar al proveedor que tiene Sol en el nombre de la empresa.', {'recurso': 'proveedores', 'consulta': 'Sol'}),
    ('consultar_detalle_operativo', 'Averíguame qué cliente quedó en la factura ciento veinte, eso solamente.', {'tipo': 'venta', 'id': 120, 'vista': 'cliente'}),
    ('ranking_productos', '¿Qué mercancía es la que menos se ha movido por unidades este mes?', {'orden': 'cantidad', 'ascendente': True}),
    ('buscar_vistas', 'Quiero llegar a la pantalla donde se hacen los cierres; pásame la dirección.', {'consulta': 'cierre caja'}),
    ('preparar_cambio_catalogo', 'Hace falta una categoría llamada Bebidas de prueba; déjame crear esa categoría.', {'entidad': 'categoria', 'operacion': 'crear', 'campos': [{'campo': 'nombre', 'valor': 'Bebidas de prueba'}]}),
    ('consultar_capacidades', 'Antes de añadir un proveedor, dime cuáles datos necesitas para su ficha.', {'entidad': 'proveedor'}),
]


class NaturalLanguageValidationTests(SimpleTestCase):
    def test_every_tool_has_a_text_and_voice_contract_case(self):
        self.assertEqual({name for name, _, _ in CASES}, set(bot.TOOL_FUNCTIONS))
        self.assertEqual(len(CASES), len(bot.TOOL_FUNCTIONS))

    def test_fake_completed_actions_are_rejected_for_all_domains(self):
        for text in ('Ya creé el proveedor.', 'Listo, actualicé el inventario.', 'He cancelado el turno.',
                     'Guardé el cambio del catálogo.', 'Programé tu reporte.', 'Preparé el pedido.',
                     'Ya generé la clave de Merk2.', 'Asigné el horario solicitado.'):
            with self.subTest(text=text), self.assertRaises(AIOutputError) as caught:
                validate_plain_reply(text)
            self.assertEqual(caught.exception.reason, 'missing_action')
        for text in ('¿Qué proveedor quieres usar?', 'No he guardado ningún cambio.', '¿A qué hora termina?'):
            self.assertEqual(validate_plain_reply(text), text)

    def test_linking_codes_and_button_events_are_not_model_history(self):
        def turn(text, intent='respuesta_ia', kind='TEXTO'):
            return SimpleNamespace(texto=text, transcripcion='', respuesta='Respuesta', intencion=intent, tipo=kind)
        history = conversation_history([
            turn('Y ahora solo Nequi'), turn('confirm:uuid', kind='CALLBACK'),
            turn('/vincular NOVA-ABCDEFGH', 'vincular'), turn('Este es mi código NOVA-ABCDEFGH'),
            turn('Quiero ver mis pagos'),
        ])
        self.assertEqual([item['text'] for item in history if item['role'] == 'user'], ['Quiero ver mis pagos', 'Y ahora solo Nequi'])
        self.assertNotIn('NOVA-', str(history))

    @override_settings(GEMINI_API_KEY='', GROQ_API_KEY='fixture', CEREBRAS_API_KEY='fixture', OPENROUTER_API_KEY='fixture')
    def test_all_compatible_providers_select_from_complete_catalog_before_schemas(self):
        phrase = 'Deja la existencia de arroz en doce por el conteo físico.'
        token = payment_context.set(PaymentContext(phrase))
        self.addCleanup(payment_context.reset, token)
        for function in (bot._groq_function_call, bot._cerebras_function_call, bot._openrouter_function_call):
            with self.subTest(function=function.__name__), patch.object(bot.requests, 'post',
                    return_value=chat_json({'herramientas': ['preparar_movimiento_inventario']})) as post:
                result = function(phrase)
                self.assertEqual(result, AIToolSelection(('preparar_movimiento_inventario',)))
                payload = post.call_args.kwargs['json']
                self.assertNotIn('tools', payload)
                self.assertEqual(payload['messages'][-1]['content'], phrase)
                for name in bot.TOOL_FUNCTIONS:
                    if name != 'consultar_varias':
                        self.assertIn('"name":"' + name + '"', payload['messages'][0]['content'])


@override_settings(GEMINI_API_KEY='fixture', GROQ_API_KEY='fixture', CEREBRAS_API_KEY='', OPENROUTER_API_KEY='', TELEGRAM_TEXT_PROVIDERS='gemini,groq')
class NaturalLanguageFlowTests(TestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        self.user = Usuario.objects.create_user('Usuario lenguaje libre', rolid=Rol.objects.create(nombre='Web Master'))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=54321, telegram_chat_id=54321)
        self.client_stub = SimpleNamespace(send_message=MagicMock(), answer_callback=MagicMock())
        MetodoPago.objects.create(codigo='efectivo', nombre='Efectivo', activo=True, es_efectivo=True)

    def message(self, text, voice=False, **changes):
        data = dict(update_id=540000 + TelegramActualizacion.objects.count(), telegram_user_id=54321,
                    telegram_chat_id=54321, chat_type='private', tipo='VOZ' if voice else 'TEXTO',
                    texto='' if voice else text, transcripcion=text if voice else '')
        return TelegramActualizacion.objects.create(**dict(data, **changes))

    def test_all_capabilities_dispatch_same_interpretation_in_text_and_voice(self):
        for name, phrase, arguments in CASES:
            for voice in (False, True):
                update = self.message(phrase, voice)
                with self.subTest(name=name, voice=voice), patch.object(bot.requests, 'post',
                        return_value=gemini_calls((name, arguments))) as post, patch.object(bot, '_execute_tool',
                        return_value=bot.BotReply('Resultado verificado por el servidor')) as execute:
                    reply = bot.build_reply(update, self.client_stub)
                    self.assertEqual(reply.text, 'Resultado verificado por el servidor')
                    execute.assert_called_once()
                    self.assertEqual(execute.call_args.args[:2], (self.profile, name))
                    expected = arguments if name != 'preparar_registro_pago' else {'concepto': 'mensajería', 'monto': '25000', 'medio_pago': 'efectivo'}
                    self.assertEqual(execute.call_args.args[2], expected)
                    post.assert_called_once()
                    payload = post.call_args.kwargs['json']
                    self.assertEqual({item['name'] for item in payload['tools'][0]['functionDeclarations']}, set(bot.TOOL_FUNCTIONS))
                    self.assertEqual(payload['contents'][-1]['parts'][0]['text'], phrase)
                    self.assertIsNone(payment_context.get())
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    @override_settings(GEMINI_API_KEY='', TELEGRAM_TEXT_PROVIDERS='groq')
    def test_semantic_router_then_arguments_keep_original_request_and_filters(self):
        phrase = 'Fija el arroz Diana en doce en Yerbabuena porque hice conteo físico, nada más.'
        args = {'producto': 'arroz Diana', 'sucursal': 'Yerbabuena', 'cantidad': 12, 'modo': 'fijar', 'motivo': 'hice conteo físico'}
        replies = [chat_json({'herramientas': ['preparar_movimiento_inventario']}), chat_calls(('preparar_movimiento_inventario', args))]
        with patch.object(bot.requests, 'post', side_effect=replies) as post, patch.object(bot, '_execute_tool', return_value=bot.BotReply('Propuesta')) as execute:
            update = self.message(phrase, True)
            bot.build_reply(update, self.client_stub)
        self.assertEqual(post.call_count, 2)
        first, second = [call.kwargs['json'] for call in post.call_args_list]
        self.assertNotIn('tools', first)
        self.assertEqual({item['function']['name'] for item in second['tools']}, {'preparar_movimiento_inventario'})
        self.assertEqual(first['messages'][-1], second['messages'][-1])
        execute.assert_called_once_with(self.profile, 'preparar_movimiento_inventario', args, update)

    @override_settings(GEMINI_API_KEY='', CEREBRAS_API_KEY='fixture', TELEGRAM_TEXT_PROVIDERS='groq,cerebras')
    def test_selected_schemas_are_reused_when_the_interpreter_provider_fails(self):
        phrase = 'Revisa qué gente tenemos en la sucursal y dame esa lista por favor.'
        replies = [chat_json({'herramientas': ['listar_empleados']}), response({}, 503), chat_calls(('listar_empleados', {'sucursal': 'Yerbabuena'}))]
        with patch.object(bot.requests, 'post', side_effect=replies) as post, patch.object(bot, '_execute_tool', return_value=bot.BotReply('Resultado')):
            self.assertEqual(bot.build_reply(self.message(phrase), self.client_stub).text, 'Resultado')
        self.assertEqual(post.call_count, 3)
        last = post.call_args.kwargs['json']
        self.assertEqual({item['function']['name'] for item in last['tools']}, {'listar_empleados'})

    def test_missing_data_question_keeps_request_for_a_natural_followup(self):
        phrase = 'Para el conteo deja arroz Diana en doce en Yerbabuena.'
        args = {'producto': 'arroz Diana', 'sucursal': 'Yerbabuena', 'cantidad': 12, 'modo': 'fijar'}
        first = self.message(phrase)
        with patch.object(bot.requests, 'post', return_value=gemini_calls(('preparar_movimiento_inventario', args))), patch.object(bot, '_execute_tool') as execute:
            reply = bot.build_reply(first, self.client_stub)
            self.assertIn('motivo', reply.text)
            execute.assert_not_called()
        first.estado, first.intencion, first.respuesta, first.procesado_en = 'PROCESADO', reply.intent, reply.text, timezone.now()
        first.save()
        answer = self.message('Es porque terminamos el conteo físico.', True)
        complete = dict(args, motivo='terminamos el conteo físico')
        with patch.object(bot.requests, 'post', return_value=gemini_calls(('preparar_movimiento_inventario', complete))) as post, patch.object(bot, '_execute_tool', return_value=bot.BotReply('Propuesta')) as execute:
            bot.build_reply(answer, self.client_stub)
        self.assertIn(phrase, str(post.call_args.kwargs['json']['contents']))
        execute.assert_called_once_with(self.profile, 'preparar_movimiento_inventario', complete, answer)

    def test_natural_confirm_or_discard_only_restores_real_pending_buttons(self):
        original = bot._execute_tool(self.profile, 'preparar_registro_pago', {'concepto': 'PRUEBA', 'monto': 1000, 'medio_pago': 'efectivo'}, self.message('Preparación ficticia'))
        action = TelegramAccionPendiente.objects.get()
        for phrase in ('Ya revisé eso, puedes confirmarlo.', 'Descarta lo que aún tengo sin guardar, por favor.'):
            for voice in (False, True):
                with self.subTest(phrase=phrase, voice=voice), patch.object(bot.requests, 'post', return_value=gemini_calls(('recuperar_botones', {}))):
                    result = bot.build_reply(self.message(phrase, voice), self.client_stub)
                self.assertEqual(result.proposal_id, original.proposal_id)
                self.assertEqual(result.reply_markup, original.reply_markup)
                action.refresh_from_db()
                self.assertEqual(action.estado, 'PENDIENTE')
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())

    def test_account_and_help_controls_use_real_profile_and_no_mutations(self):
        for name, phrase, expected in [('consultar_cuenta', '¿Bajo qué usuario estoy conectado contigo?', self.user.nombreusuario),
                                      ('consultar_ayuda', 'Cuéntame de qué maneras me ayudas con el negocio.', 'no necesitas memorizar comandos')]:
            with patch.object(bot.requests, 'post', return_value=gemini_calls((name, {}))):
                self.assertIn(expected, bot.build_reply(self.message(phrase, True), self.client_stub).text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_natural_request_still_checks_current_domain_permissions(self):
        cashier = Rol.objects.create(nombre='Cajero')
        self.user.rolid = cashier
        self.user.save()
        with patch.object(bot.requests, 'post', return_value=gemini_calls(('preparar_clave_merk2', {}))), self.assertRaises(PermissionDenied):
            bot.build_reply(self.message('Necesito la autorización especial de Merk2, la que dura una sola vez.'), self.client_stub)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_link_codes_are_handled_locally_and_never_sent_to_ai(self):
        update = self.message('Vincula mi cuenta con el código NOVA-ABCDEFGH')
        with patch.object(bot, 'link_telegram_identity', return_value=self.profile) as link, patch.object(bot, '_intelligent_function_call') as ai:
            self.assertEqual(bot.build_reply(update, self.client_stub).intent, 'vincular')
        self.assertEqual(link.call_args.kwargs['code'], 'NOVA-ABCDEFGH')
        ai.assert_not_called()
        with patch.object(bot, '_intelligent_function_call') as ai:
            self.assertEqual(bot.build_reply(self.message('Este es el código NOVA-ABCDEFGH pero no sé qué hacer'), self.client_stub).intent, 'vincular')
        ai.assert_not_called()

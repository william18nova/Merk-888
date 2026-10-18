from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import DataError, DatabaseError, IntegrityError, ProgrammingError
from django.test import SimpleTestCase, TestCase

from .models import Egreso, MetodoPago, Rol, TelegramAccionPendiente, TelegramActualizacion, TelegramUsuario, Usuario
from .services import telegram_bot as bot
from .services.telegram_runtime import SourceWatcher


class SourceWatcherTests(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'mainApp').mkdir()
        self.source = self.root / 'mainApp/models.py'
        self.source.write_text('initial')
        self.now = 0
        self.watcher = SourceWatcher(self.root, interval=1, settle=2, clock=lambda: self.now)

    def tick(self):
        self.now += 1
        return self.watcher.poll()

    def test_unchanged_sources_do_not_reload(self):
        for _ in range(4):
            self.assertEqual(self.tick(), 'ready')

    def test_updated_source_waits_for_stable_deployment(self):
        self.source.write_text('new release with tax fields')
        self.assertEqual(self.tick(), 'waiting')
        self.assertEqual(self.tick(), 'waiting')
        self.source.write_text('new release with another change')
        self.assertEqual(self.tick(), 'waiting')
        self.assertEqual(self.tick(), 'waiting')
        self.assertEqual(self.tick(), 'reload')

    def test_new_deleted_files_and_disabled_bot_are_detected(self):
        extra = self.root / 'mainApp/new_module.py'
        extra.write_text('new')
        self.assertEqual(self.tick(), 'waiting')
        extra.unlink()
        self.assertEqual(self.tick(), 'ready')
        self.source.unlink()
        self.assertEqual(self.tick(), 'waiting')
        self.tick()
        self.assertEqual(self.tick(), 'reload')

    def test_logs_and_tests_do_not_trigger_restart(self):
        (self.root / 'mainApp/test_example.py').write_text('test')
        (self.root / 'mainApp/telegram.log').write_text('log')
        self.assertEqual(self.tick(), 'ready')

    def test_temporarily_unreadable_deployment_pauses(self):
        with patch.object(self.watcher, 'snapshot', side_effect=OSError('copy in progress')):
            self.assertEqual(self.tick(), 'waiting')
        self.assertEqual(self.tick(), 'ready')

    def test_worker_reloads_between_messages_not_inside_them(self):
        events = []
        watcher = MagicMock()
        watcher.poll.side_effect = ['ready', 'waiting', 'reload']
        module = 'mainApp.management.commands.procesar_telegram_bot.'
        with patch(module + 'SourceWatcher', return_value=watcher), \
                patch(module + 'recover_stale_updates'), patch(module + 'close_old_connections'), \
                patch(module + 'is_feature_enabled', return_value=True), \
                patch(module + 'time.sleep'), \
                patch(module + 'process_next_update', side_effect=lambda: events.append('finished message') or True) as process, \
                patch(module + 'os.execv', side_effect=lambda *args: events.append('restart')) as restart:
            call_command('procesar_telegram_bot', reload=True, stdout=StringIO())
        process.assert_called_once()
        restart.assert_called_once()
        self.assertEqual(events, ['finished message', 'restart'])
        self.assertEqual(restart.call_args.args[0], restart.call_args.args[1][0])

    def test_once_never_restarts_or_creates_a_watcher(self):
        module = 'mainApp.management.commands.procesar_telegram_bot.'
        with patch(module + 'SourceWatcher') as watcher, patch(module + 'recover_stale_updates'), \
                patch(module + 'close_old_connections'), patch(module + 'is_feature_enabled', return_value=True), \
                patch(module + 'process_next_update', return_value=False), patch(module + 'os.execv') as restart:
            call_command('procesar_telegram_bot', once=True, reload=True, stdout=StringIO())
        watcher.assert_not_called()
        restart.assert_not_called()


def stale_worker_error():
    cause = Exception('PRIVATE_SQL_AND_VALUES')
    cause.pgcode = '23502'
    cause.diag = SimpleNamespace(table_name='egresos', column_name='aplica_4xmil')
    error = IntegrityError('PRIVATE_SQL_AND_VALUES')
    error.__cause__ = cause
    return error


class TelegramDatabaseErrorTests(TestCase):
    def test_schema_and_integrity_errors_are_not_retried_three_times(self):
        for number, error in enumerate((stale_worker_error(), ProgrammingError('private'),
                                        IntegrityError('private'), DataError('private')), start=970000):
            with self.subTest(error=type(error).__name__):
                update = TelegramActualizacion.objects.create(update_id=number, telegram_user_id=1,
                    telegram_chat_id=1, chat_type='private', tipo='TEXTO', texto='Solicitud de prueba')
                client = SimpleNamespace(send_message=MagicMock())
                with patch.object(bot, 'is_feature_enabled', return_value=True), \
                        patch.object(bot, 'TelegramApiClient', return_value=client), \
                        patch.object(bot, 'build_reply', side_effect=error), patch.object(bot, 'logger'):
                    self.assertTrue(bot.process_next_update())
                update.refresh_from_db()
                self.assertEqual(update.estado, 'ERROR')
                self.assertEqual(update.intentos, 1)
                client.send_message.assert_called_once()
                message = client.send_message.call_args.args[1]
                self.assertNotIn('PRIVATE_SQL_AND_VALUES', message)
                self.assertNotIn('private', message)

    def test_known_stale_model_has_useful_safe_explanation(self):
        message = bot._user_error_message(stale_worker_error(), None)
        self.assertIn('actualizar y reiniciar', message)
        self.assertNotIn('aplica_4xmil', message)
        self.assertEqual(bot._database_failure_kind(DatabaseError('connection')), 'temporary')

    def test_payment_and_tax_are_saved_once_with_current_model(self):
        user = Usuario.objects.create_user('Prueba nueva version', rolid=Rol.objects.create(nombre='Web Master'))
        profile = TelegramUsuario.objects.create(usuario=user, telegram_user_id=2, telegram_chat_id=2)
        for method, taxed, expected in (('efectivo', False, '100000.00'), ('nequi', True, '100400.00')):
            MetodoPago.objects.create(codigo=method, nombre=method, activo=True, aplica_4xmil_egresos=taxed)
            reply = bot.tool_prepare_expense(profile, {'concepto': 'AGUA', 'monto': 100000, 'medio_pago': method})
            action = TelegramAccionPendiente.objects.get(pk=reply.proposal_id)
            callback = SimpleNamespace(texto=f'confirm:{action.pk}', callback_query_id='test-only')
            client = SimpleNamespace(answer_callback=MagicMock())
            bot._handle_callback(callback, profile, client)
            bot._handle_callback(callback, profile, client)
            expense = Egreso.objects.get(medio_pago=method)
            self.assertEqual(str(expense.monto), expected)
            self.assertIs(expense.aplica_4xmil, taxed)
            self.assertIsNotNone(expense.impuesto_4xmil)
        self.assertEqual(Egreso.objects.count(), 2)

    def test_tax_overflow_asks_for_valid_amount_instead_of_internal_error(self):
        user = Usuario.objects.create_user('Prueba limites', rolid=Rol.objects.create(nombre='Web Master'))
        profile = TelegramUsuario.objects.create(usuario=user, telegram_user_id=3, telegram_chat_id=3)
        MetodoPago.objects.create(codigo='nequi', nombre='Nequi', activo=True, aplica_4xmil_egresos=True)
        with self.assertRaises(bot.TelegramBotError):
            bot.tool_prepare_expense(profile, {'concepto': 'AGUA', 'monto': '999999999999', 'medio_pago': 'nequi'})
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        self.assertFalse(Egreso.objects.exists())

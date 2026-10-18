"""No cloud/business database access. Packaging and first-run safety regression."""
import ast
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

from scripts.build_windows_integrated import eligible, extract, settings_source
from windows_installer import configuration
from windows_installer import engines, wsl_setup


class PackageSafetyTests(unittest.TestCase):
    def test_initialization_error_does_not_assume_windows_block_for_other_errors(self):
        from local_pos.integrated_prepare import initialization_failure
        self.assertIn('Control de aplicaciones (4551)', initialization_failure('plpgsql.dll unknown error 4551'))
        self.assertIn('distinguir permisos', initialization_failure('could not load plpgsql.dll permission denied'))
        self.assertNotIn('Windows bloqueó', initialization_failure('file not found'))

    def test_embedded_secrets_are_removed_but_app_settings_preserved(self):
        source = '''
SECRET_KEY = "secret-key-that-must-never-be-packaged-0123456789"
DATABASES = {"default": {"PASSWORD": "private-db-password", "HOST": "private-host"}}
POS_AGENT_TOKEN = os.getenv("POS_AGENT_TOKEN", "private-printing-token-0123456789012345")
TELEGRAM_ENV_FILE = os.getenv("TELEGRAM_ENV_FILE", "private.env")
INSTALLED_APPS = ["mainApp"]
'''
        result, secrets = settings_source(source)
        ast.parse(result)
        self.assertEqual(len(secrets), 3)
        for value in secrets:
            self.assertNotIn(value, result)
        self.assertNotIn('private-host', result)
        self.assertNotIn('private.env', result)
        self.assertIn("INSTALLED_APPS = ['mainApp']", result)
        self.assertIn('_apply_windows_connection(globals())', result)

    def test_no_local_runtime_env_history_or_fixtures(self):
        root = Path.cwd()
        for name in ('mainApp/.env', 'mainApp/db.sqlite3', 'mainApp/tests.py', 'mainApp/test_x.py',
                     'mainApp/fixtures/real.json', 'local_pos/__pycache__/worker.pyc',
                     'NovaSoft/hybrid_test_postgres_settings.py', 'hybrid_client/assets/original_sale/data.json'):
            with self.subTest(name=name):
                self.assertFalse(eligible(root / name, root))
        for name in ('mainApp/models.py', 'mainApp/migrations/0050_desglosepagoscierre.py',
                     'mainApp/templates/generar_venta.html', 'local_pos/integrated_journal.py'):
            self.assertTrue(eligible(root / name, root))

    def test_archive_cannot_escape_destination(self):
        for name in ('../outside', '/absolute', 'C:/bad', '..\\outside'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with zipfile.ZipFile(root / 'input.zip', 'w') as archive:
                    archive.writestr(name, 'unsafe')
                with self.assertRaises(RuntimeError):
                    extract(root / 'input.zip', root / 'target')

    def test_archive_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            target.mkdir()
            (target / 'file.txt').write_text('keep')
            with zipfile.ZipFile(root / 'input.zip', 'w') as archive:
                archive.writestr('file.txt', 'replace')
            with self.assertRaises(FileExistsError):
                extract(root / 'input.zip', target)
            self.assertEqual((target / 'file.txt').read_text(), 'keep')

    def test_cloud_configuration_requires_verified_tls_and_certificate(self):
        values = {'host': 'database.example.com', 'port': 24553, 'database': 'test',
                  'user': 'app', 'password': 'not-a-real-password', 'ca': 'ca.pem'}
        options = configuration.database(values)['OPTIONS']
        self.assertEqual(options['sslmode'], 'verify-full')
        self.assertEqual(options['sslrootcert'], 'ca.pem')
        with self.assertRaises(RuntimeError):
            configuration.validate(values)

    def test_each_project_has_a_different_private_connection(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': 'C:/private'}):
            self.assertNotEqual(configuration.connection_path('C:/first'), configuration.connection_path('C:/second'))
            self.assertEqual(configuration.connection_path('C:/first').name, 'connection.dpapi')

    def test_invalid_inputs_not_interpolated_into_dsn(self):
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / 'ca.pem'
            ca.write_text('test certificate')
            values = {'host': 'example.com', 'port': 5432, 'database': 'test',
                      'user': 'app', 'password': 'some password with ; spaces', 'ca': str(ca)}
            self.assertEqual(configuration.validate(values)['password'], values['password'])
            for host in ('https://example.com', 'example.com user=other', 'example.com\npassword=other'):
                with self.assertRaises(RuntimeError):
                    configuration.validate(dict(values, host=host))
            for port in (-1, 65536, 'abc'):
                with self.assertRaises(RuntimeError):
                    configuration.validate(dict(values, port=port))

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows-only')
    def test_dpapi_round_trip_never_stores_plaintext_password(self):
        try:
            import win32crypt
        except ImportError:
            self.skipTest('pywin32 unavailable in this test interpreter')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca = root / 'input.pem'
            ca.write_text('test certificate')
            connection = root / 'private/connection.dpapi'
            project = root / 'app'
            values = {'host': 'database.example.com', 'port': 24553, 'database': 'test',
                      'user': 'app', 'password': 'unit-test-password-not-real', 'ca': str(ca)}
            with patch.object(configuration, 'connection_path', return_value=connection):
                configuration.save(project, values)
                self.assertNotIn(values['password'].encode(), connection.read_bytes())
                self.assertEqual(configuration.load(project)['password'], values['password'])
                configuration.save(project, values)  # Same connection can resume.
                with self.assertRaises(RuntimeError):
                    configuration.save(project, dict(values, database='another'))
                self.assertEqual(configuration.load(project)['database'], 'test')


class EngineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'app'
        self.project.mkdir()
        self.config = self.root / 'business/config.json'
        self.bin = self.root / 'postgresql/bin'
        patcher = patch.object(engines, 'connection_path', return_value=self.root / 'private/connection.dpapi')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.messages = []

    def report(self, message, **values):
        self.messages.append((message, values))

    def choose(self):
        return engines.choose(self.project, self.config, self.bin, self.report)

    def test_native_success_is_persisted_and_not_reprobed(self):
        with patch.object(engines, 'probe') as probe:
            self.assertEqual(self.choose(), {'pg_bin': str(self.bin)})
            self.choose()
            probe.assert_called_once()
        self.assertFalse(self.config.exists())

    def test_confirmed_native_block_only_offers_wsl_without_installing(self):
        with patch.object(engines, 'probe', side_effect=engines.NativePolicyBlocked('4551')), \
                patch.object(wsl_setup, 'provision') as provision:
            self.assertIsNone(self.choose())
            self.assertIsNone(self.choose())
            provision.assert_not_called()
        self.assertTrue(self.messages[-1][1]['wsl_required'])
        self.assertFalse(self.config.parent.exists())

    def test_other_failures_never_select_wsl(self):
        for error in (RuntimeError('port occupied'), OSError('permission denied'), RuntimeError('missing dependency')):
            with patch.object(engines, 'probe', side_effect=error):
                with self.assertRaises(type(error)):
                    self.choose()
            self.assertIsNone(engines.read_state(self.project))

    def test_existing_copy_is_used_without_engine_switch(self):
        self.config.parent.mkdir()
        self.config.write_text('existing private configuration')
        with patch.object(engines, 'load_config', return_value={'existing': True}), \
                patch.object(engines, 'probe') as probe:
            self.assertEqual(self.choose(), {'existing': True})
            probe.assert_not_called()

    def test_partial_copy_refuses_fallback(self):
        self.config.parent.mkdir()
        (self.config.parent / 'pending-sales.json').write_text('keep')
        with patch.object(engines, 'probe') as probe:
            with self.assertRaisesRegex(RuntimeError, 'Ya existen'):
                self.choose()
            probe.assert_not_called()
        self.assertEqual((self.config.parent / 'pending-sales.json').read_text(), 'keep')

    def test_incomplete_config_never_falls_back(self):
        self.config.parent.mkdir()
        self.config.write_text('{}')
        with patch.object(engines, 'load_config', side_effect=RuntimeError('incomplete')), \
                patch.object(engines, 'probe') as probe:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                self.choose()
            probe.assert_not_called()

    def test_wsl_requires_explicit_boolean_consent_and_prior_block(self):
        with patch.object(wsl_setup, 'provision') as provision:
            for value in (False, 'true', None, 1):
                with self.assertRaisesRegex(RuntimeError, 'autorizar'):
                    engines.prepare_wsl(self.project, self.config, {'wsl_consent': value}, self.report)
            with self.assertRaisesRegex(RuntimeError, '4551'):
                engines.prepare_wsl(self.project, self.config, {'wsl_consent': True}, self.report)
            provision.assert_not_called()

    def test_wsl_selection_only_after_database_probe(self):
        engines.record(self.project, 'native_blocked')
        name = wsl_setup.distribution_name(self.project)
        with patch.object(wsl_setup, 'provision', return_value=name), patch.object(engines, 'probe') as probe:
            engines.prepare_wsl(self.project, self.config, {'wsl_consent': True}, self.report)
            self.assertEqual(probe.call_args.kwargs['distribution'], name)
        self.assertEqual(engines.read_state(self.project)['status'], 'wsl_ready')
        with patch.object(engines, 'probe') as probe:
            self.assertEqual(self.choose()['wsl_distribution'], name)
            probe.assert_not_called()

    def test_wsl_failure_does_not_mark_ready(self):
        engines.record(self.project, 'native_blocked')
        with patch.object(wsl_setup, 'provision', return_value='NovaPOS-test'), \
                patch.object(engines, 'probe', side_effect=RuntimeError('cannot connect')):
            with self.assertRaisesRegex(RuntimeError, 'cannot connect'):
                engines.prepare_wsl(self.project, self.config, {'wsl_consent': True}, self.report)
        self.assertEqual(engines.read_state(self.project)['status'], 'native_blocked')

    def test_missing_wsl_requests_installation_without_changing_selection(self):
        engines.record(self.project, 'native_blocked')
        with patch.object(wsl_setup, 'provision', side_effect=wsl_setup.WslUnavailable('restart needed')):
            engines.prepare_wsl(self.project, self.config, {'wsl_consent': True}, self.report)
        self.assertTrue(self.messages[-1][1]['wsl_install_required'])
        self.assertEqual(engines.read_state(self.project)['status'], 'native_blocked')

    def test_never_reprovisions_an_existing_copy(self):
        with patch.object(engines, 'existing_copy', return_value={'ready': True}), \
                patch.object(wsl_setup, 'provision') as provision:
            with self.assertRaisesRegex(RuntimeError, 'no se cambiará'):
                engines.prepare_wsl(self.project, self.config, {'wsl_consent': True}, self.report)
            provision.assert_not_called()

    def test_only_specific_4551_is_policy_evidence(self):
        self.assertTrue(engines.policy_blocked(subprocess.CalledProcessError(1, ['initdb'], stderr='plpgsql.dll: error 4551')))
        for text in ('plpgsql.dll permission denied', 'port 4551 busy', 'missing dependency', 'error 14551 plpgsql'):
            self.assertFalse(engines.policy_blocked(subprocess.CalledProcessError(1, ['initdb'], stderr=text)))
        self.assertFalse(engines.policy_blocked(RuntimeError('plpgsql error 4551')))
        error = OSError('policy')
        error.winerror = 4551
        self.assertTrue(engines.policy_blocked(error))

    def test_distribution_ownership_and_version_are_checked(self):
        destination = self.root / 'distribution'
        for base, version in ((self.root / 'other', 2), (destination, 1)):
            with patch.object(wsl_setup, 'registered_location', return_value=(str(base), version)):
                with self.assertRaises(RuntimeError):
                    wsl_setup.ensure_owned('NovaPOS-test', destination)
        with patch.object(wsl_setup, 'registered_location', return_value=(str(destination), 2)):
            wsl_setup.ensure_owned('NovaPOS-test', destination)

    def test_wsl_text_is_decoded_without_nul_characters(self):
        text = 'Acceso denegado. Wsl/E_ACCESSDENIED'
        for encoding in ('utf-16', 'utf-16-le', 'utf-8'):
            self.assertEqual(wsl_setup.decode_output(text.encode(encoding)), text)

    def test_wsl_distribution_is_per_project(self):
        self.assertNotEqual(wsl_setup.distribution_name(self.project), wsl_setup.distribution_name(self.root / 'other'))
        self.assertRegex(wsl_setup.distribution_name(self.project), '^NovaPOS-[0-9a-f]{16}$')

    def test_rootfs_mismatch_is_never_imported_or_deleted(self):
        target = self.root / 'ubuntu-24.04-amd64.tar.gz'
        target.write_bytes(b'incorrect archive')
        with self.assertRaisesRegex(RuntimeError, 'no coincide'):
            wsl_setup.download(self.root, self.report)
        self.assertEqual(target.read_bytes(), b'incorrect archive')

    def test_wsl_probe_waits_for_windows_relay_before_reporting_success(self):
        with patch('local_pos.wsl_postgres.WslPostgres') as wsl, \
                patch('local_pos.wsl_postgres.wait_for_forwarding_release') as released, \
                patch.object(engines, 'port_free'), patch.object(engines, 'sql_probe'):
            engines.probe('/usr/lib/postgresql/16/bin', self.root / 'probe', distribution='NovaPOS-test')
            wsl.return_value.stop.assert_called_once()
            released.assert_called_once_with(engines.PORT)

    def test_wsl_backup_rejects_foreign_paths_and_unknown_operations(self):
        from local_pos.wsl_backup import WslBackup
        backup = object.__new__(WslBackup)
        backup.config = {'data_dir': str(self.root), 'backend': {}, 'instance_id': 'test',
                         'pg_bin': '/usr/lib/postgresql/16/bin', 'port': 55469,
                         'database': 'nova_integrated_local', 'user': 'nova_integrated_local', 'password': 'private'}
        with self.assertRaisesRegex(RuntimeError, 'Operación'):
            backup.run('restore')
        with self.assertRaisesRegex(RuntimeError, 'carpeta privada'):
            backup.run('dump', self.root / 'outside/database.dump.partial')
        with self.assertRaisesRegex(RuntimeError, 'carpeta privada'):
            backup.run('dump', self.root / 'backups/new/wrong.sql')

    def test_wsl_backup_credentials_only_on_stdin_and_no_native_clients(self):
        from local_pos.wsl_backup import WslBackup
        backup = object.__new__(WslBackup)
        backup.config = {'backend': {}, 'instance_id': 'test', 'pg_bin': '/usr/lib/postgresql/16/bin',
                         'port': 55469, 'database': 'nova_integrated_local', 'user': 'nova_integrated_local',
                         'password': 'test-private-password'}
        backup.prefix = ['C:/Windows/System32/wsl.exe', '--distribution', 'NovaPOS-test', '--exec']
        backup.worker = '/mnt/c/app/local_pos/wsl_backup_worker.py'
        with patch('local_pos.wsl_backup.subprocess.run') as run:
            backup.run('pg_dump_version')
            command = run.call_args.args[0]
            self.assertNotIn(backup.config['password'], ' '.join(command))
            self.assertNotIn('pg_dump.exe', ' '.join(command))
            payload = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['password'], backup.config['password'])

    def test_native_backup_tool_block_is_detected_before_business_copy(self):
        error = OSError('application control')
        error.winerror = 4551
        with patch.object(engines, 'Postgres') as native, patch.object(engines, 'port_free'), \
                patch.object(engines, 'sql_probe'):
            native.return_value.run.side_effect = [None, error]  # initdb OK, pg_dump blocked
            with self.assertRaises(engines.NativePolicyBlocked):
                engines.probe(self.bin, self.root / 'native-backup-probe')
            native.return_value.stop.assert_called_once()
        self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()

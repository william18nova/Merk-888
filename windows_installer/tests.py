"""No cloud/business database access. Packaging and first-run safety regression."""
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.build_windows_integrated import eligible, extract, settings_source
from windows_installer import configuration


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


if __name__ == '__main__':
    unittest.main()

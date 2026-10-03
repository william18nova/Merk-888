from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner

from .test_runner import LocalLabRunner


class LabCleanupTests(SimpleTestCase):
    def config(self):
        return {"HOST": "127.0.0.1", "USER": "nova_full_local_lab",
                "NAME": "test_nova_full_local_lab",
                "OPTIONS": {"options": "-c lock_timeout=5000 -c statement_timeout=15000"}}

    def test_cleanup_timeout_is_temporary(self):
        config = self.config()
        original = config["OPTIONS"]
        def cleanup(*args, **kwargs):
            self.assertTrue(config["OPTIONS"]["options"].endswith("-c statement_timeout=120000"))
            self.assertIn("lock_timeout=5000", config["OPTIONS"]["options"])
        with patch.object(DiscoverRunner, "teardown_databases", side_effect=cleanup):
            LocalLabRunner().teardown_databases([(SimpleNamespace(settings_dict=config), "nova_full_local_lab", True)])
        self.assertIs(config["OPTIONS"], original)

    def test_cleanup_restores_timeout_even_on_failure(self):
        config = self.config()
        original = config["OPTIONS"]
        with patch.object(DiscoverRunner, "teardown_databases", side_effect=RuntimeError("simulated")):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                LocalLabRunner().teardown_databases([(SimpleNamespace(settings_dict=config), "nova_full_local_lab", True)])
        self.assertIs(config["OPTIONS"], original)

    def test_cleanup_refuses_other_database(self):
        for changes in ({"HOST": "remote.invalid"}, {"NAME": "defaultdb"}, {"USER": "other"}):
            config = {**self.config(), **changes}
            original = config["OPTIONS"]
            with patch.object(DiscoverRunner, "teardown_databases") as cleanup:
                with self.assertRaisesRegex(RuntimeError, "laboratorio"):
                    LocalLabRunner().teardown_databases([(SimpleNamespace(settings_dict=config), "nova_full_local_lab", True)])
                cleanup.assert_not_called()
            self.assertIs(config["OPTIONS"], original)

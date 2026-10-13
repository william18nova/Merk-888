import unittest

from .connectivity import Decision, Health, LocalState, Mode, choose_mode, may_replay_locally


class ConnectivityTests(unittest.TestCase):
    def test_cloud_is_first_even_without_a_local_installation(self):
        self.assertEqual(choose_mode(Health.AVAILABLE, LocalState()),
                         Decision(Mode.ONLINE, "default", True, "cloud_available"))

    def test_outage_never_initializes_an_empty_database(self):
        result = choose_mode(Health.DISCONNECTED, LocalState())
        self.assertEqual(result.mode, Mode.UNAVAILABLE)
        self.assertIsNone(result.database)
        self.assertFalse(result.new_operations)

    def test_ready_offline_snapshot_keeps_working(self):
        result = choose_mode(Health.DISCONNECTED, LocalState(prepared=True, authorized=True))
        self.assertEqual(result.mode, Mode.OFFLINE)
        self.assertTrue(result.new_operations)

    def test_expiry_disallows_new_writes_but_preserves_local_access(self):
        result = choose_mode(Health.DISCONNECTED, LocalState(prepared=True, pending=3))
        self.assertEqual(result.database, "offline")
        self.assertFalse(result.new_operations)

    def test_return_of_internet_does_not_bypass_pending_queue(self):
        for counts in ({"pending": 1}, {"uncertain": 1}, {"refresh_required": True}):
            with self.subTest(counts=counts):
                result = choose_mode(Health.AVAILABLE, LocalState(prepared=True, authorized=True, **counts))
                self.assertEqual(result.mode, Mode.SYNCHRONIZING)
                self.assertEqual(result.database, "offline")

    def test_missing_local_db_with_pending_state_is_not_silently_online(self):
        result = choose_mode(Health.AVAILABLE, LocalState(pending=1))
        self.assertEqual(result.mode, Mode.SYNCHRONIZING)
        self.assertFalse(result.new_operations)

    def test_conflict_and_rejected_permissions_are_not_network_outages(self):
        for health, local in ((Health.REJECTED, LocalState(prepared=True, authorized=True)),
                               (Health.AVAILABLE, LocalState(prepared=True, authorized=True, conflicts=1))):
            self.assertEqual(choose_mode(health, local).mode, Mode.BLOCKED)
            self.assertFalse(choose_mode(health, local).new_operations)

    def test_unknown_is_not_assumed_offline(self):
        self.assertEqual(choose_mode(Health.UNKNOWN, LocalState(prepared=True, authorized=True)).mode,
                         Mode.UNAVAILABLE)

    def test_only_clean_converged_state_returns_to_cloud(self):
        result = choose_mode(Health.AVAILABLE, LocalState(prepared=True, authorized=True))
        self.assertEqual(result.mode, Mode.ONLINE)

    def test_uncertain_commit_must_not_be_replayed_locally(self):
        self.assertFalse(may_replay_locally(attempted_in_cloud=True, confirmed_absent=False))
        self.assertTrue(may_replay_locally(attempted_in_cloud=True, confirmed_absent=True))
        self.assertTrue(may_replay_locally(attempted_in_cloud=False, confirmed_absent=False))

    def test_invalid_state_fails_closed(self):
        for invalid in (-1, True, "1"):
            with self.assertRaises(ValueError):
                LocalState(pending=invalid)
        with self.assertRaises(ValueError):
            choose_mode("internet", LocalState())

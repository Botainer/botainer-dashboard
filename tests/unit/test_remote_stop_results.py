"""Stop-response contract checks with an injected native reply; no SSH."""
import threading
import unittest
from unittest.mock import Mock
import uuid

from botainer_dashboard.backend_errors import BackendUnavailable
from botainer_dashboard.remote_backend import RemoteBackend


class RemoteStopResults(unittest.TestCase):
    def setUp(self):
        self.backend = RemoteBackend.__new__(RemoteBackend)
        self.backend.namespace = 'remote:synthetic'
        self.backend._lock = threading.RLock()
        self.sid = 'a' * 16
        self.row = {'session_id': self.sid, 'project_uuid': str(uuid.UUID(int=1)), 'job_id': '12345'}
        self.backend._session = lambda sid: self.row
        self.backend._connection_error = lambda: None
        self.backend._project_id = lambda uid: 'synthetic-project'
        self.backend._target_data = lambda uid: {'project_uuid': self.row['project_uuid'], 'project_path': '/synthetic/project'}
        self.backend.refresh = Mock()
        self.session = {'contextNamespace': self.backend.namespace, 'runtimeId': self.sid,
                        'jobId': '12345', 'state': 'stopped', 'stopScope': 'allocation'}
        self.backend.snapshot = lambda: {'sessions': [self.session]}

    def stop(self, reply):
        self.backend._call = Mock(return_value=reply)
        return self.backend.stop_session(self.backend.namespace, self.sid, str(uuid.uuid4()))

    def test_native_exact_ended_observation_is_explicit_but_cleanup_is_not_claimed(self):
        reply = {'confirmed': True, 'observation': {'job_id': '12345', 'state': 'stopped'}}
        result = self.stop(reply)
        self.assertTrue(result['terminationConfirmed'])
        self.assertFalse(result['helperCleanupConfirmed'])
        self.assertEqual(result['session'], self.session)
        self.backend._call.assert_called_once()
        self.assertEqual(self.backend._call.call_args.kwargs['job_id'], '12345')

    def test_false_missing_wrong_job_and_ack_only_results_are_not_termination(self):
        for reply in ({'confirmed': False}, {'confirmed': True},
                      {'confirmed': 'true', 'observation': {'job_id': '12345', 'state': 'stopped'}},
                      {'confirmed': True, 'observation': {'job_id': '54321', 'state': 'stopped'}},
                      {'confirmed': True, 'observation': {'job_id': '12345', 'state': 'running'}}):
            with self.subTest(reply=reply), self.assertRaisesRegex(BackendUnavailable, 'stop-unconfirmed'):
                self.stop(reply)
            self.backend._call.assert_called_once()
        self.backend.refresh.assert_not_called()

    def test_later_stale_view_does_not_erase_the_native_exact_termination_observation(self):
        self.session = {**self.session, 'state': 'unknown', 'stale': True}
        result = self.stop({'confirmed': True, 'observation': {'job_id': '12345', 'state': 'stopped'}})
        self.assertTrue(result['terminationConfirmed'])
        self.assertEqual(result['session']['state'], 'unknown')

    def test_changed_refresh_target_does_not_close_a_different_allocation(self):
        self.session = {**self.session, 'jobId': '54321'}
        with self.assertRaisesRegex(BackendUnavailable, 'stop-observation-unavailable'):
            self.stop({'confirmed': True, 'observation': {'job_id': '12345', 'state': 'stopped'}})

    def test_missing_job_identity_cannot_be_a_confirmed_match(self):
        self.row['job_id'] = None
        with self.assertRaisesRegex(BackendUnavailable, 'stop-unconfirmed'):
            self.stop({'confirmed': True, 'observation': {'job_id': None, 'state': 'stopped'}})


if __name__ == '__main__':
    unittest.main()

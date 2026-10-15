from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from generator.deepseek_client import DeepSeekClient, ResponseShapeError, RunDeadlineExceeded


def reply(content='{"ok": true}'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


class ModelFallbackTests(TestCase):
    def client(self):
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'primary-test', 'FALLBACK_LLM_API_KEY': 'fallback-test'}):
            return DeepSeekClient({'deepseek': {'max_retries': 1, 'retry_backoff_sec': 0,
                'fallback': {'enabled': True, 'base_url': 'https://fallback.example/v1', 'model': 'backup'}}})

    def test_healthy_primary_does_not_call_fallback(self):
        client = self.client()
        with patch.object(client.client.chat.completions, 'create', return_value=reply()), \
             patch.object(client._fallback.client.chat.completions, 'create') as backup:
            self.assertEqual(client.chat_json('primary', 's', 'p'), {'ok': True})
        backup.assert_not_called()
        self.assertEqual(client.usage.logical_calls, 1)

    def test_exhausted_primary_switches_once_and_shares_telemetry(self):
        client = self.client()
        with patch.object(client.client.chat.completions, 'create', side_effect=TimeoutError('offline')) as primary, \
             patch.object(client._fallback.client.chat.completions, 'create', return_value=reply()) as backup:
            self.assertEqual(client.chat_json('primary', 's', 'p', stage='topics'), {'ok': True})
            self.assertEqual(client.chat_json('primary', 's2', 'p2', stage='digest'), {'ok': True})
        self.assertEqual(primary.call_count, 2)
        self.assertEqual(backup.call_count, 2)
        self.assertEqual(backup.call_args.kwargs['model'], 'backup')
        self.assertEqual(backup.call_args.kwargs['messages'][1]['content'], 'p2')
        self.assertEqual(client.usage.logical_calls, 2)
        self.assertEqual(client.usage.requests, 4)
        self.assertEqual(client.usage.failed_requests, 2)
        self.assertEqual(client._fallback._deadline, client._deadline)

    def test_fallback_must_pass_same_validator(self):
        client = self.client()
        def validate(payload):
            if not payload.get('ok'):
                raise ResponseShapeError('ok required')
            return payload
        with patch.object(client.client.chat.completions, 'create', return_value=reply('{}')), \
             patch.object(client._fallback.client.chat.completions, 'create', side_effect=[reply('{}'), reply()]) as backup:
            self.assertEqual(client.chat_json('primary', 's', 'original', validate), {'ok': True})
        self.assertIn('ok required', backup.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(client.usage.logical_calls, 1)
        self.assertEqual(client.usage.requests, 4)

    def test_spent_budget_does_not_start_fallback(self):
        client = self.client()
        client._deadline = 0
        with patch.object(client._fallback.client.chat.completions, 'create') as backup:
            with self.assertRaises(RunDeadlineExceeded):
                client.chat_json('primary', 's', 'p')
        backup.assert_not_called()

    def test_both_providers_failing_is_not_success(self):
        client = self.client()
        with patch.object(client.client.chat.completions, 'create', side_effect=TimeoutError('offline')), \
             patch.object(client._fallback.client.chat.completions, 'create', side_effect=TimeoutError('offline')):
            with self.assertRaises(RuntimeError):
                client.chat_json('primary', 's', 'p')
        self.assertEqual(client.usage.requests, 4)
        self.assertEqual(client.usage.logical_calls, 1)

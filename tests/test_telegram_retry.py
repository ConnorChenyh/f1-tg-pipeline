from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from publisher.telegram import _post_with_retry


class TelegramRetryTests(unittest.TestCase):
    def test_network_request_retries_with_backoff(self) -> None:
        response = object()
        with patch("publisher.telegram.time.sleep") as sleep:
            request = Mock(side_effect=[requests.ConnectionError(), response])
            result = _post_with_retry(
                request,
                method="sendMessage",
                attempts=3,
                backoff_sec=2,
            )

        self.assertIs(result, response)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()

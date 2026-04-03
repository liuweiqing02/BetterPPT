from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core.errors import AppException
from app.services.task_service import _check_create_task_rate_limit


class _FakeRedis:
    def __init__(self, start: int = 0):
        self.value = start
        self.expire_seconds = None
        self.ttl_seconds = 42

    def incr(self, key: str) -> int:
        self.value += 1
        return self.value

    def expire(self, key: str, seconds: int) -> bool:
        self.expire_seconds = seconds
        return True

    def ttl(self, key: str) -> int:
        return self.ttl_seconds


class TaskRateLimitTestCase(unittest.TestCase):
    def test_rate_limit_allows_under_limit(self) -> None:
        fake_client = _FakeRedis(start=0)
        with patch('app.services.task_service.get_redis_client', return_value=fake_client), patch(
            'app.services.task_service.get_settings'
        ) as mock_settings:
            mock_settings.return_value.rate_limit_create_task_per_minute = 3
            _check_create_task_rate_limit(1)
            _check_create_task_rate_limit(1)
            self.assertEqual(fake_client.value, 2)
            self.assertEqual(fake_client.expire_seconds, 60)

    def test_rate_limit_raises_when_exceeded(self) -> None:
        fake_client = _FakeRedis(start=3)
        with patch('app.services.task_service.get_redis_client', return_value=fake_client), patch(
            'app.services.task_service.get_settings'
        ) as mock_settings:
            mock_settings.return_value.rate_limit_create_task_per_minute = 3
            with self.assertRaises(AppException) as exc_info:
                _check_create_task_rate_limit(1)
            self.assertEqual(exc_info.exception.status_code, 429)


if __name__ == '__main__':
    unittest.main()


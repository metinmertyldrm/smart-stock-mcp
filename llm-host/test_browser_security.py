import unittest

from browser_security import LoginRateLimiter


class LoginRateLimiterTest(unittest.TestCase):
    def test_threshold_blocks_and_recovers_after_block_window(self):
        limiter = LoginRateLimiter(max_failures=3, window_seconds=60, block_seconds=30, max_keys=100)
        self.assertEqual(limiter.retry_after("127.0.0.1", "Alice", now=100.0), 0)
        self.assertEqual(limiter.record_failure("127.0.0.1", "Alice", now=100.0), 0)
        self.assertEqual(limiter.record_failure("127.0.0.1", "Alice", now=101.0), 0)
        self.assertEqual(limiter.record_failure("127.0.0.1", "Alice", now=102.0), 30)
        self.assertEqual(limiter.retry_after("127.0.0.1", "alice", now=110.0), 22)
        self.assertEqual(limiter.retry_after("127.0.0.1", "Alice", now=133.0), 0)

    def test_success_clears_failure_state(self):
        limiter = LoginRateLimiter(max_failures=2, window_seconds=60, block_seconds=30, max_keys=100)
        limiter.record_failure("127.0.0.1", "Alice", now=100.0)
        limiter.record_success("127.0.0.1", "alice")
        self.assertEqual(limiter.retry_after("127.0.0.1", "Alice", now=101.0), 0)

    def test_source_and_username_are_both_part_of_key(self):
        limiter = LoginRateLimiter(max_failures=1, window_seconds=60, block_seconds=30, max_keys=100)
        self.assertEqual(limiter.record_failure("10.0.0.1", "alice", now=100.0), 30)
        self.assertEqual(limiter.retry_after("10.0.0.2", "alice", now=101.0), 0)
        self.assertEqual(limiter.retry_after("10.0.0.1", "bob", now=101.0), 0)

    def test_state_is_bounded(self):
        limiter = LoginRateLimiter(max_failures=2, window_seconds=60, block_seconds=30, max_keys=2)
        limiter.record_failure("1", "a", now=100.0)
        limiter.record_failure("2", "b", now=100.0)
        limiter.record_failure("3", "c", now=100.0)
        self.assertLessEqual(len(limiter._buckets), 2)


if __name__ == "__main__":
    unittest.main()

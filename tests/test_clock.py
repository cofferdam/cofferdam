"""Tests for the wall-clock abstraction."""

import unittest

from cofferdam.clock import Clock, SystemClock


class SystemClockTests(unittest.TestCase):
    def test_now_epoch_is_int(self):
        self.assertIsInstance(SystemClock().now_epoch(), int)

    def test_non_decreasing(self):
        clock = SystemClock()
        self.assertGreaterEqual(clock.now_epoch(), clock.now_epoch() - 1)

    def test_satisfies_protocol(self):
        self.assertIsInstance(SystemClock(), Clock)


if __name__ == "__main__":
    unittest.main()

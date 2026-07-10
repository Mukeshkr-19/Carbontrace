import unittest

from app.drain_app import (
    MAX_DURATION_SECONDS,
    MAX_WORK_SIZE,
    MIN_DURATION_SECONDS,
    MIN_WORK_SIZE,
    redundant_checksum,
    validate_settings,
)


class DrainAppTests(unittest.TestCase):
    def test_checksum_is_deterministic(self) -> None:
        values = [0, 1, 2, 3]
        self.assertEqual(redundant_checksum(values), redundant_checksum(values))

    def test_valid_settings_are_accepted(self) -> None:
        validate_settings(MIN_DURATION_SECONDS, MIN_WORK_SIZE)
        validate_settings(MAX_DURATION_SECONDS, MAX_WORK_SIZE)

    def test_invalid_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_settings(MIN_DURATION_SECONDS - 1, MIN_WORK_SIZE)
        with self.assertRaises(ValueError):
            validate_settings(MAX_DURATION_SECONDS + 1, MIN_WORK_SIZE)

    def test_invalid_work_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_settings(MIN_DURATION_SECONDS, MIN_WORK_SIZE - 1)
        with self.assertRaises(ValueError):
            validate_settings(MIN_DURATION_SECONDS, MAX_WORK_SIZE + 1)


if __name__ == "__main__":
    unittest.main()

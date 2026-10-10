import unittest
from datetime import datetime, timezone

from schedule_es import next_slot, valid_cadence, valid_caption


class ScheduleTests(unittest.TestCase):
    def test_cadence(self):
        self.assertTrue(valid_cadence("2026-10-13T20:00:00.000Z"))
        self.assertTrue(valid_cadence("2026-10-15T20:00:00.000Z"))
        self.assertTrue(valid_cadence("2026-10-19T01:00:00.000Z"))
        self.assertFalse(valid_cadence("2026-10-14T20:00:00.000Z"))

    def test_caption_policy(self):
        self.assertTrue(valid_caption("Un hecho histórico contado con claridad."))
        self.assertFalse(valid_caption("Guardá este post 👇 #historia"))
        self.assertFalse(valid_caption(" ".join(["palabra"] * 151)))

    def test_next_slot_uses_spread_cadence_and_avoids_occupied(self):
        now = datetime.fromisoformat("2026-10-08T18:00:00+00:00")
        occupied = {"2026-10-08T20:00:00.000Z"}
        first = next_slot(now, occupied)
        self.assertEqual(first, "2026-10-12T01:00:00.000Z")
        occupied.add(first)
        second = next_slot(now, occupied)
        self.assertEqual(second, "2026-10-13T20:00:00.000Z")


if __name__ == "__main__":
    unittest.main()

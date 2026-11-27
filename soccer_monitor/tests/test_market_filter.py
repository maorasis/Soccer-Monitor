"""Check that only whole-game goal totals survive market filtering.

Question strings are real, taken from the Gamma API on 2026-08-30.
"""
import unittest

from common import parse_game_total_line

KEEP = {
    "Viking FK vs. Aalesunds FK: O/U 2.5": 2.5,
    "1. FC Cologne vs. Hertha BSC: O/U 3.5": 3.5,
    "Viking FK vs. Aalesunds FK: O/U 0.5": 0.5,
    "Arsenal vs. Chelsea: O/U 5.5": 5.5,
}

DROP = [
    "Viking FK vs. Aalesunds FK: Viking FK O/U 1.5",              # per-team
    "1. FC Cologne vs. Hertha BSC: 1. FC Cologne O/U 2.5",        # per-team, digits in name
    "1. FC Cologne vs. Hertha BSC: 1st Half O/U 2.5",             # per-half
    "1. FC Cologne vs. Hertha BSC: Hertha BSC 2nd Half O/U 1.5",  # per-team per-half
    "1. FC Cologne vs. Real Sociedad: O/U 8.5 Corners",           # corners
    "1. FC Cologne vs. Real Sociedad: 1st Half O/U 3.5 Total Corners",
    "Will Zack Steffen win 2026 MLS Goalkeeper of the Year?",
]


class MarketFilterTests(unittest.TestCase):
    def test_whole_game_totals_are_parsed(self):
        for question, line in KEEP.items():
            with self.subTest(question=question):
                self.assertEqual(parse_game_total_line(question), line)

    def test_other_markets_are_dropped(self):
        for question in DROP:
            with self.subTest(question=question):
                self.assertIsNone(parse_game_total_line(question))


if __name__ == "__main__":
    unittest.main()

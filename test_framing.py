"""Tests for Spec fix-08 · A.1 — Change-anchored framing.

Run:
    .venv/bin/python test_framing.py

Uses stdlib unittest. Covers:

Positive cases (3):
  1. Valid change-anchored action passes
  2. next_triggers_from_portfolio returns concrete holdings-derived strings
  3. Empty holdings/watchlist → returns empty list (not generic examples)

Regression cases (2, required by user 2026-04-19):
  4. test_forbids_naked_prediction_even_with_confidence_score
     — "信心 75%" / "入場 95-100" / "停損 88" all rejected even if
       the rest of the action looks well-formed.
  5. test_rejects_change_older_than_14_days
     — validator rejects changes dated >14 days ago (prevents
       stale-anchor laundering).
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from framing import (
    validate_change_anchored_action,
    next_triggers_from_portfolio,
)


# ========================================================================= #
# Positive cases                                                              #
# ========================================================================= #

class ValidatorPositiveTest(unittest.TestCase):

    def test_valid_change_anchored_action_passes(self):
        today = date(2026, 4, 19)
        action = {
            "action": "若考慮加碼，先確認 (a) 機構對沖部位 (b) 組合 5% 上限",
            "reason": "目標價 4 天上修",
            "change": {
                "old": 140,
                "new": 175,
                "as_of": (today - timedelta(days=3)).isoformat(),
            },
        }
        ok, reason = validate_change_anchored_action(action, today=today)
        self.assertTrue(ok, f"should pass, got reason={reason!r}")
        self.assertIsNone(reason)


class NextTriggersPositiveTest(unittest.TestCase):

    def test_triggers_from_real_holdings_watchlist(self):
        pf = {
            "holdings": [
                {"symbol": "0050", "name": "元大台灣50", "price": 84.15,
                 "shares": 1000, "market_value": 84150},
                {"symbol": "2330", "name": "台積電", "price": 2030.0,
                 "shares": 10, "market_value": 20300},
            ],
            "watchlist": [
                {"symbol": "2303", "name": "聯電", "price": 73.0},
            ],
        }
        out = next_triggers_from_portfolio(pf)
        # All three should appear — 0050 + 2330 (holdings) + 2303 (watchlist)
        joined = "\n".join(out)
        self.assertIn("0050", joined)
        self.assertIn("2330", joined)
        self.assertIn("2303", joined)
        # 0050/006208 etc. are ETFs → "定期定額" framing
        self.assertIn("定期定額", joined)
        # 2330 is individual stock → "加碼觀察"
        self.assertIn("加碼觀察", joined)
        # 2303 is in watchlist → "首筆進場觀察"
        self.assertIn("首筆進場觀察", joined)
        # NEVER suggest a ticker the user doesn't own / watch
        self.assertNotIn("聯亞", joined)
        self.assertNotIn("3081", joined)


class EmptyPortfolioTest(unittest.TestCase):

    def test_empty_portfolio_returns_empty_list(self):
        # No generic examples — if user has nothing, return nothing.
        self.assertEqual(next_triggers_from_portfolio({}), [])
        self.assertEqual(next_triggers_from_portfolio(None), [])
        self.assertEqual(
            next_triggers_from_portfolio({"holdings": [], "watchlist": []}),
            [],
        )


# ========================================================================= #
# Regression cases (required by user 2026-04-19)                              #
# ========================================================================= #

class NakedPredictionRegressionTest(unittest.TestCase):
    """test_forbids_naked_prediction_even_with_confidence_score

    Naked-prediction patterns must be rejected EVEN when the action has
    a valid change anchor + fresh as_of. The change anchor alone is not
    enough — the action TEXT must also be clean of confidence-score /
    entry-price / stop-loss numbers that turn the brief into a buy signal.
    """

    def setUp(self):
        self.today = date(2026, 4, 19)
        self.fresh_change = {
            "old": 140, "new": 175,
            "as_of": (self.today - timedelta(days=3)).isoformat(),
        }

    def test_rejects_confidence_percent_in_action(self):
        action = {
            "action": "買聯亞 3081，信心 75%",
            "reason": "目標價上修",
            "change": self.fresh_change,
        }
        ok, reason = validate_change_anchored_action(action, today=self.today)
        self.assertFalse(ok)
        self.assertIn("naked prediction", reason)
        self.assertIn("信心", reason)

    def test_rejects_entry_range_in_action(self):
        action = {
            "action": "3081 入場 95-100 分批布局",
            "reason": "題材發酵",
            "change": self.fresh_change,
        }
        ok, reason = validate_change_anchored_action(action, today=self.today)
        self.assertFalse(ok)
        self.assertIn("naked prediction", reason)

    def test_rejects_stop_loss_number_in_reason(self):
        action = {
            "action": "加碼欣興",
            "reason": "停損 88，獲利目標 20%",
            "change": self.fresh_change,
        }
        ok, reason = validate_change_anchored_action(action, today=self.today)
        self.assertFalse(ok)
        self.assertIn("naked prediction", reason)


class StaleChangeRegressionTest(unittest.TestCase):
    """test_rejects_change_older_than_14_days

    If the cited change is >14 days old, the 'change anchor' is
    stale — it's not news, just old analysis being recycled as fresh.
    Validator must reject to prevent stale-anchor laundering.
    """

    def test_change_15_days_old_rejected(self):
        today = date(2026, 4, 19)
        action = {
            "action": "若考慮加碼，確認倉位上限",
            "reason": "目標價上修",
            "change": {
                "old": 140, "new": 175,
                "as_of": (today - timedelta(days=15)).isoformat(),
            },
        }
        ok, reason = validate_change_anchored_action(action, today=today)
        self.assertFalse(ok)
        self.assertIn("15 days old", reason)

    def test_change_exactly_14_days_still_ok(self):
        # Boundary: 14d is OK, 15d rejected
        today = date(2026, 4, 19)
        action = {
            "action": "若考慮加碼，確認倉位上限",
            "reason": "目標價上修",
            "change": {
                "old": 140, "new": 175,
                "as_of": (today - timedelta(days=14)).isoformat(),
            },
        }
        ok, reason = validate_change_anchored_action(action, today=today)
        self.assertTrue(ok, f"14d should pass; got {reason!r}")

    def test_missing_as_of_rejected(self):
        action = {
            "action": "若加碼，確認部位",
            "reason": "目標價上修",
            "change": {"old": 140, "new": 175},  # no as_of
        }
        ok, reason = validate_change_anchored_action(action)
        self.assertFalse(ok)
        self.assertIn("as_of", reason)

    def test_missing_change_dict_rejected(self):
        # No change = naked prediction, period.
        action = {"action": "加碼", "reason": "信心高"}
        ok, reason = validate_change_anchored_action(action)
        self.assertFalse(ok)
        # Note: naked pattern check runs first, but this action has no
        # confidence% in text so change-missing is the failure reason.
        self.assertTrue(
            "change" in reason.lower() or "anchor" in reason.lower(),
            f"expected change/anchor in reason, got {reason!r}",
        )


# ========================================================================= #

if __name__ == "__main__":
    unittest.main(verbosity=2)

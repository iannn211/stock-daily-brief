"""Tests for Spec fix-08 provenance layer.

Run:
    .venv/bin/python test_provenance.py

Uses stdlib unittest (no pytest required). Covers:
  1. envelope_roundtrip — wrap + unwrap + backward compat for raw values
  2. staleness_industry_tiered — same as_of gives different 🟡/🔴 per speed
  3. enrichment_idempotent — running twice == running once
  4. llm_inference_requires_confidence / primary_report_requires_source_ref
  5. E2E: enrich a realistic analysis JSON without exploding
"""
from __future__ import annotations

import copy
import json
import unittest
from datetime import date

from provenance import (
    envelope,
    unwrap,
    is_enveloped,
    is_stale,
    provenance_of,
    age_days,
    SOURCE_DOT,
    render_dot_html,
)
from provenance_speed_map import speed_of
from provenance_enrich import (
    enrich_analysis,
    enrich_chips,
    load_supply_chains,
    match_chain_for_theme,
)


# =========================================================================
# Unit 1 — envelope roundtrip
# =========================================================================
class EnvelopeRoundtripTest(unittest.TestCase):

    def test_wrap_and_unwrap_primitive(self):
        e = envelope(150, "primary_report", "2026-04-15",
                     source_ref="TD Cowen 2026-04-15")
        self.assertTrue(is_enveloped(e))
        self.assertEqual(unwrap(e), 150)

    def test_unwrap_raw_passthrough(self):
        self.assertEqual(unwrap(150), 150)
        self.assertEqual(unwrap("hello"), "hello")
        self.assertEqual(unwrap(None), None)
        self.assertEqual(unwrap([1, 2, 3]), [1, 2, 3])

    def test_partial_dict_is_not_envelope(self):
        # Random dicts must not be mistaken for envelopes
        self.assertFalse(is_enveloped({"value": 1}))
        self.assertFalse(is_enveloped({"value": 1, "source": "x"}))  # missing as_of
        self.assertFalse(is_enveloped({"source": "x", "as_of": "2026-01-01"}))  # missing value
        self.assertEqual(unwrap({"value": 1}), {"value": 1})

    def test_provenance_of(self):
        e = envelope("x", "llm_inference", "2026-04-15", confidence=0.7)
        src, as_of, conf, ref = provenance_of(e)
        self.assertEqual(src, "llm_inference")
        self.assertEqual(as_of, "2026-04-15")
        self.assertEqual(conf, 0.7)
        self.assertIsNone(ref)
        self.assertIsNone(provenance_of(150))  # raw → None


# =========================================================================
# Unit 2 — staleness industry tiered
# =========================================================================
class StalenessIndustryTieredTest(unittest.TestCase):

    def setUp(self):
        self.today = date(2026, 4, 20)

    def _at(self, days_ago: int):
        from datetime import timedelta
        d = self.today - timedelta(days=days_ago)
        return envelope(100, "secondary_news", d.isoformat())

    def test_fast_industry_strict(self):
        # target_price for semiconductors — 20 days should already be 🟡
        e = self._at(20)
        self.assertEqual(is_stale(e, "fast", "target_price", self.today), "🟡")

    def test_slow_industry_lenient(self):
        # Same 20 days but for food → not stale
        e = self._at(20)
        self.assertIsNone(is_stale(e, "slow", "target_price", self.today))

    def test_medium_default(self):
        e = self._at(40)  # 40 days
        # medium threshold is 30/60 for target_price → 🟡
        self.assertEqual(is_stale(e, "medium", "target_price", self.today), "🟡")

    def test_red_tier_hits(self):
        e = self._at(35)  # 35 days
        self.assertEqual(is_stale(e, "fast", "target_price", self.today), "🔴")

    def test_raw_value_never_stale(self):
        # Non-envelope → None (no staleness signal on raw)
        self.assertIsNone(is_stale(100, "fast", "target_price", self.today))

    def test_no_as_of_no_staleness(self):
        # Envelope with null as_of → None (UI must show 時間未知 separately)
        e = {"value": 100, "source": "llm_inference", "as_of": None,
             "confidence": 0.5}
        self.assertTrue(is_enveloped(e))
        self.assertIsNone(is_stale(e, "fast", "target_price", self.today))
        self.assertIsNone(age_days(e, self.today))


# =========================================================================
# Unit 3 — enrichment idempotency
# =========================================================================
class EnrichmentIdempotentTest(unittest.TestCase):

    def test_enrich_analysis_twice_no_double_wrap(self):
        mock = {
            "generated_at": "2026-04-18T20:53:34+08:00",
            "opportunities": [{
                "theme": "PCB / CCL",
                "lead_stocks": [{"symbol": "3037", "name": "欣興"}],
            }],
        }
        idx = load_supply_chains()
        enrich_analysis(mock, idx)
        first_pass = copy.deepcopy(mock)
        enrich_analysis(mock, idx)  # second time
        self.assertEqual(json.dumps(mock, ensure_ascii=False, default=str),
                         json.dumps(first_pass, ensure_ascii=False, default=str))
        # Sidecar pattern: lead_stocks stays raw (readers unbroken), and a
        # parallel _lead_stocks_prov list carries provenance envelopes.
        opp = mock["opportunities"][0]
        self.assertEqual(opp["lead_stocks"], [{"symbol": "3037", "name": "欣興"}])
        prov_list = opp.get("_lead_stocks_prov") or []
        self.assertEqual(len(prov_list), 1)
        self.assertTrue(is_enveloped(prov_list[0]))
        # Sidecar envelope carries None value (metadata-only)
        self.assertIsNone(prov_list[0]["value"])
        self.assertIn(prov_list[0]["source"],
                      {"user_input", "llm_inference"})

    def test_enrich_chips_idempotent(self):
        chips = {
            "foreign_futures": {"latest": {"date": "2026-04-17",
                                            "net_oi": -41213}},
        }
        enrich_chips(chips)
        first = copy.deepcopy(chips)
        enrich_chips(chips)
        self.assertEqual(json.dumps(chips, ensure_ascii=False, default=str),
                         json.dumps(first, ensure_ascii=False, default=str))


# =========================================================================
# Unit 4 — required fields per tier
# =========================================================================
class RequiredFieldsTest(unittest.TestCase):

    def test_llm_inference_requires_confidence(self):
        with self.assertRaises(ValueError):
            envelope("x", "llm_inference", "2026-04-15")

    def test_primary_report_requires_source_ref(self):
        with self.assertRaises(ValueError):
            envelope(150, "primary_report", "2026-04-15")

    def test_secondary_news_does_not_require_anything_extra(self):
        # Should NOT raise
        e = envelope("x", "secondary_news", "2026-04-15")
        self.assertEqual(e["source"], "secondary_news")
        self.assertNotIn("confidence", e)
        self.assertNotIn("source_ref", e)

    def test_user_input_does_not_require_anything_extra(self):
        e = envelope(1234, "user_input", "2026-04-15")
        self.assertEqual(e["source"], "user_input")

    def test_invalid_source_tier_rejected(self):
        with self.assertRaises(ValueError):
            envelope("x", "rumor", "2026-04-15")


# =========================================================================
# Unit 5 — supply_chains confirmation upgrade
# =========================================================================
class SupplyChainsConfirmationTest(unittest.TestCase):

    def test_confirmed_lead_stock_upgrades_to_user_input(self):
        # Build synthetic index: "散熱" theme contains 3324
        idx = {"散熱": {"3324", "3017"}}
        self.assertEqual(match_chain_for_theme("AI 伺服器散熱", idx), {"3324", "3017"})
        self.assertEqual(match_chain_for_theme("金融股", idx), set())

    def test_unconfirmed_stock_stays_llm_inference(self):
        idx = {"散熱": {"3324"}}
        opp = {
            "theme": "AI 伺服器散熱",
            "lead_stocks": [
                {"symbol": "3324", "name": "雙鴻"},
                {"symbol": "9999", "name": "未知"},
            ],
        }
        analysis = {"generated_at": "2026-04-18", "opportunities": [opp]}
        enrich_analysis(analysis, idx)
        # Sidecar pattern: raw lead_stocks unchanged, provenance is in sidecar
        prov_list = analysis["opportunities"][0]["_lead_stocks_prov"]
        self.assertEqual(prov_list[0]["source"], "user_input")
        self.assertEqual(prov_list[1]["source"], "llm_inference")
        # Raw lead_stocks stay readable by legacy code (no envelope wrap)
        leads = analysis["opportunities"][0]["lead_stocks"]
        self.assertEqual(leads[0].get("symbol"), "3324")
        self.assertEqual(leads[1].get("symbol"), "9999")


# =========================================================================
# E2E — full analysis JSON from today's brief
# =========================================================================
class EndToEndRealAnalysisTest(unittest.TestCase):

    def test_enrich_real_analysis_no_crash(self):
        # Load today's real JSON and make sure enrichment doesn't explode
        try:
            with open("analyses/2026-04-18.json", "r", encoding="utf-8") as f:
                analysis = json.load(f)
        except FileNotFoundError:
            self.skipTest("analyses/2026-04-18.json missing — skip E2E")
            return

        idx = load_supply_chains()
        enrich_analysis(analysis, idx)

        # Phase-1 scope: sidecar provenance on opportunities[].lead_stocks[]
        opp = analysis["opportunities"][0]
        prov_list = opp.get("_lead_stocks_prov") or []
        self.assertEqual(len(prov_list), len(opp["lead_stocks"]),
                         "sidecar length must match lead_stocks")
        for env in prov_list:
            self.assertTrue(is_enveloped(env))
            self.assertIn(env["source"], SOURCE_DOT)
        # Raw lead_stocks stay untouched (readers don't break)
        for lead in opp["lead_stocks"]:
            self.assertFalse(is_enveloped(lead))
            self.assertIn("symbol", lead)

        # Narrative fields are Phase-2 and should remain strings (raw)
        self.assertIsInstance(analysis["morning_brief"]["headline"], str)

        # Spot-check: serializing after enrichment is clean JSON (no cycles)
        s = json.dumps(analysis, ensure_ascii=False, default=str)
        self.assertGreater(len(s), 1000)
        self.assertNotIn("[object Object]", s)

    def test_render_dot_html_safe_on_all_tiers(self):
        # Sanity-check UI render doesn't crash on any tier
        today = date(2026, 4, 20)
        examples = [
            envelope(1, "primary_report", "2026-04-15", source_ref="ref"),
            envelope(2, "secondary_news", "2026-04-10"),
            envelope(3, "user_input", "2026-04-01"),
            envelope(4, "llm_inference", "2026-02-15", confidence=0.6),
            100,  # raw
        ]
        for e in examples:
            html = render_dot_html(e, "fast", "target_price", today)
            self.assertIsInstance(html, str)
            # No unescaped angle brackets inside the tooltip text
            self.assertNotIn("<script", html.lower())


# =========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)

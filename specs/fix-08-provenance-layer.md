# Fix Spec: Provenance Layer (tag every quant with source + age)

**Status**: shipped · Phase 1 landed 2026-04-19
**Author**: 2026-04-19 systematic brief audit (ian)
**Successor to**: `fix-07-consistency-llm-pass.md` (issue #8 — naked
prediction without source anchoring)

---

## Problem

Every number in the daily brief is presented as if equally trustworthy.
A target price pulled from a TD Cowen report, a Gemini-inferred theme
driver, a TWSE chips number from yesterday's close, and a
manually-curated `supply_chains.yaml` entry all render as the same plain
text. This hides three failure modes:

1. **Stale monitoring** — a "forward P/E 22x" from 60 days ago is
   treated the same as one from yesterday. For a semiconductor stock
   that's a huge miss; for a regulated utility it's fine. But the UI
   doesn't distinguish.
2. **Fabricated citations** — when Gemini names a theme lead stock, the
   UI can't tell whether the pick is yaml-confirmed (🔵 user_input) or
   pure model inference (🔴 llm_inference). User sees "Gemini said 3324
   is in the 散熱 theme" and treats that as fact.
3. **Primary-vs-secondary conflation** — a quote from TD Cowen is not
   the same as an aggregated media headline. Both currently render
   identically.

Framing shift (user directive): **every prediction must be re-framed
as "monitoring variation from an anchor"**. The anchor needs a source
and a timestamp.

---

## Design — provenance envelopes

Every contestable field CAN be wrapped in:

```python
{
    "value":       <raw value>,
    "source":      "primary_report" | "secondary_news" | "user_input" | "llm_inference",
    "as_of":       "YYYY-MM-DD" | None,       # None → UI shows "時間未知"
    "confidence":  0.0–1.0,                    # REQUIRED for llm_inference
    "source_ref":  "TD Cowen 2026-04-15 目標價上修",  # REQUIRED for primary_report
}
```

### Four source tiers (trust order 🟢 > 🔵 > 🟡 > 🔴)

| Tier | Dot | Examples |
|------|-----|----------|
| `primary_report` | 🟢 | TD Cowen / Morgan Stanley research; 10-K/Q; TWSE chips; TAIFEX futures OI |
| `user_input` | 🔵 | `supply_chains.yaml` entries; manual portfolio overrides |
| `secondary_news` | 🟡 | Gemini aggregating 財經媒體 into a single headline |
| `llm_inference` | 🔴 | Gemini thematic pick with no citation; plan_summary narrative |

### Industry-tiered staleness (speed classes)

A 20-day-old target price is stale for semis but fresh for utilities.
`provenance_speed_map.speed_of()` classifies every input into
`fast / medium / slow`; staleness thresholds live in
`provenance.STALENESS_THRESHOLDS`:

```python
"fast":   {"target_price": (14, 30), "chips": (5, 10), ...}
"medium": {"target_price": (30, 60), "chips": (5, 10), ...}
"slow":   {"target_price": (60, 120), "chips": (7, 14), ...}
```

`(yellow_days, red_days)` — at age ≥ yellow we show 🟡; at ≥ red we
show 🔴. Chips thresholds tuned for weekend gap: Friday TWSE data
shown on Sunday (age 2-3) must NOT be stale; only flag if a full
trading week passes without refresh.

### `source_ref` required for primary_report

`envelope()` raises `ValueError` when `source="primary_report"` and
`source_ref` is empty. Reason: a primary-report tier claim without a
citation is indistinguishable from `llm_inference` and is the exact
pattern of "looks authoritative, actually isn't" we want to block.

---

## Implementation

### Three new modules

- **`provenance.py`** — core: `envelope()`, `unwrap()`, `is_enveloped()`,
  `is_stale()`, `render_dot_html()`. Every reader MUST call `unwrap()`
  on potentially-enveloped fields — this is the single backward-compat
  point.
- **`provenance_speed_map.py`** — `speed_of(industry, theme_hint, sector)`
  maps yfinance English industry labels + Chinese theme substrings +
  sector fallbacks into fast/medium/slow.
- **`provenance_enrich.py`** — runtime enrichment. `enrich_chips()`
  wraps TWSE/TAIFEX numeric fields in-place (🟢 envelopes).
  `enrich_analysis()` attaches a **sidecar** `opp["_lead_stocks_prov"]`
  — a parallel list of envelope dicts, one per lead stock. Sidecar
  (not in-place envelope) because ~10 downstream read sites call
  `ls.get("symbol")` directly; wrapping would break every one.

### Runtime enrichment (not disk migration)

`build_dashboard.py` calls `enrich_chips()` in `load_market_chips()`
and `enrich_analysis()` in `load_analysis()` right after existing
validation fixes. Zero changes to `analyze.py`, zero migration of
historical `analyses/*.json`, deterministic re-enrichment for the
audit script.

### Supply-chains confirmation upgrade (🔴 → 🔵)

When Gemini names a lead stock AND `supply_chains.yaml` confirms the
ticker belongs to the same theme (tag/title/slug substring match in
`match_chain_for_theme`), the sidecar envelope for that position is
tagged `source="user_input"` with `source_ref="supply_chains.yaml
確認此檔屬於「<theme>」題材"`. Otherwise `source="llm_inference"`,
`confidence=0.7`, `source_ref="Gemini 指定（supply_chains.yaml 未收錄
此題材映射）"`.

---

## Phase 1 scope (shipped 2026-04-19)

**A.0 — Provenance layer (shipped in earlier commit `6c19296`):**
- TWSE/TAIFEX chips (`foreign_futures.latest.*`, `margin_total.latest.*`,
  `change_1d`, `change_1d_yi`) → 🟢 envelopes, rendered as dots in the
  chips strip.
- `opportunities[].lead_stocks[]` → sidecar provenance envelopes, dots
  rendered on the lead-chip row of each radar card. 🔵/🔴 mix visible
  immediately in the UI.
- Opportunity Radar legend — small text row explaining the four dots.

**A.1 — Change-anchored framing (shipped 2026-04-19, this commit):**
- Top hero label "TODAY · GO" → "🔄 今日訊號變化 · SIGNAL vs 昨日".
  No more naked-prediction framing on the most prominent card.
- Every action body gets an appended checklist template block:
  - 3 verification questions (hedged position / 5% cap / historical comparison)
  - ⚠ Risk callout on sell-side FOMO pattern
  - Historical cautionary case (台光電 1985→4570→回落 30%)
- Empty state: when `action_checklist.green = []`, render
  "今天什麼都不用做" card with concrete triggers synthesized from
  user's OWN holdings + watchlist (never generic examples).
  See `framing.next_triggers_from_portfolio()`.
- `framing.validate_change_anchored_action()` validator shipped but not
  wired — A.2 diff engine will call it as the gate before rendering
  any action as "change-anchored".

**A.1.5 observation phase (next 2-3 days):**
- Track the actual 🟢/🔵/🟡/🔴 distribution in dashboards. The B (synthesis
  card) copy has to be designed around what the data actually looks
  like, not a priori. Do NOT start B before observation is complete.

**A.2 (deferred):**
- Diff engine: compare today's `analyses/YYYY-MM-DD.json` vs the most
  recent prior analysis. Gap > 5 trading days → skip diff entirely
  and force empty state (per user's Q1 answer 2026-04-19).
- Gemini schema extension: emit `change_from` / `exit_condition` fields
  on each action so validator has something to check.
- Wire `validate_change_anchored_action()` into render path: reject
  actions that fail validator, fall through to empty state.

**B (deferred to after A.1.5 observation):**
- Single-card synthesis view — rolls up changes across chips / holdings /
  themes into one "今日發生了什麼" card.

**Out of scope (this ship) — explicitly held for later:**
- **3081 位階-100 硬 veto + 4566 散熱 demote list** — held for separate
  commit after A.1.5 observation. Two-step for clean rollback.
- **Naked-prediction validator enforcement** — validator ships in A.1
  but is NOT yet called. Wiring comes in A.2.

**Out of scope (Phase 2, deferred):**
- Narrative text fields: `morning_brief.headline / one_liner`,
  `topics[].narrative`, `opportunities[].headline / why`,
  `budget_allocation.plan_summary / allocations[].rationale`,
  `holdings_analysis[].commentary`. ~30 read sites would need
  `unwrap()` — blast-radius reduction.
- Per-stock `target_price / eps_forward / pe_forward` staleness chips
  on holdings/watchlist pages.
- Envelope emission from `analyze.py` itself (so future brand-new
  fields are born enveloped instead of being enriched at load time).

---

## Tests (`test_provenance.py`)

21 tests across 6 classes — all green:

1. **EnvelopeRoundtripTest** — wrap + unwrap + raw passthrough +
   `provenance_of` + partial dict is not an envelope
2. **StalenessIndustryTieredTest** — fast/medium/slow thresholds +
   no-as_of returns None + raw value never stale
3. **EnrichmentIdempotentTest** — running twice == running once, for
   both `enrich_analysis` sidecar and `enrich_chips` in-place
4. **RequiredFieldsTest** — `llm_inference` without confidence raises;
   `primary_report` without source_ref raises; other tiers don't
   require extras
5. **SupplyChainsConfirmationTest** — confirmed ticker → 🔵; unknown
   ticker → 🔴; raw `lead_stocks` unchanged (sidecar only)
6. **EndToEndRealAnalysisTest** — load today's real `analyses/*.json`,
   enrich, verify sidecar length matches, narrative fields stay raw,
   `render_dot_html` safe on all tiers

Run:

```
.venv/bin/python test_provenance.py
```

---

## Audit tool

`scripts/provenance_audit.py` — re-enriches every
`analyses/*.json` and reports tier distribution:

```
Files scanned: 1
Total opportunities: 5
Total lead_stocks: 15
  🟢 primary_report        0    0.0%
  🟡 secondary_news        0    0.0%
  🔵 user_input            9   60.0%
  🔴 llm_inference         6   40.0%
```

Use after any `supply_chains.yaml` edit to see how many picks got
promoted 🔴 → 🔵.

---

## Open questions — **defaults shipped, revisitable**

1. **legacy `as_of = null` strategy** → Option B shipped (UI shows
   "📅 時間未知" for historical fields without known source time).
   Revisit: backfill-as_of from brief generated_at where appropriate.
2. **Industry-tiered thresholds** → shipped. Revisit chips(5,10) if
   the weekend-gap tolerance feels off in practice.
3. **`source_ref` required for primary_report** → shipped (raises on
   missing).
4. **Monitoring-variation framing** → partial (legend text uses
   "資料來源"; per-card "vs anchor" framing deferred to Phase 2).
5. **User-input tier split** → shipped as 🔵 user_input (unified tier
   with `source_ref` that names the source: supply_chains.yaml /
   holdings YAML / etc.).

---

## Files touched (Phase 1)

```
NEW:
  provenance.py                          ~260 lines
  provenance_speed_map.py                ~200 lines
  provenance_enrich.py                   ~275 lines
  test_provenance.py                     ~270 lines
  scripts/provenance_audit.py            ~185 lines
  specs/fix-08-provenance-layer.md       this file

EDITED:
  build_dashboard.py
    + imports (provenance, speed_map, enrich)
    + module-level _SUPPLY_CHAINS_IDX cache
    + load_analysis() → enrich_analysis() after validation_fixes
    + load_market_chips() → enrich_chips() before return
    + render_market_chips_strip() → unwrap + render_dot_html on
      net_oi / change_1d / balance_yi / short_lots / change_1d_yi
    + render_radar_tab() → prov_legend row + per-lead prov_dot
    + ~65 lines of CSS for .prov-dot / .prov-stale-chip / .prov-legend
```

# Fix Spec: Consistency-Check LLM Pass

**Status**: proposed · uncommitted · ready to implement
**Author**: 2026-04-19 systematic brief audit (ian)
**Predecessor**: commit `2044b08` — fixed 7 of 9 structural contradictions
  (不追高、題材紅燈、validator writeback、macro gate、信心度對齊、GAP RADAR
  tag、集中度提醒). This spec handles the remaining 2 narrative-level
  contradictions that structural code cannot catch.

---

## Problem

The existing pipeline has two separate LLM steps (Gemini for analysis +
Python validator for structural QA) plus ~6 hard filters in
`build_dashboard.py`. After today's fix batch, **all structural
contradictions are blocked**, but **two classes of narrative contradictions
still leak through**:

### Class A · Intra-record directive mismatch (issue #7)

Same ticker, same day, two fields give opposite instructions:

```text
action_checklist.green[0]:
  "站穩 5ma 再試水 — 不要開盤直接進"

budget_allocation.allocations[0] (same ticker):
  "盤前 / 開盤價 2600 直接新倉試水"
```

No validator catches this because the **fields are individually valid JSON**.
The contradiction only exists when you read them together as a human.

### Class B · Stale prose after data-layer correction (meta issue)

When `apply_validation_fixes` rewrites opportunity lead_stocks (e.g.
removes 4566 from 散熱 theme) OR the snowball filter blocks a pick (e.g.
3081), **the explanatory prose elsewhere in the brief still references
the old data**:

- `topics[].narrative` still says "時碩工業 (4566) 伺服器散熱產品將放量"
- `budget_allocation.plan_summary` still says "將 NT$5,000 用於新倉試水
  聯亞 (3081)..." even though 3081 is no longer in `allocations[]`
- `morning_brief.headline` may reference tickers that the filters removed

No single writeback rule can safely rewrite free-form Chinese prose —
that's NLP territory.

---

## Proposed solution

Add a **post-processing LLM pass** that runs after `analyze.py` +
`validate_analysis.py` + hard-filter pipeline produces the final analysis
JSON. The pass does ONE job:

> Given the final `allocations[]` list (post-filter) and the raw prose
> fields, rewrite any prose that references tickers NOT in the final
> allocations OR contradicts the directives in allocations.

**Input shape**:
```json
{
  "final_allocations": [
    {"symbol": "3324", "action": "觀望等進場",
     "entry_condition": "等拉回 170 以下分批進"},
    {"symbol": "CASH", ...},
    {"symbol": "DIVERSIFY", ...}
  ],
  "narrative_fields": {
    "budget_allocation.plan_summary": "...(raw text)...",
    "morning_brief.headline": "...(raw text)...",
    "morning_brief.risks[*]": ["...", "..."],
    "topics[*].narrative": ["...", "..."],
    "action_checklist.green[*].reason": ["...", "..."]
  },
  "removed_tickers": ["3081", "6173", "1815", "4566", "5475", "2492"],
  "removal_reasons": {
    "3081": "snowball rule: 30d +46%, 52w-pos 100%",
    "6173": "red list (被動元件追高警告)",
    "4566": "validator: wrong theme placement"
  }
}
```

**Output shape**:
```json
{
  "rewritten": {
    "budget_allocation.plan_summary":
      "今日建議將 NT$5,000 預算分批試水 3324 雙鴻（散熱題材領導股）...
       原本提到的 3081 聯亞因短期已大漲 +46%、站上 52 週高，依雪球法
       不追高規則本次不進。",
    "topics[0].narrative": "(rewritten, 4566 reference removed)",
    ...
  },
  "changelog": [
    "budget_plan: 3081 移除，改述 3324",
    "topic[1]: 4566 (時碩工業) 不屬散熱，改述同題材 3017/3324"
  ]
}
```

### Same pass also handles Class A directive mismatch

Prompt addition:

> For each ticker appearing in BOTH `action_checklist.green` AND
> `budget_allocation.allocations`, verify the two instructions are
> compatible. If they conflict (one says wait, one says enter), rewrite
> the green action text to match the allocation's entry_condition.

---

## Implementation

### File: `consistency_pass.py` (new, ~200 lines)

```python
def run_consistency_pass(analysis: dict, removed: set[str]) -> dict:
    """Run Gemini Flash on the analysis to rewrite stale narratives.

    Args:
      analysis: the already-validated, already-filtered analysis JSON
      removed:  tickers that the hard-filter pipeline stripped out
                (snowball, red-theme, red-symbol vetoes)

    Returns:
      analysis dict with narrative fields rewritten; also writes
      consistency_changelog.json alongside validation_report.json so
      the UI can surface "3 narratives were rewritten by consistency
      pass" in the validator banner.
    """
```

### Orchestration (update `analyze.py` tail)

```python
# After validate_analysis.py runs...
from consistency_pass import run_consistency_pass
removed = _collect_removed_tickers(analysis, original_allocs)
analysis = run_consistency_pass(analysis, removed)
```

### Budget / gate

- Use `gemini-2.5-flash` (same as analyze.py) — cheap, fast.
- Only fire when `len(removed) > 0` OR `any(conflict_detected)` — skip
  the call on clean days to save cost.
- Hard-wire max_tokens ≈ 1500. Expect 200-400 output tokens on busy days.
- Timeout 15s; fall back to unrewritten prose if the call fails.

### Validation guardrails (to avoid LLM hallucination)

1. **Ticker guard**: after rewrite, ensure no removed ticker appears in
   any rewritten field. If it does → reject the rewrite, log, keep
   original.
2. **Length guard**: rewrite must be ≤ 1.5× original length. Prevents
   model from adding invented content.
3. **Key guard**: rewrite must keep `key_name` (e.g. "題材"、"進場條件")
   the same — model can only touch prose, not structure.

---

## Test cases

```python
def test_3081_prose_rewrite():
    analysis = {
        "budget_allocation": {
            "plan_summary": "建議把預算放 3081 聯亞...",
            "allocations": [{"symbol": "3324"}],  # 3081 already filtered
        }
    }
    result = run_consistency_pass(analysis, removed={"3081"})
    assert "3081" not in result["budget_allocation"]["plan_summary"]
    assert "3324" in result["budget_allocation"]["plan_summary"]


def test_directive_mismatch():
    analysis = {
        "action_checklist": {
            "green": [{"symbol": "3324", "action": "站穩 5ma 再試水"}]
        },
        "budget_allocation": {
            "allocations": [{"symbol": "3324",
                             "entry_condition": "盤前開盤價直接進場"}]
        }
    }
    result = run_consistency_pass(analysis, removed=set())
    # After rewrite, green action should match allocation
    assert "開盤" in result["action_checklist"]["green"][0]["action"] or \
           "試水" in result["budget_allocation"]["allocations"][0]["entry_condition"]
```

---

## Definition of done

- [ ] `consistency_pass.py` created + unit tests pass
- [ ] Integrated into `analyze.py` pipeline (runs after `validate_analysis.py`)
- [ ] Changelog written to `consistency_changelog.json`
- [ ] Validator banner surfaces "N narratives rewritten" when count > 0
- [ ] One end-to-end run on 2026-04-18 data shows:
  - Budget plan no longer mentions 3081
  - Topic narrative no longer claims 4566 is a 散熱 stock
  - If green + allocation disagree, they've been reconciled
- [ ] Ship gate: cost per day < NT$1 (one extra Flash call)

---

## Why this was deferred

The user's 2026-04-19 audit triaged 9 issues. Issues #1-#6 and #8-#9 are
**structural** (code-level rules that block contradictions before they
enter the data layer). Those were fixed in commit `2044b08`.

Issue #7 (and the meta class B) are **narrative** — they require
sentence-level understanding to detect and fix, which is an LLM task.
Rather than hand-craft fragile regex patches, we route through a dedicated
LLM pass.

**Risk accepted for one release cycle**: the current brief (2026-04-18)
has stale prose referencing 3081 in the budget_plan summary and 4566
in topic narratives. Structural UI (basket cards, opp chips, picks) is
all correct — the prose is the only leak. User was warned; they accepted
the gap in exchange for shipping the 7 structural fixes today.

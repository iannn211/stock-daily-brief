"""Change-anchored action framing (Spec fix-08 · A.1).

User's core directive: **every action must be anchored to a change**,
never a naked prediction. This module ships:

1. `validate_change_anchored_action()` — guard rail for A.2 diff engine.
   Rejects action dicts that look like naked predictions (confidence
   scores, specific entry/stop numbers, no change anchor, stale
   changes > 14 days).

2. `next_triggers_from_portfolio()` — empty-state helper. When there's
   nothing to do today, surface concrete triggers from the user's own
   holdings + watchlist (NOT generic examples). Outputs strings like:
     📊 2026-04-29 聯電法說會
     🎯 2330 跌破 1,950 → 加碼觀察
     🎯 0050 跌破 82 → 考慮定期定額加碼

Phase-1 note (A.1, shipped 2026-04-19)
--------------------------------------
A.1 ships the VALIDATOR + EMPTY-STATE HELPER + UI copy change (checklist
block in hero-action) as pure text work. No diff engine yet — the
validator's caller comes in A.2 when we actually build the day-to-day
diff machinery.

Current behaviour (A.1):
  - Renderer in build_dashboard.py renders Gemini action text as-is,
    framed with a checklist template.
  - When action_checklist.green is empty, renderer calls
    `next_triggers_from_portfolio()` to produce the empty state.
  - Validator is shipped but not yet wired to any decision point.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

# ---------------------------------------------------------------- validator --

# Patterns that MUST NOT appear in change-anchored action text — these are
# classic naked-prediction tells.
_NAKED_PATTERNS = [
    re.compile(r"信心\s*\d+\s*%"),           # "信心 75%"
    re.compile(r"confidence\s*\d+", re.I),   # "confidence 75"
    re.compile(r"入場\s*\d+[-~至到]\s*\d+"),  # "入場 95-100"
    re.compile(r"停損\s*\d+(\.\d+)?"),        # "停損 88"
    re.compile(r"目標\s*\d+(\.\d+)?(?!\s*[%％])"),  # "目標 200" (but not 目標 10%)
]

_MAX_CHANGE_AGE_DAYS = 14


def validate_change_anchored_action(
    action: dict,
    today: date | None = None,
) -> tuple[bool, str | None]:
    """Return (is_valid, reason_if_invalid).

    A change-anchored action MUST:
      1. Have a `change` dict with `as_of` + (`old` or `new`) fields.
      2. `change.as_of` must be within _MAX_CHANGE_AGE_DAYS of today.
      3. Text fields (`action`, `reason`) must NOT contain naked-prediction
         patterns (信心 X%, 入場 Y-Z, 停損 N, etc.).

    Returns (False, reason) for the first failure; (True, None) on pass.
    This is a HARD rule — A.2 renderer will swap in empty state rather
    than render an invalid action, so we fail loudly.
    """
    if not isinstance(action, dict):
        return False, "not a dict"

    # (3) scan text fields for naked-prediction patterns FIRST —
    # they're the loudest tell and cheapest to detect.
    for field in ("action", "reason"):
        text = action.get(field) or ""
        for pat in _NAKED_PATTERNS:
            m = pat.search(text)
            if m:
                return False, (
                    f"naked prediction in {field}: matched '{m.group(0)}' "
                    f"(pattern: {pat.pattern})"
                )

    # (1) must have a change anchor
    change = action.get("change")
    if not isinstance(change, dict):
        return False, "missing `change` anchor (no old→new delta cited)"

    if "old" not in change and "new" not in change:
        return False, "`change` must have `old` or `new` value"

    # (2) change must be fresh
    as_of = change.get("as_of")
    if not as_of:
        return False, "`change.as_of` is missing (source timestamp required)"

    try:
        d = date.fromisoformat(str(as_of)[:10])
    except Exception:
        return False, f"`change.as_of` is not ISO date: {as_of!r}"

    t = today or date.today()
    age = (t - d).days
    if age > _MAX_CHANGE_AGE_DAYS:
        return False, (
            f"`change.as_of` is {age} days old "
            f"(max {_MAX_CHANGE_AGE_DAYS}d for change-anchored actions)"
        )

    return True, None


# ------------------------------------------------------ empty-state triggers --

def next_triggers_from_portfolio(
    pf: dict | None,
    analysis: dict | None = None,
    max_items: int = 5,
) -> list[str]:
    """Generate concrete '下一個觸發' strings from the user's actual
    holdings + watchlist + analysis.

    Output examples:
      📊 2026-04-29 聯電法說會
      🎯 2330 跌破 1,950 → 加碼觀察 (-4% vs 現價 2,030)
      🎯 0050 跌破 82 → 考慮定期定額加碼 (-2.6% vs 現價 84.15)
      🎯 2303 漲破 75 → 確認突破進場 (+2.7% vs 現價 73)

    Rules:
      - Extract date-embedded catalysts from analysis text (法說會, EPS,
        納入 0050 etc.) — surface up to 2.
      - For top holdings by dollar value (not all holdings), emit a -4%
        "加碼觀察" trigger.
      - For top watchlist items, emit a -5% "首筆進場觀察" trigger.
      - Never return more than `max_items`. Never return generic examples
        (e.g., hard-coded 聯亞 if user doesn't hold it).
    """
    out: list[str] = []
    pf = pf or {}

    # --- 1. Date catalysts from analysis action/reason text (up to 2) ---
    # Gemini often embeds dates like "4/29" or "2026-04-29" in action text.
    if analysis:
        dated = _extract_dated_catalysts(analysis)
        out.extend(dated[:2])

    # --- 2. Holdings: -4% pullback trigger for top 2 by dollar value ---
    holdings = pf.get("holdings") or []
    sized: list[tuple[float, dict]] = []
    for h in holdings:
        try:
            v = float(h.get("market_value") or 0) or float(
                (h.get("price") or 0) * (h.get("shares") or 0)
            )
        except Exception:
            v = 0.0
        if v > 0 and h.get("symbol") and h.get("price"):
            sized.append((v, h))
    sized.sort(reverse=True, key=lambda t: t[0])
    for _, h in sized[:2]:
        sym = h["symbol"]
        price = float(h["price"])
        name = h.get("name", "")
        trigger = round(price * 0.96, 1 if price < 100 else 0)
        drop_pct = (trigger - price) / price * 100
        action_text = (
            "考慮定期定額加碼"
            if sym in {"0050", "006208", "VOO", "QQQ"}
            else "加碼觀察"
        )
        out.append(
            f"🎯 {sym} 跌破 {_fmt_price(trigger)} → {action_text} "
            f"({drop_pct:+.1f}% vs 現價 {_fmt_price(price)})"
        )

    # --- 3. Watchlist: -5% pullback for top 2 by price (loose proxy) ---
    watchlist = pf.get("watchlist") or []
    wl_filtered = [
        w for w in watchlist
        if w.get("symbol") and w.get("price")
    ][:2]
    for w in wl_filtered:
        sym = w["symbol"]
        price = float(w["price"])
        trigger = round(price * 0.95, 1 if price < 100 else 0)
        drop_pct = (trigger - price) / price * 100
        out.append(
            f"🎯 {sym} 跌破 {_fmt_price(trigger)} → 首筆進場觀察 "
            f"({drop_pct:+.1f}% vs 現價 {_fmt_price(price)})"
        )

    return out[:max_items]


# Catalysts surface-pattern: look for (a) a date token, (b) nearby keyword.
_DATE_TOKEN = re.compile(
    r"(?:(?P<iso>\d{4}-\d{2}-\d{2})|"
    r"(?P<md>\d{1,2}/\d{1,2}))"
)
_CATALYST_KEYWORDS = ["法說", "EPS", "財報", "納入 0050", "除息", "除權",
                      "FOMC", "CPI"]


def _extract_dated_catalysts(analysis: dict) -> list[str]:
    """Scan action_checklist + opportunities text for date-embedded catalysts.

    Produces clean short labels like:
      📊 4/29 聯電法說會
      📊 4/18 欣興納入 0050

    Strategy: for every (date, keyword) pair found in the same sentence,
    emit a label of "{date} + short subject around keyword". Subject is
    trimmed to a tight window and stripped of grammar filler (天後, 當天
    盤前, also, etc.).
    """
    chunks: list[str] = []
    for act in (analysis.get("action_checklist") or {}).get("green", []) or []:
        chunks.append(f'{act.get("action", "")} · {act.get("reason", "")}')
    for opp in (analysis.get("opportunities") or []):
        chunks.append(f'{opp.get("headline", "")} · {opp.get("why", "")}')

    found: list[str] = []
    seen: set[str] = set()
    for text in chunks:
        m = _DATE_TOKEN.search(text)
        if not m:
            continue
        kw_hit = next((k for k in _CATALYST_KEYWORDS if k in text), None)
        if not kw_hit:
            continue
        date_str = m.group("iso") or m.group("md")
        # Pick a short subject — 4 chars before the keyword + the keyword +
        # a few chars after, trimmed at sentence boundaries.
        idx = text.find(kw_hit)
        left = max(0, idx - 4)
        right = min(len(text), idx + len(kw_hit) + 4)
        snippet = text[left:right]
        # Strip common grammar filler & trailing punctuation
        for junk in ("天後", "當天盤前", "當天", "也會", "盤前"):
            snippet = snippet.replace(junk, " ")
        # Drop parens / comma / spaces / middle-dots
        snippet = re.sub(r"[(),\s·，。]+", " ", snippet).strip()
        # Drop leading digits echoing the date (e.g. "18 納入..." from "4/18")
        snippet = re.sub(r"^\d+\s*", "", snippet)
        # Drop any remaining date-shaped prefix
        snippet = re.sub(rf"^{re.escape(date_str)}\s*", "", snippet)
        # Fallback: if snippet ended up empty, skip this catalyst
        if not snippet.strip():
            continue
        key = f"{date_str}|{kw_hit}"
        if key in seen:
            continue
        seen.add(key)
        found.append(f"📊 {date_str} {snippet}")
    return found


def _fmt_price(x: float) -> str:
    """Format TW stock prices: integer above 100, 1 dp below."""
    if x >= 100:
        return f"{int(round(x)):,}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


# ---------------------------------------------------------- self-test driver --

if __name__ == "__main__":
    # Validator smoke
    ok, _ = validate_change_anchored_action({
        "action": "若加碼，先確認倉位上限",
        "reason": "目標價 4 天上修",
        "change": {"old": 140, "new": 175, "as_of": date.today().isoformat()},
    })
    assert ok, "valid change-anchored action should pass"

    bad, reason = validate_change_anchored_action({
        "action": "買聯亞 3081，信心 75%",
        "reason": "突破",
        "change": {"old": 90, "new": 100, "as_of": date.today().isoformat()},
    })
    assert not bad and "naked prediction" in (reason or "")

    print("framing.py: ✓ smoke tests passed")

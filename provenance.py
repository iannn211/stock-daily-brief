"""Provenance layer (Spec fix-08).

Every quantitative / contested field in the brief can be wrapped in a
provenance envelope so the UI can show:
  - where the value came from (source tier)
  - when it was last updated (as_of)
  - how confident the source is (confidence)
  - a human-readable reference (source_ref)

Design notes
------------
1. Envelopes are **optional**. Readers must tolerate both raw and enveloped
   values — call `unwrap()` to get the underlying value regardless.
2. Four source tiers, trust order:  🟢 > 🔵 > 🟡 > 🔴
     🟢 primary_report  — analyst report, 10-K/Q, TWSE chips, official filings
     🟡 secondary_news  — aggregated media, reporter synthesis
     🔵 user_input      — user manually entered in yaml / overrode
     🔴 llm_inference   — Gemini inference without primary citation
3. `confidence` is only meaningful (and required) for `llm_inference`.
   Other tiers are treated as 1.0 unless explicitly lowered.
4. `is_stale` uses industry-tiered thresholds (fast/medium/slow) — defined
   in `provenance_speed_map.py`. See spec fix-08 §Staleness for rationale.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

# ---------------------------------------------------------------- constants --

SOURCE_TIERS = {"primary_report", "secondary_news", "user_input", "llm_inference"}

SOURCE_DOT = {
    "primary_report":  "🟢",
    "secondary_news":  "🟡",
    "user_input":      "🔵",
    "llm_inference":   "🔴",
}

SOURCE_LABEL_ZH = {
    "primary_report":  "原始報告／官方數據",
    "secondary_news":  "媒體綜合",
    "user_input":      "使用者手動輸入",
    "llm_inference":   "LLM 推論",
}

# Staleness thresholds, keyed by (speed, field_type).
# Values are tuples of (yellow_days, red_days) — >= yellow is 🟡, >= red is 🔴.
# See provenance_speed_map.py for the industry→speed classification.
STALENESS_THRESHOLDS: dict[str, dict[str, tuple[int, int]]] = {
    "fast": {
        "target_price":     (14, 30),
        "eps_forward":      (45, 90),
        "pe_forward":       (45, 90),
        "dividend_yield":   (45, 90),
        "social_sentiment": (3, 7),
        "narrative":        (3, 7),
        # Chips threshold (5, 10) tolerates weekend gap: Fri chips shown on
        # Sun/Mon (age 2-3) should NOT be stale — market's closed, that *is*
        # the latest available. Only flag when chips truly haven't refreshed
        # for a full trading week (age >= 5).
        "chips":            (5, 10),
        "default":          (14, 30),
    },
    "medium": {
        "target_price":     (30, 60),
        "eps_forward":      (60, 120),
        "pe_forward":       (60, 120),
        "dividend_yield":   (60, 120),
        "social_sentiment": (7, 14),
        "narrative":        (7, 14),
        "chips":            (5, 10),
        "default":          (30, 60),
    },
    "slow": {
        "target_price":     (60, 120),
        "eps_forward":      (90, 180),
        "pe_forward":       (90, 180),
        "dividend_yield":   (90, 180),
        "social_sentiment": (14, 30),
        "narrative":        (14, 30),
        "chips":            (7, 14),
        "default":          (60, 120),
    },
}


# --------------------------------------------------------------- core utils --

def envelope(
    value: Any,
    source: str,
    as_of: str | None,
    confidence: float | None = None,
    source_ref: str | None = None,
) -> dict:
    """Wrap a primitive into a provenance envelope.

    Args:
        value: the underlying value (any JSON-serializable type).
        source: one of SOURCE_TIERS.
        as_of: ISO-8601 date string ("YYYY-MM-DD"); None = unknown (UI will
               show "📅 時間未知 ⚠").
        confidence: 0.0–1.0; REQUIRED for llm_inference, optional otherwise.
        source_ref: human-readable citation string (e.g. "TD Cowen 2026-04-15").
                    REQUIRED for primary_report (see spec Open Q3).

    Returns:
        Envelope dict: {value, source, as_of, [confidence], [source_ref]}

    Raises:
        ValueError: on invalid source tier or missing required fields.
    """
    if source not in SOURCE_TIERS:
        raise ValueError(
            f"source must be one of {SOURCE_TIERS!r}, got {source!r}"
        )
    if source == "llm_inference" and confidence is None:
        raise ValueError(
            "llm_inference envelopes MUST carry a confidence (0.0–1.0). "
            "If you don't know, use 0.5 as a neutral default."
        )
    if source == "primary_report" and not source_ref:
        raise ValueError(
            "primary_report envelopes MUST carry source_ref "
            "(e.g. 'TD Cowen 2026-04-15 目標價上修'). "
            "Without a citation it's indistinguishable from LLM inference."
        )
    out: dict[str, Any] = {"value": value, "source": source, "as_of": as_of}
    if confidence is not None:
        out["confidence"] = round(float(confidence), 3)
    if source_ref:
        out["source_ref"] = source_ref
    return out


def is_enveloped(x: Any) -> bool:
    """True if x is a provenance envelope (has value + source + as_of keys).

    Note: we require ALL three keys so that a random dict like
    {"value": 1, "other": "x"} doesn't falsely match.
    """
    return (
        isinstance(x, dict)
        and "value" in x
        and "source" in x
        and "as_of" in x
    )


def unwrap(x: Any) -> Any:
    """Return x['value'] if x is an envelope, else x itself.

    Readers should call this EVERYWHERE they consume a potentially-enveloped
    field. This is the single place that gives backward compatibility with
    raw values.
    """
    return x["value"] if is_enveloped(x) else x


def provenance_of(x: Any) -> tuple[str, str | None, float | None, str | None] | None:
    """Extract (source, as_of, confidence, source_ref) from an envelope.

    Returns None if x is not an envelope.
    """
    if not is_enveloped(x):
        return None
    return (
        x["source"],
        x.get("as_of"),
        x.get("confidence"),
        x.get("source_ref"),
    )


# ----------------------------------------------------------------- staleness --

def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        # Accept "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS..."
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None


def age_days(x: Any, today: date | None = None) -> int | None:
    """Days since as_of. Returns None if no valid date on envelope."""
    prov = provenance_of(x)
    if not prov:
        return None
    _, as_of, _, _ = prov
    d = _parse_date(as_of)
    if d is None:
        return None
    t = today or date.today()
    return (t - d).days


def is_stale(
    x: Any,
    speed: Literal["fast", "medium", "slow"],
    field_type: str = "default",
    today: date | None = None,
) -> str | None:
    """Return '🟡', '🔴', or None based on industry-tiered thresholds.

    Args:
        x: enveloped value (or raw — returns None for raw).
        speed: industry speed class (from provenance_speed_map.speed_of).
        field_type: which staleness table to use. Falls back to 'default'
                    if unknown.
        today: override for testing.

    Non-envelope inputs return None (no staleness signal on raw values).
    Missing as_of returns None (UI should show "時間未知" separately).
    """
    age = age_days(x, today)
    if age is None:
        return None
    table = STALENESS_THRESHOLDS.get(speed, STALENESS_THRESHOLDS["medium"])
    y, r = table.get(field_type, table["default"])
    if age >= r:
        return "🔴"
    if age >= y:
        return "🟡"
    return None


# --------------------------------------------------------------- UI helpers --

def build_tooltip(
    source: str,
    as_of: str | None,
    source_ref: str | None,
    confidence: float | None,
    age: int | None,
) -> str:
    """Compose the hover tooltip for a field dot. HTML-escape-safe in the
    sense that it returns plain text (caller is responsible for escaping)."""
    parts = [SOURCE_LABEL_ZH.get(source, source)]
    if source_ref:
        parts.append(f"「{source_ref}」")
    if as_of:
        if age is not None and age >= 0:
            parts.append(f"📅 {as_of}（{age} 天前）")
        else:
            parts.append(f"📅 {as_of}")
    else:
        parts.append("📅 時間未知")
    if confidence is not None and source == "llm_inference":
        parts.append(f"信心 {confidence:.0%}")
    return " · ".join(parts)


def render_dot_html(x: Any, speed: str = "medium", field_type: str = "default",
                    today: date | None = None) -> str:
    """Return HTML for a provenance dot + optional stale chip.

    If x is raw (not enveloped), returns empty string — nothing rendered.
    Safe to call unconditionally; backward-compatible.
    """
    prov = provenance_of(x)
    if not prov:
        return ""
    source, as_of, conf, ref = prov
    dot = SOURCE_DOT.get(source, "⚪")
    age = age_days(x, today)
    stale = is_stale(x, speed, field_type, today)  # type: ignore[arg-type]
    stale_class = ""
    stale_chip = ""
    if stale == "🟡":
        stale_class = " prov-stale-yellow"
        stale_chip = f'<span class="prov-stale-chip prov-stale-chip-yellow">⚠ {age} 天</span>'
    elif stale == "🔴":
        stale_class = " prov-stale-red"
        stale_chip = f'<span class="prov-stale-chip prov-stale-chip-red">⚠ {age} 天</span>'

    tooltip = build_tooltip(source, as_of, ref, conf, age)
    # Escape minimal HTML in tooltip (quotes)
    tooltip_esc = tooltip.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<span class="prov-dot prov-{source}{stale_class}" '
        f'title="{tooltip_esc}">{dot}</span>{stale_chip}'
    )


# ---------------------------------------------------------- self-test driver --

if __name__ == "__main__":
    # Smoke test — run via `python3 provenance.py`
    e = envelope(150, "primary_report", "2026-04-15",
                 source_ref="TD Cowen 2026-04-15")
    assert unwrap(e) == 150
    assert unwrap(150) == 150
    assert is_enveloped(e)
    assert not is_enveloped({"value": 1})
    assert not is_enveloped(150)

    prov = provenance_of(e)
    assert prov == ("primary_report", "2026-04-15", None, "TD Cowen 2026-04-15")

    # llm_inference without confidence → error
    try:
        envelope("x", "llm_inference", "2026-04-15")
        assert False, "should have raised"
    except ValueError:
        pass

    # llm_inference with confidence → ok
    e2 = envelope("x", "llm_inference", "2026-04-15", confidence=0.7)
    assert unwrap(e2) == "x"

    # primary_report without source_ref → error
    try:
        envelope(150, "primary_report", "2026-04-15")
        assert False, "should have raised"
    except ValueError:
        pass

    # staleness check — fake today to avoid real-clock dependency
    today = date(2026, 4, 20)
    e_fast_stale = envelope(150, "secondary_news", "2026-04-04")  # 16 days old
    assert is_stale(e_fast_stale, "fast", "target_price", today) == "🟡"
    assert is_stale(e_fast_stale, "slow", "target_price", today) is None
    e_very_stale = envelope(150, "secondary_news", "2026-02-01")  # ~78 days
    assert is_stale(e_very_stale, "fast", "target_price", today) == "🔴"

    print("provenance.py: ✓ smoke tests passed")

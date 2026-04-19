"""Runtime envelope injection (Spec fix-08).

Instead of migrating analyses/*.json on disk to carry provenance envelopes,
we enrich the loaded data *at runtime* — the dashboard builder calls
`enrich_analysis(analysis_dict, supply_chains)` right after load and gets back
a dict where the Phase-1 whitelist fields are wrapped in envelopes.

Why runtime enrichment (instead of migrating disk)
--------------------------------------------------
1. Zero changes to `analyze.py` (no producer-side risk).
2. Zero migration for existing `analyses/*.json` — all backward compatible.
3. Deterministic: the audit script can re-enrich historical files the same
   way to compute source distribution.
4. If Gemini eventually outputs native envelopes, `unwrap()` handles both —
   this layer just becomes a no-op for already-enveloped fields.

Rules summary (Phase-1 whitelist)
---------------------------------
chips.json (TWSE/TPEx official data):
  foreign_futures.*, margin_total.* → 🟢 primary_report, as_of = latest trading day

analyses/*.json:
  morning_brief.headline / one_liner          → 🔴 llm_inference (conf 0.7)
  topics[].narrative                          → 🔴 llm_inference (conf 0.7)
  opportunities[].headline / why              → 🔴 if no sources, 🟡 if sources present
  opportunities[].lead_stocks[]               → 🔴, upgrade to 🔵 if supply_chains.yaml
                                                  confirms (theme match + ticker match)
  budget_allocation.plan_summary              → 🔴 llm_inference
  budget_allocation.allocations[].rationale   → 🔴 llm_inference
  holdings_analysis[].commentary              → 🔴 llm_inference
"""
from __future__ import annotations

from typing import Any

try:
    import yaml  # PyYAML
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from provenance import envelope, is_enveloped, unwrap


# ---------------------------------------------------- supply_chains loader --

def load_supply_chains(path: str | None = None) -> dict[str, set[str]]:
    """Parse supply_chains.yaml into {theme_keyword → set of tickers}.

    Returns a dict mapping each keyword tag (from chains[*].tags + title) to
    the full set of tickers appearing in that chain's layers. Used later by
    `match_chain_for_theme` to look up which tickers are legit for a given
    Gemini theme.
    """
    if yaml is None:
        return {}
    path = path or "supply_chains.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    chains = data.get("chains") or {}
    kw_to_tickers: dict[str, set[str]] = {}
    for slug, chain in chains.items():
        tickers: set[str] = set()
        for layer in (chain.get("layers") or []):
            for stk in (layer.get("stocks") or []):
                sym = (stk.get("symbol") or "").strip()
                if sym:
                    tickers.add(sym)
        # Index by every tag + the title itself + the slug
        keys: list[str] = []
        keys.extend(chain.get("tags") or [])
        if chain.get("title"):
            keys.append(chain["title"])
        keys.append(slug)
        for k in keys:
            k = (k or "").strip()
            if not k:
                continue
            kw_to_tickers.setdefault(k, set()).update(tickers)
    return kw_to_tickers


def match_chain_for_theme(
    theme: str,
    kw_to_tickers: dict[str, set[str]],
) -> set[str]:
    """Return the union of tickers from all chains whose tag/title appears
    in the given theme string. Empty set if no match."""
    if not theme or not kw_to_tickers:
        return set()
    hits: set[str] = set()
    for kw, tickers in kw_to_tickers.items():
        # substring match both directions (user tag may be shorter or longer
        # than Gemini theme — whichever contains the other counts)
        if kw and (kw in theme or theme in kw):
            hits.update(tickers)
    return hits


# ---------------------------------------------------- chips.json enrichment --

def enrich_chips(chips: dict | None) -> dict | None:
    """Wrap the numeric fields in chips.json with 🟢 primary_report envelopes.

    The caller (build_dashboard) should pass the result into its existing
    render path — readers will call `unwrap()` to get the raw number back,
    but can also read provenance for rendering dots.

    Non-destructive: if chips is None, returns None. If already enveloped,
    passes through unchanged (idempotent).
    """
    if not chips:
        return chips

    # Foreign futures block
    ff = chips.get("foreign_futures") or {}
    latest_ff = ff.get("latest") or {}
    as_of_ff = latest_ff.get("date")  # e.g. "2026-04-17"
    if as_of_ff:
        for k in ("net_oi", "long_oi", "short_oi"):
            if k in latest_ff and not is_enveloped(latest_ff[k]):
                latest_ff[k] = envelope(
                    latest_ff[k],
                    "primary_report",
                    as_of_ff,
                    source_ref="TAIFEX 三大法人期貨未平倉",
                )
        if "change_1d" in ff and not is_enveloped(ff["change_1d"]):
            ff["change_1d"] = envelope(
                ff["change_1d"],
                "primary_report",
                as_of_ff,
                source_ref="TAIFEX 三大法人期貨未平倉（日變化）",
            )

    # Margin block
    mg = chips.get("margin_total") or {}
    latest_mg = mg.get("latest") or {}
    as_of_mg = latest_mg.get("date")
    if as_of_mg:
        for k in ("balance_yi", "short_lots"):
            if k in latest_mg and not is_enveloped(latest_mg[k]):
                latest_mg[k] = envelope(
                    latest_mg[k],
                    "primary_report",
                    as_of_mg,
                    source_ref="TWSE 融資融券餘額",
                )
        if "change_1d_yi" in mg and not is_enveloped(mg["change_1d_yi"]):
            mg["change_1d_yi"] = envelope(
                mg["change_1d_yi"],
                "primary_report",
                as_of_mg,
                source_ref="TWSE 融資餘額（日變化）",
            )

    return chips


# ------------------------------------------------ analysis.json enrichment --

_LLM_CONF_DEFAULT = 0.7   # Gemini narrative fields default confidence
_LLM_CONF_LOW = 0.5       # used for fields without explicit citation


def _envelope_text(
    value: Any,
    as_of: str,
    *,
    has_sources: bool = False,
    conf: float = _LLM_CONF_DEFAULT,
) -> Any:
    """Wrap a text/primitive as llm_inference OR secondary_news depending on
    whether the parent block has sources."""
    if value is None or is_enveloped(value):
        return value
    if has_sources:
        return envelope(
            value,
            "secondary_news",
            as_of,
            source_ref="Gemini 綜合財經媒體",
        )
    return envelope(value, "llm_inference", as_of, confidence=conf)


def enrich_analysis(
    analysis: dict,
    supply_chains_index: dict[str, set[str]] | None = None,
) -> dict:
    """Compute provenance metadata for Phase-1 whitelist fields.

    Mutates + returns the dict (caller can chain). Idempotent: re-running
    produces the exact same output (no double-wrap risk).

    Phase 1 scope (shipped 2026-04-19)
    ----------------------------------
    Only `opportunities[].lead_stocks[]` gets a provenance sidecar — because
    that's the single field where provenance materially changes behaviour
    (upgrade 🔴 → 🔵 when supply_chains.yaml confirms the ticker belongs to
    the theme).

    Storage strategy: **sidecar**, not envelope. We attach
    `opp["_lead_stocks_prov"]` as a parallel list of envelope dicts (same
    length + order as `lead_stocks`, each element an `envelope(None, ...)`
    carrying source / as_of / source_ref / confidence). The reason for the
    sidecar is pragmatic: ~10 read sites in build_dashboard.py call
    `ls.get("symbol")` directly on lead_stocks entries. Wrapping those in
    envelopes would break every site. Sidecar lets existing readers stay
    untouched; provenance-aware renderers just read `_lead_stocks_prov[i]`.

    Narrative text fields (headline, one_liner, narrative, plan_summary,
    rationale, commentary) are Phase-2 — ~30 read sites would need
    `unwrap()` calls added. Deferred to reduce blast radius on this first
    ship.

    See also: `enrich_chips()` for the orthogonal chips-strip enrichment
    (which DOES use in-place envelopes — chips have few read sites).
    """
    if not analysis:
        return analysis

    gen_at = analysis.get("generated_at") or analysis.get("date")
    # Extract ISO date portion only (strip time if present)
    as_of = (gen_at or "")[:10] if gen_at else None
    if not as_of:
        return analysis  # no anchor for as_of, skip enrichment

    # -- opportunities[].lead_stocks[] — supply_chains upgrade (sidecar) --
    supply_idx = supply_chains_index or {}
    for opp in (analysis.get("opportunities") or []):
        if not isinstance(opp, dict):
            continue
        theme = unwrap(opp.get("theme")) or ""
        confirmed = match_chain_for_theme(theme, supply_idx)
        leads = opp.get("lead_stocks") or []
        if not leads:
            continue
        prov_list: list[dict] = []
        for stk in leads:
            if not isinstance(stk, dict):
                # malformed entry — record an unknown-provenance placeholder
                prov_list.append(envelope(
                    None, "llm_inference", as_of, confidence=_LLM_CONF_LOW,
                ))
                continue
            sym = (stk.get("symbol") or "").strip()
            if sym and sym in confirmed:
                prov_list.append(envelope(
                    None,
                    "user_input",
                    as_of,
                    source_ref=f"supply_chains.yaml 確認此檔屬於「{theme}」題材",
                ))
            else:
                prov_list.append(envelope(
                    None,
                    "llm_inference",
                    as_of,
                    confidence=_LLM_CONF_DEFAULT,
                    source_ref="Gemini 指定（supply_chains.yaml 未收錄此題材映射）",
                ))
        opp["_lead_stocks_prov"] = prov_list

    return analysis


# ------------------------------------------------ convenience: one-shot --

def enrich_all(
    analysis: dict,
    chips: dict | None,
    supply_chains_path: str | None = "supply_chains.yaml",
) -> tuple[dict, dict | None]:
    """One-call convenience that enriches both analysis and chips.

    Returns (enriched_analysis, enriched_chips). Non-destructive for None
    inputs.
    """
    idx = load_supply_chains(supply_chains_path)
    analysis = enrich_analysis(analysis, idx) if analysis else analysis
    chips = enrich_chips(chips)
    return analysis, chips


# ---------------------------------------------------------- self-test driver --

if __name__ == "__main__":
    import json as _json

    # 1. supply_chains index load
    idx = load_supply_chains()
    assert isinstance(idx, dict)
    # Don't hard-require PyYAML or file presence — just smoke it
    if idx:
        print(f"  supply_chains index: {len(idx)} keys")

    # 2. enrich a mock analysis — Phase-1 scope: lead_stocks sidecar only
    mock = {
        "generated_at": "2026-04-18T20:53:34+08:00",
        "opportunities": [
            {
                "theme": "AI 伺服器散熱 / 液冷",
                "lead_stocks": [
                    {"symbol": "3324", "name": "雙鴻"},
                    {"symbol": "9999", "name": "未知"},
                ],
            },
        ],
    }
    enrich_analysis(mock, idx)

    # Raw lead_stocks stay untouched (readers don't break)
    assert mock["opportunities"][0]["lead_stocks"][0] == {"symbol": "3324", "name": "雙鴻"}
    # Sidecar carries provenance
    prov_list = mock["opportunities"][0].get("_lead_stocks_prov") or []
    assert len(prov_list) == 2, "sidecar length must match lead_stocks"
    # 9999 is unknown → must be 🔴 llm_inference
    assert prov_list[1]["source"] == "llm_inference"

    # Narrative fields stay raw in Phase-1
    mock2 = {
        "generated_at": "2026-04-18",
        "morning_brief": {"headline": "hi"},
    }
    enrich_analysis(mock2, idx)
    assert not is_enveloped(mock2["morning_brief"]["headline"])

    # Idempotent — re-running shouldn't double-wrap
    before = _json.dumps(mock, ensure_ascii=False, default=str)
    enrich_analysis(mock, idx)
    after = _json.dumps(mock, ensure_ascii=False, default=str)
    assert before == after, "enrichment not idempotent"

    # 3. enrich mock chips
    mock_chips = {
        "foreign_futures": {
            "latest": {"date": "2026-04-17", "net_oi": -41213},
            "change_1d": 1468,
        },
        "margin_total": {
            "latest": {"date": "2026-04-17", "balance_yi": 4271.3, "short_lots": 183500},
            "change_1d_yi": 47.1,
        },
    }
    enrich_chips(mock_chips)
    assert is_enveloped(mock_chips["foreign_futures"]["latest"]["net_oi"])
    assert mock_chips["foreign_futures"]["latest"]["net_oi"]["source"] == "primary_report"
    assert is_enveloped(mock_chips["margin_total"]["latest"]["balance_yi"])

    print("provenance_enrich.py: ✓ smoke tests passed")

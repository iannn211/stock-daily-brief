"""Provenance audit — source-tier distribution across historical analyses.

Reads every `analyses/*.json` file, re-runs the runtime enrichment
(`provenance_enrich.enrich_analysis`) on each one, then reports the
distribution of provenance tiers across `opportunities[].lead_stocks[]`.

Why run this
------------
- After a supply_chains.yaml edit, you want to know: how many historical
  🔴 lead-stocks just got promoted to 🔵?
- Regression check: if a pipeline bug ever makes Gemini stop emitting
  tickers that appear in supply_chains.yaml, 🔵 count will drop sharply.
- Sanity check: the 🟢/🔵/🟡/🔴 ratios should roughly track your
  expectations (e.g. most lead-stocks should be 🔵 once yaml coverage
  is good).

Usage
-----
    .venv/bin/python scripts/provenance_audit.py
    .venv/bin/python scripts/provenance_audit.py --since 2026-04-01
    .venv/bin/python scripts/provenance_audit.py --json > audit.json

Exit code is always 0; this is a reporting tool, not a gate. For a gate
you'd wrap it in a higher-level script that parses the JSON output.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Allow running from repo root OR from scripts/
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from provenance import SOURCE_DOT  # noqa: E402
from provenance_enrich import enrich_analysis, load_supply_chains  # noqa: E402


def audit_one(path: Path, supply_idx: dict) -> dict:
    """Run enrichment on one analysis file; return per-file stats.

    Returned stats shape:
        {
          "file": "analyses/2026-04-18.json",
          "date": "2026-04-18",
          "opportunities": 7,
          "lead_stocks_total": 23,
          "by_tier": {"user_input": 14, "llm_inference": 9},
          "confirmed_pct": 0.609,
        }
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            analysis = json.load(f)
    except Exception as e:
        return {"file": str(path.relative_to(_ROOT)), "error": str(e)}

    # Enrich adds the _lead_stocks_prov sidecar on each opportunity
    enrich_analysis(analysis, supply_idx)

    tier_counter: Counter[str] = Counter()
    total_leads = 0
    opps = analysis.get("opportunities") or []
    for opp in opps:
        if not isinstance(opp, dict):
            continue
        prov_list = opp.get("_lead_stocks_prov") or []
        total_leads += len(prov_list)
        for env in prov_list:
            tier_counter[env.get("source", "unknown")] += 1

    confirmed = tier_counter.get("user_input", 0)
    confirmed_pct = (confirmed / total_leads) if total_leads else 0.0

    return {
        "file": str(path.relative_to(_ROOT)),
        "date": (analysis.get("date")
                 or (analysis.get("generated_at") or "")[:10]
                 or ""),
        "opportunities": len(opps),
        "lead_stocks_total": total_leads,
        "by_tier": dict(tier_counter),
        "confirmed_pct": round(confirmed_pct, 3),
    }


def _fmt_pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def render_text(per_file: list[dict], overall: Counter, totals: dict) -> str:
    lines: list[str] = []
    lines.append("# Provenance audit — lead_stocks tier distribution")
    lines.append("")
    lines.append(f"Files scanned: {totals['files']}")
    lines.append(f"Total opportunities: {totals['opportunities']}")
    lines.append(f"Total lead_stocks: {totals['lead_stocks']}")
    lines.append("")
    lines.append("## Overall tier distribution")
    lines.append("")
    total_leads = max(1, totals["lead_stocks"])
    for tier, dot in SOURCE_DOT.items():
        n = overall.get(tier, 0)
        pct = n / total_leads
        lines.append(f"  {dot} {tier:<18} {n:>4}  {_fmt_pct(pct)}")
    lines.append("")

    lines.append("## Per-file breakdown (most recent first)")
    lines.append("")
    lines.append(f"  {'date':<12} {'opps':>4} {'leads':>5}  "
                 f"{'🔵 conf':>8} {'🔴 llm':>7}  confirmed%")
    per_file_sorted = sorted(per_file, key=lambda r: r.get("date", ""),
                             reverse=True)
    for rec in per_file_sorted:
        if "error" in rec:
            lines.append(f"  {rec['file']:<30}  ERROR: {rec['error']}")
            continue
        bt = rec["by_tier"]
        lines.append(
            f"  {rec['date']:<12} {rec['opportunities']:>4} "
            f"{rec['lead_stocks_total']:>5}  "
            f"{bt.get('user_input', 0):>8} "
            f"{bt.get('llm_inference', 0):>7}  "
            f"{_fmt_pct(rec['confirmed_pct'])}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", help="Only audit files dated >= YYYY-MM-DD")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of human-readable text")
    p.add_argument("--analyses-dir", default=str(_ROOT / "analyses"),
                   help="Directory of *.json analyses to audit")
    p.add_argument("--supply-chains", default=str(_ROOT / "supply_chains.yaml"),
                   help="Path to supply_chains.yaml")
    args = p.parse_args(argv)

    analyses_dir = Path(args.analyses_dir)
    if not analyses_dir.is_dir():
        print(f"ERROR: {analyses_dir} is not a directory", file=sys.stderr)
        return 2

    supply_idx = load_supply_chains(args.supply_chains)

    files = sorted(analyses_dir.glob("*.json"))
    if args.since:
        # Filenames are like 2026-04-18.json — simple string compare works
        cutoff = args.since
        files = [f for f in files if f.stem >= cutoff]

    per_file: list[dict] = []
    overall: Counter[str] = Counter()
    totals = {"files": 0, "opportunities": 0, "lead_stocks": 0}
    for f in files:
        rec = audit_one(f, supply_idx)
        per_file.append(rec)
        if "error" in rec:
            continue
        totals["files"] += 1
        totals["opportunities"] += rec["opportunities"]
        totals["lead_stocks"] += rec["lead_stocks_total"]
        for tier, n in rec["by_tier"].items():
            overall[tier] += n

    if args.json:
        out = {
            "files": per_file,
            "overall": dict(overall),
            "totals": totals,
            "supply_chains_keys": len(supply_idx),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_text(per_file, overall, totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())

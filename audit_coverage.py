"""
Proactive coverage audit — the system's "look ahead" loop.

Problem this solves:
  The user explicitly said: "我其實不想要每次都是我說了你才補上去 這樣沒有發揮你幫我
  先去搜尋並且協助我佈局的初衷." — i.e. coverage should expand proactively, not
  reactively one ticker at a time.

What this script does each build:

  1. Read supply_chains.yaml (curated chain → layer → stocks map, the "canonical
     universe we want Ian to see").
  2. Diff against portfolio.yaml → append any chain ticker that isn't yet in
     simulator_universe. Preserves comments (simple text append at EOF).
  3. Scan briefs/*.md + analyses/*.json from last 7 days, count every TW
     ticker mention. Cross-reference against the chain map + portfolio:
       - "news-hot but not in any chain" → curation candidates (flag for human)
       - "news-hot and already covered" → confirms the map is working
  4. Write coverage_report.json — consumed by dashboard (PORT tab → COVERAGE
     section) and by Gemini (fed into analyze.py prompt so it can reason
     about what's missing).

Idempotent: safe to re-run; existing tickers are skipped, counter resets daily.

Output: coverage_report.json
  {
    "fetched_at": "2026-04-18T...",
    "window_days": 7,
    "chain_totals": {"ai_pcb": {"ticker_count": 18, ...}, ...},
    "added_from_chains": [{"symbol": "3711", "name": "日月光投控",
                          "chain": "ai_pcb", "layer": "下游 · 封測 OSAT",
                          "added_at": "2026-04-18"}, ...],
    "news_frequency": {"3711": 7, "6515": 4, ...},     # top 30
    "missing_from_chains": [{"symbol": "XXXX", "name": "...",
                             "mentions": 5, "in_portfolio": false}, ...],
    "chain_membership": {"3711": ["ai_pcb"], "6515": ["ai_pcb", "semiconductor_eq"], ...}
  }
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")

PORTFOLIO_PATH = ROOT / "portfolio.yaml"
SUPPLY_CHAINS_PATH = ROOT / "supply_chains.yaml"
STOCK_UNIVERSE_PATH = ROOT / "stock_universe.json"
BRIEFS_DIR = ROOT / "briefs"
ANALYSES_DIR = ROOT / "analyses"
COVERAGE_REPORT_PATH = ROOT / "coverage_report.json"

# Window for news mention scanning
WINDOW_DAYS = 7

# Map chain slug → category label used in simulator_universe when auto-adding.
# Short & readable (matches existing category convention like "半導體代工", "PCB").
CHAIN_CATEGORY = {
    "ai_pcb":           "PCB供應鏈",
    "optics_cpo":       "光通訊",
    "thermal":          "散熱",
    "ai_server":        "AI 伺服器",
    "connectors":       "連接器",
    "passives":         "被動元件",
    "hbm_memory":       "HBM 記憶體",
    "robotics":         "機器人",
    "semiconductor_eq": "半導體設備",
}


# -------- 1. Load curated supply chains + portfolio --------

def _load_chains() -> dict:
    with SUPPLY_CHAINS_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_portfolio() -> dict:
    with PORTFOLIO_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _portfolio_symbols(pf: dict) -> set[str]:
    """Every TW ticker across holdings + watchlist + simulator_universe."""
    syms: set[str] = set()
    for key in ("holdings", "watchlist", "simulator_universe"):
        for it in (pf.get(key) or []):
            if (it.get("market") == "TW") and it.get("symbol"):
                syms.add(str(it["symbol"]).strip())
    return syms


def _chain_tickers(chains: dict) -> list[dict]:
    """Flatten chains → [{symbol, name, role, pillar, chain, layer}]."""
    out: list[dict] = []
    for slug, chain in (chains.get("chains") or {}).items():
        for layer in (chain.get("layers") or []):
            for st in (layer.get("stocks") or []):
                sym = str(st.get("symbol", "")).strip()
                if not sym:
                    continue
                out.append({
                    "symbol": sym,
                    "name":   st.get("name", ""),
                    "role":   st.get("role", ""),
                    "pillar": st.get("pillar", "growth"),
                    "chain":  slug,
                    "layer":  layer.get("name", ""),
                })
    return out


def _chain_membership(chain_entries: list[dict]) -> dict[str, list[str]]:
    """symbol → list of chain slugs it belongs to (dedup preserving order)."""
    mem: dict[str, list[str]] = {}
    for e in chain_entries:
        slugs = mem.setdefault(e["symbol"], [])
        if e["chain"] not in slugs:
            slugs.append(e["chain"])
    return mem


# -------- 2. Append missing chain tickers to portfolio.yaml --------

def _append_to_portfolio(missing: list[dict], run_date: str) -> None:
    """Append missing tickers to portfolio.yaml simulator_universe as a
    new auto-managed block. Plain text append — preserves all comments above."""
    if not missing:
        return

    # De-dup within this batch
    seen: set[str] = set()
    rows: list[dict] = []
    for e in missing:
        if e["symbol"] in seen:
            continue
        seen.add(e["symbol"])
        rows.append(e)

    # Group by chain for readable YAML
    by_chain: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_chain[r["chain"]].append(r)

    lines = [
        "",
        f"  # ── Auto-added by audit_coverage.py on {run_date} ──",
        "  # Source: supply_chains.yaml. Edit there, not here.",
    ]
    for slug, items in by_chain.items():
        category = CHAIN_CATEGORY.get(slug, slug)
        lines.append(f"  # {slug} → {category}")
        for it in items:
            name_escaped = it["name"].replace('"', '\\"')
            cat_escaped = category.replace('"', '\\"')
            lines.append(
                f'  - {{ symbol: "{it["symbol"]}", name: "{name_escaped}", '
                f'market: "TW", category: "{cat_escaped}" }}'
            )

    with PORTFOLIO_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# -------- 3. News-mention scan over briefs + analyses --------

def _load_known_tw_tickers() -> dict[str, str]:
    """ticker → name from stock_universe.json. Used to filter ticker-looking
    strings from random numbers in prose."""
    try:
        data = json.loads(STOCK_UNIVERSE_PATH.read_text(encoding="utf-8"))
        return {s["symbol"]: s.get("name", "") for s in data.get("stocks", [])}
    except Exception as e:
        print(f"  stock_universe.json load failed: {e}", file=sys.stderr)
        return {}


_TICKER_RE = re.compile(r"\b(\d{4,6}[A-Z]{0,2})\b")


def _scan_mentions(window_days: int, known_tickers: set[str]) -> Counter:
    """Count ticker mentions across recent briefs + analyses."""
    cutoff = datetime.now(TAIPEI).date() - timedelta(days=window_days)
    counter: Counter = Counter()

    # Scan briefs/*.md
    if BRIEFS_DIR.exists():
        for path in BRIEFS_DIR.glob("*.md"):
            try:
                # Filename like "2026-04-18.md" → parse date
                stem = path.stem
                if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
                    d = datetime.strptime(stem, "%Y-%m-%d").date()
                    if d < cutoff:
                        continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for m in _TICKER_RE.finditer(text):
                    sym = m.group(1)
                    if sym in known_tickers:
                        counter[sym] += 1
            except Exception:
                continue

    # Scan analyses/*.json (exclude latest.md / non-date names)
    if ANALYSES_DIR.exists():
        for path in ANALYSES_DIR.glob("*.json"):
            try:
                stem = path.stem
                if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
                    d = datetime.strptime(stem, "%Y-%m-%d").date()
                    if d < cutoff:
                        continue
                # Re-serialize JSON to string so ticker regex finds nested
                # mentions in any field (opportunities, faq, rebalance_advice…)
                text = path.read_text(encoding="utf-8", errors="ignore")
                for m in _TICKER_RE.finditer(text):
                    sym = m.group(1)
                    if sym in known_tickers:
                        counter[sym] += 1
            except Exception:
                continue

    return counter


# -------- 4. Build coverage report --------

def main() -> int:
    run_ts = datetime.now(TAIPEI)
    run_date = run_ts.strftime("%Y-%m-%d")
    print(f"[{run_ts:%Y-%m-%d %H:%M}] audit_coverage starting…", file=sys.stderr)

    chains = _load_chains()
    pf = _load_portfolio()
    pf_syms = _portfolio_symbols(pf)
    chain_entries = _chain_tickers(chains)
    membership = _chain_membership(chain_entries)

    print(f"  portfolio TW symbols:   {len(pf_syms)}", file=sys.stderr)
    print(f"  chain entries:          {len(chain_entries)} "
          f"({len(membership)} unique tickers, "
          f"{len(chains.get('chains') or {})} chains)", file=sys.stderr)

    # Step A: which chain tickers are missing from portfolio?
    missing_from_pf = [e for e in chain_entries if e["symbol"] not in pf_syms]
    # De-dup by symbol, keep first occurrence (first chain it appears in)
    seen_syms: set[str] = set()
    uniq_missing: list[dict] = []
    for e in missing_from_pf:
        if e["symbol"] in seen_syms:
            continue
        seen_syms.add(e["symbol"])
        uniq_missing.append(e)

    if uniq_missing:
        print(f"  → appending {len(uniq_missing)} new tickers to "
              f"portfolio.yaml simulator_universe", file=sys.stderr)
        for m in uniq_missing:
            print(f"      + {m['symbol']} {m['name']} "
                  f"({m['chain']}/{m['layer']})", file=sys.stderr)
        _append_to_portfolio(uniq_missing, run_date)
    else:
        print(f"  → portfolio already covers all chain tickers", file=sys.stderr)

    # Step B: scan news mentions
    known = _load_known_tw_tickers()
    print(f"  known TW ticker dictionary: {len(known)} entries", file=sys.stderr)

    mentions = _scan_mentions(WINDOW_DAYS, set(known.keys()))
    print(f"  {sum(mentions.values())} total ticker mentions across last "
          f"{WINDOW_DAYS} days, {len(mentions)} unique", file=sys.stderr)

    # Step C: find hot tickers missing from both chains AND portfolio
    # (chain + portfolio-refreshed since we just appended above)
    covered = set(membership.keys()) | pf_syms | {e["symbol"] for e in uniq_missing}
    gaps: list[dict] = []
    for sym, count in mentions.most_common(50):
        if count < 2:
            break
        if sym not in covered:
            gaps.append({
                "symbol": sym,
                "name":   known.get(sym, ""),
                "mentions": count,
                "in_portfolio": sym in pf_syms,
                "in_chains":    sym in membership,
            })

    if gaps:
        print(f"  → {len(gaps)} hot tickers NOT in any chain/portfolio:", file=sys.stderr)
        for g in gaps[:10]:
            print(f"      ! {g['symbol']} {g['name']}  ×{g['mentions']}", file=sys.stderr)
    else:
        print(f"  → no uncovered hot tickers", file=sys.stderr)

    # Step D: per-chain totals (for dashboard stats)
    chain_totals: dict[str, dict] = {}
    for slug, chain in (chains.get("chains") or {}).items():
        tickers = [
            s["symbol"] for layer in (chain.get("layers") or [])
            for s in (layer.get("stocks") or [])
        ]
        mentioned = sum(1 for t in tickers if t in mentions)
        chain_totals[slug] = {
            "title":         chain.get("title", slug),
            "ticker_count":  len(tickers),
            "unique_count":  len(set(tickers)),
            "layer_count":   len(chain.get("layers") or []),
            "mentioned_in_window": mentioned,
        }

    report = {
        "fetched_at":   run_ts.isoformat(),
        "window_days":  WINDOW_DAYS,
        "chain_totals": chain_totals,
        "added_from_chains": [
            {
                "symbol":   e["symbol"],
                "name":     e["name"],
                "chain":    e["chain"],
                "layer":    e["layer"],
                "pillar":   e["pillar"],
                "added_at": run_date,
            }
            for e in uniq_missing
        ],
        "news_frequency":   dict(mentions.most_common(30)),
        "missing_from_chains": gaps,
        "chain_membership": membership,
    }

    COVERAGE_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    kb = COVERAGE_REPORT_PATH.stat().st_size // 1024
    print(f"→ coverage_report.json: {kb} KB "
          f"({len(uniq_missing)} added, {len(gaps)} gaps flagged)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

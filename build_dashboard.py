"""
Build the static HTML dashboard.

Outputs:
  docs/index.html                — landing page: macro, portfolio, alerts, briefs, holdings
  docs/briefs/<date>.html        — per-day brief with AI analysis
  docs/holdings/<symbol>.html    — per-holding deep dive
  docs/styles.css                — shared dark theme (Bloomberg-esque)

Reads:
  briefs/*.md                    — news (from daily_brief.py)
  analyses/*.json                — Gemini analysis (from analyze.py)
  portfolio.json                 — P&L, risk, alerts (from calculate_pnl.py)
  price_history.json             — 1-year daily history (from fetch_prices.py)
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown as md
import yaml

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
BRIEFS_DIR = ROOT / "briefs"
ANALYSES_DIR = ROOT / "analyses"
DOCS_DIR = ROOT / "docs"
DOCS_BRIEFS_DIR = DOCS_DIR / "briefs"
DOCS_HOLDINGS_DIR = DOCS_DIR / "holdings"
PORTFOLIO_JSON = ROOT / "portfolio.json"
HISTORY_JSON = ROOT / "price_history.json"
STOCK_UNIVERSE_JSON = ROOT / "stock_universe.json"  # all TW stocks from TWSE/TPEx

DATE_RE = re.compile(r"^# Daily Brief — (\d{4}-\d{2}-\d{2}) \(週(.)\)", re.MULTILINE)
COUNT_RE = re.compile(r"抓到 (\d+) 則新聞")
SECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
PROMPT_MARKER = "\n---\n\n你是我的"

SENTIMENT_CLS = {"正面": "up", "負面": "dn", "中性": "flat"}
SENTIMENT_ICON = {"正面": "", "負面": "", "中性": ""}  # handled via CSS dots now


def _icon(name: str, size: int = 18) -> str:
    """Inline SVG icons — clean line style, no emojis."""
    paths = {
        "ai":       '<path d="M12 3 L13 8 L18 9 L13 10 L12 15 L11 10 L6 9 L11 8 Z" fill="currentColor"/><path d="M18 4 L18.6 6 L20.5 6.5 L18.6 7 L18 9 L17.4 7 L15.5 6.5 L17.4 6 Z" fill="currentColor"/>',
        "radar":    '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><path d="M12 12 L20 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
        "sim":      '<rect x="4" y="3" width="16" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="7" y="6" width="10" height="3" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="8.5" cy="13" r="0.8" fill="currentColor"/><circle cx="12" cy="13" r="0.8" fill="currentColor"/><circle cx="15.5" cy="13" r="0.8" fill="currentColor"/><circle cx="8.5" cy="17" r="0.8" fill="currentColor"/><circle cx="12" cy="17" r="0.8" fill="currentColor"/><circle cx="15.5" cy="17" r="0.8" fill="currentColor"/>',
        "chart":    '<polyline points="3,17 9,11 13,14 21,6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><polyline points="17,6 21,6 21,10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
        "case":     '<rect x="3" y="7" width="18" height="13" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9 7 V5 a1 1 0 0 1 1 -1 h4 a1 1 0 0 1 1 1 V7" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" stroke-width="1.5"/>',
        "news":     '<rect x="4" y="4" width="16" height="16" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="9" x2="16" y2="9" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="13" x2="16" y2="13" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="17" x2="13" y2="17" stroke="currentColor" stroke-width="1.5"/>',
        "search":   '<circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><line x1="16" y1="16" x2="20" y2="20" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        "bolt":     '<polygon points="13,3 6,14 11,14 10,21 18,10 13,10" fill="currentColor"/>',
        "target":   '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>',
        "pulse":    '<polyline points="3,12 8,12 10,6 14,18 16,12 21,12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
        "globe":    '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/><ellipse cx="12" cy="12" rx="4" ry="9" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="3" y1="12" x2="21" y2="12" stroke="currentColor" stroke-width="1.5"/>',
        "cal":      '<rect x="4" y="5" width="16" height="15" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="4" y1="10" x2="20" y2="10" stroke="currentColor" stroke-width="1.5"/><line x1="9" y1="3" x2="9" y2="7" stroke="currentColor" stroke-width="1.5"/><line x1="15" y1="3" x2="15" y2="7" stroke="currentColor" stroke-width="1.5"/>',
        "book":     '<path d="M4 5 a2 2 0 0 1 2 -2 h12 a1 1 0 0 1 1 1 v15 a1 1 0 0 1 -1 1 H6 a2 2 0 0 1 -2 -2 Z" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="9" x2="15" y2="9" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="13" x2="15" y2="13" stroke="currentColor" stroke-width="1.5"/>',
        "dollar":   '<line x1="12" y1="3" x2="12" y2="21" stroke="currentColor" stroke-width="1.6"/><path d="M16 7 H10 a2.5 2.5 0 0 0 0 5 H14 a2.5 2.5 0 0 1 0 5 H8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        "warn":     '<path d="M12 3 L22 20 H2 Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><line x1="12" y1="10" x2="12" y2="14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="17" r="0.8" fill="currentColor"/>',
        "flame":    '<path d="M12 3 C10 7 8 8 8 12 a4 4 0 0 0 8 0 c0 -3 -2 -4 -4 -9 Z M11 16 a2 2 0 0 0 2 2 a2 2 0 0 0 0 -4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
        "diamond":  '<polygon points="12,3 21,10 12,21 3,10" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="3" y1="10" x2="21" y2="10" stroke="currentColor" stroke-width="1.5"/>',
        "eye":      '<path d="M2 12 C5 6 8 4 12 4 C16 4 19 6 22 12 C19 18 16 20 12 20 C8 20 5 18 2 12 Z" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    }
    d = paths.get(name, '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.5"/>')
    return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" class="icon">{d}</svg>'


def _sec_head(title_cn: str, title_en: str = "", meta: str = "", count: int | None = None) -> str:
    """Consistent Bloomberg-style section header — no emojis."""
    en = f'<span class="sec-en mono">{html.escape(title_en)}</span>' if title_en else ""
    cnt = f'<span class="sec-count mono">{count}</span>' if count is not None else ""
    metahtml = f'<span class="sec-meta mono">{html.escape(meta)}</span>' if meta else ""
    return f'''
<div class="sec-head">
  <span class="sec-tick"></span>
  <h2 class="sec-title">{html.escape(title_cn)}</h2>
  {en}{cnt}{metahtml}
</div>
'''
PILLAR_LABEL = {"growth": "成長核心", "defense": "防禦對沖", "flexibility": "機動倉位"}
PILLAR_CLS = {"growth": "p-growth", "defense": "p-defense", "flexibility": "p-flex"}

# Populated lazily — maps ticker + Chinese name → symbol for linkification
_TICKER_ALIAS: dict[str, str] = {}


def _link_tickers(text: str, href_prefix: str = "holdings/") -> str:
    """Replace known ticker codes and names in text with anchors to deep page."""
    if not _TICKER_ALIAS or not text:
        return html.escape(text)
    escaped = html.escape(text)
    # Sort aliases by length desc so longer names match first
    keys = sorted(_TICKER_ALIAS.keys(), key=len, reverse=True)
    for alias in keys:
        if not alias or len(alias) < 2:
            continue
        sym = _TICKER_ALIAS[alias]
        # Match as a whole token (avoid matching inside larger words)
        # For digits: use word boundary; for CJK: just substring
        if alias.isdigit() or alias.isascii():
            pattern = rf"\b{re.escape(alias)}\b"
        else:
            pattern = re.escape(alias)
        replacement = f'<a href="{href_prefix}{sym}.html" class="tx-link">{alias}</a>'
        escaped = re.sub(pattern, replacement, escaped, count=3)  # limit replacements to avoid spam
    return escaped


def init_ticker_alias(pf: dict | None) -> None:
    """Build alias → symbol map from portfolio data."""
    _TICKER_ALIAS.clear()
    if not pf:
        return
    for coll in ("holdings", "watchlist", "simulator_universe"):
        for item in pf.get(coll, []) or []:
            sym = item.get("symbol")
            if not sym:
                continue
            _TICKER_ALIAS[sym] = sym
            nm = item.get("name")
            if nm:
                _TICKER_ALIAS[nm] = sym
                _TICKER_ALIAS[nm.replace("-KY", "")] = sym


# Compatibility shims for rendering code that uses older helper names.
def _is_known_symbol(sym: str) -> bool:
    return sym in _TICKER_ALIAS


def esc_linked(text: str, prefix: str = "holdings") -> str:
    """HTML-escape text and linkify known tickers / stock names."""
    return _link_tickers(text or "", href_prefix=prefix + "/")


# Backwards-compat: _KNOWN_SYMBOLS behaves like a read-only set lookup
class _KnownSymbolsProxy:
    def __contains__(self, item):
        return item in _TICKER_ALIAS
    def __iter__(self):
        return iter(_TICKER_ALIAS)
    def __len__(self):
        return len(_TICKER_ALIAS)

_KNOWN_SYMBOLS = _KnownSymbolsProxy()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_twd(n: float, sign: bool = False) -> str:
    if n is None:
        return "—"
    sign_ch = "+" if (sign and n > 0) else ("-" if n < 0 else "")
    return f"{sign_ch}NT${abs(n):,.0f}"


def _fmt_pct(n: float, digits: int = 2) -> str:
    if n is None:
        return "—"
    return f"{n:+.{digits}f}%"


def _cls(n: float | None) -> str:
    """TW convention: up = red, down = green."""
    if n is None:
        return "flat"
    if n > 0:
        return "up"
    if n < 0:
        return "dn"
    return "flat"


# Strip leading emoji/symbol chars from AI-supplied labels
# so the UI keeps a clean mono-label look.
_EMOJI_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    r"\U0001F900-\U0001F9FF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF]+\s*"
)


def _strip_leading_emoji(s: str) -> str:
    if not s:
        return s
    return _EMOJI_RE.sub("", s).strip()


def _spark_svg(points: list[dict], width: int = 120, height: int = 32,
               stroke: str = "var(--accent)") -> str:
    """Generate inline SVG sparkline. Points: [{'d': date, 'c': close}, ...]."""
    if not points or len(points) < 2:
        return '<svg class="sparkline" width="%d" height="%d"></svg>' % (width, height)
    xs = list(range(len(points)))
    closes = [p.get("c") or p.get("v") or 0 for p in points]
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx != mn else 1
    pad = 2

    def sx(i):
        return pad + (width - 2 * pad) * i / (len(points) - 1)

    def sy(c):
        return height - pad - (height - 2 * pad) * (c - mn) / rng

    d = "M " + " L ".join(f"{sx(i):.1f} {sy(c):.1f}" for i, c in enumerate(closes))

    # Color: up if last >= first, down otherwise (TW convention)
    direction_cls = "up" if closes[-1] >= closes[0] else "dn"
    color = "var(--up)" if direction_cls == "up" else "var(--dn)"

    area_pts = (
        f"{sx(0):.1f} {height - pad} " +
        " ".join(f"{sx(i):.1f} {sy(c):.1f}" for i, c in enumerate(closes)) +
        f" {sx(len(points) - 1):.1f} {height - pad}"
    )

    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" width="{width}" height="{height}" preserveAspectRatio="none">'
        f'<polygon points="{area_pts}" fill="{color}" opacity="0.12"/>'
        f'<path d="{d}" stroke="{color}" stroke-width="1.5" fill="none" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


def _sentiment_badge(sent: str) -> str:
    cls = SENTIMENT_CLS.get(sent, "flat")
    icon = SENTIMENT_ICON.get(sent, "⚪")
    return f'<span class="badge badge-{cls}">{icon} {html.escape(sent)}</span>'


def _pct_distance(current: float, ref: float) -> float:
    if not ref:
        return 0
    return (current - ref) / ref * 100


# ---------------------------------------------------------------------------
# Brief loading
# ---------------------------------------------------------------------------

def load_briefs() -> list[dict]:
    briefs: list[dict] = []
    for path in sorted(BRIEFS_DIR.glob("*.md"), reverse=True):
        if path.stem == "latest":
            continue
        content = path.read_text(encoding="utf-8")
        m = DATE_RE.search(content)
        if not m:
            continue
        count_m = COUNT_RE.search(content)
        sections = SECTION_RE.findall(content)
        tags = list(dict.fromkeys(sections))
        briefs.append({
            "date": m.group(1),
            "weekday": m.group(2),
            "count": int(count_m.group(1)) if count_m else 0,
            "tags": tags,
            "content": content,
            "path": path,
        })
    return briefs


def split_prompt(content: str) -> tuple[str, str]:
    idx = content.find(PROMPT_MARKER)
    if idx < 0:
        return "", content
    split_at = idx + len("\n---\n\n")
    return content[:split_at], content[split_at:]


def load_analysis(date: str) -> dict | None:
    path = ANALYSES_DIR / f"{date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_coverage_report() -> dict | None:
    """Load coverage_report.json from audit_coverage.py run. Returns None
    if unavailable (first run / workflow error)."""
    path = ROOT / "coverage_report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_red_list_symbols(analysis: dict | None) -> set[str]:
    """Pull every ticker Gemini flagged as 🔴 不要做 inside action_checklist.red.

    The red section is where Gemini says 'don't chase this', 'don't add to
    that' — so it's a hard veto signal. Any ticker mentioned there (by 4-6
    digit code) must NOT appear in the basket builder, even if Gemini
    contradicts itself elsewhere (e.g. also listing the same ticker as an
    opportunity lead_stock — a real bug we caught 2026-04-19 with 6173
    信昌電 appearing on both red and opportunities)."""
    if not analysis:
        return set()
    red_items = (analysis.get("action_checklist") or {}).get("red") or []
    symbols: set[str] = set()
    pat = re.compile(r"\b(\d{4,6})\b")
    for item in red_items:
        if not isinstance(item, dict):
            continue
        blob = " ".join([
            str(item.get("action") or ""),
            str(item.get("reason") or ""),
        ])
        for m in pat.finditer(blob):
            symbols.add(m.group(1))
    return symbols


def _pad_allocations_from_opportunities(allocs: list[dict], analysis: dict | None) -> list[dict]:
    """Ensure the basket builder has real alternatives even when Gemini is
    stingy with budget_allocation.allocations.

    Historically Gemini sometimes returns only 1-2 candidates, which defeats
    the point of a budget-aware picker (no matter what budget the user
    selects, it shows the same single stock). This helper tops up to ~6
    candidates by pulling from opportunities[].lead_stocks — stocks Gemini
    already flagged in the same analysis — using conservative defaults:
    -10% stop loss, +30% take profit (snowball method), NT$5,000 sizing.

    Synthetic entries are marked with synthetic=True and use the "觀望等進場"
    action so they visually differ from Gemini's primary picks. The user can
    still check them into the basket; they just come with lower confidence.

    2026-04-19 guard: tickers that appear in action_checklist.red are hard-
    vetoed — they get filtered both from Gemini's own allocations AND from
    the pad pool, so 信昌電 (6173)-style contradictions can't leak to the
    user-facing basket."""
    if not analysis:
        return allocs or []

    allocs = list(allocs or [])

    # Hard veto: drop any allocation whose symbol is on the red list. Protects
    # against Gemini contradicting itself (opportunities vs action_checklist.red).
    red_symbols = _extract_red_list_symbols(analysis)
    if red_symbols:
        allocs = [
            a for a in allocs
            if str(a.get("symbol") or "").strip() not in red_symbols
        ]

    # Stop-loss proximity guard: if Gemini set a stop that's within 5% of the
    # current price, it's essentially telling the user "enter right at the
    # stop" — terrible risk/reward. Downweight confidence to <=40 and flag the
    # allocation so the UI can badge it, rather than silently dropping it
    # (Gemini may have intentional reasons that justify the tight stop).
    try:
        pj_all = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))
        prices_all = {}
        for yft, rec in (pj_all.get("prices") or {}).items():
            sym = str(rec.get("symbol") or yft.split(".")[0]).strip()
            price = rec.get("close")
            if sym and price:
                prices_all[sym] = float(price)
    except Exception:
        prices_all = {}
    for a in allocs:
        sym = str(a.get("symbol") or "").strip()
        stop = a.get("stop_loss_price")
        current = prices_all.get(sym)
        if not sym or not stop or not current or current <= 0:
            continue
        try:
            dist_pct = (float(current) - float(stop)) / float(current) * 100.0
        except Exception:
            continue
        if 0 < dist_pct < 5:
            orig_conf = int(a.get("confidence_pct") or 50)
            a["confidence_pct"] = min(orig_conf, 40)
            a["stop_too_close"] = True
            a["stop_distance_pct"] = round(dist_pct, 1)

    def _is_cash(a: dict) -> bool:
        act = a.get("action") or ""
        return ("現金" in act) or ("不動作" in act)

    non_cash = [a for a in allocs if not _is_cash(a)]
    target = 6
    if len(non_cash) >= target:
        return allocs

    # Load current prices so synthetic entries have accurate target_shares/cost
    prices_map: dict[str, float] = {}
    try:
        pj = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))
        for yft, rec in (pj.get("prices") or {}).items():
            sym = str(rec.get("symbol") or yft.split(".")[0]).strip()
            price = rec.get("close")
            if sym and price:
                prices_map[sym] = float(price)
    except Exception:
        pass

    # Ground-truth name lookup (portfolio.yaml + supply_chains.yaml) — prefer
    # this over Gemini's claimed name to avoid propagating hallucinated labels
    # (e.g. validator caught Gemini calling 3363 "中揚光" but our record is "上詮")
    name_map: dict[str, str] = {}
    try:
        pf_raw = yaml.safe_load((ROOT / "portfolio.yaml").read_text(encoding="utf-8")) or {}
        for coll in ("holdings", "watchlist", "simulator_universe"):
            for it in pf_raw.get(coll) or []:
                sym = str(it.get("symbol") or "").strip()
                nm = it.get("name") or ""
                if sym and nm and sym not in name_map:
                    name_map[sym] = nm
    except Exception:
        pass
    try:
        sc_raw = yaml.safe_load((ROOT / "supply_chains.yaml").read_text(encoding="utf-8")) or {}
        for chain in (sc_raw.get("chains") or {}).values():
            for layer in chain.get("layers") or []:
                for s in layer.get("stocks") or []:
                    sym = str(s.get("symbol") or "").strip()
                    nm = s.get("name") or ""
                    if sym and nm and sym not in name_map:
                        name_map[sym] = nm
    except Exception:
        pass

    seen = {str(a.get("symbol") or "").strip() for a in allocs if a.get("symbol")}

    # Build per-opportunity queues so we can round-robin — this diversifies
    # themes in the padded list (otherwise all 3 lead_stocks of the first
    # opportunity get picked before we move to the next theme).
    opp_queues = []
    for o in analysis.get("opportunities") or []:
        opp_queues.append({
            "meta": {
                "confidence_pct": int(o.get("confidence_pct") or 50),
                "theme": o.get("theme") or "",
                "headline": o.get("headline") or o.get("why") or "",
                "risk": o.get("ai_warning") or o.get("risk") or "",
                "signals": o.get("signals") or [],
            },
            "leads": list(o.get("lead_stocks") or []),
        })

    # Round-robin: one lead_stock per opportunity per pass until we hit target
    # or exhaust the pool.
    while any(q["leads"] for q in opp_queues):
        if len([a for a in allocs if not _is_cash(a)]) >= target:
            break
        for q in opp_queues:
            if not q["leads"]:
                continue
            if len([a for a in allocs if not _is_cash(a)]) >= target:
                break
            ls = q["leads"].pop(0)
            sym = str(ls.get("symbol") or "").strip()
            if not sym or sym in seen:
                continue
            # Red-list veto: if this ticker is on today's 🔴 不要做 list,
            # never pad it into the basket — even if it's listed as an
            # opportunity lead_stock (Gemini contradicting itself).
            if sym in red_symbols:
                continue
            # Name: prefer ground-truth > Gemini's claim
            name = name_map.get(sym) or ls.get("name") or ""
            price = prices_map.get(sym)
            if not price or price <= 0:
                continue

            meta = q["meta"]
            budget_basis = 5000
            shares = max(1, int(budget_basis // price))
            cost = int(round(shares * price))

            rationale = f"題材「{meta['theme']}」的同業候選。"
            if meta["headline"]:
                clip = meta["headline"][:90] + ("…" if len(meta["headline"]) > 90 else "")
                rationale += clip
            if meta["signals"]:
                rationale += "（訊號：" + "、".join(meta["signals"][:3]) + "）"

            allocs.append({
                "symbol": sym,
                "name": name,
                "action": "觀望等進場",
                "target_shares": shares,
                "target_cost_twd": cost,
                "entry_condition": "盤中觀察量能，等回檔或突破再分批進",
                "stop_loss_price": round(price * 0.90, 1),
                "take_profit_price": round(price * 1.30, 1),
                "rationale": rationale,
                "data_sources": [f"從今日 opportunities「{meta['theme']}」帶入"],
                "confidence_pct": max(40, int(meta["confidence_pct"] * 0.8)),
                "risk": meta["risk"] or "同題材次選候選，進場前請確認基本面與籌碼。",
                "synthetic": True,
            })
            seen.add(sym)

    return allocs


def load_validation_report() -> dict | None:
    """Load validation_report.json from validate_analysis.py. Returns None
    if unavailable. The report shape is:
      {validated_at, analysis_file, analysis_date, analysis_model,
       issues: [{severity, category, location, message, context?}, ...],
       summary: {errors, warnings, infos, total}}"""
    path = ROOT / "validation_report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_market_chips() -> dict | None:
    """Load the market-wide 籌碼 block from chips.json (外資期貨未平倉、融資餘額).
    Needed for the "媽媽模式" data strip — surfaces headline numbers even when
    Gemini hasn't regenerated yet. Returns None if file or block missing.
    """
    path = ROOT / "chips.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    mc = data.get("market_chips") or {}
    if not mc.get("foreign_futures") and not mc.get("margin_total"):
        return None
    return mc


def render_market_chips_strip(mc: dict | None) -> str:
    """Compact one-line strip showing the 3 numbers the Threads post taught us
    to watch: 外資期貨淨 OI、融資餘額、融券張數. Each number is annotated with
    trend direction + 代表什麼. Renders above the daily AI hero.

    The philosophy: even if today's Gemini analysis hasn't narrated these
    numbers, the user can see them directly and the interpretation is pre-baked
    so they don't have to Google what "外資期貨空單 4.1 萬口" means.
    """
    if not mc:
        return ""

    items: list[str] = []

    # ---- Foreign futures net OI ----
    fx = mc.get("foreign_futures") or {}
    latest_fx = fx.get("latest") or {}
    net = latest_fx.get("net_oi")
    if net is not None:
        ch = fx.get("change_1d")
        lots_k = abs(net) / 1000.0  # in 千口 for display
        direction = "空單" if net < 0 else "多單"
        ch_bit = ""
        if ch is not None:
            arrow = "↑" if ch < 0 else "↓"  # space invert: more short = up arrow
            if net > 0:  # if net long, positive change = up in longs
                arrow = "↑" if ch > 0 else "↓"
            ch_abs = abs(ch)
            ch_bit = f" {arrow} {ch_abs:,}口"
        # Interpretation
        interp = ""
        if net < -35000:
            interp = "外資避險加重（一邊買現貨、一邊放空=怕跌）"
            tone = "warn"
        elif net < -15000:
            interp = "外資中性偏保守"
            tone = "neutral"
        elif net > 15000:
            interp = "外資偏多（期貨淨多單罕見）"
            tone = "good"
        else:
            interp = "外資倉位平衡"
            tone = "neutral"
        items.append(f'''
        <div class="mcs-item mcs-{tone}">
          <div class="mcs-k">外資期貨{direction}淨額</div>
          <div class="mcs-v">{lots_k:.1f} <span class="mcs-unit">千口</span>{ch_bit}</div>
          <div class="mcs-i">{html.escape(interp)}</div>
        </div>''')

    # ---- Margin balance ----
    mg = mc.get("margin_total") or {}
    latest_mg = mg.get("latest") or {}
    bal = latest_mg.get("balance_yi")
    if bal is not None:
        ch = mg.get("change_1d_yi")
        ch_bit = ""
        tone = "neutral"
        interp = ""
        if ch is not None:
            arrow = "↑" if ch > 0 else "↓"
            ch_bit = f" {arrow} {abs(ch):.1f}億"
            if ch > 20:
                interp = "散戶加碼融資（行情偏熱訊號）"
                tone = "warn"
            elif ch < -20:
                interp = "融資退潮（散戶觀望中）"
                tone = "good"
            else:
                interp = "融資變動不大"
                tone = "neutral"
        items.append(f'''
        <div class="mcs-item mcs-{tone}">
          <div class="mcs-k">融資餘額</div>
          <div class="mcs-v">{bal:,.0f} <span class="mcs-unit">億</span>{ch_bit}</div>
          <div class="mcs-i">{html.escape(interp)}</div>
        </div>''')

    # ---- Short lots ----
    short_lots = latest_mg.get("short_lots")
    if short_lots is not None:
        # NT: small changes don't matter; just display the level
        items.append(f'''
        <div class="mcs-item mcs-neutral">
          <div class="mcs-k">融券餘額</div>
          <div class="mcs-v">{short_lots/10000:.1f} <span class="mcs-unit">萬張</span></div>
          <div class="mcs-i">空方倉位水位</div>
        </div>''')

    if not items:
        return ""

    date_str = latest_fx.get("date") or latest_mg.get("date") or ""
    date_bit = f'<span class="mcs-date muted small mono">{html.escape(date_str)}</span>' if date_str else ""

    return f'''
    <section class="market-chips-strip">
      <div class="mcs-head">
        <span class="mcs-title">📊 市場籌碼一眼</span>
        {date_bit}
        <span class="mcs-sub muted small">外資期貨+融資餘額決定「現在是過熱還是冷卻」</span>
      </div>
      <div class="mcs-grid">
        {"".join(items)}
      </div>
    </section>
    '''


def load_supply_chains() -> dict:
    """Load supply_chains.yaml; returns chain metadata keyed by slug."""
    import yaml
    path = ROOT / "supply_chains.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("chains") or {}
    except Exception:
        return {}


def load_portfolio() -> dict | None:
    if not PORTFOLIO_JSON.exists():
        return None
    try:
        return json.loads(PORTFOLIO_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_history() -> dict:
    if not HISTORY_JSON.exists():
        return {}
    try:
        return json.loads(HISTORY_JSON.read_text(encoding="utf-8")).get("history", {})
    except Exception:
        return {}


def load_full_tw_universe() -> list[dict]:
    """Full TWSE+TPEx stock list for search autocomplete (2000+ stocks)."""
    if not STOCK_UNIVERSE_JSON.exists():
        return []
    try:
        data = json.loads(STOCK_UNIVERSE_JSON.read_text(encoding="utf-8"))
        return data.get("stocks", [])
    except Exception:
        return []


def build_news_index(briefs: list[dict], universe: list[dict]) -> dict[str, list[dict]]:
    """Scan brief markdown content for ticker / name mentions.

    Returns: {symbol: [{date, title, url, source, time, summary}]}
    """
    index: dict[str, list[dict]] = {}
    # Build lookup: {alias: symbol}. Include symbol + name + Chinese company name.
    alias_to_sym: dict[str, str] = {}
    for u in universe:
        sym = u["symbol"]
        alias_to_sym[sym] = sym
        if u.get("name"):
            # Strip "-KY" suffix for matching (common TW convention)
            alias_to_sym[u["name"]] = sym
            alias_to_sym[u["name"].replace("-KY", "")] = sym

    # Regex: matches markdown list entries like:  - [TITLE](URL) · SOURCE · MM-DD HH:MM
    entry_re = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)\s*·\s*([^·]+?)\s*·\s*(\d{2}-\d{2} \d{2}:\d{2})", re.MULTILINE)
    summary_re = re.compile(r"^\s*>\s*(.+)", re.MULTILINE)

    for b in briefs:
        content = b["content"]
        # Find all article entries with their positions
        for m in entry_re.finditer(content):
            title = m.group(1)
            url = m.group(2)
            source = m.group(3).strip()
            time_str = m.group(4)
            # Grab next line summary if present (bounded to next article or section)
            end_pos = m.end()
            next_bound = content.find("\n- [", end_pos)
            section_bound = content.find("\n## ", end_pos)
            bound = min(b for b in [next_bound, section_bound] if b > 0) if (next_bound > 0 or section_bound > 0) else len(content)
            following = content[end_pos:bound]
            sm = summary_re.search(following)
            summary = sm.group(1).strip() if sm else ""

            # Check which tickers this article mentions (title + summary)
            text = f"{title} {summary}"
            matched = set()
            for alias, sym in alias_to_sym.items():
                if len(alias) >= 2 and alias in text:
                    matched.add(sym)

            for sym in matched:
                index.setdefault(sym, []).append({
                    "date": b["date"],
                    "title": title,
                    "url": url,
                    "source": source,
                    "time": time_str,
                    "summary": summary[:200],
                })

    # Dedupe by URL per symbol; cap at 10 most recent
    for sym in index:
        seen_urls = set()
        unique = []
        for a in index[sym]:
            if a["url"] in seen_urls:
                continue
            seen_urls.add(a["url"])
            unique.append(a)
        # Sort by (date desc, time desc)
        unique.sort(key=lambda a: (a["date"], a["time"]), reverse=True)
        index[sym] = unique[:10]
    return index


"""Note: compute_recommendation now lives in calculate_pnl.py and is attached
to every holding / watchlist / universe item as item['recommendation']."""


# ---------------------------------------------------------------------------
# Macro ribbon
# ---------------------------------------------------------------------------

def render_macro_ribbon(pf: dict) -> str:
    macro = pf.get("macro", {}) if pf else {}

    def _cell(label: str, data: dict, fmt: str = "{:.1f}", show_ytd: bool = False):
        close = data.get("close")
        if close is None:
            return ""
        day = data.get("day_change_pct") or 0
        ytd = data.get("ret_ytd")
        ytd_html = (
            f'<span class="macro-ytd {_cls(ytd)}">YTD {_fmt_pct(ytd, 1)}</span>'
            if show_ytd and ytd is not None else ""
        )
        return f'''
          <div class="macro-cell">
            <div class="macro-label">{label}</div>
            <div class="macro-val mono tnum">{fmt.format(close)}</div>
            <div class="macro-delta {_cls(day)} mono">{_fmt_pct(day, 2)}</div>
            {ytd_html}
          </div>'''

    cells = [
        _cell("台股加權 ^TWII", macro.get("twii", {}), "{:.0f}", True),
        _cell("S&P 500", macro.get("spx", {}), "{:.0f}", True),
        _cell("VIX (恐慌)", macro.get("vix", {}), "{:.2f}"),
        _cell("USD/TWD", macro.get("usdtwd", {}), "{:.3f}"),
    ]
    return f'<section class="macro-ribbon wrap">{"".join(cells)}</section>'


# ---------------------------------------------------------------------------
# Portfolio card (expanded)
# ---------------------------------------------------------------------------

def render_portfolio_card(pf: dict) -> str:
    if not pf:
        return ""
    s = pf.get("summary", {})
    bench = pf.get("benchmark", {})
    risk = pf.get("risk", {})
    pillar = pf.get("pillar_allocation", {})
    attr = pf.get("attribution", {})
    alerts = pf.get("alerts", {})
    profile = pf.get("risk_profile", {})
    series = pf.get("portfolio_series", [])

    try:
        dt = datetime.fromisoformat(pf.get("as_of", ""))
        as_of_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        as_of_str = pf.get("as_of", "")

    total_value = s.get("total_value_twd", 0)
    cash_ratio = s.get("cash_ratio_pct", 0)
    day_pnl = s.get("day_pnl_twd", 0)
    day_pct = s.get("day_pnl_pct", 0)
    total_pnl = s.get("total_pnl_twd", 0)
    total_pct = s.get("total_pnl_pct", 0)
    alpha = s.get("alpha_vs_benchmark_pct", 0)
    bench_pct = bench.get("day_change_pct", 0)
    bench_sym = bench.get("symbol", "")

    # Return row
    ret_cells = []
    for label, key, fmt_digits in (
        ("7日", "ret_7d_pct", 2), ("30日", "ret_30d_pct", 2),
        ("90日", "ret_90d_pct", 2), ("1年", "ret_1y_pct", 1),
    ):
        v = s.get(key)
        ret_cells.append(
            f'<div class="ret-cell"><div class="ret-lbl">{label}</div>'
            f'<div class="mono tnum {_cls(v)}">{_fmt_pct(v, fmt_digits)}</div></div>'
        )

    # Portfolio sparkline
    spark_html = _spark_svg(series, width=340, height=48) if series else ""

    # Pillar bars
    actual = pillar.get("actual", {})
    target = pillar.get("target", {})
    pillar_rows = []
    for key in ("growth", "defense", "flexibility"):
        a = actual.get(key, 0)
        t = target.get(key, 0)
        diff = a - t
        diff_cls = "up" if diff > 5 else ("dn" if diff < -5 else "flat")
        cls = PILLAR_CLS.get(key, "")
        pillar_rows.append(f'''
          <div class="pillar-row">
            <div class="pillar-head">
              <span class="pillar-dot {cls}"></span>
              <span class="pillar-name">{PILLAR_LABEL.get(key, key)}</span>
              <span class="pillar-tgt muted mono">目標 {t:.0f}%</span>
              <span class="pillar-diff mono {diff_cls}">{'+' if diff > 0 else ''}{diff:.0f}pp</span>
            </div>
            <div class="pillar-bar"><div class="pillar-fill {cls}" style="width:{min(a, 100):.0f}%"></div></div>
            <div class="pillar-val mono tnum">{a:.0f}%</div>
          </div>
        ''')

    # Attribution chips
    pos_chips = "".join(
        f'<a class="chip chip-up" href="holdings/{h["symbol"]}.html">{h["symbol"]} {_fmt_twd(h["day_contribution"], sign=True)}</a>'
        for h in attr.get("positive", [])
    ) or '<span class="chip chip-muted">無</span>'
    neg_chips = "".join(
        f'<a class="chip chip-dn" href="holdings/{h["symbol"]}.html">{h["symbol"]} {_fmt_twd(h["day_contribution"], sign=True)}</a>'
        for h in attr.get("negative", [])
    ) or '<span class="chip chip-muted">無</span>'

    # Risk metrics
    vol = risk.get("volatility_annualized_pct", 0)
    dd30 = risk.get("drawdown_30d_pct", 0)
    dd90 = risk.get("drawdown_90d_pct", 0)
    dd1y = risk.get("drawdown_1y_pct", 0)

    return f'''
<section class="pf-card wrap">
  <div class="pf-top">
    <div class="pf-top-l">
      <div class="pf-title-row">
        <h2>投資組合 · <span class="sec-en">PORTFOLIO</span></h2>
        <span class="live-dot"></span>
      </div>
      <div class="pf-asof muted mono">AS OF {html.escape(as_of_str)} · {html.escape(profile.get("style", "—"))}</div>
    </div>
    <div class="pf-top-r">{spark_html}</div>
  </div>

  <div class="pf-hero">
    <div class="pf-hero-main">
      <div class="pf-hero-lbl muted">總市值</div>
      <div class="pf-hero-val mono tnum">{_fmt_twd(total_value)}</div>
      <div class="pf-hero-sub muted mono">現金 {cash_ratio:.1f}%</div>
    </div>
    <div class="pf-hero-side">
      <div class="pf-metric">
        <div class="muted">今日</div>
        <div class="mono tnum {_cls(day_pnl)}">{_fmt_twd(day_pnl, sign=True)}</div>
        <div class="mono tnum small {_cls(day_pnl)}">{_fmt_pct(day_pct)}</div>
      </div>
      <div class="pf-metric">
        <div class="muted">總損益</div>
        <div class="mono tnum {_cls(total_pnl)}">{_fmt_twd(total_pnl, sign=True)}</div>
        <div class="mono tnum small {_cls(total_pnl)}">{_fmt_pct(total_pct)}</div>
      </div>
      <div class="pf-metric">
        <div class="muted">vs {html.escape(bench_sym)}</div>
        <div class="mono tnum muted small">{_fmt_pct(bench_pct)}</div>
        <div class="mono tnum alpha-val {_cls(alpha)}">α {_fmt_pct(alpha)}</div>
      </div>
    </div>
  </div>

  <div class="pf-returns">{"".join(ret_cells)}</div>

  <div class="pf-split">
    <div class="pf-pillars">
      <div class="pf-sub-head">三柱配置</div>
      {"".join(pillar_rows)}
    </div>
    <div class="pf-risk">
      <div class="pf-sub-head">風險指標</div>
      <div class="risk-grid">
        <div class="risk-cell">
          <div class="muted">年化波動</div>
          <div class="mono tnum val-md">{vol:.1f}%</div>
        </div>
        <div class="risk-cell">
          <div class="muted">30日回撤</div>
          <div class="mono tnum val-md {_cls(dd30)}">{dd30:.2f}%</div>
        </div>
        <div class="risk-cell">
          <div class="muted">90日回撤</div>
          <div class="mono tnum val-md {_cls(dd90)}">{dd90:.2f}%</div>
        </div>
        <div class="risk-cell">
          <div class="muted">1年回撤</div>
          <div class="mono tnum val-md {_cls(dd1y)}">{dd1y:.2f}%</div>
        </div>
      </div>
    </div>
  </div>

  <div class="pf-attr">
    <div class="pf-sub-head small">今日歸因</div>
    <div class="attr-row"><span class="attr-lbl muted">正貢獻</span>{pos_chips}</div>
    <div class="attr-row"><span class="attr-lbl muted">負貢獻</span>{neg_chips}</div>
  </div>

  {render_alerts_block(alerts, pf.get("alert_count", 0))}
</section>
'''


def render_alerts_block(alerts: dict, total: int) -> str:
    if total == 0:
        return ''
    items = []
    for a in alerts.get("stop_loss", []):
        items.append(
            f'<div class="alert-item alert-red"><span class="alert-tag mono">STOP</span> <strong>停損觸發</strong> '
            f'{a["symbol"]} {html.escape(a["name"])}：現價 <span class="mono">{a["price"]}</span> ≤ 停損 <span class="mono">{a["stop_loss"]}</span></div>'
        )
    for a in alerts.get("take_profit", []):
        items.append(
            f'<div class="alert-item alert-green"><span class="alert-tag mono">TP</span> <strong>停利觸發</strong> '
            f'{a["symbol"]} {html.escape(a["name"])}：現價 <span class="mono">{a["price"]}</span> ≥ 停利 <span class="mono">{a["take_profit"]}</span></div>'
        )
    for a in alerts.get("nearing_stop", []):
        items.append(
            f'<div class="alert-item alert-amber"><span class="alert-tag mono">NEAR</span> <strong>接近停損</strong> '
            f'{a["symbol"]}：距離 {a["stop_loss_dist_pct"]:.1f}%</div>'
        )
    for a in alerts.get("concentration", []):
        items.append(
            f'<div class="alert-item alert-amber"><span class="alert-tag mono">CONC</span> <strong>單一持股過重</strong> '
            f'{a["symbol"]}：佔比 {a["weight_pct"]:.1f}% &gt; 上限 {a["limit_pct"]:.0f}%</div>'
        )
    for a in alerts.get("pillar", []):
        items.append(
            f'<div class="alert-item alert-purple"><span class="alert-tag mono">PILLAR</span> <strong>三柱失衡</strong> '
            f'{PILLAR_LABEL.get(a["pillar"], a["pillar"])}：現 {a["actual_pct"]:.0f}% vs 目標 {a["target_pct"]:.0f}% '
            f'(差 {a["diff_pct"]:+.1f}pp)</div>'
        )
    return f'''
<div class="pf-alerts">
  <div class="pf-sub-head with-badge">
    <span class="mono">ALERTS · 組合警報</span> <span class="badge-count">{total} ACTIVE</span>
  </div>
  <div class="alert-list">{"".join(items)}</div>
</div>
'''


# ---------------------------------------------------------------------------
# Holdings grid (expanded cards with sparklines)
# ---------------------------------------------------------------------------

def render_holdings_grid(pf: dict) -> str:
    if not pf:
        return ''
    holdings = pf.get("holdings", [])
    if not holdings:
        return ''
    cards = []
    for h in holdings:
        spark = _spark_svg(h.get("sparkline", []), width=120, height=28)
        pct52 = h.get("pct_52w", 0)
        pnl_cls = _cls(h.get("pnl"))
        day_cls = _cls(h.get("day_change_pct"))
        pillar_cls = PILLAR_CLS.get(h.get("pillar", "growth"), "")
        stop_hint = ""
        if h.get("stop_loss_dist_pct") is not None:
            d = h["stop_loss_dist_pct"]
            warn = " stop-warn" if 0 < d < 5 else ""
            stop_hint = f'<div class="mini-row muted"><span>距停損</span><span class="mono tnum{warn}">{d:+.1f}%</span></div>'
        cards.append(f'''
        <a class="holding-card" href="holdings/{h["symbol"]}.html">
          <div class="hc-head">
            <div>
              <div class="hc-sym mono">{h["symbol"]}</div>
              <div class="hc-name muted small">{html.escape(h["name"])}</div>
            </div>
            <span class="pillar-dot {pillar_cls}" title="{PILLAR_LABEL.get(h.get("pillar", "growth"), "")}"></span>
          </div>
          <div class="hc-price-row">
            <span class="mono tnum val-md">{h["price"]:.2f}</span>
            <span class="mono tnum small {day_cls}">{_fmt_pct(h["day_change_pct"])}</span>
          </div>
          {spark}
          <div class="mini-row">
            <span class="muted">市值</span><span class="mono tnum">{_fmt_twd(h["value"])}</span>
          </div>
          <div class="mini-row">
            <span class="muted">損益</span>
            <span class="mono tnum {pnl_cls}">{_fmt_twd(h["pnl"], sign=True)} ({_fmt_pct(h["pnl_pct"])})</span>
          </div>
          <div class="mini-row">
            <span class="muted">52w</span>
            <span class="mono tnum">{pct52:.0f}%位階</span>
          </div>
          {stop_hint}
        </a>''')

    # Watchlist mini cards
    watchlist_cards = []
    for w in pf.get("watchlist", []):
        spark = _spark_svg(w.get("sparkline", []), width=100, height=24)
        pct52 = w.get("pct_52w", 0)
        watchlist_cards.append(f'''
        <a class="watch-card" href="holdings/{w["symbol"]}.html">
          <div class="wc-head">
            <span class="mono">{w["symbol"]}</span>
            <span class="muted small">{html.escape(w["name"])}</span>
          </div>
          <div class="wc-price">
            <span class="mono tnum">{w["price"]:.2f}</span>
            <span class="mono tnum small {_cls(w["day_change_pct"])}">{_fmt_pct(w["day_change_pct"])}</span>
          </div>
          {spark}
          <div class="mini-row muted">
            <span>YTD <span class="{_cls(w.get("ret_ytd"))}">{_fmt_pct(w.get("ret_ytd"), 1)}</span></span>
            <span>52w <span class="mono">{pct52:.0f}%</span></span>
          </div>
        </a>
        ''')

    return f'''
<section class="holdings-grid wrap">
  <div class="section-head">
    <h2>持股明細 · <span class="sec-en">HOLDINGS</span></h2>
    <span class="muted small">{len(holdings)} 檔</span>
  </div>
  <div class="hgrid">{"".join(cards)}</div>

  <div class="section-head mt">
    <h2>追蹤清單 · <span class="sec-en">WATCHLIST</span></h2>
    <span class="muted small">{len(pf.get("watchlist", []))} 檔</span>
  </div>
  <div class="wgrid">{"".join(watchlist_cards)}</div>
</section>
'''


# ---------------------------------------------------------------------------
# Analysis section (Gemini output)
# ---------------------------------------------------------------------------

def render_analysis_section(analysis: dict) -> str:
    mp = analysis.get("market_pulse", {})
    macro_ctx = analysis.get("macro_context", {})
    diag = analysis.get("portfolio_diagnosis", {})
    topics = analysis.get("topics", [])
    holdings = analysis.get("holdings_analysis", [])
    opps = analysis.get("opportunities", [])
    actions = analysis.get("action_checklist", {"green": [], "yellow": [], "red": []})
    lp = analysis.get("learning_point", {})
    model = analysis.get("model", "gemini")
    gen_time = analysis.get("generated_at", "")
    try:
        gen_dt = datetime.fromisoformat(gen_time)
        gen_str = gen_dt.strftime("%H:%M")
    except Exception:
        gen_str = ""

    # Market pulse
    pulse_html = f'''
<section class="a-section">
  <div class="section-head">
    <h2>市場脈搏 · <span class="sec-en">MARKET PULSE</span></h2>
    <span class="muted small mono">GENERATED {gen_str}</span>
  </div>
  <div class="pulse-grid">
    <div class="pulse-cell">
      <div class="muted small">台股</div>
      {_sentiment_badge(mp.get("tw_sentiment", "中性"))}
    </div>
    <div class="pulse-cell">
      <div class="muted small">美股</div>
      {_sentiment_badge(mp.get("us_sentiment", "中性"))}
    </div>
  </div>
  <p class="pulse-narrative">{html.escape(mp.get("summary", ""))}</p>
</section>
'''

    # Macro context
    macro_html = ""
    if macro_ctx.get("narrative"):
        wp = macro_ctx.get("watchpoints", [])
        wp_html = ""
        if wp:
            wp_html = '<ul class="watchpoint-list">' + "".join(f'<li>{html.escape(w)}</li>' for w in wp) + '</ul>'
        macro_html = f'''
<section class="a-section">
  <div class="section-head"><h2>總經背景 · <span class="sec-en">MACRO</span></h2></div>
  <p class="narrative">{html.escape(macro_ctx["narrative"])}</p>
  {wp_html}
</section>
'''

    # Portfolio diagnosis
    diag_html = ""
    if diag.get("overall_health"):
        health = diag.get("overall_health", "")
        health_cls = {"良好": "up", "需調整": "amber", "高風險": "dn"}.get(health, "flat")
        diag_html = f'''
<section class="a-section diag-section">
  <div class="section-head"><h2>組合診斷 · <span class="sec-en">DIAGNOSIS</span></h2></div>
  <div class="diag-head">
    <span class="muted small">健康度</span>
    <span class="badge badge-{health_cls} large">{html.escape(health)}</span>
  </div>
  <div class="diag-body">
    <div class="diag-row"><span class="diag-lbl">關鍵議題</span>
      <div class="diag-txt">{html.escape(diag.get("key_issue", ""))}</div></div>
    <div class="diag-row"><span class="diag-lbl">調整建議</span>
      <div class="diag-txt">{html.escape(diag.get("rebalance_advice", ""))}</div></div>
  </div>
</section>
'''

    # Action checklist
    def render_actions(items, color_cls, label, icon):
        if not items:
            li = '<li class="empty">今日無建議</li>'
        else:
            li = "".join(
                f'<li><strong>{html.escape(i["action"])}</strong>'
                f'<div class="action-reason">{html.escape(i["reason"])}</div></li>'
                for i in items
            )
        return (
            f'<div class="action-col {color_cls}">'
            f'<div class="action-header"><span class="action-tag mono">{icon}</span> {label}</div>'
            f'<ul>{li}</ul></div>'
        )

    actions_html = f'''
<section class="a-section">
  <div class="section-head"><h2>今日行動 · <span class="sec-en">ACTION CHECKLIST</span></h2></div>
  <div class="actions-grid">
    {render_actions(actions.get("green", []), "action-green", "可以做", "GO")}
    {render_actions(actions.get("yellow", []), "action-yellow", "該警戒", "WATCH")}
    {render_actions(actions.get("red", []), "action-red", "不要做", "HOLD")}
  </div>
</section>
'''

    # Topics
    topic_cards = []
    for t in topics:
        tickers_chips = "".join(
            f'<span class="chip chip-muted small">{html.escape(tk)}</span>'
            for tk in t.get("tickers", [])[:6]
        )
        pts = "".join(f'<li>{html.escape(p)}</li>' for p in t.get("key_points", []))
        pts_html = f'<ul class="topic-points">{pts}</ul>' if pts else ""
        topic_cards.append(f'''
        <article class="topic-card">
          <div class="topic-head">
            <h3>{html.escape(t.get("title", ""))}</h3>
            {_sentiment_badge(t.get("sentiment", "中性"))}
          </div>
          <div class="topic-tickers">{tickers_chips}</div>
          <p class="narrative">{html.escape(t.get("narrative", ""))}</p>
          {pts_html}
        </article>''')
    topics_html = (
        f'<section class="a-section"><div class="section-head"><h2>今日主題 · <span class="sec-en">TOPICS</span></h2>'
        f'<span class="muted small">{len(topics)} 則</span></div>'
        f'{"".join(topic_cards)}</section>'
    )

    # Holdings analysis with bull/bear breakdown
    holding_cards = []
    for h in holdings:
        bb = h.get("bull_bear_breakdown", {})
        bull = bb.get("bull_pct", 0)
        bear = bb.get("bear_pct", 0)
        neu = bb.get("neutral_pct", 0)
        catalysts = h.get("key_catalysts", [])
        risks = h.get("key_risks", [])
        cat_html = (
            "<div class='hc-list-head'>催化劑</div><ul class='hc-list up-list'>" +
            "".join(f'<li>{html.escape(c)}</li>' for c in catalysts) + "</ul>"
        ) if catalysts else ""
        risk_html = (
            "<div class='hc-list-head'>風險</div><ul class='hc-list dn-list'>" +
            "".join(f'<li>{html.escape(r)}</li>' for r in risks) + "</ul>"
        ) if risks else ""
        holding_cards.append(f'''
        <article class="holding-analysis">
          <div class="ha-head">
            <h3><a href="../holdings/{html.escape(h.get("symbol", ""))}.html">{html.escape(h.get("symbol", ""))} {html.escape(h.get("name", ""))}</a></h3>
            {_sentiment_badge(h.get("outlook", "中性"))}
          </div>
          <p class="narrative">{html.escape(h.get("commentary", ""))}</p>
          <div class="bullbear">
            <div class="bb-bar">
              <div class="bb-bull" style="width:{bull}%" title="看多 {bull}%"></div>
              <div class="bb-neu"  style="width:{neu}%" title="觀望 {neu}%"></div>
              <div class="bb-bear" style="width:{bear}%" title="看空 {bear}%"></div>
            </div>
            <div class="bb-legend">
              <span class="bb-lbl bull">看多 {bull}%</span>
              <span class="bb-lbl neu">觀望 {neu}%</span>
              <span class="bb-lbl bear">看空 {bear}%</span>
            </div>
          </div>
          <div class="hc-split">{cat_html}{risk_html}</div>
        </article>''')
    holdings_html = (
        f'<section class="a-section"><div class="section-head"><h2>持股分析 · <span class="sec-en">HOLDINGS AI</span></h2></div>'
        f'{"".join(holding_cards)}</section>'
        if holding_cards else ""
    )

    # Opportunities
    opp_cards = []
    for o in opps:
        opp_cards.append(f'''
        <article class="opp-card">
          <h3>{html.escape(o.get("symbol", ""))} {html.escape(o.get("name", ""))}</h3>
          <p><span class="label-inline">論點</span>{html.escape(o.get("thesis", ""))}</p>
          <p><span class="label-inline">研究切入點</span>{html.escape(o.get("research_angle", ""))}</p>
          <p class="risk-line"><span class="label-inline dn">⚠️ 風險</span>{html.escape(o.get("risk", ""))}</p>
        </article>''')
    opps_html = (
        f'<section class="a-section"><div class="section-head"><h2>值得研究 · <span class="sec-en">OPPORTUNITIES</span> '
        f'<span class="badge-count">{len(opps)} DETECTED</span></h2></div>'
        f'{"".join(opp_cards)}</section>'
        if opp_cards else ""
    )

    # Budget allocation — full-detail basket builder on brief page.
    # Same basket-builder JS hook as the hero card; this page shows the fuller
    # rationale / entry / stop / risk per candidate.
    # We pad the list with synthetic entries from opportunities when Gemini
    # was stingy, so the budget slider has real alternatives to pick from.
    budget_alloc = analysis.get("budget_allocation", {})
    budget_section_html = ""
    raw_allocs = budget_alloc.get("allocations") or []
    allocs = _pad_allocations_from_opportunities(raw_allocs, analysis)
    if allocs:
        default_budget = int(budget_alloc.get("budget_twd", 5000)) or 5000
        rows = []
        for idx, al in enumerate(allocs):
            action = al.get("action", "")
            is_cash = "現金" in action or "不動作" in action
            is_synth = bool(al.get("synthetic"))
            cls = "alloc-cash" if is_cash else ("alloc-buy alloc-synth" if is_synth else "alloc-buy")
            srcs = al.get("data_sources") or []
            src_html = "".join(f'<span class="chip chip-muted small">{html.escape(s)}</span>' for s in srcs)
            sl = al.get("stop_loss_price")
            tp = al.get("take_profit_price")
            shares = al.get("target_shares") or 0
            cost = al.get("target_cost_twd") or 0
            conf = int(al.get("confidence_pct") or 0)
            row_levels = []
            if shares: row_levels.append(f"<strong>{shares} 股</strong>")
            if cost: row_levels.append(f"約 {_fmt_twd(cost)}")
            if al.get("entry_condition"): row_levels.append(f"進場：{html.escape(al['entry_condition'])}")
            if sl: row_levels.append(f'<span class="dn">停損 {sl}</span>')
            if tp: row_levels.append(f'<span class="up">停利 {tp}</span>')
            levels_html = " · ".join(row_levels) if row_levels else ""
            check_id = f"full-alloc-{idx}"
            if is_cash:
                pick_input = (
                    '<span class="alloc-badge alloc-hold-badge">保留現金</span>'
                )
            else:
                pick_input = (
                    f'<label for="{check_id}" class="alloc-pick-lbl small">'
                    f'<input type="checkbox" class="alloc-check" id="{check_id}" '
                    f'data-cost="{int(cost)}" data-conf="{conf}"> 加入籃子</label>'
                )
            synth_badge = (
                '<span class="alloc-synth-tag small" title="AI 未直接列入主推，由同題材候選自動補齊">次選 · 候選觀察</span>'
                if is_synth else ""
            )
            rows.append(f'''
            <article class="alloc-full-card {cls}"
                     data-cost="{int(cost)}" data-conf="{conf}" data-is-cash="{"1" if is_cash else "0"}">
              <div class="alloc-full-head">
                <div>
                  <div class="alloc-action-big">{html.escape(action)} {synth_badge}</div>
                  <h3>{html.escape(al.get("symbol", ""))} <span class="muted">{html.escape(al.get("name", ""))}</span></h3>
                </div>
                <div class="alloc-full-right">
                  <div class="alloc-conf-big mono">信心度 {conf}%</div>
                  {pick_input}
                </div>
              </div>
              {f'<div class="alloc-levels-row mono small">{levels_html}</div>' if levels_html else ''}
              <p><span class="label-inline">理由</span>{html.escape(al.get("rationale", ""))}</p>
              {"<div class='alloc-sources'><span class='label-inline'>依據</span>" + src_html + "</div>" if src_html else ""}
              <p class="risk-line"><span class="label-inline dn">⚠ 風險</span>{html.escape(al.get("risk", ""))}</p>
            </article>''')
        unalloc = budget_alloc.get("unallocated_twd", 0)
        unalloc_line = (f'<p class="muted small">保留現金 {_fmt_twd(unalloc)}（等更好的機會）</p>'
                        if unalloc and unalloc > 0 else "")
        why_not = budget_alloc.get("why_not_other_picks") or ""
        why_not_line = f'<p class="muted small"><strong>為什麼不選別檔：</strong>{html.escape(why_not)}</p>' if why_not else ""
        preset_amounts = [5000, 10000, 20000, 50000]
        preset_btns = "".join(
            f'<button type="button" class="basket-preset-btn" data-amt="{amt}">'
            f'NT${amt // 1000}k</button>' for amt in preset_amounts
        )
        budget_section_html = f'''
<section class="a-section basket-builder" id="budget">
  <div class="section-head"><h2>籃子建議 · <span class="sec-en">BASKET BUILDER</span> <span class="badge-count">SNOWBALL · {len(allocs)} 候選</span></h2></div>
  <div class="budget-plan-big">{html.escape(budget_alloc.get("plan_summary", ""))}</div>
  <div class="basket-controls basket-controls-big">
    <div class="basket-budget-row">
      <label class="basket-budget-lbl">本次預算</label>
      <div class="basket-preset-btns">{preset_btns}</div>
      <div class="basket-input-wrap">
        <span class="basket-currency">NT$</span>
        <input type="number" class="basket-budget-input mono" value="{default_budget}"
               step="1000" min="1000" max="500000" aria-label="預算（新台幣）">
      </div>
    </div>
    <p class="basket-hint muted small">▸ 改預算會自動挑信心最高的組合；也可手動勾選覆蓋。已保留不動作 / 觀望候選做參考。</p>
  </div>
  {"".join(rows)}
  <div class="basket-summary basket-summary-big mono">
    <span class="bs-cell"><span class="muted">籃子合計</span> <strong class="basket-total">NT$0</strong></span>
    <span class="bs-cell"><span class="muted">剩</span> <strong class="basket-remaining">NT${default_budget:,}</strong></span>
    <span class="bs-cell"><span class="muted">已選</span> <strong class="basket-count">0 檔</strong></span>
  </div>
  {unalloc_line}
  {why_not_line}
</section>
'''

    # Learning
    learning_html = ""
    if lp:
        learning_html = f'''
<section class="a-section learning-section">
  <div class="section-head"><h2>學習點 · <span class="sec-en">LESSON</span></h2></div>
  <div class="learning-card">
    <h3>{html.escape(lp.get("term", ""))}</h3>
    <p>{html.escape(lp.get("explanation", ""))}</p>
  </div>
</section>
'''

    # Disclaimer
    disclaimer = f'''
<section class="a-section disclaimer">
  <p>分析由 <code>{html.escape(model)}</code> 自動生成，僅供研究參考。決策責任在你自己。</p>
</section>
'''

    return (
        pulse_html + macro_html + diag_html + actions_html +
        budget_section_html +
        topics_html + holdings_html + opps_html + learning_html + disclaimer
    )


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-Hant" data-theme="dark" data-density="comfortable" data-accent="blue">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{title}</title>
<!-- Premium typography: Inter (Latin) + Noto Sans TC (繁中) + JetBrains Mono (數字/代號) -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&family=JetBrains+Mono:wght@400;500;600&display=swap">
<link rel="stylesheet" href="{css_href}">
</head>
<body>
"""

PAGE_FOOT = """
<footer class="wrap">
  <p>生成於 {now} · <a href="https://github.com/iannn211/stock-daily-brief" target="_blank">source</a></p>
</footer>
<script>
/* Time-aware greeting — overrides the static 早安 baked in at 07:30 build time
 * so opening the page at 15:00 shows 「午安」, 21:00 shows 「晚安」. */
(function () {{
  var g = document.querySelector('.bh-greeting');
  if (!g) return;
  var h = new Date().getHours();
  var label;
  if (h >= 5 && h < 11)       label = '早安';
  else if (h >= 11 && h < 13) label = '中午好';
  else if (h >= 13 && h < 18) label = '午安';
  else if (h >= 18 && h < 23) label = '晚上好';
  else                        label = '夜深了';
  g.textContent = label;
}})();

/* Basket builder — client-side budget-aware allocation picker.
 * Each .basket-builder scope has its own budget input, preset buttons,
 * allocation checkboxes, and summary line. Changing the budget re-runs the
 * greedy auto-fill (take highest confidence_pct until budget exhausted);
 * toggling a checkbox switches to manual mode and just updates totals. */
(function () {{
  function fmt(n) {{
    return 'NT$' + Math.round(n).toLocaleString('en-US');
  }}

  function setupBuilder(builder) {{
    var input     = builder.querySelector('.basket-budget-input');
    var presets   = builder.querySelectorAll('.basket-preset-btn');
    var cards     = builder.querySelectorAll('[data-cost]');
    var totalEl   = builder.querySelector('.basket-total');
    var remainEl  = builder.querySelector('.basket-remaining');
    var countEl   = builder.querySelector('.basket-count');
    if (!input || !totalEl) return;

    function recalc() {{
      var total = 0, count = 0;
      cards.forEach(function (card) {{
        var check = card.querySelector('.alloc-check');
        if (!check) return;
        var cost = parseFloat(card.getAttribute('data-cost')) || 0;
        if (check.checked && cost > 0) {{
          total += cost;
          count += 1;
          card.classList.add('alloc-picked');
        }} else {{
          card.classList.remove('alloc-picked');
        }}
      }});
      var budget = parseFloat(input.value) || 0;
      var remaining = budget - total;
      totalEl.textContent = fmt(total);
      remainEl.textContent = fmt(remaining);
      remainEl.classList.toggle('dn', remaining < 0);
      remainEl.classList.toggle('up', remaining >= 0);
      countEl.textContent = count + ' 檔';
      // Mark preset button active if its amount matches current budget
      presets.forEach(function (b) {{
        var amt = parseFloat(b.getAttribute('data-amt')) || 0;
        b.classList.toggle('is-active', Math.abs(amt - budget) < 1);
      }});
    }}

    function autoFill() {{
      var budget = parseFloat(input.value) || 0;
      // Sort non-cash cards by confidence desc, then by cost asc (cheaper first on ties).
      var buyable = Array.prototype.slice.call(cards).filter(function (c) {{
        return c.getAttribute('data-is-cash') !== '1' &&
               (parseFloat(c.getAttribute('data-cost')) || 0) > 0;
      }});
      buyable.sort(function (a, b) {{
        var ca = parseFloat(a.getAttribute('data-conf')) || 0;
        var cb = parseFloat(b.getAttribute('data-conf')) || 0;
        if (cb !== ca) return cb - ca;
        var pa = parseFloat(a.getAttribute('data-cost')) || 0;
        var pb = parseFloat(b.getAttribute('data-cost')) || 0;
        return pa - pb;
      }});
      // Uncheck everything, then greedy-pack.
      cards.forEach(function (c) {{
        var ch = c.querySelector('.alloc-check');
        if (ch) ch.checked = false;
      }});
      var spent = 0;
      buyable.forEach(function (c) {{
        var cost = parseFloat(c.getAttribute('data-cost')) || 0;
        if (cost > 0 && spent + cost <= budget) {{
          var ch = c.querySelector('.alloc-check');
          if (ch) {{ ch.checked = true; spent += cost; }}
        }}
      }});
      recalc();
    }}

    input.addEventListener('input', autoFill);
    input.addEventListener('change', autoFill);
    presets.forEach(function (btn) {{
      btn.addEventListener('click', function (e) {{
        e.preventDefault();
        var amt = btn.getAttribute('data-amt');
        if (amt) {{ input.value = amt; autoFill(); }}
      }});
    }});
    cards.forEach(function (c) {{
      var ch = c.querySelector('.alloc-check');
      if (ch) ch.addEventListener('change', recalc);
      // Swallow clicks on the deep-link so they don't toggle the label/checkbox.
      var dl = c.querySelector('.alloc-deeplink');
      if (dl) dl.addEventListener('click', function (ev) {{ ev.stopPropagation(); }});
    }});

    autoFill();
  }}

  document.querySelectorAll('.basket-builder').forEach(setupBuilder);
}})();
</script>
</body>
</html>
"""


def render_desk_sidebar(pf: dict) -> str:
    """Hyperdash-style left sidebar: compact label/value stat lists."""
    if not pf:
        return ""
    s = pf.get("summary", {})
    bench = pf.get("benchmark", {})
    risk = pf.get("risk", {})
    pillar = pf.get("pillar_allocation", {})
    alerts = pf.get("alerts", {})
    profile = pf.get("risk_profile", {})
    alert_count = pf.get("alert_count", 0)

    def _row(lbl, val, cls="", title=""):
        t = f' title="{title}"' if title else ""
        return f'<div class="stat-row"{t}><span class="stat-lbl">{lbl}</span><span class="stat-val {cls}">{val}</span></div>'

    # Overview
    ov = [
        _row("TOTAL VALUE", _fmt_twd(s.get("total_value_twd", 0))),
        _row("CASH", f"{s.get('cash_ratio_pct', 0):.1f}%"),
        _row("TODAY", f"{_fmt_twd(s.get('day_pnl_twd', 0), sign=True)} ({_fmt_pct(s.get('day_pnl_pct', 0))})",
             _cls(s.get("day_pnl_twd"))),
        _row("ALL TIME", f"{_fmt_twd(s.get('total_pnl_twd', 0), sign=True)} ({_fmt_pct(s.get('total_pnl_pct', 0))})",
             _cls(s.get("total_pnl_twd"))),
        _row(f"vs {bench.get('symbol', '0050')}", _fmt_pct(bench.get('day_change_pct', 0)), _cls(bench.get('day_change_pct', 0))),
        _row("ALPHA", _fmt_pct(s.get("alpha_vs_benchmark_pct", 0)), _cls(s.get("alpha_vs_benchmark_pct", 0))),
    ]

    # Returns
    rets = [
        _row("7D", _fmt_pct(s.get('ret_7d_pct'), 2), _cls(s.get('ret_7d_pct'))),
        _row("30D", _fmt_pct(s.get('ret_30d_pct'), 2), _cls(s.get('ret_30d_pct'))),
        _row("90D", _fmt_pct(s.get('ret_90d_pct'), 2), _cls(s.get('ret_90d_pct'))),
        _row("1Y", _fmt_pct(s.get('ret_1y_pct'), 1), _cls(s.get('ret_1y_pct'))),
    ]

    # Risk
    rk = [
        _row("VOLATILITY", f"{risk.get('volatility_annualized_pct', 0):.1f}%"),
        _row("DRAWDOWN 30D", f"{risk.get('drawdown_30d_pct', 0):.2f}%", _cls(risk.get('drawdown_30d_pct', 0))),
        _row("DRAWDOWN 90D", f"{risk.get('drawdown_90d_pct', 0):.2f}%", _cls(risk.get('drawdown_90d_pct', 0))),
        _row("DRAWDOWN 1Y", f"{risk.get('drawdown_1y_pct', 0):.2f}%", _cls(risk.get('drawdown_1y_pct', 0))),
        _row("STYLE", html.escape(profile.get("style", "—"))),
    ]

    # Pillar allocation
    actual = pillar.get("actual", {})
    target = pillar.get("target", {})
    pills_rows = []
    for key in ("growth", "defense", "flexibility"):
        a = actual.get(key, 0)
        t = target.get(key, 0)
        diff = a - t
        diff_cls = "up" if diff > 5 else ("dn" if diff < -5 else "flat")
        sign = "+" if diff > 0 else ""
        pills_rows.append(
            f'<div class="stat-row pillar-stat">'
            f'<span class="stat-lbl"><span class="pillar-dot {PILLAR_CLS.get(key, "")}"></span>{PILLAR_LABEL.get(key, key)}</span>'
            f'<span class="stat-val"><span class="mono">{a:.0f}%</span><span class="muted mono"> / {t:.0f}%</span> <span class="{diff_cls} mono small">{sign}{diff:.0f}</span></span>'
            f'</div>'
        )

    # Alerts
    alert_rows = []
    for a in alerts.get("stop_loss", []):
        alert_rows.append(f'<div class="alert-line"><span class="dn">🔴 {a["symbol"]}</span> <span class="muted">停損觸發 @{a["stop_loss"]}</span></div>')
    for a in alerts.get("take_profit", []):
        alert_rows.append(f'<div class="alert-line"><span class="up">🟢 {a["symbol"]}</span> <span class="muted">停利觸發 @{a["take_profit"]}</span></div>')
    for a in alerts.get("nearing_stop", []):
        alert_rows.append(f'<div class="alert-line"><span class="amber">🟡 {a["symbol"]}</span> <span class="muted">接近停損 {a["stop_loss_dist_pct"]:+.1f}%</span></div>')
    for a in alerts.get("concentration", []):
        alert_rows.append(f'<div class="alert-line"><span class="amber">🟠 {a["symbol"]}</span> <span class="muted">{a["weight_pct"]:.1f}% &gt; {a["limit_pct"]:.0f}%</span></div>')
    for a in alerts.get("pillar", []):
        alert_rows.append(f'<div class="alert-line"><span class="purple">🟣 {PILLAR_LABEL.get(a["pillar"], a["pillar"])}</span> <span class="muted">{a["actual_pct"]:.0f}% / {a["target_pct"]:.0f}% ({a["diff_pct"]:+.1f}pp)</span></div>')
    if not alert_rows:
        alert_rows = ['<div class="alert-line muted">無警報</div>']

    return f'''
<aside class="desk-sidebar">
  <div class="stat-block">
    <div class="stat-block-head">OVERVIEW</div>
    {"".join(ov)}
  </div>
  <div class="stat-block">
    <div class="stat-block-head">RETURNS</div>
    {"".join(rets)}
  </div>
  <div class="stat-block">
    <div class="stat-block-head">RISK (90D)</div>
    {"".join(rk)}
  </div>
  <div class="stat-block">
    <div class="stat-block-head">ALLOCATION</div>
    {"".join(pills_rows)}
  </div>
  <div class="stat-block">
    <div class="stat-block-head">ALERTS <span class="badge-count">{alert_count} ACTIVE</span></div>
    {"".join(alert_rows)}
  </div>
</aside>
'''


def render_big_chart(pf: dict) -> str:
    """Big hero chart: portfolio value 90 days."""
    if not pf:
        return ""
    series = pf.get("portfolio_series", [])
    if len(series) < 2:
        return '<div class="chart-area"><p class="muted">歷史資料不足</p></div>'

    w, h = 900, 240
    pad_l, pad_r, pad_t, pad_b = 56, 16, 24, 30

    values = [r["v"] for r in series]
    dates = [r["d"] for r in series]
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1

    iw = w - pad_l - pad_r
    ih = h - pad_t - pad_b

    def sx(i):
        return pad_l + iw * i / (len(values) - 1)

    def sy(v):
        return pad_t + ih * (1 - (v - mn) / rng)

    # Line path
    d = "M " + " L ".join(f"{sx(i):.1f} {sy(v):.1f}" for i, v in enumerate(values))
    # Area polygon
    area = "".join(f"{sx(i):.1f},{sy(v):.1f} " for i, v in enumerate(values))
    area = f"{pad_l},{pad_t + ih} " + area + f"{pad_l + iw},{pad_t + ih}"

    # Value direction
    up = values[-1] >= values[0]
    stroke = "var(--up)" if up else "var(--dn)"

    # Axis labels
    y_labels = []
    for frac in (0, 0.5, 1):
        v = mn + rng * (1 - frac)
        y = pad_t + ih * frac
        y_labels.append(
            f'<text x="{pad_l - 8}" y="{y + 4:.0f}" text-anchor="end" fill="var(--tx-3)" font-size="10" font-family="var(--font-mono)">{v / 1000:.0f}k</text>'
            f'<line x1="{pad_l}" y1="{y:.0f}" x2="{pad_l + iw}" y2="{y:.0f}" stroke="var(--line)" stroke-dasharray="2 3"/>'
        )

    # Date labels: start, 1/3, 2/3, end
    x_ticks = [0, len(dates) // 3, (2 * len(dates)) // 3, len(dates) - 1]
    x_labels = []
    for i in x_ticks:
        label = dates[i][-5:]  # MM-DD
        x_labels.append(
            f'<text x="{sx(i):.0f}" y="{h - 8}" text-anchor="middle" fill="var(--tx-3)" font-size="10" font-family="var(--font-mono)">{label}</text>'
        )

    # Current value annotation
    cur = values[-1]
    cur_x = sx(len(values) - 1)
    cur_y = sy(cur)
    delta = values[-1] - values[0]
    delta_pct = delta / values[0] * 100 if values[0] else 0
    delta_cls = "up" if delta >= 0 else "dn"

    return f'''
<div class="chart-area">
  <div class="chart-head">
    <div>
      <div class="chart-title">Portfolio Value · 90D</div>
      <div class="chart-value mono tnum">{_fmt_twd(cur)}</div>
    </div>
    <div class="chart-delta {delta_cls} mono tnum">{_fmt_twd(delta, sign=True)} ({_fmt_pct(delta_pct, 2)}) · 90d</div>
  </div>
  <svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart-svg" width="100%">
    <defs>
      <linearGradient id="g-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="{stroke}" stop-opacity="0.25"/>
        <stop offset="100%" stop-color="{stroke}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    {"".join(y_labels)}
    <polygon points="{area}" fill="url(#g-fill)"/>
    <path d="{d}" stroke="{stroke}" stroke-width="1.8" fill="none" stroke-linejoin="round"/>
    <circle cx="{cur_x:.0f}" cy="{cur_y:.0f}" r="4" fill="{stroke}"/>
    <circle cx="{cur_x:.0f}" cy="{cur_y:.0f}" r="7" fill="{stroke}" opacity="0.25"/>
    {"".join(x_labels)}
  </svg>
</div>
'''


def render_positions_table(pf: dict) -> str:
    holdings = pf.get("holdings", [])
    watchlist = pf.get("watchlist", [])
    if not holdings and not watchlist:
        return '<p class="muted">無持倉資料</p>'

    def row_holding(h):
        day_cls = _cls(h.get("day_change_pct"))
        pnl_cls = _cls(h.get("pnl"))
        pillar_cls = PILLAR_CLS.get(h.get("pillar", "growth"), "")
        sl_hint = ""
        d = h.get("stop_loss_dist_pct")
        if d is not None and 0 < d < 5:
            sl_hint = f' <span class="amber small" title="接近停損">⚠</span>'
        return f'''
        <tr onclick="location.href='holdings/{h["symbol"]}.html'">
          <td><span class="pillar-dot {pillar_cls}"></span><strong>{h["symbol"]}</strong> <span class="muted">{html.escape(h["name"])}</span>{sl_hint}</td>
          <td>{h["shares"]:,}</td>
          <td>{h["cost_basis"]:.2f}</td>
          <td>{h["price"]:.2f}</td>
          <td class="{day_cls}">{_fmt_pct(h["day_change_pct"])}</td>
          <td>{h.get("pct_52w", 0):.0f}%</td>
          <td>{_fmt_twd(h["value"])}</td>
          <td class="{pnl_cls}">{_fmt_twd(h["pnl"], sign=True)}</td>
          <td class="{pnl_cls}">{_fmt_pct(h["pnl_pct"])}</td>
        </tr>'''

    def row_watch(w):
        day_cls = _cls(w.get("day_change_pct"))
        ytd_cls = _cls(w.get("ret_ytd"))
        pillar_cls = PILLAR_CLS.get(w.get("pillar", "growth"), "")
        return f'''
        <tr onclick="location.href='holdings/{w["symbol"]}.html'">
          <td><span class="pillar-dot {pillar_cls}"></span><strong>{w["symbol"]}</strong> <span class="muted">{html.escape(w["name"])}</span></td>
          <td>—</td>
          <td>—</td>
          <td>{w["price"]:.2f}</td>
          <td class="{day_cls}">{_fmt_pct(w["day_change_pct"])}</td>
          <td>{w.get("pct_52w", 0):.0f}%</td>
          <td class="muted small">{w.get("currency", "")}</td>
          <td class="{ytd_cls}">YTD {_fmt_pct(w.get("ret_ytd"), 1)}</td>
          <td class="muted">觀察</td>
        </tr>'''

    holding_rows = "".join(row_holding(h) for h in holdings)
    watch_rows = "".join(row_watch(w) for w in watchlist)

    return f'''
<table class="data-table">
  <thead>
    <tr>
      <th>ASSET</th>
      <th>SHARES</th>
      <th>COST</th>
      <th>PRICE</th>
      <th>DAY</th>
      <th>52W</th>
      <th>VALUE</th>
      <th>PNL</th>
      <th>%</th>
    </tr>
  </thead>
  <tbody>
    <tr class="sub-head"><td colspan="9">HOLDINGS · {len(holdings)}</td></tr>
    {holding_rows}
    <tr class="sub-head"><td colspan="9">WATCHLIST · {len(watchlist)}</td></tr>
    {watch_rows}
  </tbody>
</table>
'''


def render_briefs_table(briefs: list[dict]) -> str:
    if not briefs:
        return '<p class="muted">還沒有 brief</p>'
    weekday_map = {"一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu",
                   "五": "Fri", "六": "Sat", "日": "Sun"}
    rows = []
    for b in briefs:
        has_ai = (ANALYSES_DIR / f'{b["date"]}.json').exists()
        ai_badge = '<span class="badge-ai">AI</span>' if has_ai else ''
        tags = " · ".join(html.escape(t) for t in b["tags"][:4])
        rows.append(f'''
        <tr onclick="location.href='briefs/{b["date"]}.html'">
          <td><strong>{b["date"]}</strong> <span class="muted">週{b["weekday"]} {weekday_map.get(b["weekday"], "")}</span></td>
          <td>{b["count"]}</td>
          <td class="left muted small">{tags}</td>
          <td>{ai_badge}</td>
        </tr>''')
    return f'''
<table class="data-table">
  <thead>
    <tr><th>DATE</th><th>NEWS</th><th>TAGS</th><th>AI</th></tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
'''


def _theme_sparkline_from_leads(lead_stocks: list[dict], history: dict,
                                  days: int = 14) -> list[float]:
    """Compute a theme sparkline by averaging normalized close of lead stocks."""
    series: list[list[float]] = []
    for ls in lead_stocks[:4]:
        sym = ls.get("symbol")
        if not sym:
            continue
        # Try .TW then .TWO (universe falls back silently)
        rows = history.get(f"{sym}.TW") or history.get(f"{sym}.TWO") or history.get(sym) or []
        tail = [r["close"] for r in rows[-days:]]
        if len(tail) < 2:
            continue
        # Normalize to start=100
        first = tail[0] or 1
        series.append([c / first * 100 for c in tail])
    if not series:
        return []
    # Average across stocks at each time step
    min_len = min(len(s) for s in series)
    return [sum(s[i] for s in series) / len(series) for i in range(min_len)]


def _crowding_tone(pct: int) -> str:
    if pct <= 30:
        return "crowd-low"
    if pct <= 60:
        return "crowd-mid"
    if pct <= 80:
        return "crowd-high"
    return "crowd-max"


def _stage_cls(stage: str) -> str:
    return {
        "萌芽": "stage-emerg",
        "早期": "stage-early",
        "中段": "stage-mid",
        "過熱": "stage-hot",
    }.get(stage, "")


def _theme_slug(opp: dict, idx: int) -> str:
    """Stable URL-safe slug for a theme.
    Chinese chars are dropped; idx ensures uniqueness across daily re-runs."""
    theme = opp.get("theme") or opp.get("symbol") or ""
    tag = opp.get("category_tag") or ""
    source = tag.lstrip("#") + "-" + theme
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", source).strip("-").lower()
    if not ascii_part:
        ascii_part = "theme"
    return f"{idx:02d}-{ascii_part[:40].strip('-')}"


_PRICES_CACHE: dict[str, dict] | None = None


def _load_prices_bysym() -> dict[str, dict]:
    """Load prices.json and index by display symbol. Cached.

    Needed because Gemini often picks lead_stocks (e.g. 1815 富喬, 5475 德宏,
    8358 金居) that aren't in portfolio.yaml's universe but ARE fetched by
    fetch_prices.py via the supply_chains + analyses auto-expansion.
    Without merging this into _pf_lookup, the theme + supply-chain pages
    render "—" / "基本面資料待補" for every such stock.
    """
    global _PRICES_CACHE
    if _PRICES_CACHE is not None:
        return _PRICES_CACHE
    path = ROOT / "prices.json"
    if not path.exists():
        _PRICES_CACHE = {}
        return _PRICES_CACHE
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("prices", {})
    except Exception:
        _PRICES_CACHE = {}
        return _PRICES_CACHE
    idx: dict[str, dict] = {}
    for yf_t, rec in raw.items():
        sym = str(rec.get("symbol") or "").strip()
        if not sym:
            continue
        # Normalize shape to match portfolio.json entries (price + fundamentals + returns)
        entry = {
            "symbol": sym,
            "name": rec.get("name") or sym,  # no name in prices.json; caller supplies it
            "price": rec.get("close"),
            "day_change": rec.get("day_change"),
            "day_change_pct": rec.get("day_change_pct"),
            "pct_52w": rec.get("pct_52w"),
            "high_52w": rec.get("high_52w"),
            "low_52w": rec.get("low_52w"),
            "ret_7d": rec.get("ret_7d"),
            "ret_30d": rec.get("ret_30d"),
            "ret_90d": rec.get("ret_90d"),
            "ret_ytd": rec.get("ret_ytd"),
            "currency": rec.get("currency", "TWD"),
            "fundamentals": rec.get("fundamentals") or {},
            "yf_ticker": rec.get("yf_ticker", yf_t),
        }
        idx[sym] = entry
    _PRICES_CACHE = idx
    return idx


def _pf_lookup(pf: dict | None) -> dict[str, dict]:
    """Index every known ticker (holdings + watchlist + universe) by symbol.

    Falls back to prices.json for any ticker not in portfolio.json — this
    catches supply_chains.yaml + AI lead_stocks that fetch_prices.py has
    downloaded but that aren't part of the user's portfolio. Portfolio.json
    entries win (they have richer PnL/cost-basis fields), prices.json only
    fills gaps.
    """
    idx: dict[str, dict] = {}
    if pf:
        for coll in ("holdings", "watchlist", "simulator_universe"):
            for it in pf.get(coll, []) or []:
                if it.get("symbol"):
                    idx[it["symbol"]] = it
    # Layer prices.json underneath — only add symbols NOT already in pf
    price_idx = _load_prices_bysym()
    for sym, rec in price_idx.items():
        if sym not in idx:
            idx[sym] = rec
    return idx


def _fmt_fund_num(v: float | None, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _fmt_pct_fund(v: float | None, digits: int = 1) -> str:
    """Format a ratio (0.24) as percent (24.0%)."""
    if v is None:
        return "—"
    return f"{v * 100:.{digits}f}%"


def _pe_tone(pe: float | None) -> str:
    """Return CSS class for P/E colouring on supply-chain map cards.
    🟢 合理 = up · 🟡 偏高 = (no class, neutral) · 🟠 昂貴 = amber · 🔴 泡沫 / 虧損 = dn.
    Kept loose — Gemini gets sharper tier context; dashboard just needs eyeball signal."""
    if pe is None:
        return ""
    if pe < 0:
        return "dn"
    if pe < 20:
        return "up"
    if pe < 30:
        return ""
    if pe < 50:
        return "amber"
    return "dn"


def _growth_tone(g: float | None) -> str:
    """CSS class for growth (0.25 = +25%)."""
    if g is None:
        return ""
    pct = g * 100
    if pct < -5:
        return "dn"
    if pct < 5:
        return ""
    return "up"


# ---------- 籌碼 (chips) formatting helpers ----------
# TW convention: 紅=net buy (up), 綠=net sell (down). Values are in 股 (shares);
# we display in 張 (lots of 1000 shares) for readability.

def _fmt_lots(shares: int | None, digits: int = 0) -> str:
    """Convert shares → 張 (1000 shares). Returns '—' if None/0."""
    if shares is None or shares == 0:
        return "—"
    lots = shares / 1000.0
    if abs(lots) >= 10000:
        return f"{lots / 10000:+.{max(1, digits)}f}萬"
    return f"{lots:+,.{digits}f}"


def _chips_cell(shares: int | None, with_unit: bool = False) -> str:
    """Render a signed shares cell with TW color convention (紅/綠)."""
    if shares is None or shares == 0:
        return '<span class="muted mono tnum small">—</span>'
    cls = "up" if shares > 0 else "dn"
    label = _fmt_lots(shares)
    unit = '<span class="muted small"> 張</span>' if with_unit else ""
    return f'<span class="mono tnum {cls}">{label}</span>{unit}'


def _streak_badge(streak: int | None, label_buy: str = "連買", label_sell: str = "連賣") -> str:
    """Render a ±N streak as a badge. 0/None returns dash."""
    if not streak:
        return '<span class="muted small">—</span>'
    if streak > 0:
        return f'<span class="chip-streak chip-streak-buy mono small">{label_buy}{streak}日</span>'
    return f'<span class="chip-streak chip-streak-sell mono small">{label_sell}{abs(streak)}日</span>'


def _chips_mini_spark(daily: list[dict], key: str = "foreign", width: int = 80,
                      height: int = 24) -> str:
    """Tiny bar sparkline of last-5-day net-buy values (signed)."""
    if not daily:
        return ""
    vals = [d.get(key, 0) or 0 for d in daily][::-1]  # oldest → newest (left → right)
    if not any(vals):
        return ""
    n = len(vals)
    peak = max(abs(v) for v in vals) or 1
    bar_w = max(2, (width - (n - 1) * 2) / n)
    mid = height / 2
    bars = []
    for i, v in enumerate(vals):
        x = i * (bar_w + 2)
        h = abs(v) / peak * (mid - 1)
        if v >= 0:
            y = mid - h
            cls = "chip-spark-buy"
        else:
            y = mid
            cls = "chip-spark-sell"
        bars.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_w:.1f}" height="{max(1, h):.1f}" rx="0.5"/>'
        )
    # Zero line
    bars.append(f'<line class="chip-spark-mid" x1="0" y1="{mid}" x2="{width}" y2="{mid}"/>')
    return (
        f'<svg class="chip-spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(bars)}</svg>'
    )


def _match_chain_for_theme(opp: dict, chains: dict) -> str | None:
    """Map an AI opportunity's category_tag / theme / lead_stocks to a
    supply_chains.yaml slug. Returns slug or None if no match found.

    Matching strategy (in priority order):
      1. category_tag stripped of "#" appears in chain.tags
      2. theme title substring match in chain.title or tags
      3. any lead stock symbol appears in chain's stocks
    """
    if not chains:
        return None

    tag = (opp.get("category_tag") or "").lstrip("#").strip()
    theme = (opp.get("theme") or "").strip()
    theme_norm = _strip_leading_emoji(theme)
    leads = opp.get("lead_stocks") or []
    lead_syms = {(ls.get("symbol") or "").strip() for ls in leads if ls.get("symbol")}

    # Priority 1: exact tag match
    if tag:
        for slug, chain in chains.items():
            tags = chain.get("tags") or []
            for t in tags:
                if t == tag or t in tag or tag in t:
                    return slug

    # Priority 2: theme title keyword match
    if theme_norm:
        for slug, chain in chains.items():
            title = chain.get("title", "")
            if any(t for t in (chain.get("tags") or [])
                   if t and t in theme_norm):
                return slug
            # Also match slug keywords in title
            if theme_norm in title or title in theme_norm:
                return slug

    # Priority 3: lead-stock overlap
    if lead_syms:
        best_slug = None
        best_overlap = 0
        for slug, chain in chains.items():
            chain_syms = {
                s["symbol"] for layer in (chain.get("layers") or [])
                for s in (layer.get("stocks") or []) if s.get("symbol")
            }
            overlap = len(lead_syms & chain_syms)
            if overlap > best_overlap:
                best_overlap = overlap
                best_slug = slug
        if best_overlap >= 1:
            return best_slug

    return None


def render_supply_chain_map(slug: str, chains: dict, lookup: dict,
                            lead_syms: set[str]) -> str:
    """Render a supply_chains.yaml chain's layers + stocks as a vertical
    map on the theme page. Stocks already in AI lead_stocks get highlighted
    (so the user can see "this is what AI picked" vs "this is what's
    canonically in the chain"). Links to holdings/SYMBOL.html when available."""
    chain = chains.get(slug)
    if not chain:
        return ""

    title = chain.get("title", slug)
    narrative = chain.get("narrative", "").strip()
    tags = chain.get("tags") or []
    tag_pills = " ".join(
        f'<span class="sc-map-tag mono small">{html.escape(t)}</span>'
        for t in tags
    )

    # Tier badges — visual shortcut for market cap / upside tier
    # mega/large = 存款級（已被過度覆蓋，漲幅空間小）
    # mid        = 波段級（法人會進出、題材才動）
    # small      = 雪球試水級（NT$5,000 起跳有空間滾到 5x+）
    # hidden     = 真隱形冠軍（最大 upside、流動性最差、要看法人動向進場）
    TIER_META = {
        "mega":   ("MEGA",   "mega",   "存款級"),
        "large":  ("LARGE",  "large",  "核心"),
        "mid":    ("MID",    "mid",    "波段"),
        "small":  ("SMALL",  "small",  "雪球"),
        "hidden": ("HIDDEN", "hidden", "隱形冠軍"),
    }

    # Count tiers for the header summary
    tier_counts: dict[str, int] = {}
    layer_html: list[str] = []
    total_stocks = 0
    for i, layer in enumerate(chain.get("layers") or []):
        name = layer.get("name", "")
        role = layer.get("role", "")
        stocks = layer.get("stocks") or []
        stock_cards: list[str] = []
        for st in stocks:
            sym = (st.get("symbol") or "").strip()
            if not sym:
                continue
            total_stocks += 1
            nm = st.get("name", "")
            sub_role = st.get("role", "")
            pillar = st.get("pillar", "growth")
            tier = (st.get("tier") or "mid").lower()
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            tier_label, tier_cls, tier_tooltip = TIER_META.get(
                tier, ("?", "mid", ""))
            rec = lookup.get(sym) or {}
            price = rec.get("price")
            day_pct = rec.get("day_change_pct")
            pct_52w = rec.get("pct_52w")
            fund = rec.get("fundamentals") or {}
            pe_ttm = fund.get("pe_ttm")
            eps_ttm = fund.get("eps_ttm")
            roe = fund.get("roe")
            earn_g = fund.get("earnings_growth")
            rev_g = fund.get("rev_growth")
            is_lead = sym in lead_syms
            has_page = sym in _TICKER_ALIAS

            # Highlight if AI picked this stock as a lead, AND separately
            # highlight hidden-tier (snowball upside) with a different style
            card_cls = "sc-map-stock"
            if is_lead:
                card_cls += " sc-map-stock-lead"
            if tier in ("small", "hidden"):
                card_cls += " sc-map-stock-snowball"
            card_cls += f" sc-map-tier-{tier_cls}"

            # Valuation badge (on the right, next to tier) — quick eyeball:
            # is this cheap / expensive at a glance?
            val_badge = ""
            if pe_ttm is not None:
                pe_cls = _pe_tone(pe_ttm)
                if pe_ttm < 0:
                    val_label = "虧損"
                elif pe_ttm < 20:
                    val_label = "🟢 合理"
                elif pe_ttm < 30:
                    val_label = "🟡 偏高"
                elif pe_ttm < 50:
                    val_label = "🟠 昂貴"
                else:
                    val_label = "🔴 泡沫"
                val_badge = (
                    f'<span class="sc-map-val-badge mono small {pe_cls}" '
                    f'title="P/E TTM = {pe_ttm:.1f}">{val_label}</span>'
                )

            badges: list[str] = []
            if is_lead:
                badges.append('<span class="sc-map-lead-badge mono small">AI 選</span>')
            badges.append(
                f'<span class="sc-map-tier-badge sc-map-tier-badge-{tier_cls} mono small" '
                f'title="{html.escape(tier_tooltip)}">{html.escape(tier_label)}</span>'
            )
            if val_badge:
                badges.append(val_badge)
            badges_html = " ".join(badges)

            # Link to holding page if exists
            sym_html = (
                f'<a href="../holdings/{sym}.html" class="sc-map-sym-link">'
                f'<strong class="mono">{html.escape(sym)}</strong></a>'
                if has_page else
                f'<strong class="mono">{html.escape(sym)}</strong>'
            )

            # Fundamentals row (below role, above price) — compact 4-cell
            # summary so user can eyeball PE/EPS/ROE/成長 at a glance
            # without leaving the map. Dash '—' when yfinance has no data.
            fund_bits: list[str] = []
            if pe_ttm is not None:
                fund_bits.append(
                    f'<span class="sc-map-fund-cell" title="本益比 TTM（越低越便宜，> 50 泡沫）">'
                    f'<span class="muted">PE</span> '
                    f'<span class="mono tnum {_pe_tone(pe_ttm)}">{pe_ttm:.1f}</span>'
                    f'</span>'
                )
            if eps_ttm is not None:
                fund_bits.append(
                    f'<span class="sc-map-fund-cell" title="每股盈餘 TTM（過去 4 季加總）">'
                    f'<span class="muted">EPS</span> '
                    f'<span class="mono tnum">{eps_ttm:.2f}</span>'
                    f'</span>'
                )
            if roe is not None:
                fund_bits.append(
                    f'<span class="sc-map-fund-cell" title="股東權益報酬率（> 15% 是品質股）">'
                    f'<span class="muted">ROE</span> '
                    f'<span class="mono tnum {"up" if roe > 0.15 else ("dn" if roe < 0 else "")}">'
                    f'{roe * 100:.0f}%</span>'
                    f'</span>'
                )
            growth = earn_g if earn_g is not None else rev_g
            if growth is not None:
                glabel = "EPS成長" if earn_g is not None else "營收成長"
                fund_bits.append(
                    f'<span class="sc-map-fund-cell" title="年增率（YoY）">'
                    f'<span class="muted">{glabel}</span> '
                    f'<span class="mono tnum {_growth_tone(growth)}">{growth * 100:+.0f}%</span>'
                    f'</span>'
                )
            fund_html = (
                f'<div class="sc-map-fund">{" ".join(fund_bits)}</div>'
                if fund_bits else
                '<div class="sc-map-fund muted small">基本面資料待補</div>'
            )

            # Price line (if we have it)
            price_html = ""
            if price is not None:
                day_cls = _cls(day_pct)
                day_str = _fmt_pct(day_pct) if day_pct is not None else "—"
                p52_str = f'<span class="muted small">52w {pct_52w:.0f}%</span>' if pct_52w is not None else ""
                price_html = f'''
                <div class="sc-map-price">
                  <span class="mono tnum">{price:.2f}</span>
                  <span class="mono tnum small {day_cls}">{day_str}</span>
                  {p52_str}
                </div>'''

            pillar_cls = PILLAR_CLS.get(pillar, "")
            stock_cards.append(f'''
            <div class="{card_cls}" data-tier="{tier}">
              <div class="sc-map-stock-head">
                <span class="pillar-dot {pillar_cls}"></span>
                {sym_html}
                <span class="sc-map-name">{html.escape(nm)}</span>
                <span class="sc-map-badges">{badges_html}</span>
              </div>
              <div class="sc-map-stock-role muted small">{html.escape(sub_role)}</div>
              {fund_html}
              {price_html}
            </div>''')

        layer_html.append(f'''
        <div class="sc-map-layer">
          <div class="sc-map-layer-head">
            <span class="sc-map-layer-num mono">{i + 1}</span>
            <div class="sc-map-layer-title">
              <div class="sc-map-layer-name">{html.escape(name)}</div>
              {(f'<div class="sc-map-layer-role muted small">{html.escape(role)}</div>' if role else "")}
            </div>
          </div>
          <div class="sc-map-layer-stocks">
            {"".join(stock_cards)}
          </div>
        </div>''')

    # Header tier summary: "4 mega · 7 large · 5 mid · 6 small · 3 hidden"
    tier_summary_parts: list[str] = []
    for tier_key in ("mega", "large", "mid", "small", "hidden"):
        c = tier_counts.get(tier_key, 0)
        if c:
            lbl, cls, _ = TIER_META[tier_key]
            tier_summary_parts.append(
                f'<span class="sc-map-tier-count sc-map-tier-badge-{cls} mono small">{c} {lbl}</span>'
            )
    tier_summary = " ".join(tier_summary_parts)

    # Filter pills — lets user show only hidden/small-cap candidates (snowball lens)
    filter_bar = f'''
    <div class="sc-map-filter">
      <span class="mono small muted">篩選市值級別：</span>
      <button type="button" class="sc-map-filter-btn active" data-tier="all">全部</button>
      <button type="button" class="sc-map-filter-btn" data-tier="snowball">雪球級 (small + hidden)</button>
      <button type="button" class="sc-map-filter-btn" data-tier="hidden">只看隱形冠軍</button>
      <button type="button" class="sc-map-filter-btn" data-tier="mid">中型波段</button>
    </div>
    '''

    return f'''
    <section class="th-section sc-map-section">
      <div class="th-section-head mono">
        <span>SUPPLY CHAIN · 完整供應鏈地圖</span>
        <span class="muted small"> · {len(chain.get("layers") or [])} 層 · {total_stocks} 檔</span>
      </div>
      <div class="sc-map-intro">
        <div class="sc-map-title mono">{html.escape(title)}</div>
        <div class="sc-map-tags">{tag_pills}</div>
        {(f'<div class="sc-map-narrative">{html.escape(narrative)}</div>' if narrative else "")}
        <div class="sc-map-tier-bar">{tier_summary}</div>
        <div class="sc-map-legend muted small">
          <strong>TIER 說明：</strong>
          <span class="sc-map-tier-badge-mega mono small">MEGA</span>/<span class="sc-map-tier-badge-large mono small">LARGE</span> 是「存款柱」（2330、鴻海那種，覆蓋過多漲不動）·
          <span class="sc-map-tier-badge-mid mono small">MID</span> 是波段（題材來才動）·
          <span class="sc-map-tier-badge-small mono small">SMALL</span>/<span class="sc-map-tier-badge-hidden mono small">HIDDEN</span> 是<strong>雪球能滾大的位置</strong>（NT$5,000 試水能變 50,000+）。<br>
          <strong>估值標籤（PE TTM）：</strong>
          <span class="sc-map-val-badge up mono small">🟢 合理</span> &lt; 20 ·
          <span class="sc-map-val-badge mono small">🟡 偏高</span> 20-30 ·
          <span class="sc-map-val-badge amber mono small">🟠 昂貴</span> 30-50 ·
          <span class="sc-map-val-badge dn mono small">🔴 泡沫</span> &gt; 50（半導體業可放寬至 25/40/60）。<br>
          黃底 = AI 今天挑中的 lead · 綠框 = 雪球級（小型/隱形）· 每卡底下一排是 PE / EPS / ROE / 成長（看一眼就知便宜還貴）。
          來源：<code class="mono">supply_chains.yaml</code> + <code class="mono">prices.json</code>（yfinance 每日更新）
        </div>
      </div>
      {filter_bar}
      <div class="sc-map-layers">
        {"".join(layer_html)}
      </div>
    </section>
    <script>
    (function() {{
      const section = document.currentScript.previousElementSibling;
      const btns = section.querySelectorAll('.sc-map-filter-btn');
      const cards = section.querySelectorAll('.sc-map-stock');
      btns.forEach(btn => {{
        btn.addEventListener('click', () => {{
          btns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const mode = btn.dataset.tier;
          cards.forEach(c => {{
            const t = c.dataset.tier;
            let show = true;
            if (mode === 'snowball') show = (t === 'small' || t === 'hidden');
            else if (mode === 'hidden') show = (t === 'hidden');
            else if (mode === 'mid') show = (t === 'mid');
            // else 'all'
            c.style.display = show ? '' : 'none';
          }});
        }});
      }});
    }})();
    </script>
    '''


def render_theme_page(opp: dict, pf: dict, history: dict,
                      analysis: dict | None, slug: str) -> str:
    """Full deep-dive page for one AI-identified theme.
    Shows all lead stocks with price + fundamentals + 冷熱排行 (cold-to-hot).
    Adds a SUPPLY CHAIN MAP section when the theme matches a chain in
    supply_chains.yaml (canonical coverage, not just AI lead_stocks)."""
    history = history or {}
    init_ticker_alias(pf)
    lookup = _pf_lookup(pf)

    # Match theme → supply chain. Rendered as a full vertical map right
    # after the stock table, so users can see the entire ecosystem not
    # just AI's 5-7 picks.
    chains_yaml = load_supply_chains()
    chain_slug = _match_chain_for_theme(opp, chains_yaml)
    lead_syms = {
        (ls.get("symbol") or "").strip()
        for ls in (opp.get("lead_stocks") or [])
        if ls.get("symbol")
    }
    supply_chain_html = (
        render_supply_chain_map(chain_slug, chains_yaml, lookup, lead_syms)
        if chain_slug else ""
    )

    # --- Basic theme fields ---
    theme = _strip_leading_emoji(opp.get("theme") or "未命名題材")
    tag = opp.get("category_tag", "")
    stage = opp.get("stage", "—")
    stage_cls = _stage_cls(stage)
    conf = int(opp.get("confidence_pct") or 0)
    crowd = int(opp.get("crowding_pct") or 0)
    crowd_label = _strip_leading_emoji(opp.get("crowding_label", ""))
    crowd_tone = _crowding_tone(crowd)
    timeframe = opp.get("timeframe", "—")
    headline = opp.get("headline") or opp.get("thesis", "")
    why = opp.get("why") or opp.get("research_angle", "")
    warning = opp.get("ai_warning", "")
    signals = opp.get("signals") or []
    sources = opp.get("sources") or []
    leads = opp.get("lead_stocks") or []

    # --- Build ranked rows for each lead stock ---
    rows = []
    for ls in leads:
        sym = ls.get("symbol", "")
        name = ls.get("name", "")
        rec = lookup.get(sym, {})
        fund = rec.get("fundamentals") or {}
        chips = rec.get("chips") or {}
        row = {
            "symbol": sym,
            "name": name,
            "price": rec.get("price"),
            "day_pct": rec.get("day_change_pct"),
            "pct_52w": rec.get("pct_52w"),
            "ret_7d": rec.get("ret_7d"),
            "ret_30d": rec.get("ret_30d"),
            "ret_90d": rec.get("ret_90d"),
            "ret_ytd": rec.get("ret_ytd"),
            "pe": fund.get("pe_ttm"),
            "pe_fwd": fund.get("pe_forward"),
            "eps": fund.get("eps_ttm"),
            "roe": fund.get("roe"),
            "rev_growth": fund.get("rev_growth"),
            "sector": fund.get("sector") or "",
            "has_page": sym in _TICKER_ALIAS,
            "currency": rec.get("currency", "TWD"),
            "foreign_5d": chips.get("foreign_5d"),
            "foreign_20d": chips.get("foreign_20d"),
            "foreign_streak": chips.get("foreign_streak"),
            "trust_5d": chips.get("trust_5d"),
            "trust_streak": chips.get("trust_streak"),
            "has_chips": bool(chips),
        }
        rows.append(row)

    # Sort: ascending by pct_52w so the "coldest" (low position) is on top
    rows.sort(key=lambda r: r["pct_52w"] if r["pct_52w"] is not None else 999)

    # --- Temperature buckets ---
    def _bucket(r):
        p = r["pct_52w"]
        if p is None:
            return "unknown"
        if p < 30:
            return "cold"
        if p < 70:
            return "warm"
        return "hot"

    cold_rows = [r for r in rows if _bucket(r) == "cold"]
    hot_rows = [r for r in rows if _bucket(r) == "hot"]

    # --- Table HTML ---
    def _cell(v, digits=1, suffix=""):
        return _fmt_fund_num(v, digits, suffix) if v is not None else "<span class='muted'>—</span>"

    def _pct_cell(v, digits=2):
        if v is None:
            return "<span class='muted'>—</span>"
        return f'<span class="mono tnum {_cls(v)}">{v:+.{digits}f}%</span>'

    def _52w_cell(p):
        if p is None:
            return "<span class='muted'>—</span>"
        tone = "cold" if p < 30 else ("hot" if p >= 70 else "warm")
        return (
            f'<div class="th-52w-wrap"><div class="th-52w-bar th-{tone}" '
            f'style="width:{max(2, min(100, p)):.0f}%"></div>'
            f'<span class="th-52w-val mono tnum">{p:.0f}</span></div>'
        )

    def _link_cell(r):
        if r["has_page"]:
            return f'<a class="th-chain mono" href="../holdings/{r["symbol"]}.html">DEEP →</a>'
        return '<span class="muted small">—</span>'

    table_rows = []
    for i, r in enumerate(rows):
        temp = _bucket(r)
        temp_chip = {
            "cold": '<span class="th-temp th-cold mono">COLD</span>',
            "warm": '<span class="th-temp th-warm mono">WARM</span>',
            "hot":  '<span class="th-temp th-hot mono">HOT</span>',
            "unknown": '<span class="th-temp mono muted">—</span>',
        }[temp]
        price_str = f"{r['price']:.2f}" if r["price"] is not None else "—"
        f5 = _chips_cell(r["foreign_5d"])
        f20 = _chips_cell(r["foreign_20d"])
        fstreak = _streak_badge(r["foreign_streak"])
        table_rows.append(f'''
        <tr class="th-row th-{temp}">
          <td class="th-rank mono tnum">{i+1:02d}</td>
          <td>{temp_chip}</td>
          <td><div class="th-sym mono">{html.escape(r["symbol"])}</div>
              <div class="th-name muted small">{html.escape(r["name"])}</div></td>
          <td class="mono tnum">{price_str}</td>
          <td>{_pct_cell(r["day_pct"])}</td>
          <td>{_52w_cell(r["pct_52w"])}</td>
          <td>{_pct_cell(r["ret_7d"])}</td>
          <td>{_pct_cell(r["ret_30d"])}</td>
          <td>{_pct_cell(r["ret_90d"])}</td>
          <td>{_pct_cell(r["ret_ytd"])}</td>
          <td class="mono tnum">{_cell(r["pe"], 1)}</td>
          <td class="mono tnum">{_cell(r["eps"], 2)}</td>
          <td class="mono tnum">{_fmt_pct_fund(r["roe"], 1)}</td>
          <td class="th-chips-cell">{f5}</td>
          <td class="th-chips-cell">{f20}</td>
          <td class="th-chips-cell">{fstreak}</td>
          <td class="muted small">{html.escape(r["sector"] or "—")}</td>
          <td>{_link_cell(r)}</td>
        </tr>''')

    if not table_rows:
        table_html = '<div class="muted small" style="padding:20px">主題尚未列出具體個股。</div>'
    else:
        table_html = f'''
        <div class="th-table-wrap">
          <table class="th-table">
            <thead>
              <tr>
                <th class="mono small">#</th>
                <th class="mono small">溫度</th>
                <th class="mono small">STOCK</th>
                <th class="mono small">價格</th>
                <th class="mono small">今日</th>
                <th class="mono small">52W位階</th>
                <th class="mono small">7D</th>
                <th class="mono small">30D</th>
                <th class="mono small">90D</th>
                <th class="mono small">YTD</th>
                <th class="mono small">P/E</th>
                <th class="mono small">EPS</th>
                <th class="mono small">ROE</th>
                <th class="mono small" title="外資 5 日買賣超（張）">外資5日</th>
                <th class="mono small" title="外資 20 日買賣超（張）">外資20日</th>
                <th class="mono small" title="外資連續買/賣天數">外資連續</th>
                <th class="mono small">產業</th>
                <th class="mono small"></th>
              </tr>
            </thead>
            <tbody>{"".join(table_rows)}</tbody>
          </table>
        </div>
        <div class="th-chips-legend muted small">
          外資欄位：正值（<span class="up">紅</span>）= 買超、負值（<span class="dn">綠</span>）= 賣超。數字單位為「張」（1 張 = 1,000 股）。資料來自 TWSE + TPEx 三大法人統計，滯後 1 個交易日。
        </div>'''

    # --- Temperature callouts ---
    callouts = []
    if cold_rows:
        cold_bits = [f'<strong>{html.escape(r["symbol"])}</strong> {html.escape(r["name"])}' for r in cold_rows[:3]]
        callouts.append(
            '<div class="th-callout th-cold-box"><div class="th-callout-tag mono">COLD · 還沒動</div>'
            f'<div class="th-callout-body">題材內位階低於 30%，漲幅相對落後。若基本面確認 OK（看 P/E、EPS 成長、ROE），可列入研究候選：'
            f'{"、".join(cold_bits)}</div>'
            '<div class="th-callout-foot muted small">提醒：「還沒漲」不等於「會漲」，可能反映基本面差、產業鏈位置不佳，或市場暫時不關注。進場前看個股深度頁與 AI 評分。</div></div>'
        )
    if hot_rows:
        hot_bits = [f'<strong>{html.escape(r["symbol"])}</strong> {html.escape(r["name"])}' for r in hot_rows[:3]]
        callouts.append(
            '<div class="th-callout th-hot-box"><div class="th-callout-tag mono">HOT · 已漲多</div>'
            f'<div class="th-callout-body">位階 70% 以上，短線追高風險高：{"、".join(hot_bits)}</div>'
            '<div class="th-callout-foot muted small">策略：等拉回 5-10% 再分批進場，或觀察 20 日均線是否守住。不要 FOMO。</div></div>'
        )
    callouts_html = f'<div class="th-callouts">{"".join(callouts)}</div>' if callouts else ""

    # --- Signals + sources + warning ---
    sig_chips_html = ""
    if signals:
        sig_chips = "".join(f'<span class="sig-chip">{html.escape(s)}</span>' for s in signals)
        sig_chips_html = f'<div class="th-section"><div class="th-section-head mono">SIGNALS · 訊號</div><div class="sig-row">{sig_chips}</div></div>'

    sources_html = ""
    if sources:
        src_bits = []
        for s in sources:
            if isinstance(s, dict):
                url = s.get("url", "#")
                title = s.get("title", s.get("source", url))
                src_bits.append(f'<li class="small"><a class="src-link" href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(title)}</a></li>')
            else:
                # Plain string (e.g. "經濟日報", "Yahoo股市 TW")
                src_bits.append(f'<li class="small muted">{html.escape(str(s))}</li>')
        sources_html = f'<div class="th-section"><div class="th-section-head mono">SOURCES · 資料來源</div><ul class="src-list">{"".join(src_bits)}</ul></div>'

    warn_html = ""
    if warning:
        warn_html = (
            f'<div class="th-warn"><span class="th-warn-tag mono">WARN</span> '
            f'{html.escape(warning)}</div>'
        )

    # --- Head-to-head verdict card (Tetsu Chang style, 2026-04-19) ---
    # When Gemini provides head_to_head, render as a standout card: pick vs skip
    # with PE / EPS / ROE / 52週位階 / 法人 numbers pulled from prices+chips.
    # Falls back to a heuristic synthesizer when Gemini hasn't supplied h2h:
    # compare lowest-PE + positive EPS candidate vs highest-PE or EPS-loss candidate.
    h2h_html = ""
    h2h = opp.get("head_to_head") or {}
    h2h_synth = False
    if not (h2h and h2h.get("pick_symbol") and h2h.get("skip_symbol")) and len(leads) >= 2:
        # Build candidate list with metrics
        cand = []
        for ls in leads:
            sym = ls.get("symbol", "")
            rec = lookup.get(sym) or {}
            fund = rec.get("fundamentals") or {}
            pe = fund.get("pe_ttm")
            eps = fund.get("eps_ttm")
            p52 = rec.get("pct_52w")
            if pe is None and eps is None:
                continue
            cand.append({
                "sym": sym, "name": ls.get("name", ""),
                "pe": pe, "eps": eps, "p52": p52,
                "r30": rec.get("ret_30d"),
            })
        if len(cand) >= 2:
            # Pick: best combo of positive EPS + lowest PE (if any), else lowest PE
            eligible_pick = [c for c in cand if (c["eps"] is not None and c["eps"] > 0)
                             and (c["pe"] is None or (0 < c["pe"] <= 35))]
            if eligible_pick:
                pick = min(eligible_pick,
                           key=lambda c: (c["pe"] if c["pe"] is not None else 999, -(c["eps"] or 0)))
            else:
                pick = min(cand, key=lambda c: (c["pe"] or 999))
            # Skip: prefer negative EPS, else highest PE > pick's PE
            eps_loss = [c for c in cand if c["eps"] is not None and c["eps"] < 0 and c["sym"] != pick["sym"]]
            if eps_loss:
                skip = min(eps_loss, key=lambda c: c["eps"] or 0)  # most negative EPS
            else:
                others = [c for c in cand if c["sym"] != pick["sym"] and c["pe"] is not None]
                if others:
                    skip = max(others, key=lambda c: c["pe"] or 0)
                    # Only use if the PE gap is material (2x or more)
                    if pick["pe"] and skip["pe"] and skip["pe"] < pick["pe"] * 1.8:
                        skip = None
                else:
                    skip = None
            if skip and pick["sym"] != skip["sym"]:
                h2h_synth = True
                def _fmt_n(v, digits=1, unit=""):
                    return f"{v:.{digits}f}{unit}" if v is not None else "—"
                def _pe_verdict(pe: float | None) -> str:
                    """Short Chinese label for PE level."""
                    if pe is None: return ""
                    if pe <= 0: return "虧損"
                    if pe <= 20: return "合理"
                    if pe <= 30: return "偏高"
                    if pe <= 50: return "昂貴"
                    if pe <= 100: return "泡沫"
                    return "極度泡沫"
                pick_eps_s = _fmt_n(pick["eps"], 2)
                skip_eps_s = _fmt_n(skip["eps"], 2)
                pick_pe_s = _fmt_n(pick["pe"], 1)
                skip_pe_s = _fmt_n(skip["pe"], 1)
                pick_pe_lbl = _pe_verdict(pick["pe"])
                skip_pe_lbl = _pe_verdict(skip["pe"])
                pick_bubbly = (pick["pe"] is not None and pick["pe"] > 50)
                # Verdict sentence — style depends on relative/absolute valuation health
                if skip["eps"] is not None and skip["eps"] < 0:
                    # Pick has positive EPS, skip is EPS-loss — clearest-cut case
                    verdict = (f"同題材下，我挑 {pick['sym']} {pick['name']}（PE {pick_pe_s}、EPS {pick_eps_s} 獲利），"
                               f"而非 {skip['sym']} {skip['name']}（EPS {skip_eps_s} 虧損）——EPS 為負代表本業還沒穩定，"
                               f"就算題材熱，拿雪球試水先挑能賺錢的。")
                    skip_reason = (f"EPS {skip_eps_s} 本業仍虧損；題材發酵時容易被拉上去，"
                                   f"但沒有基本面支撐的漲勢回檔也快。")
                    pick_reason = (f"PE {pick_pe_s}（{pick_pe_lbl}）、EPS {pick_eps_s} 本業賺錢，"
                                   f"雪球試水的防守底在這裡。")
                elif pick_bubbly:
                    # Both are expensive — "least-bad" framing, NOT "this one is cheap"
                    verdict = (f"同題材下兩檔估值都偏貴（{pick['sym']} PE {pick_pe_s}、{skip['sym']} PE {skip_pe_s}），"
                               f"若一定要挑，我選 {pick['sym']} {pick['name']}——差距太大時，"
                               f"{skip['sym']} 已把未來 N 年的成長都 price in。但切記整個族群都在「泡沫區」，"
                               f"進場只能用小倉位試水、嚴守停損。")
                    skip_reason = (f"PE {skip_pe_s}（{skip_pe_lbl}）已超過合理估值 5 倍以上，"
                                   f"除非 EPS 爆增 3-5 倍否則難撐；追高買進風險極高。")
                    pick_reason = (f"PE {pick_pe_s}（{pick_pe_lbl}）雖仍不便宜，但 EPS {pick_eps_s} "
                                   f"至少是獲利狀態，題材若持續還有補漲空間——但切記「least-bad，不是 cheap」。")
                else:
                    # Pick is genuinely reasonable (<=50), skip is bubbly
                    verdict = (f"同題材下，我挑 {pick['sym']} {pick['name']}（PE {pick_pe_s}，{pick_pe_lbl}）"
                               f"勝 {skip['sym']} {skip['name']}（PE {skip_pe_s}，{skip_pe_lbl}）——"
                               f"估值差距太大，{skip['sym']} 已把未來幾年的成長都 price in 了。")
                    skip_reason = (f"PE {skip_pe_s}（{skip_pe_lbl}）估值嚴重透支；"
                                   f"追高買進等於讓前面進的人下車。")
                    pick_reason = (f"PE {pick_pe_s}（{pick_pe_lbl}）、EPS {pick_eps_s}，"
                                   f"估值還算合理，若題材持續發酵還有補漲空間。")
                if pick.get("r30") is not None:
                    pick_reason += f" 近 30 日 {pick['r30']:+.1f}%。"
                if skip.get("p52") and skip["p52"] >= 80:
                    skip_reason += f" 52週位階 {skip['p52']:.0f}%，已接近頂部。"
                h2h = {
                    "pick_symbol": pick["sym"], "pick_name": pick["name"],
                    "skip_symbol": skip["sym"], "skip_name": skip["name"],
                    "verdict": verdict,
                    "pick_rationale": pick_reason,
                    "skip_rationale": skip_reason,
                }
    if h2h and h2h.get("pick_symbol") and h2h.get("skip_symbol"):
        def _h2h_stats(sym: str) -> str:
            rec = lookup.get(sym) or {}
            fund = rec.get("fundamentals") or {}
            chips = rec.get("chips") or {}
            bits = []
            pe = fund.get("pe_ttm")
            if pe is not None:
                pe_cls = "up" if 0 < pe <= 20 else ("amber" if pe <= 30 else "dn")
                bits.append(f'<span class="h2h-chip {pe_cls}">PE {pe:.1f}</span>')
            eps = fund.get("eps_ttm")
            if eps is not None:
                eps_cls = "up" if eps > 0 else "dn"
                bits.append(f'<span class="h2h-chip {eps_cls}">EPS {eps:+.2f}</span>')
            roe = fund.get("roe")
            if roe is not None:
                roe_pct = roe * 100
                roe_cls = "up" if roe_pct >= 15 else ("amber" if roe_pct >= 8 else "muted")
                bits.append(f'<span class="h2h-chip {roe_cls}">ROE {roe_pct:.0f}%</span>')
            p52 = rec.get("pct_52w")
            if p52 is not None:
                p52_cls = "up" if p52 < 40 else ("amber" if p52 < 70 else "dn")
                bits.append(f'<span class="h2h-chip {p52_cls}">52W位 {p52:.0f}</span>')
            r30 = rec.get("ret_30d")
            if r30 is not None:
                bits.append(f'<span class="h2h-chip mono {_cls(r30)}">30D {r30:+.1f}%</span>')
            f5 = chips.get("foreign_5d")
            if f5 is not None:
                f5_cls = "up" if f5 > 0 else "dn"
                bits.append(f'<span class="h2h-chip {f5_cls}">外資5D {f5:+,.0f}</span>')
            return '<div class="h2h-stats">' + "".join(bits) + '</div>' if bits else ""

        pick_sym = html.escape(h2h.get("pick_symbol", ""))
        pick_name = html.escape(h2h.get("pick_name", ""))
        skip_sym = html.escape(h2h.get("skip_symbol", ""))
        skip_name = html.escape(h2h.get("skip_name", ""))
        verdict = html.escape(h2h.get("verdict", ""))
        pick_why = html.escape(h2h.get("pick_rationale", ""))
        skip_why = html.escape(h2h.get("skip_rationale", ""))
        synth_badge = (
            '<span class="h2h-synth-badge mono small muted">系統根據 PE / EPS 自動挑選；AI 版本上線後取代</span>'
            if h2h_synth else
            '<span class="h2h-ai-badge mono small">AI 判定</span>'
        )
        h2h_html = f'''
  <section class="th-h2h">
    <div class="th-h2h-head">
      <div class="th-section-head mono">HEAD-TO-HEAD · 對比同族群的兩檔</div>
      {synth_badge}
    </div>
    <div class="h2h-verdict">{_link_tickers(verdict)}</div>
    <div class="h2h-grid">
      <div class="h2h-card h2h-pick">
        <div class="h2h-card-tag mono">PICK · 選這檔</div>
        <h3 class="h2h-card-sym"><strong>{pick_sym}</strong> <span class="muted">{pick_name}</span></h3>
        {_h2h_stats(h2h.get("pick_symbol", ""))}
        <p class="h2h-card-why small">{pick_why}</p>
        <a class="h2h-deeplink small mono" href="../holdings/{pick_sym}.html">深度頁 →</a>
      </div>
      <div class="h2h-vs mono">VS</div>
      <div class="h2h-card h2h-skip">
        <div class="h2h-card-tag mono">SKIP · 不選這檔</div>
        <h3 class="h2h-card-sym"><strong>{skip_sym}</strong> <span class="muted">{skip_name}</span></h3>
        {_h2h_stats(h2h.get("skip_symbol", ""))}
        <p class="h2h-card-why small">{skip_why}</p>
        <a class="h2h-deeplink small mono" href="../holdings/{skip_sym}.html">深度頁 →</a>
      </div>
    </div>
  </section>
'''

    # --- Assemble ---
    title = f"{theme} · THEME DEEP-DIVE"
    body = f'''
<div class="wrap th-page">
  <a class="th-back" href="../index.html#radar">← 回 Radar</a>

  <header class="th-hero">
    <div class="th-tag-row">
      <span class="th-tag mono">{html.escape(tag)}</span>
      <span class="stage-chip {stage_cls} mono">{html.escape(stage)}</span>
      <span class="th-timeframe mono muted small">{html.escape(timeframe)}</span>
    </div>
    <h1 class="th-title">{html.escape(theme)}</h1>
    <div class="th-headline">{_link_tickers(headline)}</div>
  </header>

  <section class="th-stats">
    <div class="th-stat">
      <div class="th-stat-lbl mono small muted">CONFIDENCE</div>
      <div class="th-stat-val mono tnum">{conf}<span class="small muted"> /100</span></div>
    </div>
    <div class="th-stat">
      <div class="th-stat-lbl mono small muted">CROWDING</div>
      <div class="th-crowd-row">
        <div class="th-crowd-bar"><div class="th-crowd-fill {crowd_tone}" style="width:{crowd}%"></div></div>
        <div class="th-stat-val mono tnum">{crowd}</div>
      </div>
      <div class="th-crowd-label {crowd_tone} small">{html.escape(crowd_label)}</div>
    </div>
    <div class="th-stat">
      <div class="th-stat-lbl mono small muted">LEAD STOCKS</div>
      <div class="th-stat-val mono tnum">{len(leads)}</div>
    </div>
  </section>

  <section class="th-why">
    <div class="th-section-head mono">AI 分析 · WHY</div>
    <div class="th-why-body">{_link_tickers(why)}</div>
  </section>

  {warn_html}

  {h2h_html}

  {sig_chips_html}

  <section class="th-stocks">
    <div class="th-section-head mono">題材內股票 · 冷熱排行（位階低在上）</div>
    {table_html}
  </section>

  {supply_chain_html}

  {callouts_html}

  {sources_html}

  <div class="th-foot">
    <a class="th-back" href="../index.html#radar">← 回 Radar</a>
    <span class="muted small">Theme slug: <code>{html.escape(slug)}</code></span>
  </div>
</div>
'''
    return (
        PAGE_HEAD.format(title=html.escape(title), css_href="../styles.css")
        + body
        + PAGE_FOOT.format(now=datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"))
    )


def render_radar_tab(analysis: dict | None, pf: dict | None,
                     history: dict | None = None) -> str:
    """GUSHI-style Opportunity Radar with filter pills + sort, CROWD bars,
    per-theme sparkline, lead-stocks chips with %change, sources and CTA."""
    history = history or {}
    if not analysis:
        return '<div class="radar-empty"><p class="muted">AI 分析尚未生成。下次排程後會看到機會雷達。</p></div>'

    opps = analysis.get("opportunities", [])
    if not opps:
        return '<div class="radar-empty"><p class="muted">今日 AI 未挑出新機會（市場條件可能不合適）。</p></div>'

    # Lookup current day change per symbol (from universe + holdings)
    price_lookup: dict[str, dict] = {}
    for coll in ("holdings", "watchlist", "simulator_universe"):
        for it in pf.get(coll, []) or []:
            if it.get("symbol"):
                price_lookup[it["symbol"]] = it

    cards: list[str] = []
    for idx, o in enumerate(opps):
        theme = o.get("theme") or o.get("symbol", "未命名題材")
        tag = o.get("category_tag") or f"#{theme.split()[0]}"
        stage = o.get("stage", "—")
        conf = int(o.get("confidence_pct") or 0)
        crowd = int(o.get("crowding_pct") or 0)
        crowd_label = _strip_leading_emoji(o.get("crowding_label", ""))
        headline = o.get("headline") or o.get("thesis", "")
        why = o.get("why") or o.get("research_angle", "")
        timeframe = o.get("timeframe", "—")
        lead_stocks = o.get("lead_stocks") or []
        # Legacy fallback: if using old symbol/name, convert to lead_stocks
        if not lead_stocks and o.get("symbol"):
            lead_stocks = [{"symbol": o["symbol"], "name": o.get("name", "")}]
        sources = o.get("sources") or []
        signals = o.get("signals") or []
        warning = o.get("ai_warning", "")

        # Lead stocks chips
        chips = []
        for ls in lead_stocks[:5]:
            sym = ls.get("symbol", "")
            name = ls.get("name", "")
            day_pct = 0.0
            if sym in price_lookup:
                day_pct = price_lookup[sym].get("day_change_pct") or 0
            href = f"holdings/{sym}.html" if sym in _TICKER_ALIAS else "#"
            chips.append(f'''
              <a class="lead-chip" href="{href}">
                <span class="lead-sym mono">{html.escape(sym)}</span>
                <span class="lead-name muted small">{html.escape(name)}</span>
                <span class="lead-chg mono {_cls(day_pct)}">{_fmt_pct(day_pct, 2)}</span>
              </a>''')
        chips_html = "".join(chips) if chips else ""

        # Theme sparkline (SVG)
        spark_data = _theme_sparkline_from_leads(lead_stocks, history, days=14)
        spark_svg = ""
        if len(spark_data) >= 2:
            w, h = 640, 60
            mn, mx = min(spark_data), max(spark_data)
            rng = mx - mn if mx != mn else 1

            def _sx(i):
                return 4 + (w - 8) * i / (len(spark_data) - 1)

            def _sy(v):
                return h - 4 - (h - 8) * (v - mn) / rng

            pts = " ".join(f"{_sx(i):.1f},{_sy(v):.1f}" for i, v in enumerate(spark_data))
            area = f"4,{h-4} {pts} {w-4},{h-4}"
            direction = "up" if spark_data[-1] >= spark_data[0] else "dn"
            stroke = "var(--up)" if direction == "up" else "var(--dn)"
            spark_svg = f'''
              <svg class="radar-spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" width="100%" height="60">
                <polygon points="{area}" fill="{stroke}" opacity="0.1"/>
                <polyline points="{pts}" stroke="{stroke}" stroke-width="1.8" fill="none"
                          stroke-linejoin="round" stroke-linecap="round"/>
              </svg>'''

        crowd_tone = _crowding_tone(crowd)
        stage_cls = _stage_cls(stage)
        warn_html = f'<div class="radar-warn small"><span class="radar-warn-tag mono">WARN</span> {html.escape(warning)}</div>' if warning else ""
        signals_html = ""
        if signals:
            sig_chips = "".join(f'<span class="sig-chip">{html.escape(s)}</span>' for s in signals[:5])
            signals_html = f'<div class="sig-row">{sig_chips}</div>'

        # Card — chain link goes to full theme deep-dive (all related stocks + fundamentals)
        chain_href = f"themes/{_theme_slug(o, idx)}.html"

        # Data attributes for client-side filter/sort
        data_attrs = (
            f'data-stage="{stage}" data-crowd="{crowd}" data-conf="{conf}" '
            f'data-theme="{html.escape(theme)}" data-idx="{idx}"'
        )

        cards.append(f'''
        <article class="radar-card" {data_attrs}>
          <div class="radar-card-top">
            <div class="radar-top-left">
              <span class="radar-tag mono">{html.escape(tag)}</span>
              <span class="stage-chip {stage_cls}">{html.escape(stage)}</span>
            </div>
            <div class="radar-conf">
              <span class="conf-lbl mono small muted">CONF</span>
              <span class="conf-val mono tnum">{conf}</span>
            </div>
          </div>
          <h3 class="radar-headline">{_link_tickers(headline)}</h3>
          <div class="crowd-row">
            <span class="crowd-lbl mono small muted">CROWD</span>
            <div class="crowd-bar"><div class="crowd-fill {crowd_tone}" style="width:{crowd}%"></div></div>
            <span class="crowd-val mono tnum">{crowd}</span>
            <span class="crowd-label {crowd_tone} small">{html.escape(crowd_label)}</span>
          </div>
          {spark_svg}
          <div class="leads-row">{chips_html}</div>
          {signals_html}
          <div class="radar-why small">
            <span class="why-lbl mono muted">WHY · </span>{_link_tickers(why)}
          </div>
          {warn_html}
          <div class="radar-card-foot small muted mono">
            <span>{len(sources)} SOURCES</span>
            <span class="sb-sep">·</span>
            <span>{html.escape(timeframe)}</span>
            <a class="radar-chain-link" href="{chain_href}">VIEW CHAIN →</a>
          </div>
        </article>''')

    # Filter + sort controls (client-side)
    controls = '''
<div class="radar-controls">
  <div class="radar-filter-group">
    <span class="rc-lbl mono small muted">FILTER</span>
    <button class="rc-btn active" data-filter="all">全部</button>
    <button class="rc-btn" data-filter="low">低擁擠</button>
    <button class="rc-btn" data-filter="mid">中段</button>
    <button class="rc-btn" data-filter="hot">過熱</button>
  </div>
  <div class="radar-sort-group">
    <span class="rc-lbl mono small muted">SORT</span>
    <button class="rc-btn active" data-sort="conf">AI 信心</button>
    <button class="rc-btn" data-sort="cold">冷門優先</button>
    <button class="rc-btn" data-sort="stage">題材階段</button>
  </div>
</div>
'''

    # Topics mini section
    topics = analysis.get("topics", [])
    topics_mini = []
    for t in topics[:6]:
        ticks = "".join(
            (f'<a href="holdings/{_TICKER_ALIAS[tk]}.html" class="chip chip-muted small">{html.escape(tk)}</a>'
             if tk in _TICKER_ALIAS else
             f'<span class="chip chip-muted small">{html.escape(tk)}</span>')
            for tk in t.get("tickers", [])[:5]
        )
        topics_mini.append(f'''
        <div class="radar-topic">
          <div class="radar-topic-head">
            <strong>{html.escape(t.get("title", ""))}</strong>
            {_sentiment_badge(t.get("sentiment", "中性"))}
          </div>
          <div class="topic-tickers">{ticks}</div>
          <p class="narrative small">{_link_tickers(t.get("narrative", ""))[:240]}…</p>
        </div>''')

    topics_block = ""
    if topics_mini:
        date = (analysis.get("date") or "")
        topics_block = f'''
<div class="radar-topics">
  <h3 class="radar-subtitle">TODAY · 今日主題 — {len(topics)} 個族群</h3>
  <div class="radar-topics-grid">{"".join(topics_mini)}</div>
  <div class="tab-footer">
    <a href="briefs/{date}.html" class="btn-link small">→ 看完整主題分析 + 原始新聞</a>
  </div>
</div>
'''

    return f'''
<div class="radar-body">
  <div class="radar-intro">
    <h2 class="radar-title mono">OPPORTUNITY RADAR · 機會雷達</h2>
    <p class="muted small">AI 橫掃全市場找出「你可能錯過」的題材 · {len(opps)} 個機會 · {len(topics)} 個主題</p>
  </div>
  {controls}
  <div class="radar-grid" id="radar-grid">{"".join(cards)}</div>
  {topics_block}
</div>
<script>
(function() {{
  const grid = document.getElementById('radar-grid');
  if (!grid) return;
  let state = {{ filter: 'all', sort: 'conf' }};
  function apply() {{
    const cards = Array.from(grid.querySelectorAll('.radar-card'));
    // Filter
    cards.forEach(c => {{
      const crowd = parseInt(c.dataset.crowd) || 0;
      const stage = c.dataset.stage;
      let show = true;
      if (state.filter === 'low') show = crowd <= 40;
      else if (state.filter === 'mid') show = crowd > 40 && crowd <= 70;
      else if (state.filter === 'hot') show = crowd > 70;
      c.style.display = show ? '' : 'none';
    }});
    // Sort
    const visible = cards.filter(c => c.style.display !== 'none');
    visible.sort((a, b) => {{
      const aC = parseInt(a.dataset.conf) || 0;
      const bC = parseInt(b.dataset.conf) || 0;
      const aCr = parseInt(a.dataset.crowd) || 0;
      const bCr = parseInt(b.dataset.crowd) || 0;
      const stageOrder = {{ '萌芽': 0, '早期': 1, '中段': 2, '過熱': 3 }};
      if (state.sort === 'conf') return bC - aC;
      if (state.sort === 'cold') return aCr - bCr;
      if (state.sort === 'stage') return (stageOrder[a.dataset.stage] || 0) - (stageOrder[b.dataset.stage] || 0);
      return 0;
    }});
    visible.forEach(c => grid.appendChild(c));
  }}
  document.querySelectorAll('.radar-filter-group .rc-btn').forEach(b => {{
    b.addEventListener('click', () => {{
      document.querySelectorAll('.radar-filter-group .rc-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      state.filter = b.dataset.filter;
      apply();
    }});
  }});
  document.querySelectorAll('.radar-sort-group .rc-btn').forEach(b => {{
    b.addEventListener('click', () => {{
      document.querySelectorAll('.radar-sort-group .rc-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      state.sort = b.dataset.sort;
      apply();
    }});
  }});
  apply();
}})();
</script>
'''


def render_market_mood(pf: dict, analysis: dict | None) -> str:
    """Fear & Greed donut + VIX + 4 indices mini-grid (GUSHI MarketMoodMini)."""
    if not pf:
        return ""
    mp = (analysis or {}).get("market_pulse", {}) if analysis else {}
    fg = mp.get("fear_greed_score")
    fg_label = mp.get("fear_greed_label", "")
    macro = pf.get("macro", {})

    # Donut stroke-based on value position
    pct = max(0, min(100, fg)) if fg is not None else 50
    fg_color = (
        "var(--dn)" if pct < 30 else
        "var(--amber)" if pct < 45 else
        "var(--accent)" if pct < 55 else
        "var(--amber)" if pct < 75 else
        "var(--up)"
    )
    r, stroke = 26, 6
    circ = 2 * 3.14159 * r

    donut = f'''
    <div class="mood-donut">
      <svg width="64" height="64" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r="{r}" fill="none" stroke="var(--bg-3)" stroke-width="{stroke}"/>
        <circle cx="32" cy="32" r="{r}" fill="none" stroke="{fg_color}" stroke-width="{stroke}"
                stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{circ * (1 - pct/100):.2f}"
                transform="rotate(-90 32 32)"/>
        <text x="32" y="37" text-anchor="middle" font-family="var(--font-mono)" font-size="18" font-weight="700" fill="var(--tx-1)">{pct}</text>
      </svg>
    </div>''' if fg is not None else '<div class="mood-donut muted small">—</div>'

    def _cell(label, data, fmt="{:.1f}"):
        close = data.get("close")
        if close is None:
            return ""
        day = data.get("day_change_pct") or 0
        return f'''
        <div class="mood-mini-cell">
          <div class="mood-mini-lbl mono">{label}</div>
          <div class="mono tnum mood-mini-val">{fmt.format(close)}</div>
          <div class="mono tnum small {_cls(day)}">{_fmt_pct(day, 2)}</div>
        </div>'''

    cells = [
        _cell("加權", macro.get("twii", {}), "{:.0f}"),
        _cell("S&P", macro.get("spx", {}), "{:.0f}"),
        _cell("VIX", macro.get("vix", {}), "{:.2f}"),
        _cell("TWD", macro.get("usdtwd", {}), "{:.3f}"),
    ]

    return f'''
<div class="mood-panel">
  <div class="mood-head">
    <div>
      <div class="mood-title">MARKET MOOD</div>
      <div class="mood-sub muted small">Fear & Greed · VIX · 指數</div>
    </div>
  </div>
  <div class="mood-body">
    {donut}
    <div class="mood-score">
      <div class="muted small mono">FEAR & GREED</div>
      <div class="mood-score-label">{html.escape(fg_label) if fg_label else ("—" if fg is None else str(pct))}</div>
      <div class="muted small mono">分數 {pct if fg is not None else "—"}/100</div>
    </div>
  </div>
  <div class="mood-grid">{"".join(cells)}</div>
</div>
'''


def render_catalyst_timeline(analysis: dict | None) -> str:
    """Upcoming events timeline from morning_brief.agenda."""
    if not analysis:
        return ""
    mb = analysis.get("morning_brief", {})
    agenda = mb.get("agenda", [])
    if not agenda:
        return ""
    kind_icon = {"earnings": "📊", "macro": "🌏", "event": "📌"}
    kind_cls = {"earnings": "kind-earn", "macro": "kind-macro", "event": "kind-event"}
    items = []
    for a in agenda[:6]:
        k = a.get("kind", "event")
        items.append(f'''
        <div class="cat-item {kind_cls.get(k, "")}">
          <div class="cat-icon">{kind_icon.get(k, "📌")}</div>
          <div class="cat-body">
            <div class="cat-when mono small">{html.escape(a.get("when", ""))}</div>
            <div class="cat-label">{html.escape(a.get("label", ""))}</div>
          </div>
        </div>''')
    return f'''
<div class="catalyst-panel">
  <div class="cat-head">
    <span class="cat-title mono">CATALYSTS · TODAY & UPCOMING</span>
    <span class="muted small">{len(agenda)} 個事件</span>
  </div>
  <div class="cat-list">{"".join(items)}</div>
</div>
'''


def render_validation_banner(report: dict | None) -> str:
    """Render the validation banner above the AI hero.

    Narrative format: 結論 → 每一筆的「影響 + 下一步」。User feedback was that
    the old banner just threw flags and told them to "人工確認" — which defeats
    the point of automated QA. Now each issue has been resolved by
    validate_analysis.resolve_issue() into headline + impact + next_step, and
    any issue that couldn't be resolved into action has suppress=True and is
    filtered out here.

    Shows only if the resolved report has at least one actionable issue.
    """
    if not report:
        return ""
    issues = report.get("issues") or []
    # Filter: only issues with at least a headline AND not suppressed.
    # The resolver in validate_analysis.py fills these fields.
    sev_order = {"error": 0, "warning": 1, "info": 2}
    visible = sorted(
        (i for i in issues
         if not i.get("suppress")
         and i.get("severity") in ("error", "warning")
         and i.get("headline")),
        key=lambda i: sev_order.get(i.get("severity"), 9),
    )
    if not visible:
        return ""

    errors = sum(1 for i in visible if i.get("severity") == "error")
    warnings = sum(1 for i in visible if i.get("severity") == "warning")
    ana_date = report.get("analysis_date") or ""

    # Per-issue narrative cards
    items_html = []
    for i in visible:
        sev = i.get("severity")
        sev_cls = "vb-sev-error" if sev == "error" else "vb-sev-warn"
        sev_label = "需要注意" if sev == "error" else "建議確認"
        headline = i.get("headline") or ""
        impact = i.get("impact") or ""
        next_step = i.get("next_step") or ""
        # next_step may contain \n for multiple action lines — split into <li>
        next_lines = [ln.strip() for ln in next_step.split("\n") if ln.strip()]
        next_html = ""
        if len(next_lines) > 1:
            lis = "".join(f"<li>{html.escape(ln)}</li>" for ln in next_lines)
            next_html = f"<ul class='vb-next-list'>{lis}</ul>"
        elif next_lines:
            next_html = f"<p class='vb-next-text'>{html.escape(next_lines[0])}</p>"

        items_html.append(f'''
        <article class="vb-issue">
          <header class="vb-issue-head">
            <span class="vb-sev-chip {sev_cls}">{sev_label}</span>
            <h4 class="vb-issue-title">{html.escape(headline)}</h4>
          </header>
          <div class="vb-issue-body">
            <div class="vb-row">
              <span class="vb-row-lbl">影響</span>
              <p class="vb-row-text">{html.escape(impact)}</p>
            </div>
            <div class="vb-row">
              <span class="vb-row-lbl vb-row-lbl-action">下一步</span>
              <div class="vb-row-text vb-row-next">{next_html}</div>
            </div>
          </div>
        </article>
        ''')

    # Conclusion-first headline (in user's voice — "我幫你對過了")
    if errors and warnings:
        conclusion = f"AI 分析幫你核對過了，找到 {errors} 處需要注意 + {warnings} 處建議確認的地方。"
    elif errors:
        conclusion = f"AI 分析幫你核對過了，找到 {errors} 處需要注意。"
    else:
        conclusion = f"AI 分析幫你核對過了，{warnings} 處建議確認。"

    subcopy = "下面列出每一筆的影響跟你該怎麼處理 — 沒列出來的，就是沒問題不用管。"
    date_str = f"（分析日期 {html.escape(ana_date)}）" if ana_date else ""

    return f'''
    <section class="validator-banner">
      <div class="vb-head">
        <span class="vb-icon">{_icon("warn", 18)}</span>
        <div class="vb-head-text">
          <strong class="vb-conclusion">{html.escape(conclusion)}</strong>
          <span class="muted small vb-subcopy">{html.escape(subcopy)} {date_str}</span>
        </div>
        <button class="vb-toggle" type="button" aria-expanded="true"
                onclick="const b=this.closest('.validator-banner'); b.classList.toggle('collapsed'); this.setAttribute('aria-expanded', String(!b.classList.contains('collapsed')))">收合</button>
      </div>
      <div class="vb-body">
        {"".join(items_html)}
      </div>
    </section>
    '''


def render_daily_hero(latest_brief: dict | None, analysis: dict | None,
                     pf: dict | None = None) -> str:
    """Prominent 'today's AI take' card — GUSHI-style Morning Brief Hero.

    Shows: greeting + headline + one-liner + 3 highlights (win/risk/opp) + agenda.
    """
    if not latest_brief or not analysis:
        return (
            '<div class="daily-hero muted small">'
            '今日 AI 分析尚未生成（下次排程 07:30 會自動跑）。'
            '</div>'
        )

    mp = analysis.get("market_pulse", {})
    mb = analysis.get("morning_brief", {})
    actions = analysis.get("action_checklist", {}).get("green", [])
    opps = analysis.get("opportunities", [])
    diag = analysis.get("portfolio_diagnosis", {})

    # Top action (first green)
    top_action_html = ""
    if actions:
        a = actions[0]
        top_action_html = f'''
        <div class="hero-action">
          <div class="hero-action-lbl"><span class="dot dot-up"></span>TODAY · GO</div>
          <div class="hero-action-body"><strong>{html.escape(a.get("action", ""))}</strong>
            <div class="hero-action-reason muted small">{html.escape(a.get("reason", ""))}</div>
          </div>
        </div>'''

    # Budget allocation — basket builder (v2, 2026-04-19)
    # User can adjust budget via input or preset buttons; JS auto-selects
    # highest-confidence allocations until the budget is filled. Manual
    # checkbox override is supported. This turns the static "here are 3
    # picks" card into a real portfolio-construction tool.
    # Pad allocations with opportunities.lead_stocks when Gemini is stingy
    # so the slider has real alternatives to shuffle through.
    budget_alloc = analysis.get("budget_allocation", {})
    budget_html = ""
    raw_allocs = budget_alloc.get("allocations") or []
    allocs = _pad_allocations_from_opportunities(raw_allocs, analysis)
    if allocs:
        budget_amt = int(budget_alloc.get("budget_twd", 5000)) or 5000
        plan = budget_alloc.get("plan_summary", "")
        alloc_cards = []
        for idx, al in enumerate(allocs):
            action = al.get("action", "")
            is_cash = "現金" in action or "不動作" in action
            is_synth = bool(al.get("synthetic"))
            cls = "alloc-cash" if is_cash else ("alloc-buy alloc-synth" if is_synth else "alloc-buy")
            sym = al.get("symbol", "")
            name = al.get("name", "")
            shares = al.get("target_shares") or 0
            cost = al.get("target_cost_twd") or 0
            sl = al.get("stop_loss_price")
            tp = al.get("take_profit_price")
            conf = int(al.get("confidence_pct") or 0)
            rat = al.get("rationale", "")
            shares_str = f"{shares} 股" if shares else ""
            cost_str = f"≈{_fmt_twd(cost)}" if cost else ""
            sl_str = f"停損 {sl}" if sl else ""
            tp_str = f"停利 {tp}" if tp else ""
            levels = " · ".join(s for s in (shares_str, cost_str, sl_str, tp_str) if s)
            # Link target: theme page would be cleanest but opp and alloc have
            # different schemas; fall back to the briefs page with a budget anchor.
            deep_link = f'briefs/{latest_brief["date"]}.html#budget'
            check_id = f"hero-alloc-{idx}"
            # Cash/hold items don't count toward basket total — render without
            # checkbox; otherwise every card is a budget-able candidate.
            if is_cash:
                pick_input = (
                    f'<span class="alloc-badge alloc-hold-badge">保留</span>'
                )
            else:
                pick_input = (
                    f'<input type="checkbox" class="alloc-check" id="{check_id}" '
                    f'data-cost="{int(cost)}" data-conf="{conf}" aria-label="納入籃子">'
                )
            synth_tag = ('<span class="alloc-synth-tag small" title="AI 未直接列入主推，由同題材候選自動補齊">次選</span>'
                         if is_synth else '')
            alloc_cards.append(f'''
            <label for="{check_id}" class="alloc-card {cls}"
                   data-cost="{int(cost)}" data-conf="{conf}" data-is-cash="{"1" if is_cash else "0"}">
              <div class="alloc-head">
                <span class="alloc-action">{html.escape(action)} {synth_tag}</span>
                <span class="alloc-head-right">
                  <span class="alloc-conf mono">{conf}%</span>
                  {pick_input}
                </span>
              </div>
              <div class="alloc-sym"><strong>{html.escape(sym)}</strong> <span class="muted small">{html.escape(name)}</span></div>
              <div class="alloc-levels mono small">{html.escape(levels)}</div>
              <div class="alloc-rat small">{html.escape(rat)[:140]}{"…" if len(rat) > 140 else ""}</div>
              <a class="alloc-deeplink small" href="{deep_link}">看詳情 →</a>
            </label>''')
        # Render basket-builder controls
        preset_amounts = [5000, 10000, 20000, 50000]
        preset_btns = "".join(
            f'<button type="button" class="basket-preset-btn" data-amt="{amt}">'
            f'NT${amt // 1000}k</button>' for amt in preset_amounts
        )
        budget_html = f'''
        <div class="hero-budget basket-builder">
          <div class="hero-picks-head">
            <span class="hero-action-lbl">{_icon("dollar", 14)} 籃子建議 · BASKET BUILDER</span>
            <a href="briefs/{latest_brief["date"]}.html#budget" class="btn-link small">看完整下單計畫 →</a>
          </div>
          <div class="basket-controls">
            <div class="basket-budget-row">
              <label class="basket-budget-lbl small">本次預算</label>
              <div class="basket-preset-btns">{preset_btns}</div>
              <div class="basket-input-wrap">
                <span class="basket-currency">NT$</span>
                <input type="number" class="basket-budget-input mono" value="{budget_amt}"
                       step="1000" min="1000" max="500000" aria-label="預算（新台幣）">
              </div>
            </div>
            <p class="budget-plan">{html.escape(plan)}</p>
          </div>
          <div class="alloc-grid">{"".join(alloc_cards)}</div>
          <div class="basket-summary mono small">
            <span class="bs-cell"><span class="muted">籃子合計</span> <strong class="basket-total">NT$0</strong></span>
            <span class="bs-cell"><span class="muted">剩</span> <strong class="basket-remaining">NT${budget_amt:,}</strong></span>
            <span class="bs-cell"><span class="muted">已選</span> <strong class="basket-count">0 檔</strong></span>
            <span class="bs-cell bs-hint muted">▸ 改預算會自動挑信心最高的組合；也可手動勾選。</span>
          </div>
        </div>'''

    # Health badge
    health = diag.get("overall_health", "")
    health_cls = {"良好": "up", "需調整": "amber", "高風險": "dn"}.get(health, "flat")
    diag_pill = (
        f'<span class="badge badge-{health_cls} small">{html.escape(health)}</span>'
        if health else ""
    )

    # Picks strip — theme + lead stock + headline + warning (v7 schema)
    picks_html = ""
    if opps:
        picks = []
        for idx, o in enumerate(opps[:3]):
            # Resolve a lead symbol for link target; fall back to opportunities anchor
            leads = o.get("lead_stocks") or []
            lead_sym = (leads[0].get("symbol") if leads else "") or o.get("symbol", "")
            lead_name = (leads[0].get("name") if leads else "") or o.get("name", "")

            theme = o.get("theme") or o.get("category_tag") or lead_sym or "題材"
            # Headline first, then legacy thesis, then why
            thesis = o.get("headline") or o.get("thesis") or o.get("why", "")
            # Risk priority: explicit ai_warning > legacy risk (no signal fallback —
            # signals like "量增" are bullish, not risks)
            signals = o.get("signals") or []
            risk_text = o.get("ai_warning") or o.get("risk") or ""
            conf = int(o.get("confidence_pct") or 0)
            stage = o.get("stage", "")

            # Link directly to theme deep-dive page (where user sees all related stocks)
            pick_href = f"themes/{_theme_slug(o, idx)}.html"

            # Strip leading emojis the AI may have added
            theme_clean = _strip_leading_emoji(theme)
            lead_str = f"{lead_sym} {lead_name}".strip() if lead_sym else (lead_name or "")

            risk_row = ""
            if risk_text:
                risk_row = (
                    f'<div class="pick-risk muted small"><span class="mono amber">RISK</span> · '
                    f'{html.escape(risk_text)[:60]}{"…" if len(risk_text) > 60 else ""}</div>'
                )

            meta_bits = []
            if stage:
                meta_bits.append(f'<span class="pick-stage mono small">{html.escape(stage)}</span>')
            if conf:
                meta_bits.append(f'<span class="pick-conf mono small muted">CONF {conf}</span>')
            meta_html = f'<div class="pick-meta">{" ".join(meta_bits)}</div>' if meta_bits else ""

            picks.append(f'''
            <a class="pick-card" href="{pick_href}">
              <div class="pick-head">
                <strong>{html.escape(theme_clean)}</strong>
                <span class="muted small">{html.escape(lead_str)}</span>
              </div>
              <div class="pick-thesis small">{html.escape(thesis)[:80]}{"…" if len(thesis) > 80 else ""}</div>
              {meta_html}
              {risk_row}
            </a>''')
        picks_html = f'''
        <div class="hero-picks">
          <div class="hero-picks-head">
            <span class="hero-action-lbl">{_icon("eye", 14)} 今日值得研究 · RESEARCH PICKS</span>
            <span class="muted small">{len(opps)} 檔</span>
          </div>
          <div class="pick-grid">{"".join(picks)}</div>
        </div>'''

    date_str = latest_brief["date"]
    weekday = latest_brief["weekday"]

    # GUSHI-style BriefHero: greeting + headline + one-liner + highlights + agenda
    # Greeting is rendered by JS on the client (時段自適應) so opening the page
    # at 3pm shows 「午安」, at 9pm 「晚安」 — Gemini's output ("早安" baked in at
    # 07:30 build time) is only the fallback when JS fails. Strip trailing
    # punctuation so we never output "早安！，..." (user feedback 2026-04-19).
    _raw_greeting = (mb.get("greeting") or "早安").strip()
    _raw_greeting = _raw_greeting.rstrip("！!。.， ,")
    greeting = _raw_greeting or "早安"
    headline = mb.get("headline") or mp.get("summary", "今日尚未生成摘要。")[:20]
    one_liner = mb.get("one_liner") or mp.get("summary", "")
    highlights = mb.get("highlights", [])

    # Highlights as win/risk/opp cards
    highlights_html = ""
    if highlights:
        kind_labels = {"win": "進帳", "risk": "注意", "opp": "機會"}
        kind_cls = {"win": "up", "risk": "amber", "opp": "accent"}
        cards = []
        for i, h in enumerate(highlights[:3]):
            k = h.get("kind", "opp")
            cards.append(f'''
            <div class="hl-card hl-{kind_cls.get(k, "accent")}" style="animation-delay:{i * 0.1}s">
              <div class="hl-tag">{kind_labels.get(k, k)}</div>
              <div class="hl-label">{html.escape(h.get("label", ""))}</div>
              <div class="hl-detail muted small">{html.escape(h.get("detail", ""))}</div>
            </div>''')
        highlights_html = f'<div class="hl-grid">{"".join(cards)}</div>'

    # Count for headline emphasis
    import re as _re
    headline_html = _re.sub(
        r"(\d+)",
        r'<span class="shimmer-text">\1</span>',
        html.escape(headline),
        count=1,
    )

    return f'''
<div class="brief-hero">
  <div class="bh-top">
    <span class="live-dot accent"></span>
    <span class="bh-badge mono">AI MORNING BRIEF · {date_str} 週{weekday}</span>
    {_sentiment_badge(mp.get("tw_sentiment", "中性"))}
    {diag_pill}
    <div class="bh-spacer"></div>
    <a href="briefs/{date_str}.html" class="btn-link small">→ 完整分析</a>
  </div>
  <h2 class="bh-headline">
    <span class="bh-greeting" data-fallback="{html.escape(greeting)}">{html.escape(greeting)}</span><span class="bh-sep"> · </span>{headline_html}
  </h2>
  <p class="bh-oneliner">{_link_tickers(one_liner)}</p>
  {highlights_html}
  {budget_html}
  {top_action_html}
  {picks_html}
</div>
'''


def render_ai_tab(latest_brief: dict | None, analysis: dict | None) -> str:
    if not latest_brief or not analysis:
        return '<p class="muted" style="padding:20px">尚未產生 AI 分析。請等下次排程或手動觸發。</p>'
    mp = analysis.get("market_pulse", {})
    macro_ctx = analysis.get("macro_context", {})
    diag = analysis.get("portfolio_diagnosis", {})
    actions = analysis.get("action_checklist", {"green": [], "yellow": [], "red": []})
    topics = analysis.get("topics", [])
    holdings_an = analysis.get("holdings_analysis", [])
    opps = analysis.get("opportunities", [])
    lp = analysis.get("learning_point", {})
    model = analysis.get("model", "gemini")

    # Action columns
    def action_col(items, cls, icon, label):
        if not items:
            li = '<li class="empty muted">今日無建議</li>'
        else:
            li = "".join(
                f'<li><strong>{html.escape(i["action"])}</strong>'
                f'<div class="action-reason">{html.escape(i["reason"])}</div></li>'
                for i in items
            )
        return f'<div class="action-col {cls}"><div class="action-header"><span class="action-tag mono">{icon}</span> {label}</div><ul>{li}</ul></div>'

    actions_html = (
        '<div class="actions-grid">'
        + action_col(actions.get("green", []), "action-green", "GO", "可以做")
        + action_col(actions.get("yellow", []), "action-yellow", "WATCH", "該警戒")
        + action_col(actions.get("red", []), "action-red", "HOLD", "不要做")
        + '</div>'
    )

    # Diagnosis
    diag_html = ""
    if diag.get("overall_health"):
        health = diag.get("overall_health", "")
        health_cls = {"良好": "up", "需調整": "amber", "高風險": "dn"}.get(health, "flat")
        diag_html = f'''
<div class="diag-compact">
  <div class="diag-compact-head">
    <span class="muted small">組合健康度</span>
    <span class="badge badge-{health_cls}">{html.escape(health)}</span>
  </div>
  <div class="diag-compact-body">
    <div><strong class="small muted">關鍵議題：</strong>{html.escape(diag.get("key_issue", ""))}</div>
    <div><strong class="small muted">調整建議：</strong>{html.escape(diag.get("rebalance_advice", ""))}</div>
  </div>
</div>
'''

    # Macro context
    macro_html = ""
    if macro_ctx.get("narrative"):
        wp = macro_ctx.get("watchpoints", [])
        wp_html = ""
        if wp:
            wp_html = '<ul class="watchpoint-list">' + "".join(
                f'<li>{html.escape(w)}</li>' for w in wp
            ) + '</ul>'
        macro_html = f'''
<div class="ai-block">
  <div class="tab-subhead">MACRO · 總經背景</div>
  <p class="narrative">{html.escape(macro_ctx["narrative"])}</p>
  {wp_html}
</div>
'''

    # Topics (rich, full narratives)
    topic_cards = []
    for t in topics:
        tickers_chips = "".join(
            f'<span class="chip chip-muted small">{html.escape(tk)}</span>'
            for tk in t.get("tickers", [])[:6]
        )
        pts = "".join(f'<li>{html.escape(p)}</li>' for p in t.get("key_points", []))
        pts_html = f'<ul class="topic-points">{pts}</ul>' if pts else ""
        topic_cards.append(f'''
        <article class="topic-card">
          <div class="topic-head">
            <h3>{html.escape(t.get("title", ""))}</h3>
            {_sentiment_badge(t.get("sentiment", "中性"))}
          </div>
          <div class="topic-tickers">{tickers_chips}</div>
          <p class="narrative">{_link_tickers(t.get("narrative", ""))}</p>
          {pts_html}
        </article>''')
    topics_html = (
        f'<div class="ai-block"><div class="tab-subhead">TOPICS · 今日主題 '
        f'<span class="muted small">{len(topics)} 則</span></div>'
        f'{"".join(topic_cards)}</div>'
        if topic_cards else ""
    )

    # Holdings analysis with bull/bear
    holding_cards = []
    for h in holdings_an:
        bb = h.get("bull_bear_breakdown", {})
        bull, bear, neu = bb.get("bull_pct", 0), bb.get("bear_pct", 0), bb.get("neutral_pct", 0)
        catalysts = h.get("key_catalysts", [])
        risks = h.get("key_risks", [])
        cat_html = (
            "<div class='hc-list-head'>催化劑</div><ul class='hc-list up-list'>" +
            "".join(f'<li>{html.escape(c)}</li>' for c in catalysts) + "</ul>"
        ) if catalysts else ""
        risk_html = (
            "<div class='hc-list-head'>風險</div><ul class='hc-list dn-list'>" +
            "".join(f'<li>{html.escape(r)}</li>' for r in risks) + "</ul>"
        ) if risks else ""
        holding_cards.append(f'''
        <article class="holding-analysis">
          <div class="ha-head">
            <h3><a href="holdings/{html.escape(h.get("symbol", ""))}.html">{html.escape(h.get("symbol", ""))} {html.escape(h.get("name", ""))}</a></h3>
            {_sentiment_badge(h.get("outlook", "中性"))}
          </div>
          <p class="narrative">{html.escape(h.get("commentary", ""))}</p>
          <div class="bullbear">
            <div class="bb-bar">
              <div class="bb-bull" style="width:{bull}%"></div>
              <div class="bb-neu" style="width:{neu}%"></div>
              <div class="bb-bear" style="width:{bear}%"></div>
            </div>
            <div class="bb-legend">
              <span class="bb-lbl bull">看多 {bull}%</span>
              <span class="bb-lbl neu">觀望 {neu}%</span>
              <span class="bb-lbl bear">看空 {bear}%</span>
            </div>
          </div>
          <div class="hc-split">{cat_html}{risk_html}</div>
        </article>''')
    holdings_html = (
        f'<div class="ai-block"><div class="tab-subhead">HOLDINGS · 持股分析</div>{"".join(holding_cards)}</div>'
        if holding_cards else ""
    )

    # Budget allocation — full detail (ai_tab rendering)
    budget_alloc = analysis.get("budget_allocation", {})
    budget_full_html = ""
    raw_allocs = budget_alloc.get("allocations") or []
    allocs = _pad_allocations_from_opportunities(raw_allocs, analysis)
    if allocs:
        rows = []
        for al in allocs:
            action = al.get("action", "")
            is_cash = "現金" in action or "不動作" in action
            cls = "alloc-cash" if is_cash else "alloc-buy"
            srcs = al.get("data_sources") or []
            src_html = "".join(f'<span class="chip chip-muted small">{html.escape(s)}</span>' for s in srcs)
            sl = al.get("stop_loss_price")
            tp = al.get("take_profit_price")
            shares = al.get("target_shares")
            cost = al.get("target_cost_twd")
            row_levels = []
            if shares: row_levels.append(f"<strong>{shares} 股</strong>")
            if cost: row_levels.append(f"約 {_fmt_twd(cost)}")
            if al.get("entry_condition"): row_levels.append(f"進場：{html.escape(al['entry_condition'])}")
            if sl: row_levels.append(f'<span class="dn">停損 {sl}</span>')
            if tp: row_levels.append(f'<span class="up">停利 {tp}</span>')
            rows.append(f'''
            <article class="alloc-full-card {cls}">
              <div class="alloc-full-head">
                <div>
                  <div class="alloc-action-big">{html.escape(action)}</div>
                  <h3>{html.escape(al.get("symbol", ""))} <span class="muted">{html.escape(al.get("name", ""))}</span></h3>
                </div>
                <div class="alloc-conf-big mono">信心度 {al.get("confidence_pct", 0)}%</div>
              </div>
              <div class="alloc-levels-row mono small">{" · ".join(row_levels)}</div>
              <p><span class="label-inline">理由</span>{html.escape(al.get("rationale", ""))}</p>
              {"<div class='alloc-sources'><span class='label-inline'>依據</span>" + src_html + "</div>" if src_html else ""}
              <p class="risk-line"><span class="label-inline dn">⚠ 風險</span>{html.escape(al.get("risk", ""))}</p>
            </article>''')
        unalloc = budget_alloc.get("unallocated_twd", 0)
        unalloc_line = (f'<p class="muted small">保留現金 {_fmt_twd(unalloc)}（等更好的機會）</p>'
                        if unalloc and unalloc > 0 else "")
        why_not = budget_alloc.get("why_not_other_picks") or ""
        why_not_line = f'<p class="muted small">為什麼不選別檔：{html.escape(why_not)}</p>' if why_not else ""
        budget_full_html = f'''
<div class="ai-block" id="budget">
  <div class="tab-subhead">ALLOCATION · 今日 NT${budget_alloc.get("budget_twd", 0):,.0f} 配置建議 <span class="badge-count">SNOWBALL</span></div>
  <div class="budget-plan-big">{html.escape(budget_alloc.get("plan_summary", ""))}</div>
  {"".join(rows)}
  {unalloc_line}
  {why_not_line}
</div>
'''

    # Opportunities — the star section; ticker symbols link to deep dive
    opps_html = ""
    if opps:
        rows = []
        for o in opps:
            sym = o.get("symbol", "")
            sym_link = (f'<a href="holdings/{html.escape(sym)}.html" class="stock-link">{html.escape(sym)}</a>'
                        if sym in _KNOWN_SYMBOLS else html.escape(sym))
            rows.append(f'''
            <article class="opp-card">
              <div class="opp-head">
                <h3>{sym_link} <span class="muted">{html.escape(o.get("name", ""))}</span></h3>
              </div>
              <p><span class="label-inline">論點</span>{esc_linked(o.get("thesis", ""))}</p>
              <p><span class="label-inline">研究切入點</span>{esc_linked(o.get("research_angle", ""))}</p>
              <p class="risk-line"><span class="label-inline dn">⚠ 風險</span>{esc_linked(o.get("risk", ""))}</p>
            </article>''')
        opps_html = (
            f'<div class="ai-block" id="opportunities"><div class="tab-subhead">OPPORTUNITIES · 值得研究 '
            f'<span class="badge-count">{len(opps)} DETECTED</span> <span class="muted small">· 點代碼看深度</span></div>'
            f'{"".join(rows)}</div>'
        )

    # Learning
    learning_html = ""
    if lp:
        learning_html = f'''
<div class="ai-block">
  <div class="tab-subhead">LESSON · 學習點</div>
  <div class="learning-card">
    <h3>{html.escape(lp.get("term", ""))}</h3>
    <p>{html.escape(lp.get("explanation", ""))}</p>
  </div>
</div>
'''

    return f'''
<div class="ai-tab-body">
  <div class="pulse-mini">
    <div class="pulse-mini-cell"><span class="muted small">台股</span> {_sentiment_badge(mp.get("tw_sentiment", "中性"))}</div>
    <div class="pulse-mini-cell"><span class="muted small">美股</span> {_sentiment_badge(mp.get("us_sentiment", "中性"))}</div>
    <div class="pulse-mini-summary">{html.escape(mp.get("summary", ""))}</div>
  </div>
  {diag_html}

  <div class="ai-block">
    <div class="tab-subhead">ACTION · 今日行動</div>
    {actions_html}
  </div>

  {budget_full_html}
  {opps_html}
  {holdings_html}
  {macro_html}
  {topics_html}
  {learning_html}

  <div class="tab-footer">
    <span class="muted small mono">由 {html.escape(model)} 產生 · 僅供研究參考</span>
    <a href="briefs/{latest_brief["date"]}.html" class="btn-link">→ 看完整 brief + 原始新聞</a>
  </div>
</div>
'''


def render_simulator(pf: dict, analysis: dict | None) -> tuple[str, str]:
    """Interactive trade simulator — client-side math, no API calls.

    Returns (html, js_data_blob). JS data blob should be injected in the page.
    Universe comes from portfolio.json simulator_universe + AI opportunities.
    """
    if not pf:
        return "", "{}"

    # Collect tickers from universe + holdings + watchlist + opportunities
    items: list[dict] = []
    seen: set[str] = set()

    def add(sym, name, price, market, group, pct52=None, high52=None, low52=None, category=None):
        if not sym or sym in seen or price is None:
            return
        seen.add(sym)
        items.append({
            "symbol": sym, "name": name, "price": price,
            "market": market, "group": group,
            "pct_52w": pct52, "high_52w": high52, "low_52w": low52,
            "category": category or "",
        })

    # Holdings first (user owns these)
    # Collect rec data alongside pricing
    rec_by_sym: dict[str, dict] = {}
    for coll_name in ("holdings", "watchlist", "simulator_universe"):
        for entry in pf.get(coll_name, []):
            if entry.get("recommendation"):
                rec_by_sym[entry["symbol"]] = entry["recommendation"]

    for h in pf.get("holdings", []):
        add(h["symbol"], h["name"], h.get("price"), h.get("market", "TW"),
            "HOLDINGS · 我的持股", h.get("pct_52w"), h.get("high_52w"), h.get("low_52w"),
            h.get("pillar"))

    # Watchlist
    for w in pf.get("watchlist", []):
        add(w["symbol"], w["name"], w.get("price"), w.get("market", "TW"),
            "WATCHLIST · 追蹤中", w.get("pct_52w"), w.get("high_52w"), w.get("low_52w"),
            w.get("pillar"))

    # AI opportunities — group them distinctly so they stand out
    if analysis:
        for o in analysis.get("opportunities", []):
            sym = o.get("symbol")
            if sym and sym not in seen:
                # Try to find price from universe
                u_match = next((u for u in pf.get("simulator_universe", [])
                               if u["symbol"] == sym), None)
                if u_match:
                    add(sym, o.get("name", u_match["name"]), u_match["price"],
                        u_match["market"], "AI PICKS · 今日機會",
                        u_match.get("pct_52w"), u_match.get("high_52w"),
                        u_match.get("low_52w"), u_match.get("category"))

    # Universe — group by category
    for u in pf.get("simulator_universe", []):
        if u["symbol"] in seen:
            continue
        cat = u.get("category", "其他")
        add(u["symbol"], u["name"], u["price"], u["market"], cat,
            u.get("pct_52w"), u.get("high_52w"), u.get("low_52w"), cat)

    # Get default budget
    cfg_budget = 5000
    try:
        cfg = yaml.safe_load((ROOT / "portfolio.yaml").read_text(encoding="utf-8"))
        cfg_budget = int(cfg.get("trade_budget_twd", 5000))
    except Exception:
        pass

    fx = pf.get("fx_usdtwd", 32.0)

    data_blob = json.dumps({
        "items": items,
        "defaultBudget": cfg_budget,
        "usdtwd": fx,
        "recs": rec_by_sym,
    }, ensure_ascii=False)

    return f'''
<div class="sim-tab-body">
  <div class="sim-intro muted small">
    本地即時計算。調參數看「如果我要買某檔、用多少錢、設停損停利在哪，會變怎樣」。不呼叫 AI、不花錢。
  </div>

  <div class="sim-grid">
    <div class="sim-field">
      <label class="sim-lbl">{_icon("dollar", 14)} 預算 · BUDGET</label>
      <div class="sim-budget-row">
        <span class="sim-prefix">NT$</span>
        <input type="number" id="sim-budget" value="{cfg_budget}" min="1000" step="500" class="sim-input">
      </div>
      <div class="sim-presets">
        <button class="sim-chip" data-preset="3000">3k</button>
        <button class="sim-chip" data-preset="5000">5k</button>
        <button class="sim-chip" data-preset="10000">10k</button>
        <button class="sim-chip" data-preset="20000">20k</button>
        <button class="sim-chip" data-preset="50000">50k</button>
      </div>
    </div>

    <div class="sim-field">
      <label class="sim-lbl">{_icon("chart", 14)} 標的 · TICKERS <span id="sim-count" class="muted small mono"></span></label>
      <select id="sim-ticker" class="sim-input sim-select"></select>
      <div class="sim-ticker-info mono small muted" id="sim-ticker-info">—</div>
      <div class="sim-52w-bar" id="sim-52w-bar"></div>
      <div class="sim-rec" id="sim-rec"></div>
      <a class="sim-deeplink" id="sim-deeplink" href="#">→ 看完整深度頁</a>
    </div>

    <div class="sim-field">
      <label class="sim-lbl">🎯 進場策略</label>
      <div class="sim-entry-group" id="sim-entry-group">
        <button class="sim-entry-btn active" data-strategy="market">現價進</button>
        <button class="sim-entry-btn" data-strategy="-2">限價 −2%</button>
        <button class="sim-entry-btn" data-strategy="-5">限價 −5%</button>
        <button class="sim-entry-btn" data-strategy="custom">自訂</button>
      </div>
      <div class="sim-budget-row" style="margin-top:6px">
        <span class="sim-prefix">下單價</span>
        <input type="number" id="sim-entry" value="0" step="0.01" class="sim-input">
      </div>
      <div class="sim-entry-hint muted small mono" id="sim-entry-hint">—</div>
    </div>

    <div class="sim-field">
      <label class="sim-lbl"><span class="sim-tag mono">STOP</span> 停損 −<span id="sim-sl-val" class="mono">10</span>%</label>
      <input type="range" id="sim-sl" min="3" max="25" value="10" step="1" class="sim-range">
      <div class="sim-range-labels muted small mono"><span>−3%</span><span>−25%</span></div>
    </div>

    <div class="sim-field">
      <label class="sim-lbl"><span class="sim-tag mono">TP</span> 停利 +<span id="sim-tp-val" class="mono">30</span>%</label>
      <input type="range" id="sim-tp" min="5" max="100" value="30" step="5" class="sim-range">
      <div class="sim-range-labels muted small mono"><span>+5%</span><span>+100%</span></div>
    </div>
  </div>

  <div class="sim-output" id="sim-output">
    <div class="sim-out-cell">
      <div class="muted small">可買股數</div>
      <div class="mono tnum sim-out-val" id="sim-shares">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">總成本</div>
      <div class="mono tnum sim-out-val" id="sim-cost">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">剩餘現金</div>
      <div class="mono tnum sim-out-val" id="sim-cash">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">停損價</div>
      <div class="mono tnum sim-out-val dn" id="sim-sl-price">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">停利價</div>
      <div class="mono tnum sim-out-val up" id="sim-tp-price">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">最大損失</div>
      <div class="mono tnum sim-out-val dn" id="sim-max-loss">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">目標獲利</div>
      <div class="mono tnum sim-out-val up" id="sim-max-profit">—</div>
    </div>
    <div class="sim-out-cell">
      <div class="muted small">佔組合比</div>
      <div class="mono tnum sim-out-val" id="sim-weight">—</div>
    </div>
  </div>

  <div class="sim-rules-row">
    <div class="sim-rule">
      <strong>雪球法規則</strong>
      <span class="muted small">賺到停利 → 取 50% 入 0050 存款、50% 留場繼續滾</span>
    </div>
  </div>
</div>

<script id="sim-data" type="application/json">{data_blob}</script>
<script>
(function() {{
  const el = document.getElementById('sim-data');
  if (!el) return;
  const DATA = JSON.parse(el.textContent);
  const fx = DATA.usdtwd || 32.0;
  const pfTotal = {pf.get("summary", {}).get("total_value_twd", 1)};

  const tickerSel = document.getElementById('sim-ticker');
  const budgetIn = document.getElementById('sim-budget');
  const slIn = document.getElementById('sim-sl');
  const tpIn = document.getElementById('sim-tp');
  const entryIn = document.getElementById('sim-entry');
  const slVal = document.getElementById('sim-sl-val');
  const tpVal = document.getElementById('sim-tp-val');
  const infoEl = document.getElementById('sim-ticker-info');
  const entryHint = document.getElementById('sim-entry-hint');
  const bar52w = document.getElementById('sim-52w-bar');
  const countEl = document.getElementById('sim-count');
  let entryStrategy = 'market'; // market | -2 | -5 | custom

  // Populate dropdown grouped by category, with priority groups first
  const priorityGroups = ['HOLDINGS · 我的持股', 'WATCHLIST · 追蹤中', 'AI PICKS · 今日機會'];
  const groups = {{}};
  DATA.items.forEach(it => {{
    if (it.price == null) return;
    (groups[it.group] = groups[it.group] || []).push(it);
  }});
  const sortedGroups = [
    ...priorityGroups.filter(g => groups[g]),
    ...Object.keys(groups).filter(g => !priorityGroups.includes(g)).sort(),
  ];
  sortedGroups.forEach(group => {{
    const og = document.createElement('optgroup');
    og.label = group;
    groups[group].forEach(it => {{
      const opt = document.createElement('option');
      opt.value = it.symbol;
      opt.textContent = `${{it.symbol}}  ${{it.name}}  @${{it.price.toFixed(2)}}${{it.market === 'US' ? ' USD' : ''}}`;
      og.appendChild(opt);
    }});
    tickerSel.appendChild(og);
  }});
  countEl.textContent = `(${{DATA.items.length}} 檔)`;

  function getItem(symbol) {{
    return DATA.items.find(i => i.symbol === symbol && i.price != null);
  }}

  function fmt(n, dp=0) {{
    if (n == null || Number.isNaN(n)) return '—';
    return n.toLocaleString('en-US', {{ minimumFractionDigits: dp, maximumFractionDigits: dp }});
  }}

  function suggestEntry(it, strategy) {{
    const cur = it.price;
    if (strategy === 'market') return cur;
    if (strategy === '-2') return cur * 0.98;
    if (strategy === '-5') return cur * 0.95;
    return parseFloat(entryIn.value) || cur;
  }}

  function updateEntryHint(it, entry) {{
    const cur = it.price;
    const diff = ((entry - cur) / cur * 100);
    let advice = '';
    if (it.pct_52w != null) {{
      if (it.pct_52w >= 90) advice = '[HIGH] 52週位階 ' + it.pct_52w.toFixed(0) + '%（高檔），建議限價等拉回';
      else if (it.pct_52w >= 70) advice = '52週位階 ' + it.pct_52w.toFixed(0) + '%（中高），可考慮限價 −2%';
      else if (it.pct_52w >= 30) advice = '52週位階 ' + it.pct_52w.toFixed(0) + '%（中段），現價進或限價 −2% 皆可';
      else advice = '52週位階 ' + it.pct_52w.toFixed(0) + '%（低檔），積極進場';
    }}
    entryHint.textContent = `距現價 ${{diff >= 0 ? '+' : ''}}${{diff.toFixed(2)}}%  ·  ${{advice}}`;
  }}

  function render52wBar(it) {{
    if (it.high_52w == null || it.low_52w == null) {{
      bar52w.innerHTML = '';
      return;
    }}
    const range = it.high_52w - it.low_52w;
    const curPos = range > 0 ? ((it.price - it.low_52w) / range * 100) : 50;
    const entry = parseFloat(entryIn.value) || it.price;
    const entryPos = range > 0 ? Math.max(0, Math.min(100, (entry - it.low_52w) / range * 100)) : 50;
    bar52w.innerHTML = `
      <div class="sim-52w-labels">
        <span>52w低 ${{it.low_52w.toFixed(2)}}</span>
        <span>52w高 ${{it.high_52w.toFixed(2)}}</span>
      </div>
      <div class="sim-52w-track">
        <div class="sim-52w-entry" style="left:${{entryPos.toFixed(1)}}%" title="進場 ${{entry.toFixed(2)}}"></div>
        <div class="sim-52w-cur" style="left:${{curPos.toFixed(1)}}%" title="現價 ${{it.price.toFixed(2)}}"></div>
      </div>`;
  }}

  function recalc() {{
    const sym = tickerSel.value;
    const budget = parseFloat(budgetIn.value) || 0;
    const sl = parseFloat(slIn.value);
    const tp = parseFloat(tpIn.value);
    slVal.textContent = sl;
    tpVal.textContent = tp;

    const it = getItem(sym);
    if (!it) return;

    // Update entry price from strategy
    if (entryStrategy !== 'custom') {{
      entryIn.value = suggestEntry(it, entryStrategy).toFixed(2);
    }}
    const entryPrice = parseFloat(entryIn.value) || it.price;

    const entryTwd = it.market === 'TW' ? entryPrice : entryPrice * fx;
    const maxShares = Math.floor(budget / entryTwd);
    const totalCost = maxShares * entryTwd;
    const cashLeft = budget - totalCost;
    const slPrice = entryPrice * (1 - sl/100);
    const tpPrice = entryPrice * (1 + tp/100);
    const maxLoss = totalCost * (sl/100);
    const maxProfit = totalCost * (tp/100);
    const weight = pfTotal ? (totalCost / pfTotal * 100) : 0;

    const p52 = it.pct_52w != null ? ` · 52w位階 ${{it.pct_52w.toFixed(0)}}%` : '';
    const cat = it.category ? ` · ${{it.category}}` : '';
    infoEl.textContent = `${{it.market}} · 現價 ${{it.price.toFixed(2)}}${{it.market === 'US' ? ' USD' : ''}}${{p52}}${{cat}}`;

    updateEntryHint(it, entryPrice);
    render52wBar(it);

    // Rule-based recommendation
    const recEl = document.getElementById('sim-rec');
    const dpLink = document.getElementById('sim-deeplink');
    const rec = (DATA.recs || {{}})[sym];
    if (rec) {{
      const toneCls = 'tone-' + (rec.tone || 'flat');
      recEl.innerHTML = `
        <div class="sim-rec-card ${{toneCls}}">
          <div class="sim-rec-lbl mono small">📐 規則建議</div>
          <div class="sim-rec-action ${{rec.tone || 'flat'}}">${{rec.action}}</div>
          <div class="sim-rec-price mono small">建議價 <strong>${{rec.suggested_price ? rec.suggested_price.toFixed(2) : '—'}}</strong></div>
          <div class="sim-rec-reason muted small">${{rec.reason || ''}}</div>
        </div>`;
    }} else {{
      recEl.innerHTML = '';
    }}
    if (dpLink) dpLink.href = 'holdings/' + sym + '.html';

    document.getElementById('sim-shares').textContent = maxShares > 0 ? maxShares + ' 股' : '0 股（預算不夠 1 股）';
    document.getElementById('sim-cost').textContent = 'NT$' + fmt(totalCost);
    document.getElementById('sim-cash').textContent = 'NT$' + fmt(cashLeft);
    document.getElementById('sim-sl-price').textContent = slPrice.toFixed(2);
    document.getElementById('sim-tp-price').textContent = tpPrice.toFixed(2);
    document.getElementById('sim-max-loss').textContent = '−NT$' + fmt(maxLoss);
    document.getElementById('sim-max-profit').textContent = '+NT$' + fmt(maxProfit);
    document.getElementById('sim-weight').textContent = weight.toFixed(2) + '%';
  }}

  budgetIn.addEventListener('input', recalc);
  tickerSel.addEventListener('change', recalc);
  slIn.addEventListener('input', recalc);
  tpIn.addEventListener('input', recalc);
  entryIn.addEventListener('input', () => {{ entryStrategy = 'custom'; document.querySelectorAll('.sim-entry-btn').forEach(b => b.classList.toggle('active', b.dataset.strategy === 'custom')); recalc(); }});
  document.querySelectorAll('.sim-chip').forEach(b => {{
    b.addEventListener('click', () => {{
      budgetIn.value = b.dataset.preset;
      recalc();
    }});
  }});
  document.querySelectorAll('.sim-entry-btn').forEach(b => {{
    b.addEventListener('click', () => {{
      if (b.dataset.strategy === 'custom') {{
        entryStrategy = 'custom';
        entryIn.focus();
      }} else {{
        entryStrategy = b.dataset.strategy;
      }}
      document.querySelectorAll('.sim-entry-btn').forEach(x => x.classList.toggle('active', x === b));
      recalc();
    }});
  }});

  // Init
  if (tickerSel.options.length > 0) recalc();
}})();
</script>
''', data_blob


def render_macro_strip(pf: dict) -> str:
    """Compact macro strip for main area (wider, horizontal)."""
    if not pf:
        return ""
    macro = pf.get("macro", {})

    def _cell(label, key, fmt="{:.1f}"):
        d = macro.get(key, {})
        close = d.get("close")
        if close is None:
            return ""
        day = d.get("day_change_pct") or 0
        ytd = d.get("ret_ytd")
        ytd_str = f'<span class="muted tnum small">YTD {_fmt_pct(ytd, 1)}</span>' if ytd is not None else ""
        return f'''
        <div class="macro-strip-cell">
          <div class="muted small">{label}</div>
          <div class="mono tnum macro-strip-val">{fmt.format(close)}</div>
          <div class="macro-strip-delta"><span class="mono tnum {_cls(day)}">{_fmt_pct(day, 2)}</span> {ytd_str}</div>
        </div>'''

    return f'''
<div class="macro-strip">
  {_cell("加權 ^TWII", "twii", "{:.0f}")}
  {_cell("S&P 500", "spx", "{:.0f}")}
  {_cell("VIX", "vix", "{:.2f}")}
  {_cell("USD/TWD", "usdtwd", "{:.3f}")}
</div>
'''


# ---------------------------------------------------------------------------
# GUSHI-style v3 — Portfolio / Macro / News / Chat tab renderers
# ---------------------------------------------------------------------------

def _risk_grade(val: float, thresholds: list[tuple[float, str]]) -> str:
    """Return letter grade from (threshold, grade) list. First match wins."""
    for t, g in thresholds:
        if val <= t:
            return g
    return thresholds[-1][1]


def render_portfolio_tab(pf: dict, analysis: dict | None = None,
                         coverage: dict | None = None) -> str:
    """GUSHI-style Portfolio tab: 4 big KPI cards + full holdings table +
    weekly attribution bars + risk metrics panel + COVERAGE section
    (proactive curation grounded in supply_chains.yaml + audit_coverage.py
    + Gemini's coverage_suggestions). No emojis, all mono."""
    if not pf:
        return '<p class="muted" style="padding:20px">無組合資料。</p>'

    s = pf.get("summary", {})
    bench = pf.get("benchmark", {})
    risk = pf.get("risk", {})
    holdings = pf.get("holdings", [])
    weekly = pf.get("weekly_attribution", [])

    total_value = s.get("total_value_twd", 0)
    total_pnl = s.get("total_pnl_twd", 0)
    total_pct = s.get("total_pnl_pct", 0)
    day_pnl = s.get("day_pnl_twd", 0)
    day_pct = s.get("day_pnl_pct", 0)
    alpha = s.get("alpha_vs_benchmark_pct", 0)
    bench_sym = bench.get("symbol", "0050")

    # 4 big KPI cards (GUSHI style)
    kpi_cards = f'''
    <div class="pfv2-kpi-grid">
      <div class="pfv2-kpi">
        <div class="pfv2-kpi-lbl mono">TOTAL VALUE</div>
        <div class="pfv2-kpi-val mono tnum">{_fmt_twd(total_value)}</div>
        <div class="pfv2-kpi-sub muted mono small">含現金 · TWD</div>
      </div>
      <div class="pfv2-kpi">
        <div class="pfv2-kpi-lbl mono">TOTAL P&amp;L</div>
        <div class="pfv2-kpi-val mono tnum {_cls(total_pnl)}">{_fmt_twd(total_pnl, sign=True)}</div>
        <div class="pfv2-kpi-sub mono small {_cls(total_pct)}">{_fmt_pct(total_pct)} since inception</div>
      </div>
      <div class="pfv2-kpi">
        <div class="pfv2-kpi-lbl mono">TODAY P&amp;L</div>
        <div class="pfv2-kpi-val mono tnum {_cls(day_pnl)}">{_fmt_twd(day_pnl, sign=True)}</div>
        <div class="pfv2-kpi-sub mono small {_cls(day_pct)}">{_fmt_pct(day_pct)} day</div>
      </div>
      <div class="pfv2-kpi">
        <div class="pfv2-kpi-lbl mono">ALPHA vs {html.escape(bench_sym)}</div>
        <div class="pfv2-kpi-val mono tnum {_cls(alpha)}">{_fmt_pct(alpha)}</div>
        <div class="pfv2-kpi-sub mono small muted">bench {_fmt_pct(bench.get("day_change_pct", 0))}</div>
      </div>
    </div>
    '''

    # Full holdings table (SHARES / AVG COST / LAST / TODAY / VALUE / P&L / % / WEIGHT)
    total_val_incl_cash = s.get("total_value_twd", 0) or 1
    rows = []
    for h in holdings:
        weight = (h.get("value", 0) / total_val_incl_cash * 100) if total_val_incl_cash else 0
        day_cls = _cls(h.get("day_change_pct"))
        pnl_cls = _cls(h.get("pnl"))
        pillar_cls = PILLAR_CLS.get(h.get("pillar", "growth"), "")
        rows.append(f'''
        <tr onclick="location.href='holdings/{h["symbol"]}.html'">
          <td class="tk-cell"><span class="pillar-dot {pillar_cls}"></span><strong class="mono">{h["symbol"]}</strong>
            <span class="muted">{html.escape(h.get("name", ""))}</span></td>
          <td class="mono tnum right">{h.get("shares", 0):,}</td>
          <td class="mono tnum right">{h.get("cost_basis", 0):.2f}</td>
          <td class="mono tnum right">{h.get("price", 0):.2f}</td>
          <td class="mono tnum right {day_cls}">{_fmt_pct(h.get("day_change_pct"))}</td>
          <td class="mono tnum right">{_fmt_twd(h.get("value", 0))}</td>
          <td class="mono tnum right {pnl_cls}">{_fmt_twd(h.get("pnl", 0), sign=True)}</td>
          <td class="mono tnum right {pnl_cls}">{_fmt_pct(h.get("pnl_pct"))}</td>
          <td class="mono tnum right"><div class="weight-bar-wrap"><div class="weight-bar-fill" style="width:{min(weight, 100):.1f}%"></div><span class="weight-bar-val">{weight:.1f}%</span></div></td>
        </tr>''')
    positions_html = f'''
    <div class="pfv2-section">
      {_sec_head("持倉明細", "POSITIONS", count=len(holdings))}
      <div class="pfv2-table-wrap">
        <table class="pfv2-table">
          <thead>
            <tr>
              <th class="left">ASSET</th>
              <th class="right">SHARES</th>
              <th class="right">AVG COST</th>
              <th class="right">LAST</th>
              <th class="right">TODAY</th>
              <th class="right">VALUE</th>
              <th class="right">P&amp;L</th>
              <th class="right">%</th>
              <th class="right">WEIGHT</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
    </div>
    '''

    # Weekly attribution bars (5 trading days)
    weekly_html = ""
    if weekly:
        max_abs = max((abs(w["pnl"]) for w in weekly), default=1) or 1
        bars = []
        for w in weekly:
            pnl = w["pnl"]
            pct = w["pct"]
            h_pct = abs(pnl) / max_abs * 100
            cls = _cls(pnl)
            direction = "up" if pnl >= 0 else "dn"
            bars.append(f'''
            <div class="wk-bar-col">
              <div class="wk-bar-stack">
                <div class="wk-bar-val mono tnum small {cls}">{_fmt_twd(pnl, sign=True)}</div>
                <div class="wk-bar-bg">
                  <div class="wk-bar-fill wk-bar-{direction}" style="height:{h_pct:.1f}%"></div>
                </div>
              </div>
              <div class="wk-bar-pct mono tnum small {cls}">{_fmt_pct(pct)}</div>
              <div class="wk-bar-day muted small mono">{w["date"][-5:]} · 週{w["weekday"]}</div>
            </div>''')
        weekly_html = f'''
        <div class="pfv2-section">
          {_sec_head("本週歸因", "WEEKLY ATTRIBUTION", meta=f"{len(weekly)} trading days")}
          <div class="wk-bars">{"".join(bars)}</div>
        </div>
        '''

    # Risk metrics panel (B- grade concentration, volatility, beta, sharpe)
    vol = risk.get("volatility_annualized_pct", 0) or 0
    dd_30 = risk.get("drawdown_30d_pct", 0) or 0
    dd_90 = risk.get("drawdown_90d_pct", 0) or 0
    dd_1y = risk.get("drawdown_1y_pct", 0) or 0
    # Concentration grade — based on largest single position weight
    max_weight = max(((h.get("value", 0) / total_val_incl_cash * 100) for h in holdings), default=0)
    conc_grade = _risk_grade(max_weight, [(30, "A"), (50, "B+"), (65, "B"), (75, "B-"), (85, "C"), (100, "D")])
    conc_tone = "up" if max_weight < 50 else ("amber" if max_weight < 75 else "dn")
    # Volatility grade
    vol_grade = _risk_grade(vol, [(10, "A"), (15, "B+"), (20, "B"), (25, "B-"), (35, "C"), (100, "D")])
    vol_tone = "up" if vol < 15 else ("amber" if vol < 25 else "dn")
    # Simple beta / sharpe estimates (placeholder — refine with proper regression later)
    ret_30d = s.get("ret_30d_pct") or 0
    beta_proxy = round((ret_30d / (bench.get("ret_30d_pct") or 1)) if bench.get("ret_30d_pct") else 1.0, 2)
    sharpe_proxy = round((s.get("ret_90d_pct") or 0) / (vol or 1), 2)

    risk_html = f'''
    <div class="pfv2-section">
      {_sec_head("風險指標", "RISK METRICS", meta="90D 統計")}
      <div class="risk-grid-v2">
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">集中度</div>
          <div class="risk-cell-val mono tnum {conc_tone}">{conc_grade}</div>
          <div class="risk-cell-sub muted mono small">MAX {max_weight:.1f}%</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">波動率 (年化)</div>
          <div class="risk-cell-val mono tnum {vol_tone}">{vol:.1f}%</div>
          <div class="risk-cell-sub muted mono small">等級 {vol_grade}</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">Beta (vs {html.escape(bench_sym)})</div>
          <div class="risk-cell-val mono tnum">{beta_proxy}</div>
          <div class="risk-cell-sub muted mono small">30D 相對敏感度</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">Sharpe 估值</div>
          <div class="risk-cell-val mono tnum">{sharpe_proxy}</div>
          <div class="risk-cell-sub muted mono small">90D 回報 ÷ 波動</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">最大回撤 30D</div>
          <div class="risk-cell-val mono tnum {_cls(dd_30)}">{dd_30:.2f}%</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">最大回撤 90D</div>
          <div class="risk-cell-val mono tnum {_cls(dd_90)}">{dd_90:.2f}%</div>
        </div>
        <div class="risk-cell-v2">
          <div class="risk-cell-lbl muted mono small">最大回撤 1Y</div>
          <div class="risk-cell-val mono tnum {_cls(dd_1y)}">{dd_1y:.2f}%</div>
        </div>
      </div>
    </div>
    '''

    coverage_html = render_coverage_section(pf, analysis, coverage)

    return f'''
<div class="pfv2-wrap">
  {kpi_cards}
  {render_big_chart(pf)}
  {positions_html}
  {weekly_html}
  {risk_html}
  {coverage_html}
</div>
'''


def render_coverage_section(pf: dict, analysis: dict | None,
                            coverage: dict | None) -> str:
    """Proactive coverage panel shown inside PORT tab.

    Three sub-blocks:
      1. SUPPLY-CHAIN ATLAS — 9 chains, ticker count + news mention count
      2. AI PICKS — coverage_suggestions from Gemini (today's new candidates
         grounded in today's news)
      3. GAP RADAR — missing_from_chains (tickers news talks about that aren't
         in any chain — human curation candidates)
      4. JUST ADDED — added_from_chains from today's audit run (transparency
         so the user can see what got auto-appended)
    """
    if not coverage and not (analysis and analysis.get("coverage_suggestions")):
        return ""

    chains_yaml = load_supply_chains()
    chain_totals = (coverage or {}).get("chain_totals") or {}
    suggestions = (analysis or {}).get("coverage_suggestions") or []
    gaps = (coverage or {}).get("missing_from_chains") or []
    added = (coverage or {}).get("added_from_chains") or []
    window_days = (coverage or {}).get("window_days", 7)

    # -- 1. SUPPLY-CHAIN ATLAS --
    atlas_cells: list[str] = []
    for slug, t in chain_totals.items():
        title = t.get("title", slug)
        count = t.get("unique_count", 0)
        layers = t.get("layer_count", 0)
        hot = t.get("mentioned_in_window", 0)
        pct = (hot / count * 100) if count else 0
        bar_cls = "up" if pct >= 50 else ("amber" if pct >= 25 else "muted")
        atlas_cells.append(f'''
        <div class="cov-chain-card">
          <div class="cov-chain-title mono">{html.escape(title)}</div>
          <div class="cov-chain-stats mono small">
            <span>{count} 檔</span>
            <span class="muted">·</span>
            <span>{layers} 層</span>
            <span class="muted">·</span>
            <span class="{bar_cls}">最近 {window_days} 日 {hot} 檔上新聞</span>
          </div>
          <div class="cov-chain-bar-bg">
            <div class="cov-chain-bar-fill {bar_cls}" style="width:{min(pct, 100):.0f}%"></div>
          </div>
        </div>''')
    atlas_block = ""
    if atlas_cells:
        atlas_block = f'''
        <div class="cov-sub">
          <div class="cov-sub-head">
            <span class="mono small">SUPPLY-CHAIN ATLAS</span>
            <span class="muted mono small">{len(chain_totals)} 條鏈 · 來源 supply_chains.yaml</span>
          </div>
          <div class="cov-chain-grid">
            {"".join(atlas_cells)}
          </div>
        </div>'''

    # -- 2. AI PICKS (coverage_suggestions) --
    PRIORITY_CLS = {"high": "dn", "medium": "amber", "low": "muted"}
    PRIORITY_LABEL = {"high": "高優先", "medium": "中優先", "low": "觀察中"}
    picks_rows: list[str] = []
    for s in suggestions:
        sym = s.get("symbol", "")
        name = s.get("name", "")
        chain = s.get("chain_slug", "")
        layer = s.get("layer_name", "")
        why = s.get("why_now", "")
        pri = (s.get("priority") or "medium").lower()
        pri_cls = PRIORITY_CLS.get(pri, "muted")
        pri_label = PRIORITY_LABEL.get(pri, pri)
        chain_title = chains_yaml.get(chain, {}).get("title", chain) if chain != "new" else "新題材（需新增鏈）"
        picks_rows.append(f'''
        <div class="cov-pick-row">
          <div class="cov-pick-head">
            <div class="cov-pick-sym">
              <strong class="mono">{html.escape(sym)}</strong>
              <span class="muted">{html.escape(name)}</span>
            </div>
            <span class="cov-pick-priority {pri_cls} mono small">{pri_label}</span>
          </div>
          <div class="cov-pick-chain mono small muted">
            歸類 → {html.escape(chain_title)} / {html.escape(layer)}
          </div>
          <div class="cov-pick-why">{html.escape(why)}</div>
        </div>''')
    picks_block = ""
    if picks_rows:
        picks_block = f'''
        <div class="cov-sub">
          <div class="cov-sub-head">
            <span class="mono small">AI COVERAGE PICKS · 今日新增追蹤候選</span>
            <span class="muted mono small">{len(picks_rows)} 檔 · Gemini 主動提議</span>
          </div>
          <div class="cov-pick-intro muted small">
            以下是 AI 掃過今日新聞後，覺得你該放進追蹤池但還沒納入的票。
            編輯 <code class="mono">supply_chains.yaml</code> 把認同的加進去，下次 build 就會自動進 simulator。
          </div>
          <div class="cov-pick-list">
            {"".join(picks_rows)}
          </div>
        </div>'''

    # -- 3. GAP RADAR --
    gap_rows: list[str] = []
    for g in gaps[:12]:
        sym = g.get("symbol", "")
        name = g.get("name", "")
        mentions = g.get("mentions", 0)
        in_pf = g.get("in_portfolio", False)
        badge = '<span class="cov-gap-badge-in mono small">已在追蹤池</span>' if in_pf else '<span class="cov-gap-badge-out mono small">未納入</span>'
        gap_rows.append(f'''
        <tr>
          <td><strong class="mono">{html.escape(sym)}</strong>
              <span class="muted">{html.escape(name)}</span></td>
          <td class="mono tnum right">{mentions}</td>
          <td class="right">{badge}</td>
        </tr>''')
    gap_block = ""
    if gap_rows:
        gap_block = f'''
        <div class="cov-sub">
          <div class="cov-sub-head">
            <span class="mono small">GAP RADAR · 新聞提到但追蹤池缺的票</span>
            <span class="muted mono small">{len(gaps)} 檔 · 最近 {window_days} 日</span>
          </div>
          <div class="cov-gap-intro muted small">
            這些 ticker 在最近 {window_days} 日新聞裡被多次提到，但不在任何供應鏈分類內。
            值得人工檢查：是否該加進 supply_chains.yaml 的某條鏈（或開一條新鏈）。
          </div>
          <table class="cov-gap-table">
            <thead>
              <tr>
                <th class="left">TICKER</th>
                <th class="right">提及次數</th>
                <th class="right">狀態</th>
              </tr>
            </thead>
            <tbody>{"".join(gap_rows)}</tbody>
          </table>
        </div>'''

    # -- 4. JUST ADDED --
    added_block = ""
    if added:
        # Group by chain for tidy display
        by_chain: dict[str, list[dict]] = {}
        for a in added:
            by_chain.setdefault(a.get("chain", ""), []).append(a)
        added_chain_cards: list[str] = []
        for slug, items in by_chain.items():
            chain_title = chains_yaml.get(slug, {}).get("title", slug)
            item_html = " · ".join(
                f'<span class="mono small">{html.escape(i["symbol"])} {html.escape(i["name"])}</span>'
                for i in items
            )
            added_chain_cards.append(f'''
            <div class="cov-added-row">
              <div class="cov-added-chain mono small muted">{html.escape(chain_title)}</div>
              <div class="cov-added-syms">{item_html}</div>
            </div>''')
        added_block = f'''
        <div class="cov-sub">
          <div class="cov-sub-head">
            <span class="mono small">JUST ADDED · 今日自動加入追蹤池</span>
            <span class="muted mono small">{len(added)} 檔 · audit_coverage.py</span>
          </div>
          <div class="cov-added-intro muted small">
            這些是 supply_chains.yaml 有但 portfolio.yaml 還沒收的票，今天跑 audit 時自動補進 simulator_universe。
          </div>
          <div class="cov-added-list">
            {"".join(added_chain_cards)}
          </div>
        </div>'''

    # Assembly
    meta_parts: list[str] = []
    if chain_totals:
        meta_parts.append(f"{len(chain_totals)} 條供應鏈")
    if suggestions:
        meta_parts.append(f"{len(suggestions)} 個 AI 候選")
    if gaps:
        meta_parts.append(f"{len(gaps)} 個缺口")
    meta_str = " · ".join(meta_parts) if meta_parts else "supply_chains.yaml"

    return f'''
    <div class="pfv2-section cov-section">
      {_sec_head("覆蓋範圍 · 主動佈局", "COVERAGE · PROACTIVE CURATION", meta=meta_str)}
      <div class="cov-lede muted small">
        這個區塊是「你不用每次提醒，系統就先替你做功課」的成果。Gemini 掃新聞、
        audit_coverage.py 掃供應鏈，每天主動提出該加入追蹤的票。
      </div>
      {picks_block}
      {gap_block}
      {atlas_block}
      {added_block}
    </div>
    '''


# ── Macro tab ────────────────────────────────────────────────────────────

_MACRO_GRID_DEF = [
    # (key, label_cn, label_en, fmt)
    ("twii",  "加權 ^TWII",   "TAIEX",        "{:.0f}"),
    ("spx",   "S&P 500",       "SPX",          "{:.0f}"),
    ("ndx",   "Nasdaq",        "NDX",          "{:.0f}"),
    ("sox",   "費半",          "SOX",          "{:.0f}"),
    ("n225",  "日經 225",      "N225",         "{:.0f}"),
    ("hsi",   "恆生指數",      "HSI",          "{:.0f}"),
]

_MACRO_RATES_DEF = [
    ("us10y", "美 10Y 殖利率", "US10Y",        "{:.2f}%"),
    ("vix",   "恐慌指數 VIX",  "VIX",          "{:.2f}"),
    ("dxy",   "美元指數 DXY",  "DXY",          "{:.2f}"),
    ("usdtwd","USD/TWD 匯率",  "FX",           "{:.3f}"),
    ("gold",  "黃金 GC=F",     "GOLD",         "{:.0f}"),
    ("oil",   "西德州原油",    "WTI",          "{:.2f}"),
    ("btc",   "比特幣",        "BTC",          "{:,.0f}"),
]


def _macro_cell(pf: dict, key: str, label_cn: str, label_en: str, fmt: str,
                history: dict | None = None) -> str:
    macro = pf.get("macro", {}) if pf else {}
    d = macro.get(key) or {}
    close = d.get("close")
    if close is None:
        return f'''
        <div class="macro-idx-card empty">
          <div class="macro-idx-head">
            <span class="mono">{html.escape(label_en)}</span>
            <span class="muted small">{html.escape(label_cn)}</span>
          </div>
          <div class="macro-idx-val muted">—</div>
        </div>'''
    day = d.get("day_change_pct") or 0
    ytd = d.get("ret_ytd")
    pct52 = d.get("pct_52w")
    # Build sparkline from history if available
    history = history or {}
    hist_key_map = {
        "twii": "^TWII", "spx": "^GSPC", "ndx": "^IXIC", "sox": "^SOX",
        "n225": "^N225", "hsi": "^HSI", "vix": "^VIX", "us10y": "^TNX",
        "dxy": "DX-Y.NYB", "usdtwd": "TWD=X", "gold": "GC=F",
        "oil": "CL=F", "btc": "BTC-USD",
    }
    spark_svg = ""
    hist = history.get(hist_key_map.get(key, key)) or []
    if len(hist) >= 2:
        spark_svg = _spark_svg(
            [{"c": r["close"]} for r in hist[-30:]],
            width=180, height=36,
            stroke=("var(--up)" if day >= 0 else "var(--dn)"),
        )
    ytd_html = f'<span class="macro-idx-ytd mono small {_cls(ytd)}">YTD {_fmt_pct(ytd, 1)}</span>' if ytd is not None else ""
    pct52_html = f'<span class="macro-idx-52w muted small mono">52w {pct52:.0f}%</span>' if pct52 is not None else ""
    return f'''
    <div class="macro-idx-card">
      <div class="macro-idx-head">
        <span class="mono">{html.escape(label_en)}</span>
        <span class="muted small">{html.escape(label_cn)}</span>
      </div>
      <div class="macro-idx-val mono tnum">{fmt.format(close)}</div>
      <div class="macro-idx-delta">
        <span class="mono tnum {_cls(day)}">{_fmt_pct(day, 2)}</span>
        {ytd_html}
        {pct52_html}
      </div>
      <div class="macro-idx-spark">{spark_svg}</div>
    </div>
    '''


def render_macro_tab(pf: dict, analysis: dict | None,
                     history: dict | None = None) -> str:
    """GUSHI-style Macro tab: AI 本週宏觀觀點 banner + GLOBAL INDICES grid +
    RATES · FX · COMMODITIES + RISK MAP."""
    if not pf:
        return '<p class="muted" style="padding:20px">無組合資料。</p>'

    # AI macro banner
    macro_ctx = (analysis or {}).get("macro_context", {}) or {}
    macro_narr = macro_ctx.get("narrative") or macro_ctx.get("summary") or ""
    macro_impact = macro_ctx.get("impact") or ""
    watchpoints = macro_ctx.get("watchpoints") or []
    wp_html = ""
    if watchpoints:
        wp_html = "<ul class='macro-wp-list'>" + "".join(
            f"<li>{_link_tickers(w)}</li>" for w in watchpoints[:5]
        ) + "</ul>"
    banner_html = ""
    if macro_narr:
        banner_html = f'''
        <div class="macro-banner">
          <div class="macro-banner-lbl mono">
            <span class="live-dot accent"></span>
            AI 本週宏觀觀點 · MACRO VIEW
          </div>
          <p class="macro-banner-text">{_link_tickers(macro_narr)}</p>
          {f'<p class="macro-banner-impact muted small">{_link_tickers(macro_impact)}</p>' if macro_impact else ""}
          {wp_html}
        </div>
        '''

    # Global indices grid
    idx_cells = "".join(
        _macro_cell(pf, key, cn, en, fmt, history)
        for key, cn, en, fmt in _MACRO_GRID_DEF
    )

    # Rates · FX · Commodities table
    rates_cells = "".join(
        _macro_cell(pf, key, cn, en, fmt, history)
        for key, cn, en, fmt in _MACRO_RATES_DEF
    )

    # Risk map — rule-based from macro values
    macro = pf.get("macro", {})
    vix_val = (macro.get("vix") or {}).get("close") or 0
    us10y_val = (macro.get("us10y") or {}).get("close") or 0
    dxy_val = (macro.get("dxy") or {}).get("close") or 0
    sox_day = (macro.get("sox") or {}).get("day_change_pct") or 0

    def _risk_row(name_cn: str, name_en: str, level: str, tone: str, detail: str) -> str:
        return f'''
        <div class="risk-map-row">
          <div class="risk-map-name">
            <span class="mono">{html.escape(name_en)}</span>
            <span class="muted small">{html.escape(name_cn)}</span>
          </div>
          <div class="risk-map-detail muted small">{html.escape(detail)}</div>
          <span class="risk-map-level risk-{tone} mono">{level}</span>
        </div>
        '''

    # VIX-based market risk
    if vix_val < 16:
        vix_level, vix_tone, vix_detail = "LOW", "low", f"VIX {vix_val:.1f} — 風險偏好高"
    elif vix_val < 22:
        vix_level, vix_tone, vix_detail = "MID", "mid", f"VIX {vix_val:.1f} — 中性區間"
    else:
        vix_level, vix_tone, vix_detail = "HIGH", "high", f"VIX {vix_val:.1f} — 避險升溫"

    # 10Y based rate policy risk
    if us10y_val < 3.8:
        r_level, r_tone, r_detail = "LOW", "low", f"US10Y {us10y_val:.2f}% — 利率環境寬鬆"
    elif us10y_val < 4.5:
        r_level, r_tone, r_detail = "MID", "mid", f"US10Y {us10y_val:.2f}% — 中性"
    else:
        r_level, r_tone, r_detail = "HIGH", "high", f"US10Y {us10y_val:.2f}% — 壓制成長股"

    # DXY-based EM risk
    if dxy_val == 0:
        dxy_level, dxy_tone, dxy_detail = "—", "mid", "資料不足"
    elif dxy_val < 100:
        dxy_level, dxy_tone, dxy_detail = "LOW", "low", f"DXY {dxy_val:.1f} — 弱美元利台股外資"
    elif dxy_val < 105:
        dxy_level, dxy_tone, dxy_detail = "MID", "mid", f"DXY {dxy_val:.1f} — 中性"
    else:
        dxy_level, dxy_tone, dxy_detail = "HIGH", "high", f"DXY {dxy_val:.1f} — 強美元抽離新興"

    # AI capex / SOX-based tech risk
    if sox_day > 1.5:
        sox_level, sox_tone, sox_detail = "LOW", "low", f"SOX +{sox_day:.1f}% — 半導體強勢"
    elif sox_day > -1.5:
        sox_level, sox_tone, sox_detail = "MID", "mid", f"SOX {sox_day:+.1f}% — 震盪"
    else:
        sox_level, sox_tone, sox_detail = "HIGH", "high", f"SOX {sox_day:+.1f}% — 半導體走弱警戒"

    risk_map_html = f'''
    <div class="pfv2-section">
      {_sec_head("風險地圖", "RISK MAP", meta="rule-based")}
      <div class="risk-map">
        {_risk_row("市場情緒", "MARKET FEAR", vix_level, vix_tone, vix_detail)}
        {_risk_row("Fed 政策", "RATE POLICY", r_level, r_tone, r_detail)}
        {_risk_row("美元指數", "USD STRENGTH", dxy_level, dxy_tone, dxy_detail)}
        {_risk_row("AI Capex", "SEMI CYCLE", sox_level, sox_tone, sox_detail)}
      </div>
    </div>
    '''

    return f'''
<div class="pfv2-wrap">
  <div class="macro-hero">
    <h1 class="macro-hero-title">全球宏觀脈動 <span class="sec-en mono">MACRO PULSE</span></h1>
    <p class="macro-hero-sub muted small">實時指數 · 利率 · 匯率 · 商品 · AI 風險地圖</p>
  </div>
  {banner_html}
  <div class="pfv2-section">
    {_sec_head("全球指數", "GLOBAL INDICES", count=len(_MACRO_GRID_DEF))}
    <div class="macro-idx-grid">{idx_cells}</div>
  </div>
  <div class="pfv2-section">
    {_sec_head("利率 · 匯率 · 商品", "RATES · FX · COMMODITIES", count=len(_MACRO_RATES_DEF))}
    <div class="macro-idx-grid">{rates_cells}</div>
  </div>
  {risk_map_html}
</div>
'''


# ── News tab with tier badges ─────────────────────────────────────────────

# Source → (tier, kind). Tier: T1 = 頂級財經媒體, T2 = 一般媒體, T3 = 聚合.
# Kind: BREAKING/BROKER/MEDIA/MACRO/DATA.
_NEWS_SOURCE_TIER = {
    # T1 財經頂級
    "Bloomberg": ("T1", "MEDIA"),
    "Reuters":   ("T1", "MEDIA"),
    "路透":      ("T1", "MEDIA"),
    "彭博":      ("T1", "MEDIA"),
    "FT":        ("T1", "MEDIA"),
    "WSJ":       ("T1", "MEDIA"),
    "華爾街日報": ("T1", "MEDIA"),
    "Nikkei":    ("T1", "MEDIA"),
    "日經":      ("T1", "MEDIA"),
    # T2 台灣主流
    "經濟日報":   ("T2", "MEDIA"),
    "工商時報":   ("T2", "MEDIA"),
    "自由時報":   ("T2", "MEDIA"),
    "聯合新聞網": ("T2", "MEDIA"),
    "中央社":     ("T2", "MEDIA"),
    "ETtoday":    ("T2", "MEDIA"),
    "鉅亨":       ("T2", "MEDIA"),
    "鉅亨網":     ("T2", "MEDIA"),
    "Anue鉅亨":  ("T2", "MEDIA"),
    "TVBS":       ("T2", "MEDIA"),
    "科技新報":   ("T2", "MEDIA"),
    "科技報橘":   ("T2", "MEDIA"),
    "數位時代":   ("T2", "MEDIA"),
    "CTEE":       ("T2", "MEDIA"),
    "MoneyDJ":    ("T2", "DATA"),
    "TechNews":   ("T2", "MEDIA"),
    # T2 券商 / 法人
    "富邦投顧":   ("T2", "BROKER"),
    "元大投顧":   ("T2", "BROKER"),
    "凱基投顧":   ("T2", "BROKER"),
    "群益投顧":   ("T2", "BROKER"),
    "國泰證券":   ("T2", "BROKER"),
    # T3 aggregators
    "Google News": ("T3", "MEDIA"),
    "Yahoo 新聞":  ("T3", "MEDIA"),
    "Yahoo":       ("T3", "MEDIA"),
}


def _classify_source(source: str) -> tuple[str, str]:
    """Return (tier, kind) for a news source."""
    src = (source or "").strip()
    if src in _NEWS_SOURCE_TIER:
        return _NEWS_SOURCE_TIER[src]
    # substring match
    for key, val in _NEWS_SOURCE_TIER.items():
        if key in src or src in key:
            return val
    # default fallback
    return ("T3", "MEDIA")


def _classify_article(title: str, source: str) -> tuple[str, str]:
    """Return (tier, kind) with kind overrides based on title keywords."""
    tier, kind = _classify_source(source)
    t = title or ""
    # Breaking news override
    if any(kw in t for kw in ("快訊", "即時", "BREAKING", "速報", "突發")):
        return ("T1", "BREAKING")
    # Broker report
    if any(kw in t for kw in ("目標價", "喊買", "評等", "調升", "調降", "投顧", "券商", "外資", "法人", "買進", "賣出")):
        return (tier, "BROKER") if tier != "T3" else ("T2", "BROKER")
    # Macro
    if any(kw in t for kw in ("Fed", "聯準會", "升息", "降息", "GDP", "CPI", "通膨", "失業率", "PMI", "FOMC", "央行", "利率")):
        return (tier, "MACRO")
    # Data / research
    if any(kw in t for kw in ("財報", "EPS", "營收", "毛利", "法說", "研究報告")):
        return (tier, "DATA")
    return (tier, kind)


def _parse_brief_articles(brief: dict, limit: int = 40) -> list[dict]:
    """Extract article entries from a brief's markdown content."""
    content = brief.get("content", "")
    entry_re = re.compile(
        r"^- \[([^\]]+)\]\(([^)]+)\)\s*·\s*([^·]+?)\s*·\s*(\d{2}-\d{2} \d{2}:\d{2})",
        re.MULTILINE,
    )
    summary_re = re.compile(r"^\s*>\s*(.+)", re.MULTILINE)
    section_re = re.compile(r"^### (.+)$", re.MULTILINE)

    # Map position → section heading
    sections: list[tuple[int, str]] = [
        (m.start(), m.group(1).strip()) for m in section_re.finditer(content)
    ]

    def _section_at(pos: int) -> str:
        cur = ""
        for s_pos, s_name in sections:
            if s_pos < pos:
                cur = s_name
            else:
                break
        return cur

    out: list[dict] = []
    for m in entry_re.finditer(content):
        title = m.group(1)
        url = m.group(2)
        source = m.group(3).strip()
        time_str = m.group(4)
        end_pos = m.end()
        next_bound = content.find("\n- [", end_pos)
        section_bound = content.find("\n## ", end_pos)
        bounds = [b for b in (next_bound, section_bound) if b > 0]
        bound = min(bounds) if bounds else len(content)
        following = content[end_pos:bound]
        sm = summary_re.search(following)
        summary = sm.group(1).strip() if sm else ""
        tier, kind = _classify_article(title, source)
        out.append({
            "date": brief["date"],
            "title": title,
            "url": url,
            "source": source,
            "time": time_str,
            "summary": summary,
            "tier": tier,
            "kind": kind,
            "section": _section_at(m.start()),
        })
        if len(out) >= limit:
            break
    return out


def render_news_tab(briefs: list[dict], pf: dict | None) -> str:
    """GUSHI-style News feed with tier badges and kind labels.

    Pulls articles from the most recent briefs (up to 3 days),
    classifies each with T1/T2/T3 tier + BREAKING/BROKER/MEDIA/MACRO/DATA kind.
    """
    if not briefs:
        return '<p class="muted" style="padding:20px">還沒有 brief。</p>'

    # Gather articles from the newest 3 briefs
    articles: list[dict] = []
    for b in briefs[:3]:
        articles.extend(_parse_brief_articles(b, limit=50))

    if not articles:
        return '<p class="muted" style="padding:20px">Brief 內找不到可解析的新聞條目。</p>'

    # Dedupe by URL, keep order
    seen = set()
    unique: list[dict] = []
    for a in articles:
        if a["url"] in seen:
            continue
        seen.add(a["url"])
        unique.append(a)

    # Sort by date desc, time desc
    unique.sort(key=lambda a: (a["date"], a["time"]), reverse=True)

    # Build ticker-name index for "related tickers" chips
    name_to_sym = dict(_TICKER_ALIAS)

    def _related_tickers(text: str, max_chips: int = 3) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        seen_syms: set[str] = set()
        for alias in sorted(name_to_sym.keys(), key=len, reverse=True):
            if len(alias) < 2:
                continue
            if alias in text:
                sym = name_to_sym[alias]
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                # Find display name
                disp = sym
                if pf:
                    for coll in ("holdings", "watchlist", "simulator_universe"):
                        for item in pf.get(coll, []) or []:
                            if item.get("symbol") == sym:
                                disp = item.get("name") or sym
                                break
                        if disp != sym:
                            break
                found.append((sym, disp))
                if len(found) >= max_chips:
                    break
        return found

    # Impact heuristic based on tier + kind
    def _impact(tier: str, kind: str) -> tuple[str, str]:
        if kind == "BREAKING":
            return "HIGH", "high"
        if tier == "T1":
            return "HIGH", "high"
        if kind in ("BROKER", "DATA", "MACRO"):
            return "MID", "mid"
        return "LOW", "low"

    # Filter pills: all / tiers / kinds
    tiers = sorted({a["tier"] for a in unique})
    kinds = sorted({a["kind"] for a in unique})

    tier_pills = "".join(
        f'<button class="news-pill mono" data-filter="tier:{t}">{t}</button>'
        for t in tiers
    )
    kind_pills = "".join(
        f'<button class="news-pill mono" data-filter="kind:{k}">{html.escape(k)}</button>'
        for k in kinds
    )

    # Card list
    cards: list[str] = []
    for a in unique[:80]:
        tier = a["tier"]
        kind = a["kind"]
        impact_label, impact_tone = _impact(tier, kind)
        related = _related_tickers(f"{a['title']} {a.get('summary', '')}", max_chips=3)
        chips_html = "".join(
            f'<a class="news-ticker-chip" href="holdings/{sym}.html">'
            f'<span class="mono">{html.escape(sym)}</span>'
            f'<span class="muted small">{html.escape(nm[:8])}</span></a>'
            for sym, nm in related
        )
        summary_html = (
            f'<p class="news-summary muted small">{html.escape(a["summary"][:160])}'
            f'{"…" if len(a.get("summary", "")) > 160 else ""}</p>'
            if a.get("summary") else ""
        )
        cards.append(f'''
        <article class="news-card" data-tier="{tier}" data-kind="{kind}">
          <div class="news-card-head">
            <span class="news-tier news-tier-{tier.lower()} mono">{tier}</span>
            <span class="news-kind news-kind-{kind.lower()} mono">{kind}</span>
            <span class="news-source mono">{html.escape(a["source"])}</span>
            <span class="news-time muted mono small">{html.escape(a["date"][-5:])} · {html.escape(a["time"][-5:])}</span>
            <span class="news-spacer"></span>
            <span class="news-impact news-impact-{impact_tone} mono">IMPACT · {impact_label}</span>
          </div>
          <a class="news-title" href="{html.escape(a["url"])}" target="_blank" rel="noopener">{html.escape(a["title"])}</a>
          {summary_html}
          {f'<div class="news-tickers">{chips_html}</div>' if chips_html else ""}
        </article>''')

    return f'''
<div class="pfv2-wrap">
  <div class="macro-hero">
    <h1 class="macro-hero-title">新聞即時流 <span class="sec-en mono">NEWS STREAM</span></h1>
    <p class="macro-hero-sub muted small">T1/T2 分級 · 券商 / 媒體 / 總經 / 財報 · 近 3 天</p>
  </div>
  <div class="news-filter-bar">
    <button class="news-pill active mono" data-filter="all">ALL · {len(unique)}</button>
    {tier_pills}
    {kind_pills}
  </div>
  <div class="news-feed">{"".join(cards)}</div>
</div>
<script>
(function() {{
  const bar = document.querySelector('.news-filter-bar');
  const cards = document.querySelectorAll('.news-feed .news-card');
  if (!bar) return;
  bar.addEventListener('click', (e) => {{
    const btn = e.target.closest('.news-pill');
    if (!btn) return;
    bar.querySelectorAll('.news-pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const f = btn.dataset.filter;
    cards.forEach(c => {{
      if (f === 'all') {{ c.style.display = ''; return; }}
      const [k, v] = f.split(':');
      c.style.display = (c.dataset[k] === v) ? '' : 'none';
    }});
  }});
}})();
</script>
'''


# ── Screener tab ────────────────────────────────────────────────────────

def render_screen_tab(pf: dict) -> str:
    """Client-side stock screener. Pulls all symbols from portfolio.yaml
    (holdings + watchlist + simulator_universe) and lets the user filter
    by price / returns / fundamentals / chips with live preset buttons."""
    init_ticker_alias(pf)

    # Collect unique tickers across collections
    seen: set[str] = set()
    rows: list[dict] = []
    for coll in ("holdings", "watchlist", "simulator_universe"):
        for it in pf.get(coll, []) or []:
            sym = it.get("symbol", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            fund = it.get("fundamentals") or {}
            chips = it.get("chips") or {}
            rows.append({
                "symbol": sym,
                "name": it.get("name", ""),
                "market": it.get("market", "TW"),
                "pillar": it.get("pillar", ""),
                "source": coll,
                "price": it.get("price"),
                "day_pct": it.get("day_change_pct"),
                "pct_52w": it.get("pct_52w"),
                "ret_7d": it.get("ret_7d"),
                "ret_30d": it.get("ret_30d"),
                "ret_90d": it.get("ret_90d"),
                "ret_ytd": it.get("ret_ytd"),
                "pe": fund.get("pe_ttm"),
                "pe_fwd": fund.get("pe_forward"),
                "eps": fund.get("eps_ttm"),
                "eps_growth": fund.get("earnings_growth"),
                "rev_growth": fund.get("rev_growth"),
                "roe": fund.get("roe"),
                "profit_margin": fund.get("profit_margin"),
                "pb": fund.get("pb"),
                "market_cap": fund.get("market_cap"),
                "sector": fund.get("sector") or "",
                "industry": fund.get("industry") or "",
                "foreign_5d": chips.get("foreign_5d"),
                "foreign_20d": chips.get("foreign_20d"),
                "foreign_streak": chips.get("foreign_streak"),
                "trust_5d": chips.get("trust_5d"),
                "trust_streak": chips.get("trust_streak"),
                "has_page": sym in _TICKER_ALIAS,
            })

    # Build unique list of sectors for the sector filter dropdown
    sectors = sorted({r["sector"] for r in rows if r["sector"]})

    # Embed as JSON so JS can filter/sort without a round-trip
    import json as _json
    data_json = _json.dumps(rows, ensure_ascii=False)

    # Server-render a default table (shows everything at first)
    def _num_attr(v):
        # JS uses data-attrs as strings; empty = no value = excluded from filter
        if v is None:
            return ""
        return f"{v}"

    def _fmt_pct_cell(v, digits=2):
        if v is None:
            return '<span class="muted small">—</span>'
        cls = "up" if v > 0 else ("dn" if v < 0 else "")
        return f'<span class="mono tnum {cls}">{v:+.{digits}f}%</span>'

    def _fmt_num(v, digits=1):
        if v is None:
            return '<span class="muted small">—</span>'
        return f'<span class="mono tnum">{v:.{digits}f}</span>'

    def _fmt_ratio_pct(v, digits=1):
        if v is None:
            return '<span class="muted small">—</span>'
        return f'<span class="mono tnum">{v*100:+.{digits}f}%</span>'

    def _link(r):
        if r["has_page"]:
            return f'<a class="sc-link mono" href="holdings/{r["symbol"]}.html">DEEP →</a>'
        return '<span class="muted small">—</span>'

    table_rows = []
    for r in rows:
        sym = r["symbol"]
        src_chip = {"holdings": "H", "watchlist": "W", "simulator_universe": "U"}.get(r["source"], "")
        src_cls = {"holdings": "sc-src-h", "watchlist": "sc-src-w", "simulator_universe": "sc-src-u"}.get(r["source"], "")
        price_str = f"{r['price']:.2f}" if r["price"] is not None else "—"
        table_rows.append(
            f'<tr class="sc-row" '
            f'data-sym="{html.escape(sym)}" '
            f'data-name="{html.escape(r["name"])}" '
            f'data-sector="{html.escape(r["sector"])}" '
            f'data-source="{r["source"]}" '
            f'data-day="{_num_attr(r["day_pct"])}" '
            f'data-52w="{_num_attr(r["pct_52w"])}" '
            f'data-ret7="{_num_attr(r["ret_7d"])}" '
            f'data-ret30="{_num_attr(r["ret_30d"])}" '
            f'data-ret90="{_num_attr(r["ret_90d"])}" '
            f'data-retytd="{_num_attr(r["ret_ytd"])}" '
            f'data-pe="{_num_attr(r["pe"])}" '
            f'data-eps="{_num_attr(r["eps"])}" '
            f'data-epsg="{_num_attr(r["eps_growth"])}" '
            f'data-revg="{_num_attr(r["rev_growth"])}" '
            f'data-roe="{_num_attr(r["roe"])}" '
            f'data-pm="{_num_attr(r["profit_margin"])}" '
            f'data-f5="{_num_attr(r["foreign_5d"])}" '
            f'data-f20="{_num_attr(r["foreign_20d"])}" '
            f'data-fstreak="{_num_attr(r["foreign_streak"])}" '
            f'data-tstreak="{_num_attr(r["trust_streak"])}" '
            f'>'
            f'<td><span class="sc-src {src_cls} mono small" title="H=持有 W=觀察 U=Universe">{src_chip}</span></td>'
            f'<td><div class="sc-sym mono">{html.escape(sym)}</div>'
            f'<div class="sc-name muted small">{html.escape(r["name"])}</div></td>'
            f'<td class="mono tnum small">{price_str}</td>'
            f'<td>{_fmt_pct_cell(r["day_pct"])}</td>'
            f'<td>{_fmt_num(r["pct_52w"], 0) if r["pct_52w"] is not None else _fmt_num(None)}</td>'
            f'<td>{_fmt_pct_cell(r["ret_7d"])}</td>'
            f'<td>{_fmt_pct_cell(r["ret_30d"])}</td>'
            f'<td>{_fmt_pct_cell(r["ret_90d"])}</td>'
            f'<td>{_fmt_pct_cell(r["ret_ytd"])}</td>'
            f'<td>{_fmt_num(r["pe"])}</td>'
            f'<td>{_fmt_ratio_pct(r["eps_growth"])}</td>'
            f'<td>{_fmt_ratio_pct(r["roe"])}</td>'
            f'<td>{_chips_cell(r["foreign_5d"])}</td>'
            f'<td>{_streak_badge(r["foreign_streak"])}</td>'
            f'<td class="muted small">{html.escape(r["sector"] or "—")}</td>'
            f'<td>{_link(r)}</td>'
            f'</tr>'
        )

    sector_options = "".join(
        f'<option value="{html.escape(s)}">{html.escape(s)}</option>' for s in sectors
    )

    total = len(rows)

    # Preset buttons — each has data-filters JSON that JS applies directly
    presets = [
        {
            "key": "growth",
            "label": "成長小將",
            "desc": "EPS 成長 >15% · ROE >15% · P/E <30",
            "filters": {"eps_growth_min": 15, "roe_min": 15, "pe_max": 30},
        },
        {
            "key": "dip",
            "label": "逢低布局",
            "desc": "52 週位階 <40% · ROE >10% · EPS 成長 >0%",
            "filters": {"pct_52w_max": 40, "roe_min": 10, "eps_growth_min": 0},
        },
        {
            "key": "foreign",
            "label": "外資進場",
            "desc": "外資 5 日買超 · 連續買超 ≥2 日",
            "filters": {"foreign_5d_min": 0, "foreign_streak_min": 2},
        },
        {
            "key": "strong",
            "label": "強者恆強",
            "desc": "90 日漲 >10% · YTD >0 · 52週位階 >60%",
            "filters": {"ret_90d_min": 10, "ret_ytd_min": 0, "pct_52w_min": 60},
        },
        {
            "key": "quality",
            "label": "品質好公司",
            "desc": "ROE >15% · 淨利率 >15% · EPS >0",
            "filters": {"roe_min": 15, "profit_margin_min": 15, "eps_min": 0},
        },
    ]
    preset_html = "".join(
        f'<button class="sc-preset" data-preset=\'{html.escape(_json.dumps(p["filters"]))}\'>'
        f'<span class="sc-preset-label mono">{html.escape(p["label"])}</span>'
        f'<span class="sc-preset-desc muted small">{html.escape(p["desc"])}</span>'
        f'</button>'
        for p in presets
    )

    return f'''
<section class="sc-wrap" aria-label="選股 Screener">
  <div class="sc-head">
    <div>
      <h2 class="sc-title">SCREENER · 選股過濾器</h2>
      <div class="muted small">{total} 檔來自你的 Portfolio + Watchlist + Universe · 資料來自 yfinance + TWSE/TPEx 三大法人</div>
    </div>
    <div class="sc-actions">
      <button id="sc-save" class="sc-btn mono" title="儲存當前過濾條件">SAVE</button>
      <button id="sc-load" class="sc-btn mono" title="載入最後儲存的條件">LOAD</button>
      <button id="sc-reset" class="sc-btn sc-btn-ghost mono">RESET</button>
    </div>
  </div>

  <div class="sc-presets">
    <div class="sc-presets-label mono small muted">快速套用 · QUICK PRESETS</div>
    <div class="sc-preset-row">{preset_html}</div>
  </div>

  <div class="sc-filters">
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">搜尋</label>
      <input type="text" id="sc-search" class="sc-input" placeholder="代號 / 名稱">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">52W 位階</label>
      <div class="sc-range">
        <input type="number" id="sc-52w-min" class="sc-input-num" placeholder="min" min="0" max="100">
        <span class="muted small">–</span>
        <input type="number" id="sc-52w-max" class="sc-input-num" placeholder="max" min="0" max="100">
      </div>
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">YTD % 至少</label>
      <input type="number" id="sc-ytd-min" class="sc-input-num" placeholder="-20">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">90日 % 至少</label>
      <input type="number" id="sc-90d-min" class="sc-input-num" placeholder="0">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">P/E 最大</label>
      <input type="number" id="sc-pe-max" class="sc-input-num" placeholder="30">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">EPS % 至少</label>
      <input type="number" id="sc-epsg-min" class="sc-input-num" placeholder="15">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">ROE % 至少</label>
      <input type="number" id="sc-roe-min" class="sc-input-num" placeholder="15">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">淨利率 % 至少</label>
      <input type="number" id="sc-pm-min" class="sc-input-num" placeholder="10">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">外資 5 日</label>
      <select id="sc-f5" class="sc-input">
        <option value="">不限</option>
        <option value="buy">買超</option>
        <option value="sell">賣超</option>
      </select>
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">外資連續 (≥)</label>
      <input type="number" id="sc-fstreak-min" class="sc-input-num" placeholder="2">
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">產業</label>
      <select id="sc-sector" class="sc-input">
        <option value="">所有產業</option>
        {sector_options}
      </select>
    </div>
    <div class="sc-filter-group">
      <label class="sc-filter-label mono small">清單</label>
      <select id="sc-source" class="sc-input">
        <option value="">全部</option>
        <option value="holdings">持股</option>
        <option value="watchlist">觀察清單</option>
        <option value="simulator_universe">Universe</option>
      </select>
    </div>
  </div>

  <div class="sc-count-row">
    <span class="sc-count mono" id="sc-count">{total} 檔</span>
    <span class="sc-sort-lbl mono small muted">點擊欄位標題可排序</span>
  </div>

  <div class="sc-table-wrap">
    <table class="sc-table">
      <thead>
        <tr>
          <th class="mono small" title="H=持有 W=觀察 U=Universe">來源</th>
          <th class="mono small" data-sort="name">STOCK</th>
          <th class="mono small" data-sort="price">價格</th>
          <th class="mono small" data-sort="day">今日</th>
          <th class="mono small" data-sort="52w">52W</th>
          <th class="mono small" data-sort="ret7">7D</th>
          <th class="mono small" data-sort="ret30">30D</th>
          <th class="mono small" data-sort="ret90">90D</th>
          <th class="mono small" data-sort="retytd">YTD</th>
          <th class="mono small" data-sort="pe">P/E</th>
          <th class="mono small" data-sort="epsg">EPS%</th>
          <th class="mono small" data-sort="roe">ROE</th>
          <th class="mono small" data-sort="f5">外資5日</th>
          <th class="mono small" data-sort="fstreak">連續</th>
          <th class="mono small" data-sort="sector">產業</th>
          <th class="mono small"></th>
        </tr>
      </thead>
      <tbody id="sc-tbody">{"".join(table_rows)}</tbody>
    </table>
    <div id="sc-empty" class="sc-empty muted" style="display:none">
      沒有符合條件的個股。試試放寬條件或 RESET。
    </div>
  </div>

  <div class="sc-foot muted small">
    <strong>怎麼用這個工具？</strong>先用 Preset 測試一組條件，看哪些股票被篩出來。點 DEEP → 進深度頁看 AI 評分 / 基本面 / 籌碼面。
    篩選器是「研究候選清單」不是「買進訊號」；基本面數字來自 yfinance，TW 個股有時會延遲或缺漏。
  </div>
</section>

<script>
(function() {{
  const tbody = document.getElementById('sc-tbody');
  const rows = Array.from(tbody.querySelectorAll('tr.sc-row'));
  const countEl = document.getElementById('sc-count');
  const emptyEl = document.getElementById('sc-empty');
  const controls = {{
    search: document.getElementById('sc-search'),
    p52_min: document.getElementById('sc-52w-min'),
    p52_max: document.getElementById('sc-52w-max'),
    ytd_min: document.getElementById('sc-ytd-min'),
    r90_min: document.getElementById('sc-90d-min'),
    pe_max:  document.getElementById('sc-pe-max'),
    epsg_min: document.getElementById('sc-epsg-min'),
    roe_min: document.getElementById('sc-roe-min'),
    pm_min:  document.getElementById('sc-pm-min'),
    f5:      document.getElementById('sc-f5'),
    fstreak_min: document.getElementById('sc-fstreak-min'),
    sector:  document.getElementById('sc-sector'),
    source:  document.getElementById('sc-source'),
  }};
  const STORAGE_KEY = 'screener_filters_v1';

  function num(attr) {{ return attr === '' ? null : Number(attr); }}

  function filterRow(tr) {{
    const d = tr.dataset;
    const q = controls.search.value.trim().toLowerCase();
    if (q) {{
      const hay = (d.sym + ' ' + d.name).toLowerCase();
      if (!hay.includes(q)) return false;
    }}
    const p52 = num(d['52w']);
    if (controls.p52_min.value !== '' && (p52 === null || p52 < Number(controls.p52_min.value))) return false;
    if (controls.p52_max.value !== '' && (p52 === null || p52 > Number(controls.p52_max.value))) return false;

    const ytd = num(d.retytd);
    if (controls.ytd_min.value !== '' && (ytd === null || ytd < Number(controls.ytd_min.value))) return false;

    const r90 = num(d.ret90);
    if (controls.r90_min.value !== '' && (r90 === null || r90 < Number(controls.r90_min.value))) return false;

    const pe = num(d.pe);
    if (controls.pe_max.value !== '' && (pe === null || pe > Number(controls.pe_max.value))) return false;

    // EPS growth is stored as ratio (0.15 = 15%) — user enters 15
    const epsg = num(d.epsg);
    if (controls.epsg_min.value !== '' && (epsg === null || epsg * 100 < Number(controls.epsg_min.value))) return false;

    const roe = num(d.roe);
    if (controls.roe_min.value !== '' && (roe === null || roe * 100 < Number(controls.roe_min.value))) return false;

    const pm = num(d.pm);
    if (controls.pm_min.value !== '' && (pm === null || pm * 100 < Number(controls.pm_min.value))) return false;

    const f5 = num(d.f5);
    if (controls.f5.value === 'buy'  && !(f5 !== null && f5 > 0)) return false;
    if (controls.f5.value === 'sell' && !(f5 !== null && f5 < 0)) return false;

    const fstreak = num(d.fstreak);
    if (controls.fstreak_min.value !== '' && (fstreak === null || fstreak < Number(controls.fstreak_min.value))) return false;

    if (controls.sector.value && d.sector !== controls.sector.value) return false;
    if (controls.source.value && d.source !== controls.source.value) return false;

    return true;
  }}

  function applyFilters() {{
    let visible = 0;
    for (const tr of rows) {{
      if (filterRow(tr)) {{ tr.style.display = ''; visible++; }}
      else tr.style.display = 'none';
    }}
    countEl.textContent = visible + ' / ' + rows.length + ' 檔';
    emptyEl.style.display = visible === 0 ? '' : 'none';
  }}

  Object.values(controls).forEach(el => {{
    el.addEventListener('input', applyFilters);
    el.addEventListener('change', applyFilters);
  }});

  // Presets — apply JSON filters to inputs
  document.querySelectorAll('.sc-preset').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const f = JSON.parse(btn.dataset.preset);
      // Clear first
      for (const k in controls) {{
        if (controls[k].tagName === 'SELECT') controls[k].value = '';
        else controls[k].value = '';
      }}
      if (f.pct_52w_min  != null) controls.p52_min.value = f.pct_52w_min;
      if (f.pct_52w_max  != null) controls.p52_max.value = f.pct_52w_max;
      if (f.ret_ytd_min  != null) controls.ytd_min.value = f.ret_ytd_min;
      if (f.ret_90d_min  != null) controls.r90_min.value = f.ret_90d_min;
      if (f.pe_max       != null) controls.pe_max.value = f.pe_max;
      if (f.eps_growth_min != null) controls.epsg_min.value = f.eps_growth_min;
      if (f.roe_min      != null) controls.roe_min.value = f.roe_min;
      if (f.profit_margin_min != null) controls.pm_min.value = f.profit_margin_min;
      if (f.foreign_5d_min != null) controls.f5.value = f.foreign_5d_min >= 0 ? 'buy' : 'sell';
      if (f.foreign_streak_min != null) controls.fstreak_min.value = f.foreign_streak_min;
      // Visual feedback
      document.querySelectorAll('.sc-preset').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilters();
    }});
  }});

  // Save / Load / Reset
  document.getElementById('sc-save').addEventListener('click', () => {{
    const state = {{}};
    for (const k in controls) state[k] = controls[k].value;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    alert('已儲存過濾條件。');
  }});
  document.getElementById('sc-load').addEventListener('click', () => {{
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {{ alert('沒有已儲存的過濾條件。'); return; }}
    try {{
      const state = JSON.parse(raw);
      for (const k in state) if (controls[k]) controls[k].value = state[k];
      applyFilters();
    }} catch (e) {{ alert('儲存的條件無法讀取。'); }}
  }});
  document.getElementById('sc-reset').addEventListener('click', () => {{
    for (const k in controls) controls[k].value = '';
    document.querySelectorAll('.sc-preset.active').forEach(b => b.classList.remove('active'));
    applyFilters();
  }});

  // Sortable columns — click TH to toggle asc/desc
  const ths = document.querySelectorAll('.sc-table thead th[data-sort]');
  const sortState = {{ col: null, dir: 1 }};
  function numericKey(tr, key) {{
    const v = tr.dataset[key];
    if (v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }}
  ths.forEach(th => {{
    th.addEventListener('click', () => {{
      const k = th.dataset.sort;
      if (sortState.col === k) sortState.dir *= -1;
      else {{ sortState.col = k; sortState.dir = 1; }}
      ths.forEach(x => x.classList.remove('sc-sort-asc', 'sc-sort-desc'));
      th.classList.add(sortState.dir > 0 ? 'sc-sort-asc' : 'sc-sort-desc');
      const sorted = [...rows].sort((a, b) => {{
        if (k === 'name' || k === 'sector') {{
          const av = (a.dataset[k] || a.dataset.name || '').toLowerCase();
          const bv = (b.dataset[k] || b.dataset.name || '').toLowerCase();
          return av.localeCompare(bv) * sortState.dir;
        }}
        const av = numericKey(a, k);
        const bv = numericKey(b, k);
        // null goes to bottom regardless of direction
        if (av === null && bv === null) return 0;
        if (av === null) return 1;
        if (bv === null) return -1;
        return (av - bv) * sortState.dir;
      }});
      sorted.forEach(tr => tbody.appendChild(tr));
    }});
  }});
}})();
</script>
'''


# ── Chat tab (stub) ─────────────────────────────────────────────────────

def render_chat_tab(pf: dict | None, analysis: dict | None) -> str:
    """Honest chat tab — this dashboard has NO live LLM backend.

    Shows:
      1. Big CTA to ask Claude Code (the tool Ian built this with) directly
      2. Daily FAQ — pre-generated Q&A from analysis.faq (if provided by analyze.py)
         fallback: derive questions from holdings / today's opportunities
      3. Context bundle copy-buttons — one-click copy a prompt with
         portfolio + today's analysis JSON for pasting into external LLMs
    """
    import json as _json
    init_ticker_alias(pf)
    pf = pf or {}
    analysis = analysis or {}

    # ---- FAQ items ----
    # Preferred: AI-generated faq array in analysis.json with {q, a} dicts.
    faq: list[dict] = list(analysis.get("faq") or [])

    # Fallback: synthesize a handful from available data if empty.
    if not faq:
        holdings = pf.get("holdings") or []
        opps = analysis.get("opportunities") or []
        mb = analysis.get("morning_brief") or {}

        if mb.get("one_liner") or mb.get("summary"):
            faq.append({
                "q": "今天市場的一句話摘要？",
                "a": mb.get("one_liner") or mb.get("summary") or "",
                "tag": "市場",
            })
        # Biggest holding by value
        top = sorted(holdings, key=lambda h: h.get("value", 0), reverse=True)[:1]
        for h in top:
            faq.append({
                "q": f"{h['symbol']} {h.get('name','')} 今天表現如何？",
                "a": (
                    f"今日 {h.get('day_change_pct', 0):+.2f}%，目前市值 "
                    f"${h.get('value', 0):,.0f}，累計損益 "
                    f"{h.get('pnl_pct', 0):+.2f}%。"
                    f" 52 週位階 {h.get('pct_52w', 0):.0f}%。"
                ),
                "tag": "個股",
            })
        # Top opportunity
        for o in opps[:1]:
            faq.append({
                "q": f"{_strip_leading_emoji(o.get('theme','題材'))} 這個題材怎麼看？",
                "a": (
                    o.get("headline") or o.get("thesis")
                    or o.get("why") or ""
                )[:280],
                "tag": "題材",
            })
        # Risk
        risks = analysis.get("top_risks") or []
        if risks:
            r = risks[0]
            if isinstance(r, dict):
                faq.append({
                    "q": "今天要注意什麼風險？",
                    "a": r.get("description") or r.get("summary") or r.get("title") or "",
                    "tag": "風險",
                })
            else:
                faq.append({"q": "今天要注意什麼風險？", "a": str(r), "tag": "風險"})

    # Cap to 8 for readability
    faq = faq[:8]

    # ---- Context bundle (copyable prompt) ----
    # Trim to essentials for paste-ability
    summary = pf.get("summary") or {}
    ctx = {
        "date": pf.get("as_of", ""),
        "portfolio_summary": {
            "total_value_twd": summary.get("total_value_twd"),
            "day_pnl_pct": summary.get("day_pnl_pct"),
            "total_pnl_pct": summary.get("total_pnl_pct"),
            "alpha_vs_benchmark_pct": summary.get("alpha_vs_benchmark_pct"),
        },
        "holdings": [
            {
                "symbol": h.get("symbol"),
                "name": h.get("name"),
                "shares": h.get("shares"),
                "cost_basis": h.get("cost_basis"),
                "price": h.get("price"),
                "pnl_pct": h.get("pnl_pct"),
                "day_change_pct": h.get("day_change_pct"),
                "pct_52w": h.get("pct_52w"),
            }
            for h in (pf.get("holdings") or [])
        ],
        "today_one_liner": (analysis.get("morning_brief") or {}).get("one_liner", ""),
        "opportunities_top3": [
            {
                "theme": _strip_leading_emoji(o.get("theme", "")),
                "stage": o.get("stage"),
                "confidence_pct": o.get("confidence_pct"),
                "crowding_pct": o.get("crowding_pct"),
                "lead_stocks": [ls.get("symbol") for ls in (o.get("lead_stocks") or [])[:5]],
            }
            for o in (analysis.get("opportunities") or [])[:3]
        ],
    }
    ctx_json = _json.dumps(ctx, ensure_ascii=False, indent=2)

    prompt_templates = [
        {
            "key": "risk",
            "title": "問：組合風險 & 下一筆怎麼配",
            "body": (
                "我是 Taiwan 的小白投資者，偏好雪球式滾存（先累積小部位→有信心再放大）。"
                "以下是我今天的組合快照與 AI 分析。請幫我回答：\n"
                "1. 我的組合目前最大風險是什麼？\n"
                "2. 下一筆 NT$5,000–10,000 建議怎麼配？\n"
                "3. 有哪一檔已偏離我的「雪球」策略（例如漲太多／位階太高）？\n\n"
                "請直接點名 ticker，不要給籠統建議。"
            ),
        },
        {
            "key": "theme",
            "title": "問：某個題材值不值得跟",
            "body": (
                "我看到今天 AI 分析有列出這些題材機會。我想深入了解：\n"
                "【填你有興趣的題材名稱】\n"
                "請幫我判斷：\n"
                "1. 這個題材目前在什麼階段（夢階段 / 營收驗證 / 本夢比）？\n"
                "2. 領先股 vs. 落後股分別是誰？為什麼？\n"
                "3. 如果我要跟，哪一檔位階最合理？哪一檔最貴？\n"
                "4. 最大風險是什麼（退潮時會跌多少）？"
            ),
        },
        {
            "key": "learn",
            "title": "問：把一個財報術語講給我懂",
            "body": (
                "我是小白，請用「具體例子＋我熟悉的公司（TSMC / 0050 等）」解釋下面這個概念，"
                "不要只給定義。\n\n"
                "【填你想懂的詞：P/E、ROE、EPS 成長、本益比成長比 PEG、毛利率、營業利益率 …】\n\n"
                "最後請告訴我：這個指標是「越高越好」還是「要看情境」？什麼時候會誤判？"
            ),
        },
    ]

    # Encode templates + context into data-* for clipboard JS
    prompt_cards = "".join(
        f'''<div class="ch-card">
          <div class="ch-card-head">
            <span class="ch-card-title mono">{html.escape(t["title"])}</span>
            <button class="ch-copy-btn mono small" data-prompt-key="{t["key"]}">COPY →</button>
          </div>
          <textarea class="ch-card-body mono" id="ch-prompt-{t["key"]}" readonly>{html.escape(t["body"])}

---
以下是今日組合 + 分析資料（JSON）：
{html.escape(ctx_json)}</textarea>
        </div>''' for t in prompt_templates
    )

    # FAQ cards
    if faq:
        faq_cards = "".join(
            f'''<details class="ch-faq">
              <summary>
                <span class="ch-faq-tag mono small">{html.escape(item.get("tag","Q"))}</span>
                <span class="ch-faq-q">{html.escape(item.get("q",""))}</span>
              </summary>
              <div class="ch-faq-a">{_link_tickers(item.get("a",""))}</div>
            </details>'''
            for item in faq
        )
    else:
        faq_cards = '<div class="muted small">今日 AI 尚未生成 FAQ。排程執行後會出現 5–8 題常見問題。</div>'

    # "Ask Claude Code" CTA
    return f'''
<div class="ch-shell">

  <section class="ch-cta-box">
    <div class="ch-cta-head">
      <div class="ch-cta-icon">{_icon("ai", 28)}</div>
      <div>
        <h2 class="ch-cta-title">要「對話」請回 Claude Code</h2>
        <p class="ch-cta-sub muted">
          這個 dashboard 是一份「每日快照」，不是 chatbot。
          真正的分析對話發生在 <strong>Claude Code</strong>（你 build 這個 repo 的地方）。
          那裡能讀你的全部資料、跑程式、改你的 portfolio.yaml —— 比這裡的靜態 UI 強很多。
        </p>
      </div>
    </div>
    <div class="ch-cta-buttons">
      <a class="ch-cta-btn ch-cta-btn-primary mono" href="https://claude.ai/code" target="_blank" rel="noopener">
        打開 Claude Code →
      </a>
      <a class="ch-cta-btn mono" href="https://claude.ai/new" target="_blank" rel="noopener">
        或用 Claude.ai（貼下面 prompt）
      </a>
      <a class="ch-cta-btn mono" href="https://chatgpt.com" target="_blank" rel="noopener">
        或 ChatGPT
      </a>
    </div>
  </section>

  <section class="ch-section">
    <div class="ch-section-head">
      <h3 class="ch-section-title mono">TODAY'S FAQ · 今日重點 Q&amp;A</h3>
      <span class="muted small mono">由今日 AI 分析自動抽取</span>
    </div>
    <div class="ch-faq-list">{faq_cards}</div>
  </section>

  <section class="ch-section">
    <div class="ch-section-head">
      <h3 class="ch-section-title mono">CONTEXT PROMPTS · 一鍵複製去問 AI</h3>
      <span class="muted small mono">自動打包你的組合 + 今日分析</span>
    </div>
    <p class="muted small ch-section-sub">
      複製 → 貼到 Claude / ChatGPT → 你會得到「有你資料」的具體答案，不是空泛回答。
      【方括號】裡的地方改成你的問題再送出。
    </p>
    <div class="ch-card-grid">{prompt_cards}</div>
  </section>

  <section class="ch-section ch-why">
    <div class="ch-section-head">
      <h3 class="ch-section-title mono">WHY NO LIVE CHAT · 為什麼這裡不做即時對話</h3>
    </div>
    <ul class="ch-why-list">
      <li><strong>成本</strong>：LLM API 按 token 計費，每次問答要完整吃一次 portfolio 的 context，穩定訪客下每日 $2–10 美金，不值得。</li>
      <li><strong>時效</strong>：dashboard 是靜態站（GitHub Pages 每日排程重 build），沒有 backend 可以即時呼叫 API。</li>
      <li><strong>品質</strong>：Claude Code 能執行程式、讀你的原始 JSON、修改你的 portfolio.yaml，那才是你最需要的互動 —— 做到這邊就夠了。</li>
    </ul>
  </section>

</div>

<script>
(function() {{
  document.querySelectorAll('.ch-copy-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const key = btn.dataset.promptKey;
      const ta = document.getElementById('ch-prompt-' + key);
      if (!ta) return;
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      try {{
        navigator.clipboard.writeText(ta.value).then(() => {{
          const orig = btn.textContent;
          btn.textContent = 'COPIED ✓';
          btn.classList.add('ch-copy-success');
          setTimeout(() => {{ btn.textContent = orig; btn.classList.remove('ch-copy-success'); }}, 1800);
        }});
      }} catch (e) {{
        document.execCommand('copy');
        btn.textContent = 'COPIED ✓';
      }}
    }});
  }});
}})();
</script>
'''


def render_today_focus(pf: dict, coverage_report: dict | None,
                       analysis: dict | None) -> str:
    """Top-of-page 3-column panel answering the user's 2026-04-19 critique:
    「我整個看完也不知道我該關注哪些產業 哪些是目前價格低但預計會上漲」.

    Splits today's watchable names into three easy buckets:
      💎 便宜將漲  — PE ≤ 20 + 52週位階 ≤ 65% + 新聞熱度 ≥ 2 (合理估值 + 未高位 + 有題材)
      🔥 正在上漲  — ret_7d ≥ +3% OR (ret_30d ≥ +10% AND 52週位階 ≥ 60%)
      ⚠️ 警戒追高  — 52週位階 ≥ 90% AND (PE ≥ 35 OR ret_30d ≥ +20%)

    All data already in prices.json / coverage_report.json — no extra Gemini
    call. User can glance at this and know what to open next.
    """
    if not pf:
        return ""

    # Combined universe to rank — de-dupes by symbol
    universe: list[dict] = []
    seen: set[str] = set()
    for coll in ("holdings", "watchlist", "simulator_universe"):
        for it in pf.get(coll, []) or []:
            sym = it.get("symbol")
            if sym and sym not in seen:
                seen.add(sym)
                universe.append(it)

    # News mention counts (last-7-day heat)
    news_freq: dict[str, int] = {}
    if coverage_report:
        for sym, cnt in (coverage_report.get("news_frequency") or {}).items():
            try:
                news_freq[str(sym)] = int(cnt)
            except (TypeError, ValueError):
                continue

    cheap: list[dict] = []   # 便宜將漲
    hot: list[dict] = []     # 正在上漲
    danger: list[dict] = []  # 警戒追高

    for it in universe:
        sym = it.get("symbol") or ""
        if not sym or sym.startswith("^"):
            continue
        # Skip macro/FX tickers
        yf_t = it.get("yf_ticker") or sym
        if "=" in yf_t or yf_t.endswith("-USD"):
            continue

        price = it.get("price")
        ret_7d = it.get("ret_7d")
        ret_30d = it.get("ret_30d")
        ret_ytd = it.get("ret_ytd")
        pct_52w = it.get("pct_52w")
        fund = it.get("fundamentals") or {}
        pe = fund.get("pe_ttm")
        eps = fund.get("eps_ttm")
        earn_g = fund.get("earnings_growth")
        rev_g = fund.get("rev_growth")
        growth = earn_g if earn_g is not None else rev_g
        mentions = news_freq.get(sym, 0)

        # Build a one-liner reason so user understands why we flagged it
        stub = {
            "symbol": sym,
            "name": it.get("name", ""),
            "price": price,
            "pct_52w": pct_52w,
            "pe": pe,
            "eps": eps,
            "ret_7d": ret_7d,
            "ret_30d": ret_30d,
            "ret_ytd": ret_ytd,
            "growth": growth,
            "mentions": mentions,
        }

        # --- Bucket 1: 便宜將漲 ---
        # PE 合理 + 未高位 + 新聞有題材 + (可選) 正成長
        if (pe is not None and 0 < pe <= 20
                and pct_52w is not None and pct_52w <= 65
                and mentions >= 1):
            # Score: lower PE + lower 52w position + more mentions = higher priority
            # (bonus for positive growth)
            score = (25 - pe) * 2 + (70 - pct_52w) + mentions * 3
            if growth is not None and growth > 0:
                score += growth * 20
            stub["_score"] = score
            stub["_reason"] = f"PE {pe:.1f} · 52週位階 {pct_52w:.0f}% · 新聞 ×{mentions}"
            cheap.append(stub)

        # --- Bucket 2: 正在上漲 ---
        elif ((ret_7d is not None and ret_7d >= 3)
              or (ret_30d is not None and ret_30d >= 10
                  and pct_52w is not None and pct_52w >= 60)):
            score = (ret_7d or 0) * 3 + (ret_30d or 0) + mentions * 2
            stub["_score"] = score
            r7 = f"{ret_7d:+.1f}%" if ret_7d is not None else "—"
            r30 = f"{ret_30d:+.1f}%" if ret_30d is not None else "—"
            stub["_reason"] = f"7日 {r7} · 30日 {r30} · 52週位階 {(pct_52w or 0):.0f}%"
            hot.append(stub)

        # --- Bucket 3: 警戒追高 ---
        if (pct_52w is not None and pct_52w >= 90
                and ((pe is not None and pe >= 35)
                     or (ret_30d is not None and ret_30d >= 20)
                     or (ret_ytd is not None and ret_ytd >= 60))):
            danger_score = pct_52w + (pe or 0) + (ret_30d or 0)
            stub_d = dict(stub)
            stub_d["_score"] = danger_score
            pe_str = f"PE {pe:.0f}" if pe is not None else "PE —"
            r30 = f"{ret_30d:+.0f}%" if ret_30d is not None else "—"
            stub_d["_reason"] = f"位階 {pct_52w:.0f}% · {pe_str} · 30日 {r30}"
            danger.append(stub_d)

    # Rank & cap
    cheap.sort(key=lambda x: x["_score"], reverse=True)
    hot.sort(key=lambda x: x["_score"], reverse=True)
    danger.sort(key=lambda x: x["_score"], reverse=True)
    cheap = cheap[:5]
    hot = hot[:5]
    danger = danger[:5]

    # If every bucket is empty, skip the panel (don't render empty chrome)
    if not (cheap or hot or danger):
        return ""

    def _card_col(title_emoji: str, title: str, subtitle: str,
                  items: list[dict], tone_cls: str, empty_msg: str) -> str:
        if not items:
            rows_html = f'<div class="tf-empty muted small">{html.escape(empty_msg)}</div>'
        else:
            rows = []
            for it in items:
                sym = it["symbol"]
                nm = it["name"] or ""
                reason = it.get("_reason", "")
                price = it.get("price")
                p_str = f"{price:.2f}" if price is not None else "—"
                has_page = sym in _TICKER_ALIAS
                sym_html = (
                    f'<a href="holdings/{sym}.html" class="tf-sym">'
                    f'<strong class="mono">{html.escape(sym)}</strong></a>'
                    if has_page else
                    f'<strong class="mono">{html.escape(sym)}</strong>'
                )
                rows.append(f'''
                <li class="tf-row">
                  <div class="tf-row-top">
                    {sym_html}
                    <span class="tf-name">{html.escape(nm)}</span>
                    <span class="tf-price mono tnum">{p_str}</span>
                  </div>
                  <div class="tf-reason muted small">{html.escape(reason)}</div>
                </li>''')
            rows_html = f'<ul class="tf-list">{"".join(rows)}</ul>'
        return f'''
        <div class="tf-col tf-col-{tone_cls}">
          <div class="tf-col-head">
            <div class="tf-col-title">{title_emoji} {html.escape(title)}</div>
            <div class="tf-col-sub muted small">{html.escape(subtitle)}</div>
          </div>
          {rows_html}
        </div>'''

    return f'''
    <section class="today-focus">
      <div class="tf-head">
        <div>
          <div class="tf-kicker mono small">TODAY'S FOCUS</div>
          <h2 class="tf-title">今日該關注什麼？</h2>
        </div>
        <p class="tf-lead muted small">
          直接看三欄：哪些<strong>便宜有題材</strong>可以試水、哪些<strong>動能正強</strong>可以跟、哪些<strong>位階過高</strong>要避免追。資料來自 prices.json + coverage_report.json（非 AI 推論）。
        </p>
      </div>
      <div class="tf-grid">
        {_card_col("💎", "便宜將漲", "PE ≤ 20 · 52週位階 ≤ 65% · 新聞有熱度", cheap, "cheap",
                   "今天沒有符合三條件的名單。低估值+未高位+有新聞的組合不多見，通常要等回檔才會出現。")}
        {_card_col("🔥", "正在上漲", "近 7 日 ≥ +3% 或 30 日 ≥ +10%（有動能）", hot, "hot",
                   "今天沒有明顯動能標的。")}
        {_card_col("⚠️", "警戒追高", "52週位階 ≥ 90% 且（PE 高 或 30 日漲多）", danger, "danger",
                   "今天沒有過熱警訊 👍")}
      </div>
      <div class="tf-foot muted small">
        提示：💎 便宜將漲 是最適合 NT$5,000 試水的位置（下跌風險低、有題材時爆發快）。
        🔥 正在上漲 通常要等回檔（不追高是雪球法鐵律）。
        ⚠️ 警戒追高 裡若有你的持倉，考慮分批停利回 0050 存款。
      </div>
    </section>
    '''


def render_index(briefs: list[dict], pf: dict | None,
                 history: dict | None = None) -> str:
    history = history or {}
    if not pf:
        # Fallback for no portfolio data
        return (
            PAGE_HEAD.format(title="Stock AI Desk", css_href="styles.css")
            + '<div class="empty-state wrap"><h1>📈 Stock AI Desk</h1><p>無組合資料</p></div>'
            + PAGE_FOOT.format(now=datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"))
        )

    latest_brief = briefs[0] if briefs else None
    latest_analysis = load_analysis(latest_brief["date"]) if latest_brief else None
    coverage_report = load_coverage_report()
    # Build ticker alias map for linkification across all rendered text
    init_ticker_alias(pf)

    try:
        as_of = datetime.fromisoformat(pf.get("as_of", ""))
        as_of_str = as_of.strftime("%Y-%m-%d %H:%M")
        date_str = as_of.strftime("%Y-%m-%d")
        weekday_zh = "一二三四五六日"[as_of.weekday()]
    except Exception:
        as_of_str = pf.get("as_of", "")
        date_str = ""
        weekday_zh = ""

    sidebar = render_desk_sidebar(pf)
    validation_report = load_validation_report()
    validation_banner = render_validation_banner(validation_report)
    market_chips_data = load_market_chips()
    market_chips_strip = render_market_chips_strip(market_chips_data)
    hero = render_daily_hero(latest_brief, latest_analysis, pf)
    focus_panel = render_today_focus(pf, coverage_report, latest_analysis)
    mood_panel = render_market_mood(pf, latest_analysis)
    catalyst_panel = render_catalyst_timeline(latest_analysis)
    chart = render_big_chart(pf)
    macro_strip = render_macro_strip(pf)
    positions = render_positions_table(pf)
    briefs_table = render_briefs_table(briefs)
    ai_tab = render_ai_tab(latest_brief, latest_analysis)
    radar_tab = render_radar_tab(latest_analysis, pf, history)
    sim_html, _ = render_simulator(pf, latest_analysis)
    # New GUSHI-style tabs
    portfolio_tab = render_portfolio_tab(pf, latest_analysis, coverage_report)
    macro_tab = render_macro_tab(pf, latest_analysis, history)
    news_tab = render_news_tab(briefs, pf)
    screen_tab = render_screen_tab(pf)
    chat_tab = render_chat_tab(pf, latest_analysis)

    # Thin portfolio summary strip values
    s = pf.get("summary", {})
    bench = pf.get("benchmark", {})
    total_value = s.get("total_value_twd", 0)
    day_pnl = s.get("day_pnl_twd", 0)
    day_pct = s.get("day_pnl_pct", 0)
    total_pnl = s.get("total_pnl_twd", 0)
    total_pct = s.get("total_pnl_pct", 0)
    alpha = s.get("alpha_vs_benchmark_pct", 0)
    alert_count = pf.get("alert_count", 0)

    body = f'''
<div class="shell">
  <aside class="sidenav" aria-label="主導航">
    <div class="sidenav-logo">{_icon("bolt", 22)}</div>
    <button class="sn-btn active" data-tab="ai" title="AI Brief">
      {_icon("ai")}<span class="sn-label">AI</span>
    </button>
    <button class="sn-btn" data-tab="radar" title="Opportunity Radar">
      {_icon("radar")}<span class="sn-label">RADAR</span>
    </button>
    <button class="sn-btn" data-tab="screen" title="Screener · 選股">
      {_icon("search")}<span class="sn-label">SCREEN</span>
    </button>
    <button class="sn-btn" data-tab="sim" title="Simulator">
      {_icon("sim")}<span class="sn-label">SIM</span>
    </button>
    <button class="sn-btn" data-tab="portfolio" title="Portfolio">
      {_icon("chart")}<span class="sn-label">PORT</span>
    </button>
    <button class="sn-btn" data-tab="macro" title="Macro Pulse">
      {_icon("globe")}<span class="sn-label">MACRO</span>
    </button>
    <button class="sn-btn" data-tab="briefs" title="News Stream">
      {_icon("news")}<span class="sn-label">NEWS</span>
      <span class="sn-badge">{len(briefs)}</span>
    </button>
    <button class="sn-btn" data-tab="chat" title="AI Chat">
      {_icon("ai")}<span class="sn-label">CHAT</span>
    </button>
    <button class="sn-btn" data-tab="positions" title="Holdings (legacy)">
      {_icon("case")}<span class="sn-label">HOLD</span>
    </button>
  </aside>

  <div class="shell-main">
    <header class="top-bar">
      <div class="top-brand">
        <h1 class="top-title">STOCK AI DESK</h1>
        <span class="top-date mono">{date_str} · 週{weekday_zh}</span>
        <span class="live-dot accent"></span>
      </div>
      <div class="top-search-wrap">
        <span class="top-search-icon">{_icon("search", 14)}</span>
        <input type="text" id="top-search" class="top-search-input" placeholder="Search · 2330 · 台積電 · NVDA · 光通訊 …" autocomplete="off">
        <div class="top-search-results" id="top-search-results"></div>
      </div>
      <div class="summary-strip">
        <div class="ss-cell ss-main">
          <span class="ss-lbl">組合</span>
          <span class="ss-val mono tnum">{_fmt_twd(total_value)}</span>
        </div>
        <div class="ss-cell">
          <span class="ss-lbl">今日</span>
          <span class="ss-val mono tnum {_cls(day_pnl)}">{_fmt_pct(day_pct)}</span>
        </div>
        <div class="ss-cell">
          <span class="ss-lbl">α</span>
          <span class="ss-val mono tnum {_cls(alpha)}">{_fmt_pct(alpha)}</span>
        </div>
        {f'<div class="ss-cell ss-alert"><span class="ss-lbl">ALRT</span><span class="ss-val mono tnum amber">{alert_count}</span></div>' if alert_count > 0 else ''}
      </div>
    </header>

    <main class="main-panel">
      <div class="tab-panel active" data-panel="ai">
        {market_chips_strip}
        {validation_banner}
        {hero}
        {focus_panel}
        <div class="mood-cat-row">
          {mood_panel}
          {catalyst_panel}
        </div>
        {ai_tab}
      </div>
      <div class="tab-panel" data-panel="radar">
        {radar_tab}
      </div>
      <div class="tab-panel" data-panel="screen">
        {screen_tab}
      </div>
      <div class="tab-panel" data-panel="sim">
        {sim_html}
      </div>
      <div class="tab-panel" data-panel="portfolio">
        {portfolio_tab}
      </div>
      <div class="tab-panel" data-panel="macro">
        {macro_tab}
      </div>
      <div class="tab-panel" data-panel="briefs">
        {news_tab}
      </div>
      <div class="tab-panel" data-panel="chat">
        {chat_tab}
      </div>
      <div class="tab-panel" data-panel="positions">
        {positions}
        <section class="portfolio-detail">
          {sidebar}
        </section>
      </div>
    </main>

    <footer class="status-bar">
      <div class="sb-left mono">
        <span class="live-dot accent"></span>
        <span class="sb-status">MARKET OPEN</span>
        <span class="sb-div">|</span>
        <span id="sb-clock">TPE {html.escape(as_of_str.split(' ')[-1] if ' ' in as_of_str else as_of_str)}</span>
        <span class="sb-div">|</span>
        <span>VIEW: <span id="sb-view" class="sb-view">AI</span></span>
        <span class="sb-div">|</span>
        <span>SCANNING · {len(pf.get("simulator_universe", []))} TICKERS · {len(briefs)} BRIEFS</span>
      </div>
      <div class="sb-right mono">
        <span>AI: Gemini 2.5 · Quant Engine v2</span>
      </div>
    </footer>
<script>
// Live clock + view name in status bar
(function() {{
  const clock = document.getElementById('sb-clock');
  const viewEl = document.getElementById('sb-view');
  const viewMap = {{ai: 'AI', radar: 'RADAR', screen: 'SCREEN', sim: 'SIM', portfolio: 'PORT', macro: 'MACRO', briefs: 'NEWS', chat: 'CHAT', positions: 'HOLD'}};
  function tick() {{
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    if (clock) clock.textContent = `TPE ${{hh}}:${{mm}}:${{ss}}`;
  }}
  tick(); setInterval(tick, 1000);
  // Update view name on tab change
  window.addEventListener('hashchange', () => {{
    const k = (location.hash || '').replace('#', '') || 'ai';
    if (viewEl) viewEl.textContent = viewMap[k] || 'AI';
  }});
  // Also wire existing tab buttons to update view label
  document.querySelectorAll('.sn-btn').forEach(b => {{
    b.addEventListener('click', () => {{
      if (viewEl) viewEl.textContent = viewMap[b.dataset.tab] || 'AI';
    }});
  }});
  const init = (location.hash || '').replace('#', '') || 'ai';
  if (viewEl) viewEl.textContent = viewMap[init] || 'AI';
}})();
</script>
  </div>
</div>

<script>
// Tab switching with URL hash persistence (sidenav-based)
function setTab(t) {{
  document.querySelectorAll('.sn-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === t));
  if (location.hash !== '#' + t) history.replaceState(null, '', '#' + t);
}}
document.querySelectorAll('.sn-btn').forEach(btn => {{
  btn.addEventListener('click', () => setTab(btn.dataset.tab));
}});
// Restore from hash (only if matches a tab)
const initTab = (location.hash || '').replace('#', '');
if (initTab && document.querySelector(`.sn-btn[data-tab="${{initTab}}"]`)) setTab(initTab);

// --- Search: autocomplete across ALL TW stocks (2000+) + our tracked universe ---
(function() {{
  // Tracked stocks (have deep pages + prices)
  const TRACKED = {json.dumps([
      {"symbol": it.get("symbol"), "name": it.get("name", ""),
       "category": it.get("category", ""),
       "group": "📊 已追蹤"}
      for it in (pf.get("simulator_universe") or []) + pf.get("holdings", []) + pf.get("watchlist", [])
      if it.get("symbol")
  ], ensure_ascii=False)};
  const TRACKED_SYMS = new Set(TRACKED.map(t => t.symbol));
  // Full TW universe (just symbol+name, no prices) — from TWSE/TPEx
  const FULL = {json.dumps([
      {"symbol": s.get("symbol"), "name": s.get("name", ""), "group": "🔎 全市場"}
      for s in load_full_tw_universe()
      if s.get("symbol")
  ], ensure_ascii=False)};
  const INDEX = [...TRACKED, ...FULL.filter(s => !TRACKED_SYMS.has(s.symbol))];
  const seen = new Set();
  const uniq = INDEX.filter(x => !seen.has(x.symbol) && seen.add(x.symbol));
  const input = document.getElementById('top-search');
  const results = document.getElementById('top-search-results');
  if (!input) return;
  let activeIdx = -1;

  function render(matches) {{
    if (!matches.length) {{ results.classList.remove('open'); results.innerHTML=''; return; }}
    results.innerHTML = matches.slice(0, 12).map((m, i) => {{
      const isTracked = TRACKED_SYMS.has(m.symbol);
      // Untracked stocks now open the universal inspect page (速查) which
      // fetches FinMind data client-side and renders a rule-based Chinese
      // verdict — instead of punting the user to Yahoo with no interpretation.
      const href = isTracked
        ? `holdings/${{m.symbol}}.html`
        : `inspect.html?sym=${{encodeURIComponent(m.symbol)}}&name=${{encodeURIComponent(m.name || '')}}`;
      const target = '';  // always same-tab now — both destinations are ours
      const badge = isTracked
        ? `<span class="search-result-cat tracked">${{m.category || '追蹤中'}}</span>`
        : `<span class="search-result-cat untracked">速查 →</span>`;
      return `
      <a class="search-result${{i === activeIdx ? ' active' : ''}}" href="${{href}}" ${{target}}>
        <span class="search-result-sym">${{m.symbol}}</span>
        <span class="search-result-name">${{m.name}}</span>
        ${{badge}}
      </a>`;
    }}).join('');
    results.classList.add('open');
  }}

  input.addEventListener('input', () => {{
    const q = input.value.toLowerCase().trim();
    if (!q) {{ results.classList.remove('open'); return; }}
    const matches = uniq.filter(x =>
      x.symbol.toLowerCase().includes(q) ||
      (x.name || '').toLowerCase().includes(q) ||
      (x.category || '').toLowerCase().includes(q)
    );
    activeIdx = -1;
    render(matches);
  }});

  input.addEventListener('keydown', (e) => {{
    const items = results.querySelectorAll('.search-result');
    if (e.key === 'ArrowDown') {{ e.preventDefault(); activeIdx = Math.min(activeIdx+1, items.length-1); render(Array.from(items).map(x => ({{symbol: x.querySelector('.search-result-sym').textContent, name: x.querySelector('.search-result-name').textContent, category: x.querySelector('.search-result-cat').textContent}}))); }}
    if (e.key === 'ArrowUp')   {{ e.preventDefault(); activeIdx = Math.max(activeIdx-1, 0); }}
    if (e.key === 'Enter' && items[activeIdx]) {{ e.preventDefault(); items[activeIdx].click(); }}
    if (e.key === 'Escape') {{ input.blur(); results.classList.remove('open'); }}
  }});

  document.addEventListener('click', (e) => {{
    if (!e.target.closest('.top-search-wrap')) results.classList.remove('open');
  }});
}})();
</script>
'''
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    return (
        PAGE_HEAD.format(title="Stock AI Desk", css_href="styles.css")
        + body
        + PAGE_FOOT.format(now=now)
    )


def render_brief_page(brief: dict) -> str:
    _, copyable = split_prompt(brief["content"])
    weekday_map = {"一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu",
                   "五": "Fri", "六": "Sat", "日": "Sun"}
    day_en = weekday_map.get(brief["weekday"], "")

    analysis = load_analysis(brief["date"])
    if analysis:
        analysis_html = render_analysis_section(analysis)
        raw_news_html = md.markdown(copyable, extensions=["tables", "fenced_code", "sane_lists"])
        main_html = (
            f'{analysis_html}'
            f'<details class="raw-news wrap">'
            f'<summary>📰 展開原始新聞（按產業分類）</summary>'
            f'<div class="raw-news-body">{raw_news_html}</div>'
            f'</details>'
        )
        actions_bar = ""
    else:
        html_body = md.markdown(copyable, extensions=["tables", "fenced_code", "sane_lists"])
        main_html = f'<div class="brief-body wrap">{html_body}</div>'
        actions_bar = f'''
  <div class="actions">
    <button id="copy-btn" class="btn-primary">📋 複製 Prompt 貼到 Claude.ai</button>
    <a href="https://claude.ai/new" target="_blank" class="btn-secondary">🚀 開 Claude.ai</a>
  </div>
  <p class="hint">AI 分析尚未生成，改用手動貼 Claude.ai 流程。</p>
'''

    body = f'''
<header class="brief-header wrap">
  <a href="../index.html" class="back">← 回首頁</a>
  <h1 class="mono">Daily Brief · {brief["date"]}</h1>
  <p class="meta muted">週{brief["weekday"]} · {day_en} · {brief["count"]} 則新聞</p>
  {actions_bar}
</header>

<main>
{main_html}
</main>

<div id="prompt-source" hidden>{html.escape(copyable)}</div>
<script>
const btn = document.getElementById('copy-btn');
if (btn) {{
  const src = document.getElementById('prompt-source');
  btn.addEventListener('click', async () => {{
    try {{
      await navigator.clipboard.writeText(src.textContent);
      const orig = btn.textContent;
      btn.textContent = '✅ 已複製！';
      setTimeout(() => btn.textContent = orig, 2500);
    }} catch (e) {{ alert('複製失敗：' + e.message); }}
  }});
}}
</script>
'''
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    return (
        PAGE_HEAD.format(title=f'{brief["date"]} · Stock AI Desk', css_href="../styles.css")
        + body
        + PAGE_FOOT.format(now=now)
    )


# ---------------------------------------------------------------------------
# Per-holding deep-dive page
# ---------------------------------------------------------------------------

def render_holding_page(holding: dict, pf: dict, history: dict,
                        latest_analysis: dict | None,
                        is_watchlist: bool = False,
                        news_for_ticker: list[dict] | None = None,
                        page_kind: str = "holding") -> str:
    """Render deep-dive page for a stock.

    page_kind: "holding" (user owns), "watchlist" (tracking), "universe" (just available)
    """
    sym = holding["symbol"]
    name = holding["name"]
    market = holding.get("market", "TW")
    yf_t = holding.get("yf_ticker", "")
    pillar = holding.get("pillar", "growth")
    pillar_cls = PILLAR_CLS.get(pillar, "")

    # Big sparkline from history. History is keyed on the ORIGINAL requested
    # ticker (e.g. "3081.TW") even when fetch_prices.py fell back to ".TWO".
    requested_key = f"{sym}.TW" if market == "TW" else sym
    hist_rows = history.get(requested_key) or history.get(yf_t) or []
    big_spark = _spark_svg(
        [{"c": r["close"]} for r in hist_rows[-90:]],
        width=600, height=100,
    ) if hist_rows else ""

    # Find holding in portfolio.holdings (owned) or build from watchlist
    if is_watchlist:
        data = holding
        price = data.get("price")
        day_pct = data.get("day_change_pct", 0)
        pnl_section = ""
    else:
        data = holding
        price = data.get("price")
        day_pct = data.get("day_change_pct", 0)
        pnl_section = f'''
      <div class="dd-cell">
        <div class="muted small">持股數</div>
        <div class="mono tnum val-md">{data.get("shares", 0):,}</div>
      </div>
      <div class="dd-cell">
        <div class="muted small">成本均價</div>
        <div class="mono tnum val-md">{data.get("cost_basis", 0):.2f}</div>
      </div>
      <div class="dd-cell">
        <div class="muted small">市值</div>
        <div class="mono tnum val-md">{_fmt_twd(data.get("value", 0))}</div>
      </div>
      <div class="dd-cell">
        <div class="muted small">損益</div>
        <div class="mono tnum val-md {_cls(data.get("pnl"))}">{_fmt_twd(data.get("pnl", 0), sign=True)} ({_fmt_pct(data.get("pnl_pct"))})</div>
      </div>
    '''

    # Returns
    rets = []
    for label, key, d in (
        ("今日", "day_change_pct", 2), ("7日", "ret_7d", 2),
        ("30日", "ret_30d", 2), ("90日", "ret_90d", 2),
        ("YTD", "ret_ytd", 1),
    ):
        v = data.get(key)
        rets.append(f'''
        <div class="dd-ret-cell">
          <div class="muted small">{label}</div>
          <div class="mono tnum {_cls(v)}">{_fmt_pct(v, d)}</div>
        </div>''')

    # 52w range bar
    pct52 = data.get("pct_52w", 0)
    high52 = data.get("high_52w", 0)
    low52 = data.get("low_52w", 0)
    pos = max(0, min(100, pct52))

    # Stop / Take-profit — no emojis, dot indicators
    sl = data.get("stop_loss")
    tp = data.get("take_profit")
    rules_html = ""
    if sl or tp:
        rows = []
        if sl:
            d = data.get("stop_loss_dist_pct") or 0
            rows.append(f'<div class="rule-row"><span class="dn mono"><span class="dot dot-dn"></span>STOP</span><span class="mono tnum">{sl}</span><span class="muted mono tnum small">距離 {d:+.1f}%</span></div>')
        if tp:
            d = data.get("take_profit_dist_pct") or 0
            rows.append(f'<div class="rule-row"><span class="up mono"><span class="dot dot-up"></span>TARGET</span><span class="mono tnum">{tp}</span><span class="muted mono tnum small">距離 {d:+.1f}%</span></div>')
        rules_html = f'<div class="dd-rules"><div class="pf-sub-head small">規則</div>{"".join(rows)}</div>'

    # AI commentary from latest analysis
    ai_html = ""
    if latest_analysis:
        for ha in latest_analysis.get("holdings_analysis", []):
            if ha.get("symbol") == sym:
                bb = ha.get("bull_bear_breakdown", {})
                bull, bear, neu = bb.get("bull_pct", 0), bb.get("bear_pct", 0), bb.get("neutral_pct", 0)
                catalysts = ha.get("key_catalysts", [])
                risks = ha.get("key_risks", [])
                cat = "".join(f'<li>{html.escape(c)}</li>' for c in catalysts)
                risk_items = "".join(f'<li>{html.escape(r)}</li>' for r in risks)
                ai_html = f'''
<section class="dd-ai wrap">
  <div class="section-head"><h2>{_icon("ai", 18)} AI 觀點 · <span class="sec-en">AI ANALYSIS</span></h2>{_sentiment_badge(ha.get("outlook", "中性"))}</div>
  <p class="narrative">{html.escape(ha.get("commentary", ""))}</p>
  <div class="bullbear">
    <div class="bb-bar">
      <div class="bb-bull" style="width:{bull}%"></div>
      <div class="bb-neu"  style="width:{neu}%"></div>
      <div class="bb-bear" style="width:{bear}%"></div>
    </div>
    <div class="bb-legend">
      <span class="bb-lbl bull">看多 {bull}%</span>
      <span class="bb-lbl neu">觀望 {neu}%</span>
      <span class="bb-lbl bear">看空 {bear}%</span>
    </div>
  </div>
  <div class="hc-split">
    {"<div class='hc-list-head'>催化劑</div><ul class='hc-list up-list'>" + cat + "</ul>" if catalysts else ""}
    {"<div class='hc-list-head'>風險</div><ul class='hc-list dn-list'>" + risk_items + "</ul>" if risks else ""}
  </div>
</section>
'''
                break

    # Recommendation card (buy/sell/hold with suggested price)
    rec = data.get("recommendation") or {}
    rec_html = ""
    if rec:
        tone_cls = {"up": "up", "dn": "dn", "amber": "amber", "flat": "flat"}.get(rec.get("tone", "flat"), "flat")
        rec_html = f'''
<section class="wrap dd-rec-section">
  <div class="dd-rec-card tone-{tone_cls}">
    <div class="dd-rec-head">
      <div class="muted small mono">建議動作（規則式）</div>
      <div class="dd-rec-action {tone_cls}">{html.escape(rec.get("action", ""))}</div>
    </div>
    <div class="dd-rec-body">
      <div class="dd-rec-price-row">
        <div class="dd-rec-price-cell">
          <div class="muted small">建議價格</div>
          <div class="mono tnum val-md">{rec.get("suggested_price", "—")}</div>
        </div>
        <div class="dd-rec-price-cell">
          <div class="muted small">預期停利 (+30%)</div>
          <div class="mono tnum up">{rec.get("suggested_price", 0) * 1.3:.2f}</div>
        </div>
        <div class="dd-rec-price-cell">
          <div class="muted small">預期停損 (−10%)</div>
          <div class="mono tnum dn">{rec.get("suggested_price", 0) * 0.9:.2f}</div>
        </div>
      </div>
      <p class="dd-rec-reason">{html.escape(rec.get("reason", ""))}</p>
    </div>
  </div>
</section>
'''

    # Recent news for this ticker
    news_html = ""
    if news_for_ticker:
        rows = []
        for n in news_for_ticker[:6]:
            summary_html = f'<div class="dd-news-summary muted small">{html.escape(n["summary"])[:150]}{"…" if len(n.get("summary", "")) > 150 else ""}</div>' if n.get("summary") else ""
            rows.append(f'''
            <li class="dd-news-item">
              <a href="{html.escape(n["url"])}" target="_blank" rel="noopener" class="dd-news-title">{html.escape(n["title"])}</a>
              <div class="dd-news-meta muted small mono">{html.escape(n["source"])} · {html.escape(n["date"])} {html.escape(n["time"])}</div>
              {summary_html}
            </li>''')
        news_html = f'''
<section class="wrap dd-news-section">
  <div class="section-head">
    <h2>相關新聞 · <span class="sec-en">NEWS</span> <span class="muted small">({len(news_for_ticker)} 則)</span></h2>
  </div>
  <ul class="dd-news-list">{"".join(rows)}</ul>
</section>
'''

    # ── AI VERDICT big rating circle ──────────────────────────
    # Score = blend of rule tone, 52w percentile, day change, AI bull/bear
    rec_tone = (data.get("recommendation") or {}).get("tone", "flat")
    tone_score = {"up": 4.2, "dn": 1.8, "amber": 2.7, "flat": 3.0}.get(rec_tone, 3.0)
    # 52w contribution: low = +, high = -
    p52 = data.get("pct_52w") or 50
    p52_adj = (50 - p52) / 100  # -0.5 to +0.5
    # Day adjustment (small)
    day_adj = max(-0.3, min(0.3, (day_pct or 0) / 20))
    # Bull/bear adjustment from AI if present
    bull_adj = 0.0
    ai_bull = ai_bear = ai_neu = 0
    ai_outlook_str = ""
    ai_narrative = ""
    if latest_analysis:
        for ha in latest_analysis.get("holdings_analysis", []):
            if ha.get("symbol") == sym:
                bb = ha.get("bull_bear_breakdown", {})
                ai_bull = bb.get("bull_pct", 0) or 0
                ai_bear = bb.get("bear_pct", 0) or 0
                ai_neu = bb.get("neutral_pct", 0) or 0
                bull_adj = (ai_bull - ai_bear) / 100  # -1 to +1
                ai_outlook_str = ha.get("outlook", "")
                ai_narrative = ha.get("commentary", "")
                break
    score = max(1.0, min(5.0, tone_score + p52_adj + day_adj + bull_adj))
    # Action label + tone
    if score >= 4.2:
        verdict_label, verdict_tone = "STRONG BUY", "up"
    elif score >= 3.6:
        verdict_label, verdict_tone = "BUY", "up"
    elif score >= 2.8:
        verdict_label, verdict_tone = "HOLD", "flat"
    elif score >= 2.0:
        verdict_label, verdict_tone = "TRIM", "amber"
    else:
        verdict_label, verdict_tone = "SELL", "dn"

    # Circle SVG — big rating dial
    circle_r = 56
    circle_c = 2 * 3.1415926 * circle_r
    progress = circle_c * (score / 5.0)
    verdict_svg = f'''
    <svg class="verdict-dial" viewBox="0 0 140 140" width="140" height="140">
      <circle cx="70" cy="70" r="{circle_r}" fill="none" stroke="var(--bg-3)" stroke-width="10"/>
      <circle cx="70" cy="70" r="{circle_r}" fill="none"
              stroke="var(--{verdict_tone})" stroke-width="10" stroke-linecap="round"
              stroke-dasharray="{progress:.1f} {circle_c:.1f}"
              transform="rotate(-90 70 70)"/>
      <text x="70" y="76" text-anchor="middle"
            fill="var(--tx-1)" font-size="32" font-weight="700"
            font-family="var(--font-mono)">{score:.1f}</text>
      <text x="70" y="96" text-anchor="middle"
            fill="var(--tx-3)" font-size="10" font-weight="600"
            letter-spacing="1" font-family="var(--font-mono)">OUT OF 5</text>
    </svg>
    '''
    ai_narrative_short = (ai_narrative[:160] + "…") if len(ai_narrative) > 160 else ai_narrative
    if not ai_narrative_short:
        ai_narrative_short = (data.get("recommendation") or {}).get("reason", "") or "依規則引擎綜合判斷。"

    verdict_card = f'''
    <section class="dd-verdict-card wrap">
      <div class="verdict-left">
        <div class="verdict-dial-wrap tone-{verdict_tone}">{verdict_svg}</div>
      </div>
      <div class="verdict-mid">
        <div class="verdict-lbl mono">AI VERDICT · 綜合評等</div>
        <div class="verdict-action {verdict_tone}">{verdict_label}</div>
        <div class="verdict-narrative">{_link_tickers(ai_narrative_short)}</div>
        <div class="verdict-meta muted small mono">
          規則 · Gemini 2.5 · 52週 {p52:.0f}% · {ai_outlook_str or "中性"}
        </div>
      </div>
      <div class="verdict-right">
        <div class="sentiment-bar-box">
          <div class="sentiment-bar-head mono small">
            <span>市場情緒 · SENTIMENT</span>
            <span class="muted">{ai_bull + ai_bear + ai_neu}%</span>
          </div>
          <div class="sentiment-bar">
            <div class="sentiment-seg sb-bull" style="width:{ai_bull}%"><span>多 {ai_bull}</span></div>
            <div class="sentiment-seg sb-neu"  style="width:{ai_neu}%"><span>平 {ai_neu}</span></div>
            <div class="sentiment-seg sb-bear" style="width:{ai_bear}%"><span>空 {ai_bear}</span></div>
          </div>
          <div class="sentiment-counts mono small muted">
            看多 {ai_bull}% · 觀望 {ai_neu}% · 看空 {ai_bear}%
          </div>
        </div>
      </div>
    </section>
    '''

    # Tab navigation (OVERVIEW / AI / FINANCIALS / HOLDERS / FILINGS)
    # Only OVERVIEW + AI have real data — rest are placeholders.
    tabs_nav = '''
    <nav class="dd-tabs wrap">
      <button class="dd-tab active" data-dd-tab="overview">概覽 · OVERVIEW</button>
      <button class="dd-tab" data-dd-tab="ai">AI 觀點 · AI</button>
      <button class="dd-tab" data-dd-tab="fin">財報 · FINANCIALS</button>
      <button class="dd-tab" data-dd-tab="hold">股東 · HOLDERS</button>
      <button class="dd-tab" data-dd-tab="news">新聞 · NEWS</button>
    </nav>
    '''

    # Financials panel — now populated from yfinance fundamentals
    fund = data.get("fundamentals") or {}
    fin_rows = []
    def _frow(label_cn: str, label_en: str, val_html: str, hint: str = ""):
        hint_html = f'<div class="dd-fin-hint muted small">{hint}</div>' if hint else ""
        return (
            f'<div class="dd-fin-row"><div class="dd-fin-lbl">'
            f'<div class="dd-fin-cn">{label_cn}</div>'
            f'<div class="dd-fin-en mono small muted">{label_en}</div></div>'
            f'<div class="dd-fin-val mono tnum">{val_html}</div>'
            f'{hint_html}</div>'
        )
    def _fin_eps(v):
        return f"{v:.2f}" if v is not None else '<span class="muted">—</span>'
    def _fin_ratio(v, digits=1):
        return f"{v:.{digits}f}" if v is not None else '<span class="muted">—</span>'
    def _fin_pct(v, digits=1):
        return f"{v*100:+.{digits}f}%" if v is not None else '<span class="muted">—</span>'
    def _fin_cap(v):
        if v is None:
            return '<span class="muted">—</span>'
        if v >= 1e12:
            return f"{v/1e12:.2f} 兆"
        if v >= 1e8:
            return f"{v/1e8:.1f} 億"
        return f"{v:,.0f}"
    pe_ttm = fund.get("pe_ttm")
    pe_fwd = fund.get("pe_forward")
    eps_ttm = fund.get("eps_ttm")
    eps_fwd = fund.get("eps_forward")
    roe = fund.get("roe")
    pm = fund.get("profit_margin")
    rev_g = fund.get("rev_growth")
    earn_g = fund.get("earnings_growth")
    mcap = fund.get("market_cap")
    pb = fund.get("pb")
    div_y = fund.get("dividend_yield")
    beta = fund.get("beta")
    sector = fund.get("sector") or ""
    industry = fund.get("industry") or ""

    # Green/red light scoring: 亮綠燈 = 合乎成長股 / 合理估值 的條件
    lights = []
    if pe_ttm is not None:
        # Very rough heuristic: PE < 25 合理, 25-45 偏高, >45 昂貴
        tone = "green" if pe_ttm < 25 else ("amber" if pe_ttm < 45 else "red")
        lights.append(("估值 P/E", f"{pe_ttm:.1f}", tone, "<25 合理 / 25-45 偏高 / >45 昂貴（粗略標準）"))
    if earn_g is not None:
        tone = "green" if earn_g > 0.15 else ("amber" if earn_g > 0 else "red")
        lights.append(("EPS 成長 YoY", f"{earn_g*100:+.1f}%", tone, ">15% 佳 / 0-15% 平緩 / <0 衰退"))
    elif rev_g is not None:
        tone = "green" if rev_g > 0.15 else ("amber" if rev_g > 0 else "red")
        lights.append(("營收成長 YoY", f"{rev_g*100:+.1f}%", tone, ">15% 佳 / 0-15% 平緩 / <0 衰退"))
    if roe is not None:
        tone = "green" if roe > 0.15 else ("amber" if roe > 0.08 else "red")
        lights.append(("ROE", f"{roe*100:.1f}%", tone, ">15% 優 / 8-15% 可接受 / <8% 偏低"))
    if pm is not None:
        tone = "green" if pm > 0.2 else ("amber" if pm > 0.08 else "red")
        lights.append(("淨利率", f"{pm*100:.1f}%", tone, ">20% 高 / 8-20% 一般 / <8% 薄"))
    if pct52 is not None:
        tone = "green" if pct52 < 40 else ("amber" if pct52 < 75 else "red")
        lights.append(("52週位階", f"{pct52:.0f}%", tone, "<40% 低檔 / 40-75% 中段 / >75% 高檔追高"))

    lights_html = ""
    if lights:
        chips = []
        green_count = sum(1 for _, _, t, _ in lights if t == "green")
        total = len(lights)
        for label, val, tone, hint in lights:
            chips.append(
                f'<div class="dd-light dd-light-{tone}">'
                f'<div class="dd-light-head"><span class="dd-light-lbl mono small">{html.escape(label)}</span>'
                f'<span class="dd-light-dot dd-light-dot-{tone}"></span></div>'
                f'<div class="dd-light-val mono tnum">{html.escape(val)}</div>'
                f'<div class="dd-light-hint muted small">{html.escape(hint)}</div></div>'
            )
        lights_html = f'''
        <div class="dd-fin-score">
          <div class="dd-fin-score-head">
            <span class="mono small muted">基本面體檢 · FUNDAMENTAL CHECK</span>
            <span class="mono tnum dd-fin-score-val">{green_count} / {total} 亮綠燈</span>
          </div>
          <div class="dd-lights-grid">{"".join(chips)}</div>
          <div class="dd-fin-disclaimer muted small">
            這個燈號是粗略啟發式，不是買賣訊號。投資需搭配產業分析 + 籌碼面 + 個人風險承受度。
          </div>
        </div>'''

    fin_rows.extend([
        _frow("本益比 (TTM)", "P/E TTM", _fin_ratio(pe_ttm), "以過去 4 季 EPS 計算"),
        _frow("預估本益比", "P/E Forward", _fin_ratio(pe_fwd), "以分析師預估 EPS 計算"),
        _frow("每股盈餘 (TTM)", "EPS TTM", _fin_eps(eps_ttm), "過去 4 季加總"),
        _frow("預估 EPS", "EPS Forward", _fin_eps(eps_fwd), "下個會計年度預估"),
        _frow("股東權益報酬率", "ROE", _fin_pct(roe), "公司運用股東資金的效率"),
        _frow("淨利率", "Profit Margin", _fin_pct(pm), "每元營收能賺到多少"),
        _frow("營收成長 YoY", "Revenue Growth", _fin_pct(rev_g), "近 4 季營收 vs 去年同期"),
        _frow("EPS 成長 YoY", "Earnings Growth", _fin_pct(earn_g), ""),
        _frow("股價淨值比", "P/B", _fin_ratio(pb, 2), ""),
        _frow("股息殖利率", "Dividend Yield", _fin_pct(div_y, 2), ""),
        _frow("Beta", "Beta", _fin_ratio(beta, 2), ">1 波動高於大盤"),
        _frow("市值", "Market Cap", _fin_cap(mcap), ""),
    ])

    sector_row = ""
    if sector or industry:
        sector_row = f'<div class="dd-fin-sector muted small">產業：{html.escape(sector)}{" / " + html.escape(industry) if industry else ""}</div>'

    if any(v is not None for v in (pe_ttm, eps_ttm, roe, pm, rev_g, earn_g, mcap, pb)):
        stub_fin = f'''
        <section class="wrap" data-dd-panel="fin">
          {lights_html}
          {sector_row}
          <div class="dd-fin-grid">{"".join(fin_rows)}</div>
          <div class="dd-fin-foot muted small">
            資料來源：yfinance (Yahoo Finance)。TW 個股可能有延遲或缺漏；以公開資訊觀測站為準。
          </div>
        </section>
        '''
    else:
        stub_fin = '''
        <section class="wrap dd-stub" data-dd-panel="fin">
          <div class="dd-stub-box">
            <div class="dd-stub-icon">''' + _icon("chart", 22) + '''</div>
            <div class="dd-stub-title">Financials · 財報資料暫缺</div>
            <div class="muted small">yfinance 對此個股無基本面資料（可能是 ETF / ADR / 新上市）。</div>
          </div>
        </section>
        '''
    # --- HOLDERS panel: 三大法人籌碼面 ---
    chips = data.get("chips") or {}
    if chips:
        f5 = chips.get("foreign_5d", 0) or 0
        f20 = chips.get("foreign_20d", 0) or 0
        fstr = chips.get("foreign_streak", 0) or 0
        t5 = chips.get("trust_5d", 0) or 0
        t20 = chips.get("trust_20d", 0) or 0
        tstr = chips.get("trust_streak", 0) or 0
        d5 = chips.get("dealer_5d", 0) or 0
        d20 = chips.get("dealer_20d", 0) or 0
        tot5 = chips.get("total_5d", 0) or 0
        tot20 = chips.get("total_20d", 0) or 0
        days_inc = chips.get("days_included", 0)
        daily = chips.get("daily", []) or []

        def _card(title: str, val_5d: int, val_20d: int, streak: int, spark_key: str) -> str:
            tone_5 = "up" if val_5d > 0 else ("dn" if val_5d < 0 else "muted")
            tone_20 = "up" if val_20d > 0 else ("dn" if val_20d < 0 else "muted")
            spark = _chips_mini_spark(daily, key=spark_key, width=120, height=30)
            return f'''
            <div class="dd-chip-card">
              <div class="dd-chip-card-head">
                <span class="dd-chip-card-title mono">{html.escape(title)}</span>
                {_streak_badge(streak)}
              </div>
              <div class="dd-chip-card-stats">
                <div class="dd-chip-stat">
                  <div class="muted small mono">5 日累計</div>
                  <div class="mono tnum val-md {tone_5}">{_fmt_lots(val_5d)}<span class="muted small"> 張</span></div>
                </div>
                <div class="dd-chip-stat">
                  <div class="muted small mono">20 日累計</div>
                  <div class="mono tnum val-md {tone_20}">{_fmt_lots(val_20d)}<span class="muted small"> 張</span></div>
                </div>
              </div>
              <div class="dd-chip-spark-wrap">
                {spark}
                <div class="muted small mono">近 5 日每日買賣超</div>
              </div>
            </div>'''

        # Total card: combined three-investor
        tot_tone5 = "up" if tot5 > 0 else ("dn" if tot5 < 0 else "muted")
        tot_tone20 = "up" if tot20 > 0 else ("dn" if tot20 < 0 else "muted")

        # Daily table (last 5 days)
        daily_rows = []
        for d in daily[:5]:
            daily_rows.append(f'''
            <tr>
              <td class="mono small">{html.escape(d.get("d", ""))}</td>
              <td>{_chips_cell(d.get("foreign"))}</td>
              <td>{_chips_cell(d.get("trust"))}</td>
              <td>{_chips_cell(d.get("dealer"))}</td>
              <td>{_chips_cell(d.get("total"))}</td>
            </tr>''')
        daily_table = ""
        if daily_rows:
            daily_table = f'''
            <div class="dd-chip-section">
              <div class="dd-chip-section-head mono small muted">每日買賣超明細 · 最近 5 日</div>
              <div class="dd-chip-daily-wrap">
                <table class="dd-chip-daily">
                  <thead>
                    <tr>
                      <th class="mono small">日期</th>
                      <th class="mono small">外資</th>
                      <th class="mono small">投信</th>
                      <th class="mono small">自營商</th>
                      <th class="mono small">三大法人</th>
                    </tr>
                  </thead>
                  <tbody>{"".join(daily_rows)}</tbody>
                </table>
              </div>
            </div>'''

        # Headline summary — pick dominant signal
        summary_bits = []
        if fstr >= 3:
            summary_bits.append(f"外資連{fstr}日買超")
        elif fstr <= -3:
            summary_bits.append(f"外資連{abs(fstr)}日賣超")
        if tstr >= 3:
            summary_bits.append(f"投信連{tstr}日買超")
        elif tstr <= -3:
            summary_bits.append(f"投信連{abs(tstr)}日賣超")
        if f20 > 0 and t20 > 0:
            summary_bits.append("20 日外資＋投信同步買超")
        elif f20 < 0 and t20 < 0:
            summary_bits.append("20 日外資＋投信同步賣超")

        summary_html = ""
        if summary_bits:
            summary_html = (
                f'<div class="dd-chip-summary mono small">'
                f'{" · ".join(html.escape(s) for s in summary_bits)}'
                f'</div>'
            )

        stub_hold = f'''
        <section class="wrap dd-chip-panel" data-dd-panel="hold">
          <div class="dd-chip-head">
            <div>
              <div class="dd-chip-title mono">三大法人買賣超 · HOLDERS</div>
              <div class="muted small mono">過去 {days_inc} 個交易日 · 單位：張 · 正值（<span class="up">紅</span>）買超、負值（<span class="dn">綠</span>）賣超</div>
            </div>
            {summary_html}
          </div>

          <div class="dd-chip-grid">
            {_card("外資", f5, f20, fstr, "foreign")}
            {_card("投信", t5, t20, tstr, "trust")}
            {_card("自營商", d5, d20, 0, "dealer")}
            <div class="dd-chip-card dd-chip-card-total">
              <div class="dd-chip-card-head">
                <span class="dd-chip-card-title mono">三大法人合計</span>
              </div>
              <div class="dd-chip-card-stats">
                <div class="dd-chip-stat">
                  <div class="muted small mono">5 日累計</div>
                  <div class="mono tnum val-md {tot_tone5}">{_fmt_lots(tot5)}<span class="muted small"> 張</span></div>
                </div>
                <div class="dd-chip-stat">
                  <div class="muted small mono">20 日累計</div>
                  <div class="mono tnum val-md {tot_tone20}">{_fmt_lots(tot20)}<span class="muted small"> 張</span></div>
                </div>
              </div>
              <div class="dd-chip-spark-wrap">
                {_chips_mini_spark(daily, key="total", width=120, height=30)}
                <div class="muted small mono">近 5 日三大法人合計</div>
              </div>
            </div>
          </div>

          {daily_table}

          <div class="dd-chip-disclaimer muted small">
            <strong>怎麼看：</strong>外資＋投信同步買超（且連續多日），通常代表機構認同，但短線可能已漲多；
            反之外資連續賣超可能是風險訊號，但也可能只是匯率避險或 ETF 調整。籌碼面只是拼圖的一片，
            搭配基本面（AI 評分／FINANCIALS 分頁）與位階（52 週區間）一起看。
            資料來自 TWSE + TPEx 三大法人統計，滯後 1 個交易日。
          </div>
        </section>
        '''
    else:
        stub_hold = '''
        <section class="wrap dd-stub" data-dd-panel="hold">
          <div class="dd-stub-box">
            <div class="dd-stub-icon">''' + _icon("case", 22) + '''</div>
            <div class="dd-stub-title">Holders · 籌碼資料暫缺</div>
            <div class="muted small">尚未抓到這檔的三大法人買賣超資料（可能是 ETF 成分以外 / 新上市 / 資料延遲）。</div>
          </div>
        </section>
        '''

    price_str = f"{price:.2f}" if price is not None else "—"
    status_str = {"holding": "持有中", "watchlist": "觀察中", "universe": "可查詢"}.get(page_kind, "—")
    body = f'''
<header class="brief-header wrap">
  <a href="../index.html" class="back">← 回首頁</a>
  <div class="dd-hero-row">
    <div>
      <h1 class="mono">{sym} <span class="muted"> · {html.escape(name)}</span></h1>
      <p class="meta muted small">
        <span class="pillar-dot {pillar_cls}"></span> {PILLAR_LABEL.get(pillar, pillar)}
        · {html.escape(data.get("market", "TW"))}
        · {status_str}
      </p>
    </div>
    <div class="dd-price">
      <div class="mono tnum val-xl">{price_str}</div>
      <div class="mono tnum small {_cls(day_pct)}">{_fmt_pct(day_pct)} today</div>
    </div>
  </div>
</header>

{verdict_card}
{tabs_nav}

<div class="dd-panel active" data-dd-panel="overview">
  <section class="wrap">
    {big_spark}
  </section>

  <section class="wrap dd-metrics">
    <div class="dd-grid">
      {pnl_section}
    </div>
    <div class="dd-rets">{"".join(rets)}</div>
    <div class="dd-52w">
      <div class="dd-52w-labels muted small mono">
        <span>52w 低 {low52:.2f}</span>
        <span class="mono">目前 {pct52:.0f}% 位階</span>
        <span>52w 高 {high52:.2f}</span>
      </div>
      <div class="dd-52w-bar"><div class="dd-52w-pos" style="left:{pos}%"></div></div>
    </div>
    {rules_html}
  </section>

  {rec_html}
</div>

<div class="dd-panel" data-dd-panel="ai">
  {ai_html or '<section class="wrap dd-stub"><div class="dd-stub-box"><div class="dd-stub-title">此個股今日暫無 AI 個別觀點</div><div class="muted small">AI 會挑選當日優先分析的持股／機會清單中的票。</div></div></section>'}
</div>

{stub_fin}
{stub_hold}

<div class="dd-panel" data-dd-panel="news">
  {news_html or '<section class="wrap dd-stub"><div class="dd-stub-box"><div class="dd-stub-title">此個股近期無相關新聞</div><div class="muted small">Brief 中未偵測到此 ticker 的提及。</div></div></section>'}
</div>

<script>
(function() {{
  const tabs = document.querySelectorAll('.dd-tab');
  const panels = document.querySelectorAll('.dd-panel, .dd-stub[data-dd-panel]');
  tabs.forEach(t => t.addEventListener('click', () => {{
    const key = t.dataset.ddTab;
    tabs.forEach(x => x.classList.toggle('active', x === t));
    panels.forEach(p => {{
      const match = p.dataset.ddPanel === key;
      if (p.classList.contains('dd-stub')) {{
        p.style.display = match ? '' : 'none';
      }} else {{
        p.classList.toggle('active', match);
      }}
    }});
  }}));
  // Hide stubs initially (only overview active)
  document.querySelectorAll('.dd-stub[data-dd-panel]').forEach(s => s.style.display = 'none');
}})();
</script>
'''
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    title = f'{sym} {name} · Stock AI Desk'
    return (
        PAGE_HEAD.format(title=title, css_href="../styles.css")
        + body
        + PAGE_FOOT.format(now=now)
    )


# ---------------------------------------------------------------------------
# CSS — Bloomberg-esque dark theme
# ---------------------------------------------------------------------------

STYLES_CSS = """
/* ── Design tokens ────────────────────────────────────────── */
/* 2026-04-19 Phase I: warmer charcoal palette, Inter + Noto Sans TC,
   bumped base size 15.5px, tighter hierarchy. User feedback: old palette
   felt 廉價, font size hard to read, font not 好看. */
:root {
  /* Background: warmer near-black charcoal (was pure navy-black) */
  --bg-0: #0a0d12;
  --bg-1: #11151c;
  --bg-2: #161b24;
  --bg-3: #1e2431;
  --bg-4: #2a3142;
  --line: rgba(255,255,255,0.07);
  --line-2: rgba(255,255,255,0.12);
  /* Text: slightly warmer off-white (less clinical) */
  --tx-1: #eef1f6;
  --tx-2: #aab3c0;
  --tx-3: #6e7685;
  --tx-4: #464c5a;
  /* TW color convention: 紅=漲 / 綠=跌. Desaturated a touch for less-kitsch feel */
  --up:    #ef4444;
  --up-bg: rgba(239,68,68,0.12);
  --up-soft: #f87171;
  --dn:    #10b981;
  --dn-bg: rgba(16,185,129,0.12);
  --dn-soft: #34d399;
  --amber:  #f59e0b;
  --amber-bg: rgba(245,158,11,0.12);
  --purple: #a78bfa;
  --purple-bg: rgba(167,139,250,0.12);
  /* Accent: warmer steel-blue with slightly less saturation (was a bit SaaS-generic) */
  --accent: #6690ff;
  --accent-2: #8bafff;
  --accent-glow: rgba(102,144,255,0.32);
  --accent-soft: rgba(102,144,255,0.12);
  --pillar-growth: #6690ff;
  --pillar-defense: #f59e0b;
  --pillar-flex: #a78bfa;
  /* Font stack: Inter (Latin letterforms are tight + premium) → Noto Sans TC
     (chosen over PingFang for web consistency across OS) → fallbacks */
  --font-sans: "Inter", "Noto Sans TC", -apple-system, BlinkMacSystemFont, "PingFang TC", "Helvetica Neue", Helvetica, Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace;
  /* Base size bumped 14 → 15.5px per user feedback "字的大小讓人沒辦法看清楚" */
  --fs-base: 15.5px;
  --fs-small: 13px;
  --fs-tiny:  11.5px;
  --fs-h1: 28px;
  --fs-h2: 22px;
  --fs-h3: 17px;
  --pad: 16px; --pad-sm: 12px; --gap: 12px; --r: 14px; --r-sm: 10px;
}

* { box-sizing: border-box; }
html, body {
  margin: 0; background: var(--bg-0); color: var(--tx-1);
  font-family: var(--font-sans); -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility; line-height: 1.65;
  font-size: var(--fs-base);
  font-feature-settings: "cv11", "ss01", "tnum";  /* Inter refinements + tabular nums */
}
body { overflow-x: hidden; }
a { color: var(--accent-2); text-decoration: none; }
a:hover { color: #b8d0ff; }


/* Section header EN label — subtle mono uppercase */
.sec-en {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--tx-3);
  letter-spacing: 1px;
  font-weight: 600;
  margin-left: 4px;
}
/* Dot indicators (replace emoji colored circles) */
.dot {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.dot-up { background: var(--dn); box-shadow: 0 0 6px var(--dn); }
.dot-warn { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
.dot-dn { background: var(--up); box-shadow: 0 0 6px var(--up); }

/* ── Layout ── */
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px; }
.mono { font-family: var(--font-mono); font-feature-settings: "tnum"; font-weight: 500; }
.tnum { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
.muted { color: var(--tx-3); }
/* Bumped .small 12 → 13px so labels aren't eye-strain (user 2026-04-19). */
.small { font-size: var(--fs-small); }
.tiny  { font-size: var(--fs-tiny); }
.val-md { font-size: 18px; font-weight: 600; }
.val-xl { font-size: 36px; font-weight: 700; letter-spacing: -0.5px; }

/* ── TW convention: up=red, down=green ── */
.up  { color: var(--up); }
.dn  { color: var(--dn); }
.flat { color: var(--tx-2); }
.amber  { color: var(--amber); }
.purple { color: var(--purple); }

/* ── Header ── */
.site-header {
  padding: 32px 0 20px; border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(91,141,255,0.04) 0%, transparent 100%);
}
.title-row { display: flex; align-items: center; gap: 10px; }
.site-header h1 {
  margin: 0; font-size: 28px; font-weight: 700; letter-spacing: -0.3px;
}
.site-header .subtitle { margin: 4px 0 0; font-size: 14px; }

.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--up); box-shadow: 0 0 8px var(--up);
  animation: pulse 1.8s ease-in-out infinite;
}
.live-dot.accent { background: var(--accent); box-shadow: 0 0 10px var(--accent-glow); }
@keyframes pulse {
  0%,100% { opacity: 1; transform: scale(1); }
  50%     { opacity: 0.45; transform: scale(0.8); }
}

/* ── Macro ribbon ── */
.macro-ribbon {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1px; margin: 20px auto;
  background: var(--line); border: 1px solid var(--line); border-radius: var(--r);
  overflow: hidden;
}
.macro-cell {
  padding: 14px 18px; background: var(--bg-1);
  display: flex; flex-direction: column; gap: 2px;
}
.macro-label { font-size: 11px; color: var(--tx-3); letter-spacing: 0.5px; text-transform: uppercase; }
.macro-val { font-size: 20px; font-weight: 700; }
.macro-delta { font-size: 13px; }
.macro-ytd { font-size: 11px; margin-left: 8px; }

/* ── Portfolio card ── */
.pf-card {
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r); padding: 24px 24px 20px; margin: 20px auto;
  position: relative; overflow: hidden;
}
.pf-card::before {
  content: ""; position: absolute; inset: 0;
  background: radial-gradient(100% 50% at 50% -20%, var(--accent-soft), transparent 70%);
  pointer-events: none;
}
.pf-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 20px; position: relative; }
.pf-title-row { display: flex; align-items: center; gap: 10px; }
.pf-top h2 { margin: 0; font-size: 18px; font-weight: 700; letter-spacing: 0.3px; }
.pf-asof { font-size: 11px; letter-spacing: 0.5px; margin-top: 4px; }
.pf-top-r { flex-shrink: 0; }

.pf-hero {
  display: grid; grid-template-columns: 1fr 1.5fr; gap: 24px;
  padding: 16px 0 20px; border-bottom: 1px solid var(--line);
  position: relative;
}
.pf-hero-lbl { font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 4px; }
.pf-hero-val { font-size: 36px; font-weight: 700; letter-spacing: -0.5px; line-height: 1.1; }
.pf-hero-sub { font-size: 12px; margin-top: 4px; }
.pf-hero-side { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.pf-metric > div:first-child { font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 4px; }
.pf-metric > .mono { font-size: 18px; font-weight: 700; }
.pf-metric > .small { font-size: 12px; font-weight: 500; }
.alpha-val { font-size: 15px; font-weight: 700; }

.pf-returns {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  padding: 16px 0; border-bottom: 1px solid var(--line); position: relative;
}
.ret-cell { text-align: center; padding: 8px 4px; }
.ret-lbl { font-size: 11px; color: var(--tx-3); margin-bottom: 4px; letter-spacing: 0.3px; }
.ret-cell .mono { font-size: 15px; font-weight: 600; }

.pf-split {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
  padding: 18px 0 8px; border-bottom: 1px solid var(--line); position: relative;
}
.pf-sub-head {
  font-size: 11px; color: var(--tx-3); letter-spacing: 0.6px;
  text-transform: uppercase; margin-bottom: 10px; font-weight: 600;
}
.pf-sub-head.small { margin-bottom: 6px; }
.pf-sub-head.with-badge { display: flex; align-items: center; gap: 8px; }
.badge-count {
  font-size: 10px; padding: 2px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent-2);
  border: 1px solid var(--accent-soft); letter-spacing: 0.5px;
}

/* Pillars */
.pillar-row { display: grid; grid-template-columns: 2fr 3fr 1fr; gap: 8px; align-items: center; margin-bottom: 8px; }
.pillar-head { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.pillar-name { font-weight: 600; }
.pillar-tgt { font-size: 10px; }
.pillar-diff { font-size: 10px; }
.pillar-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.pillar-dot.p-growth  { background: var(--pillar-growth); box-shadow: 0 0 6px var(--pillar-growth); }
.pillar-dot.p-defense { background: var(--pillar-defense); box-shadow: 0 0 6px var(--pillar-defense); }
.pillar-dot.p-flex    { background: var(--pillar-flex); box-shadow: 0 0 6px var(--pillar-flex); }
.pillar-bar { height: 6px; background: var(--bg-3); border-radius: 3px; overflow: hidden; }
.pillar-fill { height: 100%; border-radius: 3px; transition: width 0.6s ease; }
.pillar-fill.p-growth  { background: var(--pillar-growth); }
.pillar-fill.p-defense { background: var(--pillar-defense); }
.pillar-fill.p-flex    { background: var(--pillar-flex); }
.pillar-val { font-size: 13px; font-weight: 600; text-align: right; }

/* Risk grid */
.risk-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.risk-cell { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 10px 12px; }
.risk-cell .muted { font-size: 11px; letter-spacing: 0.3px; }

/* Attribution */
.pf-attr { padding: 14px 0 6px; border-bottom: 1px solid var(--line); position: relative; }
.attr-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.attr-lbl { font-size: 11px; min-width: 52px; letter-spacing: 0.3px; }

/* Chips */
.chip {
  display: inline-flex; align-items: center; padding: 3px 10px;
  border-radius: 999px; font-size: 11px; font-weight: 600;
  border: 1px solid var(--line-2); background: var(--bg-2); color: var(--tx-2);
  text-decoration: none;
}
.chip.chip-up    { color: var(--up-soft); background: var(--up-bg); border-color: rgba(255,59,59,0.25); }
.chip.chip-dn    { color: var(--dn-soft); background: var(--dn-bg); border-color: rgba(27,217,124,0.25); }
.chip.chip-muted { color: var(--tx-3); }
.chip.small { padding: 2px 8px; font-size: 10px; }

/* Alerts */
.pf-alerts { padding-top: 16px; position: relative; }
.alert-list { display: flex; flex-direction: column; gap: 8px; }
.alert-item {
  padding: 10px 14px; border-radius: var(--r-sm); font-size: 13px;
  border: 1px solid; display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
}
.alert-item strong { font-weight: 700; }
.alert-red    { background: var(--up-bg); border-color: rgba(255,59,59,0.3); color: var(--up-soft); }
.alert-green  { background: var(--dn-bg); border-color: rgba(27,217,124,0.3); color: var(--dn-soft); }
.alert-amber  { background: var(--amber-bg); border-color: rgba(255,181,71,0.3); color: var(--amber); }
.alert-purple { background: var(--purple-bg); border-color: rgba(181,132,255,0.3); color: var(--purple); }
.alert-tag {
  display: inline-block; padding: 2px 7px; border-radius: 4px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.8px;
  background: rgba(255,255,255,0.08); border: 1px solid currentColor;
  color: inherit; margin-right: 2px;
}

/* Market chips strip — headline 籌碼 numbers at top of AI tab.
   Data mom quotes in the Threads post: 外資期貨淨 OI、融資餘額、融券張數.
   Each cell is 數字 + 日變化 + 「代表什麼」 short interpretation.
   Appears even before Gemini regenerates, so the numbers are always fresh. */
.market-chips-strip {
  margin: 0 0 14px;
  padding: 12px 14px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
}
.market-chips-strip .mcs-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 10px;
}
.market-chips-strip .mcs-title {
  font-size: 13px; font-weight: 700; color: var(--tx-1);
}
.market-chips-strip .mcs-date { margin-left: 4px; }
.market-chips-strip .mcs-sub { margin-left: auto; font-size: 11px; }
.market-chips-strip .mcs-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}
.mcs-item {
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
  display: flex; flex-direction: column; gap: 3px;
}
.mcs-item.mcs-warn { border-left: 3px solid var(--amber); }
.mcs-item.mcs-good { border-left: 3px solid var(--dn-soft); }
.mcs-item.mcs-neutral { border-left: 3px solid var(--tx-3); }
.mcs-k { font-size: 11.5px; color: var(--tx-3); font-weight: 600; letter-spacing: 0.3px; }
.mcs-v { font-size: 18px; font-weight: 700; color: var(--tx-1); font-variant-numeric: tabular-nums; line-height: 1.2; }
.mcs-v .mcs-unit { font-size: 12px; font-weight: 500; color: var(--tx-2); }
.mcs-i { font-size: 12px; color: var(--tx-2); line-height: 1.4; margin-top: 2px; }
.mcs-item.mcs-warn .mcs-i { color: var(--amber); }
.mcs-item.mcs-good .mcs-i { color: var(--dn-soft); }

/* Validator banner — sits above the AI hero when Python QA catches
   issues in today's Gemini output. Narrative layout: 結論 → 每一筆 影響 + 下一步.
   Goal: insight + next step, not raw flags. */
.validator-banner {
  margin: 0 0 16px;
  padding: 14px 16px;
  border: 1px solid rgba(255,181,71,0.35);
  background: linear-gradient(180deg, var(--amber-bg) 0%, rgba(0,0,0,0) 100%);
  border-radius: var(--r);
}
.validator-banner .vb-head {
  display: flex; align-items: flex-start; gap: 10px;
  color: var(--tx-1);
}
.validator-banner .vb-icon {
  color: var(--amber); display: inline-flex; flex-shrink: 0; margin-top: 2px;
}
.validator-banner .vb-head-text {
  display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0;
}
.validator-banner .vb-conclusion {
  font-size: 14px; font-weight: 700; color: var(--tx-1); line-height: 1.4;
}
.validator-banner .vb-subcopy {
  font-size: 12px; line-height: 1.5;
}
.validator-banner .vb-toggle {
  flex-shrink: 0;
  background: transparent; border: 1px solid var(--line);
  color: var(--tx-2); padding: 3px 10px; border-radius: 4px;
  font-size: 11px; cursor: pointer;
}
.validator-banner .vb-toggle:hover { border-color: var(--accent); color: var(--accent); }
.validator-banner .vb-body {
  margin-top: 14px;
  display: flex; flex-direction: column; gap: 10px;
}
.validator-banner.collapsed .vb-body { display: none; }

/* Per-issue narrative card */
.vb-issue {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-left: 3px solid var(--amber);
  border-radius: 6px;
  padding: 12px 14px;
}
.vb-issue-head {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 8px;
}
.vb-sev-chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.5px;
  flex-shrink: 0;
}
.vb-sev-chip.vb-sev-error {
  background: var(--up-bg); color: var(--up-soft);
  border: 1px solid rgba(255,59,59,0.3);
}
.vb-sev-chip.vb-sev-warn {
  background: var(--amber-bg); color: var(--amber);
  border: 1px solid rgba(255,181,71,0.3);
}
.vb-issue-title {
  margin: 0; font-size: 14px; font-weight: 700; color: var(--tx-1);
  line-height: 1.4;
}
.vb-issue-body {
  display: flex; flex-direction: column; gap: 8px;
}
.vb-row {
  display: grid; grid-template-columns: 52px 1fr; gap: 10px; align-items: start;
}
.vb-row-lbl {
  font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
  color: var(--tx-3); text-align: right; padding-top: 2px;
}
.vb-row-lbl-action { color: var(--accent); }
.vb-row-text {
  margin: 0;
  font-size: 13px; line-height: 1.55; color: var(--tx-1);
}
.vb-next-text {
  margin: 0;
  font-size: 13px; line-height: 1.55; color: var(--tx-1);
}
.vb-next-list {
  margin: 0; padding-left: 18px;
  display: flex; flex-direction: column; gap: 4px;
}
.vb-next-list li {
  font-size: 13px; line-height: 1.55; color: var(--tx-1);
}

@media (max-width: 600px) {
  .vb-row { grid-template-columns: 1fr; gap: 2px; }
  .vb-row-lbl { text-align: left; }
}

/* Sparkline */
.sparkline { display: block; }

/* ── Holdings grid ── */
.holdings-grid { margin: 28px auto; }
.section-head { display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 14px; }
.section-head.mt { margin-top: 30px; }
.section-head h2 { margin: 0; font-size: 17px; font-weight: 700; letter-spacing: 0.2px; }
.hgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.holding-card {
  background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r);
  padding: 16px; color: inherit; text-decoration: none;
  transition: border-color 0.15s, background 0.15s;
  display: flex; flex-direction: column; gap: 8px;
}
.holding-card:hover { border-color: var(--accent); background: var(--bg-2); }
.hc-head { display: flex; justify-content: space-between; align-items: flex-start; }
.hc-sym { font-size: 16px; font-weight: 700; }
.hc-name { font-size: 11px; margin-top: 2px; }
.hc-price-row { display: flex; justify-content: space-between; align-items: baseline; }
.mini-row { display: flex; justify-content: space-between; font-size: 12px; }
.mini-row .mono { font-size: 12px; }
.mini-row .stop-warn { color: var(--amber); }

.wgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.watch-card {
  background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm);
  padding: 12px; color: inherit; text-decoration: none;
  transition: border-color 0.15s, background 0.15s;
  display: flex; flex-direction: column; gap: 6px;
}
.watch-card:hover { border-color: var(--accent); background: var(--bg-2); }
.wc-head { display: flex; justify-content: space-between; align-items: baseline; }
.wc-price { display: flex; justify-content: space-between; align-items: baseline; }

/* ── Brief list ── */
.briefs-section { margin: 30px auto 40px; }
.search-box {
  width: 100%; padding: 12px 16px; background: var(--bg-2);
  color: var(--tx-1); border: 1px solid var(--line-2); border-radius: var(--r-sm);
  font-size: 14px; outline: none; margin-bottom: 14px;
}
.search-box:focus { border-color: var(--accent); background: var(--bg-3); }
.briefs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.brief-card {
  background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r);
  padding: 16px; color: inherit; text-decoration: none;
  transition: border-color 0.15s, background 0.15s;
}
.brief-card:hover { border-color: var(--accent); background: var(--bg-2); }
.bc-top { display: flex; justify-content: space-between; align-items: flex-start; }
.bc-date { font-size: 16px; font-weight: 700; }
.bc-day { font-size: 11px; margin-top: 2px; }
.bc-count { font-size: 12px; margin: 8px 0 10px; }
.bc-tags { display: flex; flex-wrap: wrap; gap: 4px; min-height: 20px; margin-bottom: 10px; }
.bc-link { font-size: 12px; color: var(--accent-2); font-weight: 600; }
.ai-badge {
  font-size: 10px; padding: 2px 8px; background: var(--accent-soft);
  color: var(--accent-2); border-radius: 999px; letter-spacing: 0.3px; font-weight: 600;
}
.empty { text-align: center; color: var(--tx-3); padding: 40px 0; grid-column: 1 / -1; }

/* ── Brief page ── */
.brief-header {
  padding: 24px 20px 16px; border-bottom: 1px solid var(--line);
}
.back { font-size: 13px; color: var(--tx-3); }
.brief-header h1 { margin: 6px 0 2px; font-size: 22px; }
.brief-header .meta { margin: 0; font-size: 12px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.btn-primary, .btn-secondary {
  padding: 9px 14px; border-radius: var(--r-sm); font-size: 13px; font-weight: 600;
  cursor: pointer; border: 1px solid var(--line-2);
  background: var(--bg-3); color: var(--tx-1); font-family: inherit;
}
.btn-primary { background: var(--accent); color: #fff; border-color: transparent; box-shadow: 0 4px 14px var(--accent-glow); }

/* ── Analysis sections ── */
.a-section { padding: 24px 20px; max-width: 1120px; margin: 0 auto; border-bottom: 1px solid var(--line); }
.a-section:last-of-type { border-bottom: none; }

.pulse-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 14px; }
.pulse-cell {
  background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-sm);
  padding: 12px 16px; display: flex; justify-content: space-between; align-items: center;
}
.pulse-narrative {
  font-size: 14.5px; line-height: 1.75; color: var(--tx-1);
  margin: 0; padding: 14px 16px; background: var(--bg-2);
  border-radius: var(--r-sm); border-left: 3px solid var(--accent);
}

.narrative {
  font-size: 14.5px; line-height: 1.85; color: var(--tx-1); margin: 6px 0 10px;
}

.watchpoint-list { margin: 10px 0 0; padding-left: 22px; font-size: 13px; color: var(--tx-2); }
.watchpoint-list li { margin: 5px 0; line-height: 1.65; }

/* Badge */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  border: 1px solid;
}
.badge-up   { color: var(--up-soft); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }
.badge-dn   { color: var(--dn-soft); background: var(--dn-bg); border-color: rgba(27,217,124,0.3); }
.badge-flat { color: var(--tx-2); background: var(--bg-2); border-color: var(--line-2); }
.badge-amber { color: var(--amber); background: var(--amber-bg); border-color: rgba(255,181,71,0.3); }
.badge.large { padding: 5px 14px; font-size: 14px; font-weight: 700; }

/* Diagnosis */
.diag-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.diag-body { background: var(--bg-2); border-radius: var(--r-sm); padding: 14px 16px; border-left: 3px solid var(--amber); }
.diag-row { margin-bottom: 10px; }
.diag-row:last-child { margin-bottom: 0; }
.diag-lbl { display: block; font-size: 11px; color: var(--tx-3); letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 4px; }
.diag-txt { font-size: 14px; line-height: 1.7; }

/* Action checklist */
.actions-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.action-col {
  padding: 14px 16px 10px; border-radius: var(--r-sm);
  border: 1px solid;
}
.action-green  { background: rgba(27,217,124,0.06); border-color: rgba(27,217,124,0.28); }
.action-yellow { background: rgba(255,181,71,0.06); border-color: rgba(255,181,71,0.28); }
.action-red    { background: rgba(255,59,59,0.06);  border-color: rgba(255,59,59,0.28); }
.action-header { font-weight: 700; font-size: 13px; margin-bottom: 10px; letter-spacing: 0.3px; display: flex; align-items: center; gap: 8px; }
.action-tag {
  display: inline-block; padding: 2px 7px; border-radius: 3px;
  font-size: 10px; font-weight: 700; letter-spacing: 1px;
  border: 1px solid currentColor;
  background: rgba(255,255,255,0.04);
}
.action-green .action-tag { color: var(--dn); }
.action-yellow .action-tag { color: var(--amber); }
.action-red .action-tag { color: var(--up); }
.action-col ul { margin: 0; padding-left: 18px; font-size: 13px; }
.action-col li { margin: 8px 0; line-height: 1.6; }
.action-col li.empty { color: var(--tx-3); font-style: italic; }
.action-reason { color: var(--tx-2); font-size: 12px; margin-top: 4px; font-weight: 400; line-height: 1.55; }

/* Topic card */
.topic-card {
  background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r);
  padding: 18px 20px; margin-bottom: 12px;
}
.topic-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.topic-head h3 { margin: 0; font-size: 16px; color: var(--accent-2); }
.topic-tickers { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 10px; }
.topic-points { margin: 10px 0 0; padding-left: 22px; font-size: 13px; color: var(--tx-2); }
.topic-points li { margin: 5px 0; line-height: 1.65; }

/* Holding analysis */
.holding-analysis {
  background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r);
  padding: 18px 20px; margin-bottom: 12px;
}
.ha-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.ha-head h3 { margin: 0; font-size: 15px; }
.ha-head h3 a { color: var(--accent-2); }

/* Bull/bear bar */
.bullbear { margin-top: 12px; }
.bb-bar {
  display: flex; height: 8px; border-radius: 4px; overflow: hidden;
  background: var(--bg-3);
}
.bb-bull { background: linear-gradient(90deg, #ff3b3b, #ff6e6e); }
.bb-neu  { background: var(--tx-4); }
.bb-bear { background: linear-gradient(90deg, #1bd97c, #34e693); }
.bb-legend { display: flex; gap: 14px; font-size: 11px; margin-top: 6px; }
.bb-lbl.bull { color: var(--up-soft); }
.bb-lbl.neu  { color: var(--tx-3); }
.bb-lbl.bear { color: var(--dn-soft); }

.hc-split { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 14px; }
.hc-list-head { font-size: 11px; color: var(--tx-3); letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 6px; font-weight: 600; }
.hc-list { margin: 0 0 10px; padding-left: 20px; font-size: 12.5px; }
.hc-list li { margin: 4px 0; line-height: 1.55; }
.up-list li { color: var(--up-soft); }
.dn-list li { color: var(--dn-soft); }

/* Opportunity card */
.opp-card {
  background: var(--bg-1); border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-sm); padding: 14px 18px; margin-bottom: 10px;
}
.opp-card h3 { margin: 0 0 8px; font-size: 15px; color: var(--accent-2); }
.opp-card p { margin: 6px 0; font-size: 13.5px; line-height: 1.7; color: var(--tx-1); }
.label-inline {
  display: inline-block; font-size: 11px; background: var(--bg-3);
  color: var(--tx-2); padding: 2px 8px; border-radius: 4px;
  margin-right: 8px; font-weight: 600;
}
.label-inline.dn { color: var(--up-soft); background: var(--up-bg); }
.risk-line { color: var(--tx-2); font-size: 13px; }

/* Learning */
.learning-card {
  background: linear-gradient(135deg, rgba(91,141,255,0.08) 0%, var(--bg-1) 100%);
  border: 1px solid var(--accent-soft); border-radius: var(--r);
  padding: 18px 22px;
}
.learning-card h3 { margin: 0 0 8px; font-size: 16px; color: var(--accent-2); }
.learning-card p { margin: 0; font-size: 14.5px; line-height: 1.85; }

/* Disclaimer */
.disclaimer p { margin: 0; font-size: 11px; color: var(--tx-3); line-height: 1.6; text-align: center; }
.disclaimer code { background: var(--bg-2); padding: 1px 6px; border-radius: 3px; }

/* Raw news */
.raw-news { margin: 20px auto 40px; padding: 16px 20px; }
.raw-news > summary {
  cursor: pointer; font-size: 13px; color: var(--tx-3); padding: 6px 0;
  user-select: none; font-weight: 600;
}
.raw-news > summary:hover { color: var(--accent-2); }
.raw-news-body { margin-top: 14px; font-size: 13px; color: var(--tx-2); }
.raw-news-body h2 { font-size: 15px; color: var(--tx-3); margin-top: 22px; }
.raw-news-body h3 { font-size: 13px; color: var(--accent-2); margin-top: 14px; }
.raw-news-body a { word-break: break-word; }
.raw-news-body blockquote {
  margin: 4px 0 8px; padding: 4px 0 4px 12px;
  border-left: 2px solid var(--line-2); color: var(--tx-3); font-size: 12px;
}

/* ── Per-holding deep dive ── */
.dd-hero-row {
  display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 16px;
}
.dd-price { text-align: right; }
.dd-metrics { margin: 20px auto 40px; }
.dd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; margin-bottom: 16px; }
.dd-cell { background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 10px 14px; }
.dd-rets { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; margin-bottom: 16px; background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 12px; }
.dd-ret-cell { text-align: center; padding: 4px; }
.dd-ret-cell .muted { font-size: 11px; margin-bottom: 2px; }
.dd-ret-cell .mono { font-size: 14px; font-weight: 600; }
.dd-52w { margin: 20px 0; padding: 14px 16px; background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm); }
.dd-52w-labels { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 11px; }
.dd-52w-bar { position: relative; height: 6px; background: var(--bg-3); border-radius: 3px; }
.dd-52w-pos { position: absolute; top: -3px; width: 12px; height: 12px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent-glow); transform: translateX(-50%); }
.dd-rules { background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 12px 14px; margin-top: 16px; }
.rule-row { display: flex; align-items: center; gap: 12px; padding: 4px 0; font-size: 13px; }
.rule-row .mono { font-size: 14px; font-weight: 600; }
.dd-ai { margin: 20px auto; padding: 0 20px 30px; }

/* ── Footer ── */
footer { padding: 24px 20px 36px; text-align: center; color: var(--tx-4); font-size: 11px; border-top: 1px solid var(--line); margin-top: 40px; }
footer a { color: var(--tx-3); }

/* ────────────────────────────────────────────────────────────
   APP SHELL — Gushi-Terminal style: left icon nav + top bar + status
   ──────────────────────────────────────────────────────────── */
.shell {
  display: grid;
  grid-template-columns: 64px 1fr;
  min-height: 100vh;
}
.sidenav {
  position: sticky; top: 0;
  height: 100vh;
  display: flex; flex-direction: column;
  align-items: center;
  padding: 14px 0 16px;
  gap: 4px;
  background: var(--bg-1);
  border-right: 1px solid var(--line);
  z-index: 40;
}
.sidenav-logo {
  font-size: 22px;
  width: 40px; height: 40px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 12px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-soft), transparent);
  border: 1px solid var(--accent-soft);
}
.sn-btn {
  position: relative;
  width: 48px; height: 52px;
  background: transparent; border: none; border-radius: 10px;
  color: var(--tx-3); font-family: inherit; cursor: pointer;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 3px;
  transition: all 0.15s;
}
.sn-icon { font-size: 18px; line-height: 1; }
.sn-label {
  font-size: 9px; font-weight: 700;
  font-family: var(--font-mono);
  letter-spacing: 0.4px;
  opacity: 0.6;
}
.sn-btn:hover { background: var(--bg-2); color: var(--tx-1); }
.sn-btn.active {
  background: var(--accent-soft); color: var(--accent-2);
  box-shadow: inset 3px 0 0 var(--accent);
}
.sn-btn.active .sn-label { opacity: 1; }
.sn-badge {
  position: absolute; top: 4px; right: 4px;
  min-width: 16px; padding: 1px 4px;
  font-size: 9px; font-weight: 700;
  background: var(--accent); color: #fff;
  border-radius: 8px; font-family: var(--font-mono);
}

.shell-main { display: flex; flex-direction: column; min-width: 0; min-height: 100vh; }

.top-bar {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 18px;
  padding: 12px 24px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 30;
  backdrop-filter: saturate(180%) blur(10px);
}
.top-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 0; }
.top-title {
  margin: 0; font-size: 13px; font-weight: 700;
  letter-spacing: 0.6px; font-family: var(--font-mono);
}
.top-date {
  font-size: 10px; color: var(--tx-3);
  letter-spacing: 0.5px; text-transform: uppercase;
  padding: 2px 8px; background: var(--bg-3); border-radius: 4px;
}
.top-search-wrap { max-width: 420px; margin: 0 auto; width: 100%; }

.status-bar {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 8px 24px;
  background: var(--bg-1);
  border-top: 1px solid var(--line);
  font-size: 10px; color: var(--tx-3);
  letter-spacing: 0.4px;
  position: sticky; bottom: 0; z-index: 20;
  margin-top: auto;
}
.sb-left, .sb-right { display: flex; align-items: center; gap: 10px; font-size: 10px; }
.sb-lbl { text-transform: uppercase; color: var(--tx-4); font-weight: 700; }
.sb-sep { color: var(--tx-4); }
.sb-div { color: var(--tx-4); font-weight: 400; opacity: 0.6; }
.sb-status { color: var(--up); font-weight: 700; letter-spacing: 0.8px; }
.sb-view { color: var(--accent-2); font-weight: 700; letter-spacing: 0.8px; }

.main-panel {
  flex: 1;
  padding: 22px 28px 40px;
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
}

.main-panel > .tab-panel { display: none; }
.main-panel > .tab-panel.active { display: block; }

.portfolio-detail {
  margin-top: 18px;
  padding: 18px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}
.portfolio-detail .desk-sidebar { position: static; max-height: none; padding: 0; }
.portfolio-detail .stat-block { padding-bottom: 10px; margin-bottom: 0; }

/* Mobile: sidenav becomes bottom bar */
@media (max-width: 720px) {
  .shell { grid-template-columns: 1fr; }
  .sidenav {
    position: fixed;
    bottom: 0; left: 0; right: 0; top: auto;
    height: auto; width: 100%;
    flex-direction: row; justify-content: space-around;
    padding: 8px 4px max(8px, env(safe-area-inset-bottom, 8px));
    border-right: none; border-top: 1px solid var(--line);
    gap: 0;
  }
  .sidenav-logo { display: none; }
  .sn-btn { height: 44px; width: auto; flex: 1; }
  .sn-btn.active { box-shadow: inset 0 3px 0 var(--accent); }
  .sn-badge { top: 0; right: 0; }
  .top-bar { grid-template-columns: 1fr; gap: 10px; padding: 10px 14px; }
  .top-brand { justify-content: space-between; }
  .summary-strip { flex-wrap: wrap; }
  .ss-cell { flex: 1; min-width: 0; padding: 6px 10px; }
  .ss-val { font-size: 12px; }
  .main-panel { padding: 14px 14px 80px; }
  .status-bar { display: none; }
}

/* Legacy class stubs — hide the old top-header and main-tabs so nothing double-renders */
.top-header, .main-tabs { display: none; }


.summary-strip {
  display: grid;
  grid-template-columns: 2fr repeat(auto-fit, minmax(140px, 1fr));
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}
.ss-cell {
  background: var(--bg-1);
  padding: 12px 18px;
  display: flex; flex-direction: column; gap: 4px;
}
.ss-cell.ss-main { background: linear-gradient(90deg, var(--bg-1), var(--bg-2)); }
.ss-cell.ss-alert { background: var(--amber-bg); }
.ss-lbl {
  font-size: 10px; color: var(--tx-3);
  letter-spacing: 0.6px; text-transform: uppercase;
  font-weight: 600;
}
.ss-val { font-size: 15px; font-weight: 700; }
.ss-cell.ss-main .ss-val { font-size: 22px; font-weight: 800; }

.main-tabs {
  display: flex;
  gap: 8px;
  padding: 18px 24px 0;
  max-width: 1200px;
  margin: 0 auto;
  overflow-x: auto;
  scrollbar-width: none;
}
.main-tabs::-webkit-scrollbar { display: none; }
.mt-btn {
  flex: 0 0 auto;
  display: flex; align-items: center; gap: 8px;
  padding: 12px 20px;
  background: var(--bg-1);
  color: var(--tx-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.mt-btn:hover { background: var(--bg-2); color: var(--tx-1); border-color: var(--line-2); }
.mt-btn.active {
  background: var(--accent-soft);
  color: var(--accent-2);
  border-color: var(--accent);
  box-shadow: 0 2px 12px var(--accent-glow);
}
.mt-icon { font-size: 16px; }
.mt-count {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 10px;
  background: var(--bg-3);
  color: var(--tx-3);
  font-family: var(--font-mono);
  font-weight: 700;
}
.mt-btn.active .mt-count { background: var(--accent); color: #fff; }

.main-panel {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px 24px 60px;
}

.portfolio-detail {
  margin-top: 18px;
  padding: 18px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}
.portfolio-detail .desk-sidebar { position: static; max-height: none; padding: 0; }
.portfolio-detail .stat-block { padding-bottom: 10px; margin-bottom: 0; }

/* Tab panel reset */
.main-panel > .tab-panel { display: none; }
.main-panel > .tab-panel.active { display: block; }

/* Mobile: tabs become horizontal scroll, narrower */
@media (max-width: 720px) {
  .top-header { padding: 14px 14px 0; }
  .top-title { font-size: 17px; }
  .summary-strip { grid-template-columns: 1fr 1fr; font-size: 11px; }
  .ss-cell { padding: 10px 12px; }
  .ss-cell.ss-main { grid-column: 1 / -1; }
  .ss-cell.ss-main .ss-val { font-size: 20px; }
  .ss-val { font-size: 13px; }
  .main-tabs { padding: 14px 14px 0; gap: 6px; }
  .mt-btn { padding: 10px 14px; font-size: 13px; }
  .mt-label { display: none; }
  .mt-btn.active .mt-label { display: inline; }  /* Show label only on active on mobile */
  .mt-icon { font-size: 18px; }
  .main-panel { padding: 14px 14px 40px; }
  .portfolio-detail { grid-template-columns: 1fr; padding: 14px; }
}

/* ────────────────────────────────────────────────────────────
   DESK LAYOUT (Hyperdash-style) — (now inside Portfolio tab)
   ──────────────────────────────────────────────────────────── */
.desk-topbar {
  display: flex; align-items: center; gap: 14px;
  padding: 12px 24px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: saturate(180%) blur(10px);
}
.desk-brand { display: flex; align-items: center; gap: 8px; }
.desk-logo { font-weight: 700; font-size: 14px; letter-spacing: 0.3px; }
.desk-breadcrumb {
  color: var(--tx-3); font-size: 10px;
  letter-spacing: 0.6px; text-transform: uppercase;
}
.desk-spacer { flex: 1; }

/* Legacy .desk class — kept for compatibility; only used inside Portfolio tab's grid now */
.desk {
  display: block;
  padding: 0;
  max-width: none;
  margin: 0;
}
.desk-sidebar {
  position: sticky; top: 70px;
  align-self: start;
  max-height: calc(100vh - 90px);
  overflow-y: auto;
  padding-right: 4px;
}
.desk-sidebar::-webkit-scrollbar { width: 6px; }
.desk-sidebar::-webkit-scrollbar-thumb { background: var(--line-2); border-radius: 3px; }

.desk-main {
  display: flex; flex-direction: column; gap: 16px;
  min-width: 0;
}

/* Sidebar stat blocks */
.stat-block { padding-bottom: 16px; margin-bottom: 6px; }
.stat-block-head {
  font-size: 10px;
  color: var(--tx-3);
  letter-spacing: 1.2px;
  text-transform: uppercase;
  font-weight: 700;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 4px;
  font-family: var(--font-mono);
  display: flex; align-items: center; justify-content: space-between;
}
.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 6px 0;
  font-size: 12px;
  gap: 10px;
}
.stat-row + .stat-row { border-top: 1px solid rgba(255,255,255,0.03); }
.stat-lbl {
  color: var(--tx-3);
  font-size: 10px;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  font-family: var(--font-mono);
  white-space: nowrap;
  flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
}
.stat-val {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 13px;
  text-align: right;
  white-space: nowrap;
}
.pillar-stat .stat-val { font-size: 11px; }
.alert-line {
  font-size: 11px;
  padding: 5px 0;
  font-family: var(--font-mono);
  letter-spacing: 0.2px;
}
.alert-line + .alert-line { border-top: 1px solid rgba(255,255,255,0.03); }

/* Macro strip (horizontal cells inside desk-main) */
.macro-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.macro-strip-cell {
  padding: 12px 14px;
  background: var(--bg-1);
  display: flex; flex-direction: column; gap: 2px;
}
.macro-strip-cell .muted { font-size: 10px; letter-spacing: 0.5px; text-transform: uppercase; }
.macro-strip-val { font-size: 18px; font-weight: 700; }
.macro-strip-delta { font-size: 11px; display: flex; gap: 8px; align-items: baseline; }

/* Big chart area */
.chart-area {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 20px;
  position: relative;
  overflow: hidden;
}
.chart-area::after {
  content: "HYPERDASH";
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  font-size: 68px;
  font-weight: 900;
  letter-spacing: 6px;
  color: var(--tx-4);
  opacity: 0.06;
  pointer-events: none;
  font-family: var(--font-mono);
}
.chart-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 14px; position: relative; z-index: 1;
}
.chart-title {
  font-size: 11px; color: var(--tx-3);
  letter-spacing: 0.6px; text-transform: uppercase;
  font-family: var(--font-mono); font-weight: 600;
}
.chart-value { font-size: 26px; font-weight: 700; margin-top: 2px; }
.chart-delta { font-size: 12px; align-self: flex-end; }
.chart-svg { display: block; width: 100%; height: auto; }

/* Tabs */
.desk-panel {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}
.tabs {
  display: flex; gap: 0;
  border-bottom: 1px solid var(--line);
  background: var(--bg-2);
  overflow-x: auto;
}
.tabs::-webkit-scrollbar { display: none; }
.tab-btn {
  padding: 12px 20px;
  background: none; border: none;
  color: var(--tx-3);
  font-family: var(--font-mono);
  font-size: 11px; font-weight: 600;
  letter-spacing: 0.6px; text-transform: uppercase;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
  transition: color 0.15s;
}
.tab-btn:hover { color: var(--tx-1); }
.tab-btn.active {
  color: var(--accent-2);
  border-bottom-color: var(--accent);
  background: var(--bg-1);
}
.tab-panel { display: none; }
.tab-panel.active { display: block; padding: 0; }

/* Data table — shared */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}
.data-table thead { position: sticky; top: 0; background: var(--bg-1); z-index: 1; }
.data-table th {
  text-align: right;
  padding: 10px 12px;
  color: var(--tx-3);
  border-bottom: 1px solid var(--line);
  font-weight: 600;
  font-size: 10px;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  white-space: nowrap;
}
.data-table th:first-child { text-align: left; padding-left: 18px; }
.data-table th:last-child { padding-right: 18px; }
.data-table td {
  padding: 11px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  text-align: right;
  white-space: nowrap;
}
.data-table td:first-child { text-align: left; padding-left: 18px; }
.data-table td:last-child { padding-right: 18px; }
.data-table td.left { text-align: left; }
.data-table tbody tr { cursor: pointer; transition: background 0.1s; }
.data-table tbody tr:hover { background: var(--bg-2); }
.data-table .sub-head td {
  background: var(--bg-2);
  color: var(--tx-3);
  font-size: 10px;
  letter-spacing: 1px;
  text-transform: uppercase;
  font-weight: 700;
  padding: 8px 18px;
  cursor: default;
}
.data-table .sub-head td:hover { background: var(--bg-2); }

.badge-ai {
  display: inline-block; font-size: 9px; font-weight: 700;
  padding: 2px 7px; border-radius: 3px;
  background: var(--accent-soft); color: var(--accent-2);
  letter-spacing: 0.5px;
}

/* GUSHI-style Brief Hero */
.brief-hero {
  padding: 22px 24px 24px;
  border-radius: var(--r);
  background: linear-gradient(135deg, var(--bg-1), var(--accent-soft));
  border: 1px solid var(--accent-soft);
  position: relative;
  overflow: hidden;
  margin-bottom: 14px;
}
.brief-hero::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2), transparent);
}
.bh-top {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 10px; flex-wrap: wrap;
}
.bh-badge {
  font-size: 10px; letter-spacing: 1.2px;
  text-transform: uppercase; color: var(--accent-2);
  font-weight: 700;
}
.bh-spacer { flex: 1; }
.bh-headline {
  margin: 8px 0 10px; font-size: 28px; font-weight: 700;
  letter-spacing: -0.4px; line-height: 1.25;
}
.bh-oneliner {
  margin: 0 0 16px; font-size: 14px;
  color: var(--tx-2); line-height: 1.7;
  max-width: 760px;
}
.shimmer-text {
  background: linear-gradient(90deg, var(--accent) 0%, var(--accent-2) 30%, #fff 50%, var(--accent-2) 70%, var(--accent) 100%);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 4s linear infinite;
  font-weight: 800;
}
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

/* Highlight cards (win / risk / opp) */
.hl-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}
.hl-card {
  padding: 12px 14px;
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  animation: fade-up 0.5s ease-out backwards;
}
@keyframes fade-up {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
.hl-tag {
  display: inline-block;
  font-size: 10px; font-weight: 700;
  padding: 2px 8px; border-radius: 4px;
  letter-spacing: 0.4px;
  font-family: var(--font-mono);
}
.hl-up .hl-tag    { background: var(--up-bg); color: var(--up); }
.hl-amber .hl-tag { background: var(--amber-bg); color: var(--amber); }
.hl-accent .hl-tag{ background: var(--accent-soft); color: var(--accent-2); }
.hl-label { font-size: 13px; font-weight: 600; margin: 6px 0 4px; line-height: 1.4; }
.hl-detail { font-size: 11px; line-height: 1.5; }

/* Mood + Catalyst row — side by side on desktop */
.mood-cat-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 14px;
}
@media (max-width: 720px) {
  .mood-cat-row { grid-template-columns: 1fr; }
  .hl-grid { grid-template-columns: 1fr; }
  .brief-hero { padding: 16px 16px 18px; }
  .bh-headline { font-size: 22px; }
}

/* Market Mood panel */
.mood-panel {
  padding: 16px 18px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
}
.mood-head { margin-bottom: 12px; }
.mood-title {
  font-family: var(--font-mono);
  font-size: 11px; font-weight: 700;
  letter-spacing: 0.8px; text-transform: uppercase;
  color: var(--tx-3);
}
.mood-sub { font-size: 10px; margin-top: 2px; }
.mood-body {
  display: flex; align-items: center; gap: 14px;
  padding: 8px 0; border-bottom: 1px solid var(--line);
}
.mood-donut { flex-shrink: 0; }
.mood-score { flex: 1; }
.mood-score-label { font-size: 15px; font-weight: 700; margin: 2px 0; }
.mood-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px; margin-top: 10px;
}
.mood-mini-cell {
  padding: 8px 10px;
  background: var(--bg-2);
  border-radius: 4px;
  border: 1px solid var(--line);
}
.mood-mini-lbl { font-size: 9px; color: var(--tx-3); letter-spacing: 0.5px; }
.mood-mini-val { font-size: 13px; font-weight: 700; margin: 2px 0; }

/* Catalyst Timeline */
.catalyst-panel {
  padding: 16px 18px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
}
.cat-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--line);
}
.cat-title {
  font-size: 11px; font-weight: 700;
  letter-spacing: 0.8px; text-transform: uppercase;
  color: var(--tx-3);
}
.cat-list { display: flex; flex-direction: column; gap: 10px; }
.cat-item {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 8px 10px;
  background: var(--bg-2);
  border-radius: var(--r-sm);
  border-left: 3px solid var(--line-2);
}
.cat-item.kind-earn  { border-left-color: var(--accent); }
.cat-item.kind-macro { border-left-color: var(--amber); }
.cat-item.kind-event { border-left-color: var(--dn); }
.cat-icon { font-size: 16px; line-height: 1; }
.cat-body { flex: 1; min-width: 0; }
.cat-when {
  color: var(--accent-2); font-size: 11px;
  font-weight: 600; margin-bottom: 2px;
}
.cat-label { font-size: 13px; line-height: 1.5; }

/* Daily Hero — top of main column */
.daily-hero {
  position: relative;
  background: linear-gradient(135deg, rgba(91,141,255,0.08) 0%, var(--bg-1) 50%);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 18px 22px 22px;
  overflow: hidden;
}
.daily-hero::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2), transparent);
}
.hero-top {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 10px; gap: 12px; flex-wrap: wrap;
}
.hero-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hero-badge {
  font-size: 10px; font-weight: 700;
  padding: 3px 10px; border-radius: 4px;
  background: var(--accent-soft); color: var(--accent-2);
  letter-spacing: 0.6px; text-transform: uppercase;
}
.hero-oneliner {
  font-size: 15px; line-height: 1.75; color: var(--tx-1);
  margin: 6px 0 14px;
  font-weight: 500;
}

.hero-action {
  background: rgba(27,217,124,0.06);
  border: 1px solid rgba(27,217,124,0.22);
  border-left: 3px solid var(--dn);
  border-radius: var(--r-sm);
  padding: 12px 14px;
  margin: 8px 0 12px;
}
.hero-action-lbl {
  font-size: 10px; font-weight: 700;
  color: var(--dn-soft);
  letter-spacing: 0.6px; text-transform: uppercase;
  font-family: var(--font-mono);
  margin-bottom: 4px;
}
.hero-action-body strong { font-size: 14px; display: block; margin-bottom: 3px; line-height: 1.55; }
.hero-action-reason { font-size: 12px; line-height: 1.6; }

.hero-picks { margin-top: 10px; }
.hero-picks-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.pick-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
.pick-card {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  text-decoration: none;
  color: inherit;
  transition: transform 0.12s, border-color 0.12s;
  display: flex; flex-direction: column; gap: 4px;
}
.pick-card:hover { border-color: var(--accent); transform: translateY(-1px); }
.pick-head { display: flex; gap: 6px; align-items: baseline; }
.pick-head strong { font-family: var(--font-mono); font-size: 14px; letter-spacing: 0.3px; }
.pick-thesis { color: var(--tx-2); line-height: 1.6; font-size: 12px; }
.pick-risk { line-height: 1.5; font-size: 11px; }

/* Simulator tab */
.sim-tab-body { padding: 20px 22px 24px; display: flex; flex-direction: column; gap: 16px; }
.sim-intro { padding: 8px 10px; background: var(--bg-2); border-radius: var(--r-sm); border-left: 3px solid var(--accent); }
.sim-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
  background: var(--bg-2);
  padding: 16px;
  border-radius: var(--r);
}
.sim-field { display: flex; flex-direction: column; gap: 8px; }
.sim-lbl {
  font-size: 11px;
  color: var(--tx-3);
  letter-spacing: 0.6px;
  text-transform: uppercase;
  font-weight: 700;
  font-family: var(--font-mono);
  display: flex; align-items: center; gap: 6px;
}
.sim-tag {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 9.5px; font-weight: 700; letter-spacing: 1px;
  border: 1px solid currentColor;
}
.sim-field:has(#sim-sl) .sim-tag { color: var(--up); }
.sim-field:has(#sim-tp) .sim-tag { color: var(--dn); }
.sim-input {
  padding: 10px 12px;
  background: var(--bg-1);
  color: var(--tx-1);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 14px;
  outline: none;
  width: 100%;
}
.sim-input:focus { border-color: var(--accent); }
.sim-select { cursor: pointer; }
.sim-budget-row { display: flex; align-items: center; gap: 4px; }
.sim-prefix {
  padding: 10px 8px;
  background: var(--bg-3);
  border-radius: 8px 0 0 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--tx-3);
  border: 1px solid var(--line-2);
  border-right: none;
}
.sim-budget-row .sim-input { border-radius: 0 8px 8px 0; }
.sim-presets { display: flex; gap: 4px; flex-wrap: wrap; }
.sim-chip {
  padding: 4px 10px;
  background: var(--bg-3);
  color: var(--tx-2);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--font-mono);
  cursor: pointer;
  transition: all 0.15s;
}
.sim-chip:hover { background: var(--accent-soft); color: var(--accent-2); border-color: var(--accent); }
.sim-range {
  -webkit-appearance: none;
  width: 100%;
  height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  outline: none;
}
.sim-range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px; height: 18px;
  background: var(--accent);
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 2px 8px var(--accent-glow);
}
.sim-range::-moz-range-thumb {
  width: 18px; height: 18px;
  background: var(--accent);
  border-radius: 50%;
  border: none;
  cursor: pointer;
}
.sim-range-labels { display: flex; justify-content: space-between; }
.sim-ticker-info { font-size: 11px; }

.sim-output {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}
.sim-out-cell {
  background: var(--bg-1);
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px;
}
.sim-out-cell .muted { font-size: 10px; letter-spacing: 0.6px; text-transform: uppercase; }
.sim-out-val { font-size: 18px; font-weight: 700; }

.sim-rules-row {
  padding: 12px 14px;
  background: var(--amber-bg);
  border: 1px solid rgba(255,181,71,0.3);
  border-radius: var(--r-sm);
  font-size: 13px;
}
.sim-rule strong { color: var(--amber); margin-right: 8px; font-size: 12px; }

/* ── Radar tab (GUSHI-style) ── */
.radar-empty { padding: 40px 20px; text-align: center; }
.radar-body { padding: 0; }
.radar-intro { padding: 20px 22px 14px; border-bottom: 1px solid var(--line); }
.radar-title { margin: 0 0 6px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; }

/* Filter + sort controls */
.radar-controls {
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 12px;
  padding: 14px 22px;
  border-bottom: 1px solid var(--line);
  background: var(--bg-2);
}
.radar-filter-group, .radar-sort-group {
  display: flex; align-items: center; gap: 6px;
}
.rc-lbl {
  letter-spacing: 0.6px; font-weight: 700;
  padding-right: 4px;
}
.rc-btn {
  padding: 5px 12px;
  background: transparent;
  color: var(--tx-2);
  border: 1px solid transparent;
  border-radius: 6px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.rc-btn:hover { background: var(--bg-3); color: var(--tx-1); }
.rc-btn.active {
  background: var(--accent-soft); color: var(--accent-2);
  border-color: var(--accent);
}

.radar-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
  gap: 14px;
  padding: 18px 22px;
}
@media (max-width: 720px) {
  .radar-grid { grid-template-columns: 1fr; padding: 14px; }
}

.radar-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 16px 18px 14px;
  display: flex; flex-direction: column; gap: 12px;
  transition: border-color 0.15s, transform 0.15s;
}
.radar-card:hover { border-color: var(--accent); transform: translateY(-1px); }

/* Top row: tag + stage + CONF */
.radar-card-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 8px;
}
.radar-top-left { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.radar-tag {
  font-size: 12px; font-weight: 700;
  padding: 3px 10px;
  background: var(--bg-3); color: var(--tx-2);
  border-radius: 4px;
  letter-spacing: 0.3px;
}
.stage-chip {
  font-size: 11px; font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid;
  letter-spacing: 0.4px;
}
.stage-chip.stage-emerg { color: #b584ff; background: rgba(181,132,255,0.1); border-color: rgba(181,132,255,0.3); }
.stage-chip.stage-early { color: var(--dn-soft); background: var(--dn-bg); border-color: rgba(27,217,124,0.3); }
.stage-chip.stage-mid   { color: var(--accent-2); background: var(--accent-soft); border-color: var(--accent); }
.stage-chip.stage-hot   { color: var(--up-soft); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }
.radar-conf {
  display: flex; align-items: baseline; gap: 6px;
  flex-shrink: 0;
}
.conf-lbl { letter-spacing: 0.6px; font-weight: 700; }
.conf-val { font-size: 26px; font-weight: 800; color: var(--accent-2); }

.radar-headline {
  margin: 0;
  font-size: 16px; font-weight: 700;
  line-height: 1.5;
  color: var(--tx-1);
}

/* CROWD bar */
.crowd-row {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  gap: 10px;
  align-items: center;
}
.crowd-lbl { letter-spacing: 0.6px; font-weight: 700; }
.crowd-bar {
  height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  overflow: hidden;
}
.crowd-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease-out;
}
.crowd-fill.crowd-low  { background: linear-gradient(90deg, var(--dn) 0%, var(--dn-soft) 100%); }
.crowd-fill.crowd-mid  { background: linear-gradient(90deg, var(--accent) 0%, var(--accent-2) 100%); }
.crowd-fill.crowd-high { background: linear-gradient(90deg, var(--amber) 0%, #ffcd78 100%); }
.crowd-fill.crowd-max  { background: linear-gradient(90deg, var(--up) 0%, var(--up-soft) 100%); }
.crowd-val { font-size: 14px; font-weight: 700; }
.crowd-label.crowd-low  { color: var(--dn-soft); }
.crowd-label.crowd-mid  { color: var(--accent-2); }
.crowd-label.crowd-high { color: var(--amber); }
.crowd-label.crowd-max  { color: var(--up-soft); }

.radar-spark { display: block; margin: 2px 0; }

/* Lead stocks chips */
.leads-row {
  display: flex; gap: 6px; flex-wrap: wrap;
}
.lead-chip {
  display: flex; align-items: baseline; gap: 6px;
  padding: 5px 10px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
  color: inherit;
  transition: border-color 0.15s, background 0.15s;
}
.lead-chip:hover { border-color: var(--accent); background: var(--bg-3); }
.lead-sym { font-weight: 700; font-size: 13px; letter-spacing: 0.2px; }
.lead-name { font-size: 11px; }
.lead-chg { font-weight: 700; font-size: 12px; }

/* Signals chips */
.sig-row { display: flex; gap: 4px; flex-wrap: wrap; }
.sig-chip {
  font-size: 10px; font-weight: 600;
  padding: 2px 7px;
  background: var(--accent-soft); color: var(--accent-2);
  border-radius: 3px;
  font-family: var(--font-mono);
  letter-spacing: 0.3px;
}

.radar-why {
  font-size: 12.5px; line-height: 1.65; color: var(--tx-2);
  padding: 10px 12px;
  background: var(--bg-2); border-radius: var(--r-sm);
  border-left: 2px solid var(--accent-soft);
}
.why-lbl { letter-spacing: 0.6px; font-weight: 700; }

.radar-warn {
  padding: 8px 12px;
  background: var(--amber-bg);
  border: 1px solid rgba(255,181,71,0.3);
  border-radius: var(--r-sm);
  color: var(--amber);
  font-size: 11px;
  line-height: 1.5;
  display: flex; align-items: center; gap: 6px;
}
.radar-warn-tag {
  display: inline-block; padding: 2px 6px; border-radius: 3px;
  font-size: 9.5px; font-weight: 700; letter-spacing: 0.8px;
  background: rgba(255,181,71,0.15); border: 1px solid rgba(255,181,71,0.4);
  color: var(--amber);
}

.radar-card-foot {
  display: flex; align-items: center; gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  letter-spacing: 0.5px;
}
.radar-card-foot .sb-sep { color: var(--tx-4); }
.radar-chain-link {
  margin-left: auto;
  color: var(--accent-2);
  font-weight: 700;
  letter-spacing: 0.5px;
}
.radar-chain-link:hover { color: var(--accent); }

.radar-topics { padding: 18px 22px 24px; border-top: 1px solid var(--line); }
.radar-subtitle { margin: 0 0 14px; font-size: 16px; font-weight: 700; }
.radar-topics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.radar-topic {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px 14px;
}
.radar-topic-head {
  display: flex; justify-content: space-between;
  align-items: baseline; margin-bottom: 6px;
}
.radar-topic-head strong { font-size: 14px; color: var(--accent-2); }
.radar-topic .narrative.small { font-size: 12px; line-height: 1.6; color: var(--tx-2); margin: 8px 0 0; }

/* Inline ticker link (inside narratives) */
.tx-link {
  color: var(--accent-2);
  text-decoration: none;
  border-bottom: 1px dashed var(--accent-soft);
}
.tx-link:hover { border-bottom-color: var(--accent); background: var(--accent-soft); padding: 0 2px; border-radius: 2px; }

/* ── Deep page: Recommendation card + News section ── */
.dd-rec-section { margin: 14px auto; }
.dd-rec-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-left: 4px solid var(--line-2);
  border-radius: var(--r);
  padding: 16px 20px;
}
.dd-rec-card.tone-up    { border-left-color: var(--dn); background: linear-gradient(90deg, var(--dn-bg), transparent 40%); }
.dd-rec-card.tone-dn    { border-left-color: var(--up); background: linear-gradient(90deg, var(--up-bg), transparent 40%); }
.dd-rec-card.tone-amber { border-left-color: var(--amber); background: linear-gradient(90deg, var(--amber-bg), transparent 40%); }
.dd-rec-card.tone-flat  { border-left-color: var(--tx-4); }
.dd-rec-head { margin-bottom: 12px; }
.dd-rec-action {
  font-size: 20px; font-weight: 700;
  letter-spacing: -0.2px; margin-top: 4px;
}
.dd-rec-action.up    { color: var(--dn); }
.dd-rec-action.dn    { color: var(--up); }
.dd-rec-action.amber { color: var(--amber); }
.dd-rec-action.flat  { color: var(--tx-1); }
.dd-rec-price-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 12px;
  margin-bottom: 10px;
}
.dd-rec-price-cell {
  padding: 10px 12px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}
.dd-rec-price-cell .muted { font-size: 10px; letter-spacing: 0.4px; text-transform: uppercase; margin-bottom: 3px; }
.dd-rec-reason {
  margin: 8px 0 0;
  font-size: 13px; line-height: 1.7;
  color: var(--tx-2);
}

.dd-news-section { margin: 20px auto 40px; }
.dd-news-list { list-style: none; padding: 0; margin: 0; }
.dd-news-item {
  padding: 12px 16px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  margin-bottom: 8px;
  transition: border-color 0.15s;
}
.dd-news-item:hover { border-color: var(--accent); }
.dd-news-title {
  font-size: 14px; font-weight: 600;
  color: var(--tx-1);
  display: block; line-height: 1.5;
}
.dd-news-title:hover { color: var(--accent-2); }
.dd-news-meta { margin-top: 4px; font-size: 11px; }
.dd-news-summary {
  margin-top: 6px;
  font-size: 12px; line-height: 1.6;
  color: var(--tx-2);
}

/* ── Search box in top header ── */
.top-search-wrap {
  position: relative;
  margin-top: 10px;
  max-width: 420px;
}
.top-search-input {
  width: 100%;
  padding: 10px 14px 10px 36px;
  background: var(--bg-1);
  color: var(--tx-1);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  font-size: 13px;
  font-family: var(--font-mono);
  outline: none;
  transition: border-color 0.15s, background 0.15s;
}
.top-search-input:focus { border-color: var(--accent); background: var(--bg-2); }
.top-search-icon {
  position: absolute; left: 12px; top: 50%;
  transform: translateY(-50%);
  color: var(--tx-3); font-size: 14px;
  pointer-events: none;
}
.top-search-results {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0;
  background: var(--bg-1);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  max-height: 340px;
  overflow-y: auto;
  box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  z-index: 100;
  display: none;
}
.top-search-results.open { display: block; }
.search-result {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--line);
  text-decoration: none;
  color: var(--tx-1);
}
.search-result:last-child { border-bottom: none; }
.search-result:hover { background: var(--bg-2); }
.search-result-sym { font-family: var(--font-mono); font-size: 13px; font-weight: 700; min-width: 60px; }
.search-result-name { flex: 1; font-size: 13px; color: var(--tx-2); }
.search-result-cat { font-size: 10px; color: var(--tx-3); padding: 2px 7px; background: var(--bg-3); border-radius: 3px; }
.search-result-cat.tracked { color: var(--accent-2); background: var(--accent-soft); }
.search-result-cat.untracked { color: var(--tx-3); background: var(--bg-3); }
.search-result.active { background: var(--accent-soft); }

/* Entry strategy buttons */
.sim-entry-group { display: flex; gap: 4px; flex-wrap: wrap; }
.sim-entry-btn {
  flex: 1; min-width: 70px;
  padding: 7px 8px;
  background: var(--bg-3);
  color: var(--tx-2);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--font-mono);
  cursor: pointer;
  transition: all 0.15s;
}
.sim-entry-btn:hover { background: var(--bg-4); }
.sim-entry-btn.active {
  background: var(--accent-soft);
  color: var(--accent-2);
  border-color: var(--accent);
}
.sim-entry-hint { margin-top: 2px; font-size: 11px; line-height: 1.5; }

/* 52w range bar */
.sim-52w-bar { margin-top: 8px; }
.sim-52w-bar:empty { display: none; }
.sim-52w-labels {
  display: flex; justify-content: space-between;
  font-size: 10px;
  color: var(--tx-3);
  font-family: var(--font-mono);
  margin-bottom: 4px;
}
.sim-52w-track {
  position: relative;
  height: 6px;
  background: linear-gradient(90deg, var(--dn-bg), var(--bg-3) 50%, var(--up-bg));
  border-radius: 3px;
}
.sim-52w-cur, .sim-52w-entry {
  position: absolute;
  top: -4px;
  width: 12px; height: 12px;
  border-radius: 50%;
  transform: translateX(-50%);
  border: 2px solid var(--bg-1);
}
.sim-52w-cur { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.sim-52w-entry { background: var(--amber); box-shadow: 0 0 8px rgba(255,181,71,0.5); }

/* Simulator rule-based recommendation */
.sim-rec { margin-top: 8px; }
.sim-rec:empty { display: none; }
.sim-rec-card {
  padding: 10px 12px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-left: 3px solid var(--tx-3);
  border-radius: var(--r-sm);
  display: flex; flex-direction: column; gap: 3px;
}
.sim-rec-card.tone-up { border-left-color: var(--up); background: linear-gradient(90deg, rgba(255,59,59,0.05), var(--bg-2) 50%); }
.sim-rec-card.tone-dn { border-left-color: var(--dn); background: linear-gradient(90deg, rgba(27,217,124,0.05), var(--bg-2) 50%); }
.sim-rec-card.tone-amber { border-left-color: var(--amber); background: linear-gradient(90deg, rgba(255,181,71,0.05), var(--bg-2) 50%); }
.sim-rec-lbl { color: var(--tx-3); letter-spacing: 0.5px; text-transform: uppercase; }
.sim-rec-action { font-size: 14px; font-weight: 700; }
.sim-rec-action.up { color: var(--up); }
.sim-rec-action.dn { color: var(--dn); }
.sim-rec-action.amber { color: var(--amber); }
.sim-rec-action.flat { color: var(--tx-2); }
.sim-rec-price { color: var(--tx-2); }
.sim-rec-price strong { color: var(--accent-2); font-size: 14px; }
.sim-rec-reason { line-height: 1.5; }
.sim-deeplink {
  display: inline-block; margin-top: 6px;
  font-size: 11px; font-family: var(--font-mono);
  color: var(--accent-2); text-decoration: none;
}
.sim-deeplink:hover { color: #b8d0ff; }

/* Budget allocation — hero + full detail */
.hero-budget {
  background: linear-gradient(135deg, rgba(255,181,71,0.08) 0%, var(--bg-2) 100%);
  border: 1px solid rgba(255,181,71,0.25);
  border-left: 3px solid var(--amber);
  border-radius: var(--r-sm);
  padding: 12px 14px;
  margin: 8px 0 12px;
}
.budget-plan {
  font-size: 13px; line-height: 1.65;
  color: var(--tx-1);
  margin: 6px 0 10px;
}
.alloc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
}
.alloc-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  display: flex; flex-direction: column; gap: 5px;
}
.alloc-card.alloc-buy { border-left: 3px solid var(--dn); }
.alloc-card.alloc-cash { border-left: 3px solid var(--tx-4); }
/* Synthetic candidates — padded from opportunities when Gemini is stingy.
   Slightly dimmer border + dashed accent to signal "secondary pick". */
.alloc-card.alloc-synth { border-left: 3px dashed var(--tx-3); opacity: 0.92; }
.alloc-full-card.alloc-synth { border-left: 3px dashed var(--tx-3); opacity: 0.96; }
.alloc-synth-tag {
  display: inline-block; margin-left: 6px; padding: 1px 6px;
  border-radius: 3px; font-size: 9px; font-weight: 600;
  background: var(--bg-2); color: var(--tx-3);
  border: 1px dashed var(--tx-4); letter-spacing: 0.4px;
}
.alloc-head { display: flex; justify-content: space-between; align-items: center; }
.alloc-action {
  font-size: 10px; font-weight: 700;
  padding: 2px 8px; border-radius: 4px;
  background: var(--amber-bg); color: var(--amber);
  letter-spacing: 0.3px;
}
.alloc-conf { font-size: 11px; color: var(--tx-3); }
.alloc-sym strong { font-family: var(--font-mono); font-size: 14px; }
.alloc-levels { color: var(--tx-2); font-size: 11px; line-height: 1.5; }
.alloc-rat { color: var(--tx-2); font-size: 11px; line-height: 1.55; margin-top: 2px; }

.budget-plan-big {
  font-size: 14.5px; line-height: 1.8; padding: 12px 14px;
  background: var(--bg-2); border-radius: var(--r-sm);
  border-left: 3px solid var(--amber);
  margin-bottom: 12px;
}
.alloc-full-card {
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r); padding: 14px 18px; margin-bottom: 10px;
}
.alloc-full-card.alloc-buy { border-left: 3px solid var(--dn); }
.alloc-full-card.alloc-cash { border-left: 3px solid var(--tx-4); }
.alloc-full-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 14px; margin-bottom: 8px;
}
.alloc-action-big {
  font-size: 11px; font-weight: 700;
  color: var(--amber); letter-spacing: 0.5px;
  margin-bottom: 2px;
}
.alloc-full-card h3 { margin: 0; font-size: 17px; }
.alloc-conf-big { font-size: 12px; color: var(--tx-3); }
.alloc-full-right {
  display: flex; flex-direction: column; gap: 8px;
  align-items: flex-end; flex-shrink: 0;
}
.alloc-levels-row { background: var(--bg-2); padding: 8px 12px; border-radius: var(--r-sm); font-size: 12px; margin: 8px 0 10px; line-height: 1.7; }
.alloc-sources { margin: 6px 0; display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }

/* Basket builder — client-side budget-aware picker on hero + #budget section. */
.basket-builder .basket-controls {
  display: flex; flex-direction: column; gap: 8px;
  margin-bottom: 10px;
}
.basket-controls-big {
  background: var(--bg-2); padding: 12px 14px;
  border-radius: var(--r-sm); border: 1px solid var(--line);
  margin-bottom: 14px;
}
.basket-budget-row {
  display: flex; flex-wrap: wrap; align-items: center;
  gap: 8px 10px;
}
.basket-budget-lbl {
  color: var(--tx-3); letter-spacing: 0.5px;
  text-transform: uppercase; font-weight: 700;
  font-family: var(--font-mono); font-size: 11px;
}
.basket-preset-btns { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.basket-preset-btn {
  background: var(--bg-1); border: 1px solid var(--line);
  color: var(--tx-2); padding: 5px 10px;
  border-radius: 999px; font-size: 12px; font-family: var(--font-mono);
  cursor: pointer; transition: all .15s ease;
}
.basket-preset-btn:hover { border-color: var(--accent); color: var(--tx-1); }
.basket-preset-btn.is-active {
  background: var(--accent-bg, rgba(102,144,255,.18));
  border-color: var(--accent); color: var(--accent);
}
.basket-input-wrap {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r-sm); padding: 4px 10px;
  margin-left: auto;
}
.basket-currency { color: var(--tx-3); font-size: 12px; font-family: var(--font-mono); }
.basket-budget-input {
  background: transparent; border: 0; color: var(--tx-1);
  font-size: 14px; font-weight: 600; width: 90px; text-align: right;
  font-family: var(--font-mono);
}
.basket-budget-input:focus { outline: none; color: var(--accent); }
.basket-budget-input::-webkit-outer-spin-button,
.basket-budget-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.basket-hint { margin: 0; line-height: 1.55; }

/* Allocation card checkbox + picked state */
.alloc-card { cursor: pointer; user-select: none; transition: all .15s ease; }
.alloc-card:hover { border-color: var(--accent); }
.alloc-card.alloc-picked {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 6px 14px rgba(102,144,255,0.12);
  background: linear-gradient(135deg, rgba(102,144,255,0.08) 0%, var(--bg-1) 70%);
}
.alloc-head-right { display: inline-flex; align-items: center; gap: 8px; }
.alloc-check {
  width: 16px; height: 16px; cursor: pointer;
  accent-color: var(--accent);
}
.alloc-badge {
  font-size: 10px; letter-spacing: 0.4px; padding: 2px 7px;
  border-radius: 4px; font-family: var(--font-mono); font-weight: 600;
}
.alloc-hold-badge { background: var(--bg-2); color: var(--tx-3); border: 1px solid var(--line); }
.alloc-deeplink {
  display: inline-block; margin-top: 6px;
  color: var(--accent); text-decoration: none;
  font-family: var(--font-mono);
}
.alloc-deeplink:hover { color: var(--tx-1); text-decoration: underline; }

.alloc-pick-lbl {
  display: inline-flex; align-items: center; gap: 5px;
  color: var(--tx-2); cursor: pointer; user-select: none;
  padding: 3px 8px; background: var(--bg-2);
  border-radius: 999px; border: 1px solid var(--line);
  transition: all .15s ease;
}
.alloc-pick-lbl:hover { border-color: var(--accent); color: var(--tx-1); }
.alloc-full-card.alloc-picked {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 6px 14px rgba(102,144,255,0.08);
}

/* Summary bar */
.basket-summary {
  display: flex; flex-wrap: wrap; gap: 10px 18px;
  padding: 10px 12px; margin-top: 10px;
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  align-items: center;
}
.basket-summary-big {
  padding: 14px 18px; font-size: 14px;
  position: sticky; bottom: 12px; z-index: 2;
  background: var(--bg-1);
  box-shadow: 0 4px 16px rgba(0,0,0,0.3);
}
.basket-summary .bs-cell { display: inline-flex; gap: 5px; align-items: baseline; }
.basket-summary .bs-hint { flex: 1; text-align: right; font-family: inherit; }
.basket-summary strong { font-size: 14px; }
.basket-summary-big strong { font-size: 16px; }
.basket-summary .up { color: var(--up); }
.basket-summary .dn { color: var(--dn); }

@media (max-width: 640px) {
  .basket-budget-row { gap: 6px; }
  .basket-input-wrap { margin-left: 0; width: 100%; }
  .basket-budget-input { flex: 1; }
  .basket-summary .bs-hint { display: none; }
}

/* AI tab blocks */
.ai-block { padding: 0 6px; margin-top: 4px; }
.ai-block + .ai-block { padding-top: 10px; border-top: 1px solid var(--line); margin-top: 14px; }
.opp-head h3 { margin: 0; font-size: 15px; color: var(--accent-2); }

/* AI tab compact */
.ai-tab-body { padding: 18px 20px 22px; display: flex; flex-direction: column; gap: 16px; }
.pulse-mini {
  display: grid; grid-template-columns: auto auto 1fr; gap: 14px; align-items: center;
  padding: 12px 16px; background: var(--bg-2); border-radius: var(--r-sm);
  border-left: 3px solid var(--accent);
}
.pulse-mini-cell { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.pulse-mini-summary { color: var(--tx-2); font-size: 13px; line-height: 1.7; }

.diag-compact {
  padding: 12px 16px; background: var(--bg-2); border-radius: var(--r-sm);
  border-left: 3px solid var(--amber);
}
.diag-compact-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.diag-compact-body { font-size: 13px; line-height: 1.7; display: flex; flex-direction: column; gap: 4px; }

.tab-subhead {
  font-size: 11px; color: var(--tx-3);
  letter-spacing: 0.6px; text-transform: uppercase;
  font-weight: 700; font-family: var(--font-mono);
  margin: 6px 0 -2px;
  display: flex; gap: 8px; align-items: baseline;
}

.tab-footer { padding-top: 8px; text-align: right; }
.btn-link { color: var(--accent-2); font-size: 13px; font-weight: 600; font-family: var(--font-mono); }
.btn-link:hover { color: #b8d0ff; }

.empty-state { padding: 60px 0; text-align: center; }

/* Desk mobile fallback — stack sidebar above main */
@media (max-width: 960px) {
  .desk {
    grid-template-columns: 1fr;
    padding: 12px 14px 30px;
    gap: 14px;
  }
  .desk-sidebar {
    position: static;
    max-height: none;
    padding-right: 0;
  }
  .desk-topbar { padding: 10px 14px; flex-wrap: wrap; }
  .desk-breadcrumb { font-size: 9px; }
  .macro-strip { grid-template-columns: repeat(2, 1fr); }
  .data-table { font-size: 11px; }
  .data-table th, .data-table td { padding: 8px 6px; }
  .data-table th:first-child, .data-table td:first-child { padding-left: 12px; }
  .data-table th:last-child, .data-table td:last-child { padding-right: 12px; }
  .stat-block { padding: 12px 14px; background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-sm); }
  .chart-area { padding: 14px; }
  .chart-area::after { font-size: 38px; letter-spacing: 4px; }
  .chart-value { font-size: 22px; }
}

/* Tweaks panel */
.tweaks-panel {
  position: fixed; bottom: 84px; right: 20px;
  width: 280px; z-index: 1000;
  background: var(--bg-1); border: 1px solid var(--line-2);
  border-radius: 14px; padding: 16px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  display: none;
}
.tweaks-panel.open { display: block; }
.tweaks-title {
  font-size: 11px; font-weight: 700;
  letter-spacing: 1px; text-transform: uppercase;
  margin-bottom: 14px;
  display: flex; justify-content: space-between; align-items: center;
  font-family: var(--font-mono);
}
.tweaks-close { background: none; border: none; color: var(--tx-3); cursor: pointer; font-size: 18px; }
.tweak-row { margin-bottom: 14px; }
.tweak-row-label {
  font-size: 10px; color: var(--tx-3); margin-bottom: 6px;
  text-transform: uppercase; letter-spacing: 0.6px; font-family: var(--font-mono);
}
.tweak-row-options { display: flex; gap: 6px; flex-wrap: wrap; }
.tweak-opt {
  padding: 6px 10px; border-radius: 7px;
  background: var(--bg-3); color: var(--tx-2);
  border: 1px solid transparent;
  font-size: 12px; font-weight: 600; font-family: inherit;
  cursor: pointer; transition: all 0.15s;
}
.tweak-opt:hover { background: var(--bg-4); }
.tweak-opt.active {
  background: var(--accent-soft);
  color: var(--accent-2);
  border-color: var(--accent);
}
.color-swatch {
  width: 26px; height: 26px; border-radius: 50%;
  cursor: pointer; border: 2px solid transparent;
  transition: all 0.15s;
}
.color-swatch.active { border-color: var(--tx-1); transform: scale(1.1); }
.fab {
  position: fixed; bottom: 20px; right: 20px;
  width: 48px; height: 48px; border-radius: 50%;
  background: var(--accent); border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
  box-shadow: 0 8px 20px var(--accent-glow);
  z-index: 999; color: white;
}

/* Light theme adjustments */
[data-theme="light"] body { background: var(--bg-0); }
[data-theme="light"] .desk-topbar { background: rgba(255,255,255,0.9); }
[data-theme="light"] .chart-area::after { color: #000; opacity: 0.04; }

/* Density: dense — squeeze padding */
[data-density="dense"] .stat-row { padding: 4px 0; }
[data-density="dense"] .data-table th,
[data-density="dense"] .data-table td { padding: 7px 10px; }
[data-density="dense"] .chart-area { padding: 14px; }

/* Accent overrides */
[data-accent="purple"] { --accent: #a685ff; --accent-2: #bda2ff; --accent-glow: rgba(166,133,255,0.35); --accent-soft: rgba(166,133,255,0.14); }
[data-accent="green"]  { --accent: #3fd99a; --accent-2: #6fe6b5; --accent-glow: rgba(63,217,154,0.35); --accent-soft: rgba(63,217,154,0.14); }
[data-accent="amber"]  { --accent: #ffb547; --accent-2: #ffc878; --accent-glow: rgba(255,181,71,0.4); --accent-soft: rgba(255,181,71,0.14); }

/* ── Mobile ── */
@media (max-width: 720px) {
  .pf-hero { grid-template-columns: 1fr; gap: 14px; }
  .pf-hero-side { grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .pf-hero-val { font-size: 28px; }
  .pf-split { grid-template-columns: 1fr; gap: 16px; }
  .pf-returns { grid-template-columns: repeat(2, 1fr); }
  .pf-top { flex-direction: column; }
  .pf-top-r { width: 100%; overflow: hidden; }
  .site-header h1 { font-size: 22px; }
  .val-xl { font-size: 28px; }
  .dd-rets { grid-template-columns: repeat(3, 1fr); }
  .hc-split { grid-template-columns: 1fr; gap: 10px; }
  .macro-ribbon { grid-template-columns: repeat(2, 1fr); }
  .actions-grid { gap: 8px; }
  .topic-card, .holding-analysis { padding: 14px; }
  .narrative { font-size: 13.5px; }
}

/* ================================================================= */
/* ── GUSHI-style v3 · PORT / MACRO / NEWS / CHAT ────────────────── */
/* ================================================================= */

.pfv2-wrap {
  padding: 20px 24px 40px;
  max-width: 1400px;
  margin: 0 auto;
  display: flex; flex-direction: column; gap: 22px;
}

/* 4 big KPI cards */
.pfv2-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
.pfv2-kpi {
  background: linear-gradient(180deg, var(--bg-1) 0%, var(--bg-2) 100%);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 18px 20px;
  position: relative;
  overflow: hidden;
}
.pfv2-kpi::after {
  content: "";
  position: absolute; inset: 0;
  background: radial-gradient(120% 80% at 0% 0%, var(--accent-soft), transparent 60%);
  pointer-events: none;
  opacity: 0.7;
}
.pfv2-kpi-lbl {
  font-size: 10px; letter-spacing: 1.5px;
  color: var(--tx-3);
  font-weight: 600;
  margin-bottom: 8px;
  position: relative;
}
.pfv2-kpi-val {
  font-size: 26px; font-weight: 700;
  letter-spacing: -0.4px;
  line-height: 1.1;
  position: relative;
}
.pfv2-kpi-sub {
  margin-top: 6px;
  font-size: 11px;
  position: relative;
}

/* Section wrapper reused across PORT / MACRO */
.pfv2-section {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 18px 20px 22px;
}
.pfv2-section .sec-head { margin-bottom: 14px; }

/* Positions table (GUSHI wide) */
.pfv2-table-wrap { overflow-x: auto; }
.pfv2-table {
  width: 100%; border-collapse: collapse;
  font-size: 13px;
}
.pfv2-table thead th {
  text-align: left; padding: 10px 12px;
  font-size: 10px; letter-spacing: 1px; font-weight: 600;
  color: var(--tx-3); border-bottom: 1px solid var(--line);
  background: var(--bg-2);
  font-family: var(--font-mono);
}
.pfv2-table thead th.right { text-align: right; }
.pfv2-table tbody td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.pfv2-table tbody tr { cursor: pointer; transition: background 0.12s; }
.pfv2-table tbody tr:hover { background: var(--bg-2); }
.pfv2-table td.right { text-align: right; }
.pfv2-table td.left { text-align: left; }
.pfv2-table .tk-cell { min-width: 200px; }
.weight-bar-wrap {
  position: relative;
  width: 100px; margin-left: auto;
  height: 18px; background: var(--bg-3);
  border-radius: 3px; overflow: hidden;
}
.weight-bar-fill {
  position: absolute; inset: 0 auto 0 0;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  border-radius: 3px;
  transition: width 0.4s;
}
.weight-bar-val {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-family: var(--font-mono); font-weight: 600;
  color: var(--tx-1);
  text-shadow: 0 0 4px rgba(0,0,0,0.6);
}

/* Weekly attribution bars */
.wk-bars {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 12px;
}
.wk-bar-col {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 12px 8px;
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
}
.wk-bar-stack {
  display: flex; flex-direction: column; align-items: center;
  gap: 4px; height: 120px;
  width: 100%; justify-content: flex-end;
}
.wk-bar-val { font-size: 11px; font-weight: 600; }
.wk-bar-bg {
  width: 28px; height: 80px; background: var(--bg-3);
  border-radius: 4px; overflow: hidden;
  display: flex; align-items: flex-end;
}
.wk-bar-fill {
  width: 100%;
  border-radius: 4px;
  transition: height 0.5s;
}
.wk-bar-fill.wk-bar-up { background: linear-gradient(180deg, var(--up-soft), var(--up)); }
.wk-bar-fill.wk-bar-dn { background: linear-gradient(0deg, var(--dn-soft), var(--dn)); }
.wk-bar-pct { font-size: 11px; font-weight: 600; }
.wk-bar-day { font-size: 10px; letter-spacing: 0.3px; }

/* Risk metrics grid */
.risk-grid-v2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.risk-cell-v2 {
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px 14px;
}
.risk-cell-lbl { font-size: 10px; letter-spacing: 0.8px; margin-bottom: 6px; text-transform: uppercase; }
.risk-cell-val { font-size: 22px; font-weight: 700; line-height: 1.1; }
.risk-cell-sub { font-size: 10px; margin-top: 4px; letter-spacing: 0.3px; }

/* ── Macro tab ────────────────────────────────────────────── */
.macro-hero {
  padding: 4px 0 6px;
}
.macro-hero-title {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.3px;
}
.macro-hero-title .sec-en { font-size: 11px; margin-left: 8px; }
.macro-hero-sub { margin: 0; letter-spacing: 0.4px; }

.macro-banner {
  background: linear-gradient(120deg, rgba(91,141,255,0.08), rgba(91,141,255,0.02));
  border: 1px solid var(--accent-soft);
  border-left: 3px solid var(--accent);
  border-radius: var(--r);
  padding: 16px 18px 14px;
  position: relative;
}
.macro-banner-lbl {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; color: var(--accent-2);
  font-weight: 600; letter-spacing: 1px;
  margin-bottom: 8px;
}
.macro-banner-text {
  margin: 0 0 6px;
  font-size: 14.5px; line-height: 1.75;
  color: var(--tx-1);
}
.macro-banner-impact { margin: 6px 0 0; line-height: 1.6; }
.macro-wp-list {
  margin: 8px 0 0; padding-left: 20px;
  font-size: 12.5px; color: var(--tx-2);
}
.macro-wp-list li { margin: 3px 0; line-height: 1.65; }

/* Global indices grid */
.macro-idx-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
}
.macro-idx-card {
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px 14px;
  display: flex; flex-direction: column; gap: 6px;
  transition: border-color 0.12s, background 0.12s;
}
.macro-idx-card:hover { border-color: var(--accent); background: var(--bg-3); }
.macro-idx-card.empty { opacity: 0.5; }
.macro-idx-head {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 11px; letter-spacing: 0.5px;
}
.macro-idx-head .mono { font-weight: 700; color: var(--tx-1); }
.macro-idx-val {
  font-size: 20px; font-weight: 700; line-height: 1.1;
  letter-spacing: -0.3px;
}
.macro-idx-delta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  font-size: 11px;
}
.macro-idx-ytd, .macro-idx-52w { letter-spacing: 0.3px; }
.macro-idx-spark { margin-top: 2px; }
.macro-idx-spark svg { display: block; width: 100%; height: 36px; }

/* Risk map */
.risk-map {
  display: flex; flex-direction: column; gap: 8px;
}
.risk-map-row {
  display: grid; grid-template-columns: 1fr 2fr auto;
  gap: 14px; align-items: center;
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px 16px;
}
.risk-map-name { display: flex; flex-direction: column; gap: 2px; }
.risk-map-name .mono { font-size: 12px; font-weight: 700; letter-spacing: 0.5px; color: var(--tx-1); }
.risk-map-name .muted { font-size: 11px; letter-spacing: 0.3px; }
.risk-map-detail { font-size: 12px; line-height: 1.55; }
.risk-map-level {
  font-size: 11px; font-weight: 700; padding: 4px 12px;
  letter-spacing: 1px; border-radius: 4px;
  border: 1px solid;
}
.risk-low  { color: var(--dn); background: var(--dn-bg); border-color: rgba(27,217,124,0.3); }
.risk-mid  { color: var(--amber); background: var(--amber-bg); border-color: rgba(255,181,71,0.3); }
.risk-high { color: var(--up); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }

/* ── News tab · tier badges ─────────────────────────────────── */
.news-filter-bar {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 4px 0 8px;
}
.news-pill {
  padding: 6px 12px;
  background: var(--bg-2); color: var(--tx-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 11px; letter-spacing: 0.6px;
  cursor: pointer; transition: all 0.12s;
}
.news-pill:hover { border-color: var(--accent); color: var(--tx-1); }
.news-pill.active {
  background: var(--accent-soft); color: var(--accent-2);
  border-color: var(--accent); font-weight: 600;
}
.news-feed {
  display: flex; flex-direction: column; gap: 10px;
}
.news-card {
  background: var(--bg-1); border: 1px solid var(--line);
  border-left: 3px solid var(--line-2);
  border-radius: var(--r-sm);
  padding: 14px 16px 12px;
  transition: border-color 0.12s, background 0.12s;
}
.news-card:hover { border-color: var(--accent); background: var(--bg-2); }
.news-card[data-tier="T1"] { border-left-color: var(--up); }
.news-card[data-tier="T2"] { border-left-color: var(--accent); }
.news-card[data-tier="T3"] { border-left-color: var(--tx-4); }
.news-card[data-kind="BREAKING"] { border-left-color: var(--up); background: rgba(255,59,59,0.04); }

.news-card-head {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  margin-bottom: 8px;
  font-size: 10px; letter-spacing: 0.5px;
}
.news-tier {
  padding: 2px 7px; border-radius: 3px;
  font-weight: 700; letter-spacing: 1px;
  border: 1px solid;
}
.news-tier-t1 { color: var(--up); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }
.news-tier-t2 { color: var(--accent-2); background: var(--accent-soft); border-color: var(--accent-soft); }
.news-tier-t3 { color: var(--tx-3); background: var(--bg-2); border-color: var(--line); }

.news-kind {
  padding: 2px 7px; border-radius: 3px;
  font-weight: 700;
  background: var(--bg-3); color: var(--tx-2);
  border: 1px solid var(--line);
}
.news-kind-breaking { color: var(--up); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }
.news-kind-broker   { color: var(--purple); background: var(--purple-bg); border-color: rgba(181,132,255,0.3); }
.news-kind-media    { color: var(--tx-2); }
.news-kind-macro    { color: var(--accent-2); background: var(--accent-soft); border-color: var(--accent-soft); }
.news-kind-data     { color: var(--amber); background: var(--amber-bg); border-color: rgba(255,181,71,0.3); }

.news-source { font-weight: 600; color: var(--tx-2); }
.news-time { font-family: var(--font-mono); }
.news-spacer { flex: 1; }
.news-impact {
  padding: 2px 7px; border-radius: 3px; font-weight: 700; letter-spacing: 0.8px;
  border: 1px solid;
}
.news-impact-high { color: var(--up); background: var(--up-bg); border-color: rgba(255,59,59,0.3); }
.news-impact-mid  { color: var(--amber); background: var(--amber-bg); border-color: rgba(255,181,71,0.3); }
.news-impact-low  { color: var(--tx-3); background: var(--bg-2); border-color: var(--line); }

.news-title {
  display: block;
  font-size: 15px; font-weight: 600;
  color: var(--tx-1); margin: 2px 0 4px;
  line-height: 1.5; letter-spacing: -0.1px;
}
.news-title:hover { color: var(--accent-2); }
.news-summary {
  margin: 4px 0 8px; line-height: 1.65;
}
.news-tickers {
  display: flex; flex-wrap: wrap; gap: 6px;
}
.news-ticker-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px;
  background: var(--bg-2); border: 1px solid var(--line-2);
  border-radius: 999px;
  color: var(--tx-2); font-size: 11px;
}
.news-ticker-chip:hover { border-color: var(--accent); color: var(--accent-2); background: var(--bg-3); }
.news-ticker-chip .mono { font-weight: 700; color: var(--tx-1); }

/* ── Chat tab ─────────────────────────────────────────────── */
.chat-shell {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1px; background: var(--line);
  min-height: calc(100vh - 140px);
  margin: 0; padding: 0;
}
.chat-sidebar {
  background: var(--bg-1);
  padding: 16px;
  display: flex; flex-direction: column; gap: 10px;
  overflow-y: auto;
}
.chat-sidebar-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 4px;
  font-size: 11px; letter-spacing: 1px;
  color: var(--tx-3); font-weight: 600;
  text-transform: uppercase;
}
.chat-new-btn {
  background: var(--accent-soft); color: var(--accent-2);
  border: 1px solid var(--accent-soft);
  border-radius: var(--r-sm);
  padding: 5px 10px; font-size: 11px;
  cursor: pointer;
}
.chat-new-btn:hover { background: var(--accent); color: #fff; }
.chat-thread-list {
  display: flex; flex-direction: column; gap: 4px;
}
.chat-thread {
  display: flex; flex-direction: column; gap: 4px;
  padding: 10px 12px;
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  color: var(--tx-1); text-decoration: none;
  transition: border-color 0.12s, background 0.12s;
}
.chat-thread:hover { border-color: var(--accent); background: var(--bg-3); }
.chat-thread.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.chat-thread-title { font-size: 13px; font-weight: 600; line-height: 1.4; }
.chat-thread-meta, .chat-thread-preview { font-size: 11px; line-height: 1.4; }

.chat-main {
  background: var(--bg-0);
  display: flex; flex-direction: column;
}
.chat-main-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 18px 22px 14px; border-bottom: 1px solid var(--line);
  gap: 16px; flex-wrap: wrap;
}
.chat-title { margin: 0 0 4px; font-size: 18px; font-weight: 700; letter-spacing: -0.2px; }
.chat-model-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chat-model-chip {
  padding: 3px 9px;
  background: var(--bg-2); border: 1px solid var(--line-2);
  border-radius: 999px; font-size: 10px; letter-spacing: 0.8px;
  color: var(--tx-2); font-weight: 600;
}
.chat-feed {
  flex: 1; overflow-y: auto;
  padding: 22px; display: flex; flex-direction: column; gap: 16px;
}
.chat-msg { display: flex; flex-direction: column; gap: 4px; max-width: 760px; }
.chat-msg-user { align-self: flex-end; align-items: flex-end; }
.chat-msg-user .chat-msg-body {
  background: var(--accent); color: #fff;
  padding: 10px 14px; border-radius: 14px 14px 4px 14px;
  font-size: 14px; line-height: 1.6;
}
.chat-msg-ai .chat-msg-body {
  background: var(--bg-1); border: 1px solid var(--line);
  padding: 14px 16px; border-radius: 4px 14px 14px 14px;
  font-size: 14px; line-height: 1.7; color: var(--tx-1);
}
.chat-msg-meta {
  display: flex; align-items: center; gap: 6px;
  color: var(--tx-3);
  letter-spacing: 0.5px;
}
.chat-sources {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  padding: 8px 0 0;
}
.chat-source-chip {
  display: inline-flex; align-items: center;
  padding: 3px 8px;
  background: var(--bg-2); border: 1px solid var(--line-2);
  border-radius: 4px;
  font-size: 10px; color: var(--tx-2); letter-spacing: 0.5px;
}
.chat-source-chip:hover { border-color: var(--accent); color: var(--accent-2); }
.chat-empty-note {
  margin: 20px 0 0;
  padding: 12px 14px;
  border: 1px dashed var(--line-2);
  border-radius: var(--r-sm);
  background: var(--bg-1);
  line-height: 1.65;
}

.chat-suggest-bar {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 10px 22px; border-top: 1px solid var(--line);
  background: var(--bg-1);
}
.chat-suggest-chip {
  padding: 6px 12px;
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 11px; letter-spacing: 0.3px;
  color: var(--tx-2); cursor: pointer;
  transition: border-color 0.12s, color 0.12s;
}
.chat-suggest-chip:hover { border-color: var(--accent); color: var(--accent-2); }

.chat-input-bar {
  display: flex; gap: 8px;
  padding: 14px 22px 20px;
  background: var(--bg-1); border-top: 1px solid var(--line);
}
.chat-input {
  flex: 1;
  padding: 12px 14px;
  background: var(--bg-2); color: var(--tx-1);
  border: 1px solid var(--line-2); border-radius: var(--r-sm);
  font-size: 13px;
  outline: none;
}
.chat-input:focus { border-color: var(--accent); background: var(--bg-3); }
.chat-input:disabled { opacity: 0.6; cursor: not-allowed; }
.chat-send {
  padding: 10px 20px;
  background: var(--accent); color: #fff;
  border: none; border-radius: var(--r-sm);
  font-size: 13px; font-weight: 600; letter-spacing: 0.5px;
  cursor: pointer;
}
.chat-send:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── NEW Chat tab (rebuild v8) · honest FAQ + Claude Code CTA ── */
.ch-shell {
  max-width: 1100px; margin: 0 auto;
  padding: 24px 24px 60px; display: flex; flex-direction: column; gap: 24px;
}

.ch-cta-box {
  padding: 24px 28px; background: linear-gradient(120deg, var(--bg-1), var(--bg-2));
  border: 1px solid var(--line); border-radius: var(--r-sm);
  position: relative; overflow: hidden;
}
.ch-cta-box::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--accent), var(--up), var(--amber));
  opacity: 0.8;
}
.ch-cta-head { display: flex; gap: 18px; align-items: flex-start; margin-bottom: 18px; }
.ch-cta-icon {
  color: var(--accent); flex-shrink: 0;
  width: 44px; height: 44px; border-radius: 50%;
  background: rgba(108,180,255,0.1);
  display: flex; align-items: center; justify-content: center;
  border: 1px solid rgba(108,180,255,0.3);
}
.ch-cta-title { margin: 0 0 6px; font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }
.ch-cta-sub { font-size: 13.5px; line-height: 1.6; margin: 0; }
.ch-cta-sub strong { color: var(--tx-1); font-weight: 700; }

.ch-cta-buttons { display: flex; flex-wrap: wrap; gap: 10px; }
.ch-cta-btn {
  padding: 11px 20px; background: var(--bg-2); color: var(--tx-1);
  border: 1px solid var(--line-2); border-radius: var(--r-sm);
  text-decoration: none; font-size: 13px; font-weight: 600;
  letter-spacing: 0.3px; transition: all 0.15s;
}
.ch-cta-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-bg); }
.ch-cta-btn-primary {
  background: var(--accent); color: #fff; border-color: var(--accent);
}
.ch-cta-btn-primary:hover {
  background: var(--accent-2, var(--accent)); color: #fff;
  opacity: 0.92; transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(108,180,255,0.3);
}

.ch-section { display: flex; flex-direction: column; gap: 12px; }
.ch-section-head {
  display: flex; align-items: baseline; justify-content: space-between;
  padding-bottom: 6px; border-bottom: 1px solid var(--line);
  gap: 12px; flex-wrap: wrap;
}
.ch-section-title {
  font-size: 12.5px; letter-spacing: 1px; font-weight: 700; margin: 0;
  color: var(--tx-1); text-transform: uppercase;
}
.ch-section-sub { margin: -4px 0 4px; line-height: 1.55; }

.ch-faq-list { display: flex; flex-direction: column; gap: 6px; }
.ch-faq {
  padding: 12px 16px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: var(--r-sm);
  transition: border-color 0.15s;
}
.ch-faq:hover { border-color: var(--line-2); }
.ch-faq[open] { border-color: var(--accent); background: rgba(108,180,255,0.04); }
.ch-faq summary {
  cursor: pointer; list-style: none; display: flex;
  align-items: center; gap: 10px;
}
.ch-faq summary::-webkit-details-marker { display: none; }
.ch-faq summary::after {
  content: '＋'; margin-left: auto;
  color: var(--tx-3); font-weight: 700; font-size: 16px;
  transition: transform 0.15s;
}
.ch-faq[open] summary::after { content: '−'; color: var(--accent); }
.ch-faq-tag {
  padding: 2px 8px; background: var(--bg); color: var(--tx-3);
  border: 1px solid var(--line-2); border-radius: 3px;
  font-weight: 700; letter-spacing: 0.5px; flex-shrink: 0;
}
.ch-faq-q { font-size: 14px; color: var(--tx-1); font-weight: 600; flex: 1; }
.ch-faq-a {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line);
  font-size: 13.5px; line-height: 1.7; color: var(--tx-2);
}

.ch-card-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
.ch-card {
  padding: 14px 16px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: var(--r-sm);
  display: flex; flex-direction: column; gap: 8px;
}
.ch-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.ch-card-title { font-size: 12.5px; color: var(--tx-1); font-weight: 700; letter-spacing: 0.3px; }
.ch-copy-btn {
  padding: 4px 10px; background: var(--accent); color: #fff;
  border: none; border-radius: 3px; cursor: pointer; font-weight: 700;
  letter-spacing: 0.5px; transition: all 0.15s;
}
.ch-copy-btn:hover { opacity: 0.92; transform: translateY(-1px); }
.ch-copy-btn.ch-copy-success { background: var(--dn); }
.ch-card-body {
  width: 100%; min-height: 120px; max-height: 180px;
  padding: 10px 12px; background: var(--bg);
  border: 1px solid var(--line-2); border-radius: 3px;
  color: var(--tx-2); font-size: 11.5px; line-height: 1.55;
  resize: vertical; font-family: var(--font-mono);
  white-space: pre-wrap; word-break: break-word;
}
.ch-card-body:focus { outline: none; border-color: var(--accent); }

.ch-why {
  padding: 16px 20px; background: var(--bg-2);
  border-radius: var(--r-sm); border-left: 3px solid var(--amber);
}
.ch-why .ch-section-head { border-bottom: none; padding-bottom: 0; margin-bottom: 8px; }
.ch-why-list {
  margin: 0; padding-left: 22px; color: var(--tx-2);
  font-size: 13px; line-height: 1.7; list-style: disc;
}
.ch-why-list li { margin: 6px 0; }
.ch-why-list strong { color: var(--tx-1); font-weight: 700; }

@media (max-width: 720px) {
  .ch-shell { padding: 16px 16px 40px; }
  .ch-cta-title { font-size: 18px; }
  .ch-card-grid { grid-template-columns: 1fr; }
}

/* ── Stock deep page · AI VERDICT + tabs ─────────────────── */
.dd-verdict-card {
  display: grid;
  grid-template-columns: auto 1fr 320px;
  gap: 24px;
  align-items: center;
  padding: 20px 24px;
  margin: 20px auto 0;
  background: linear-gradient(120deg, var(--bg-1), var(--bg-2));
  border: 1px solid var(--line);
  border-radius: var(--r);
  max-width: 1120px;
}
.verdict-left { display: flex; align-items: center; justify-content: center; }
.verdict-dial-wrap { position: relative; }
.verdict-dial { display: block; filter: drop-shadow(0 0 14px var(--accent-glow)); }
.verdict-dial-wrap.tone-up .verdict-dial { filter: drop-shadow(0 0 14px rgba(255,59,59,0.4)); }
.verdict-dial-wrap.tone-dn .verdict-dial { filter: drop-shadow(0 0 14px rgba(27,217,124,0.4)); }
.verdict-dial-wrap.tone-amber .verdict-dial { filter: drop-shadow(0 0 14px rgba(255,181,71,0.4)); }

.verdict-mid { display: flex; flex-direction: column; gap: 6px; }
.verdict-lbl {
  font-size: 11px; letter-spacing: 1.5px; font-weight: 600;
  color: var(--tx-3);
}
.verdict-action {
  font-size: 32px; font-weight: 800;
  letter-spacing: 1.5px; line-height: 1.1;
  font-family: var(--font-mono);
}
.verdict-narrative { font-size: 14px; line-height: 1.7; color: var(--tx-1); margin-top: 4px; }
.verdict-meta { margin-top: 6px; letter-spacing: 0.4px; }

.verdict-right { padding-left: 6px; }
.sentiment-bar-box {
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px 14px;
}
.sentiment-bar-head {
  display: flex; justify-content: space-between;
  font-size: 10px; letter-spacing: 0.8px; font-weight: 600;
  color: var(--tx-3);
  margin-bottom: 8px;
}
.sentiment-bar {
  display: flex;
  height: 10px;
  background: var(--bg-3);
  border-radius: 5px;
  overflow: hidden;
}
.sentiment-seg {
  height: 100%;
  position: relative;
  transition: width 0.5s;
}
.sentiment-seg span {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-family: var(--font-mono); font-weight: 700;
  color: rgba(255,255,255,0.85);
}
.sb-bull { background: linear-gradient(90deg, var(--up-soft), var(--up)); }
.sb-neu  { background: var(--tx-4); }
.sb-bear { background: linear-gradient(90deg, var(--dn), var(--dn-soft)); }
.sentiment-counts { margin-top: 6px; letter-spacing: 0.4px; }

/* Deep page tabs */
.dd-tabs {
  display: flex; flex-wrap: wrap; gap: 2px;
  margin: 18px auto 0;
  border-bottom: 1px solid var(--line);
  max-width: 1120px;
}
.dd-tab {
  padding: 10px 16px;
  background: transparent; color: var(--tx-3);
  border: none; border-bottom: 2px solid transparent;
  font-size: 12px; letter-spacing: 0.5px; font-weight: 600;
  font-family: var(--font-sans);
  cursor: pointer;
  transition: color 0.12s, border-color 0.12s;
}
.dd-tab:hover { color: var(--tx-1); }
.dd-tab.active {
  color: var(--accent-2);
  border-bottom-color: var(--accent);
}
.dd-panel { display: none; padding: 14px 0 32px; }
.dd-panel.active { display: block; }
.dd-panel > section:first-child { margin-top: 16px; }

.dd-stub { padding: 14px 0 32px; }
.dd-stub-box {
  max-width: 720px; margin: 14px auto;
  padding: 28px 24px;
  background: var(--bg-1); border: 1px dashed var(--line-2);
  border-radius: var(--r);
  text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
}
.dd-stub-icon { color: var(--accent); opacity: 0.6; }
.dd-stub-title { font-size: 15px; font-weight: 600; color: var(--tx-1); }

/* ── Mobile adjustments for v3 ───────────────────────────── */
@media (max-width: 860px) {
  .pfv2-wrap { padding: 14px; gap: 16px; }
  .pfv2-kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .pfv2-kpi-val { font-size: 20px; }
  .risk-map-row { grid-template-columns: 1fr; gap: 4px; }
  .risk-map-level { align-self: flex-start; }
  .chat-shell { grid-template-columns: 1fr; }
  .chat-sidebar { max-height: 200px; }
  .weight-bar-wrap { width: 80px; }
  .dd-verdict-card { grid-template-columns: auto 1fr; gap: 16px; padding: 16px; }
  .verdict-right { grid-column: 1 / -1; padding-left: 0; }
  .verdict-action { font-size: 24px; }
  .dd-tabs { padding: 0 14px; }
}

/* ── Stock deep-dive FINANCIALS panel (Phase B) ── */
.dd-fin-score {
  margin: 0 auto 20px;
  padding: 16px 18px;
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r-sm);
}
.dd-fin-score-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 12px; letter-spacing: 0.6px;
}
.dd-fin-score-val { font-size: 14px; color: var(--tx-1); font-weight: 700; }
.dd-lights-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.dd-light {
  padding: 10px 12px; border-radius: var(--r-sm);
  border: 1px solid; background: var(--bg-2);
}
.dd-light-green { border-color: rgba(27,217,124,0.3); }
.dd-light-amber { border-color: rgba(255,181,71,0.3); }
.dd-light-red   { border-color: rgba(255,59,59,0.3); }
.dd-light-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.dd-light-lbl { letter-spacing: 0.4px; color: var(--tx-3); font-weight: 700; }
.dd-light-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dd-light-dot-green { background: var(--dn); box-shadow: 0 0 6px rgba(27,217,124,0.5); }
.dd-light-dot-amber { background: var(--amber); box-shadow: 0 0 6px rgba(255,181,71,0.5); }
.dd-light-dot-red   { background: var(--up); box-shadow: 0 0 6px rgba(255,59,59,0.5); }
.dd-light-val { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
.dd-light-green .dd-light-val { color: var(--dn); }
.dd-light-amber .dd-light-val { color: var(--amber); }
.dd-light-red   .dd-light-val { color: var(--up); }
.dd-light-hint { font-size: 10.5px; line-height: 1.4; }
.dd-fin-disclaimer { margin-top: 12px; line-height: 1.5; }
.dd-fin-sector { margin-bottom: 12px; }
.dd-fin-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px; margin-top: 8px;
}
.dd-fin-row {
  padding: 10px 12px; background: var(--bg-1);
  border: 1px solid var(--line); border-radius: var(--r-sm);
}
.dd-fin-lbl { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.dd-fin-cn { font-size: 12px; color: var(--tx-2); }
.dd-fin-en { letter-spacing: 0.4px; }
.dd-fin-val { font-size: 16px; font-weight: 700; margin-top: 4px; color: var(--tx-1); }
.dd-fin-hint { font-size: 10px; margin-top: 2px; line-height: 1.4; }
.dd-fin-foot { margin-top: 16px; text-align: right; font-size: 10.5px; }

/* ── Theme deep-dive page (v8 Phase A) ── */
.th-page { max-width: 1400px; margin: 0 auto; padding: 24px 20px 60px; }
.th-back {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--tx-3); font-size: 12px; text-decoration: none;
  font-family: var(--font-mono); letter-spacing: 0.5px;
}
.th-back:hover { color: var(--accent); }
.th-hero {
  padding: 24px 0 18px; border-bottom: 1px solid var(--line);
  margin-bottom: 22px;
}
.th-tag-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.th-tag {
  display: inline-block; padding: 4px 10px; border-radius: 4px;
  background: var(--bg-2); color: var(--accent);
  font-size: 11px; font-weight: 700; letter-spacing: 0.6px;
  border: 1px solid var(--line-2);
}
.th-timeframe { padding-left: 8px; }
.th-title {
  font-size: 28px; margin: 0 0 8px; font-weight: 700;
  letter-spacing: 0.2px;
}
.th-headline {
  font-size: 15px; color: var(--tx-2); line-height: 1.55;
  max-width: 900px;
}
.th-stats {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 14px; margin-bottom: 22px;
}
.th-stat {
  padding: 14px 16px; background: var(--bg-1);
  border: 1px solid var(--line); border-radius: var(--r-sm);
}
.th-stat-lbl { letter-spacing: 0.6px; margin-bottom: 6px; }
.th-stat-val { font-size: 22px; font-weight: 700; }
.th-crowd-row { display: flex; align-items: center; gap: 10px; margin: 4px 0; }
.th-crowd-bar {
  flex: 1; height: 6px; background: var(--bg-2); border-radius: 3px; overflow: hidden;
}
.th-crowd-fill { height: 100%; transition: width .3s; }
.th-crowd-fill.crowd-low  { background: var(--dn); }
.th-crowd-fill.crowd-mid  { background: var(--amber); }
.th-crowd-fill.crowd-high { background: var(--up); }
.th-crowd-label.crowd-low  { color: var(--dn); }
.th-crowd-label.crowd-mid  { color: var(--amber); }
.th-crowd-label.crowd-high { color: var(--up); }

.th-section { margin: 22px 0; }
.th-section-head {
  font-size: 11px; letter-spacing: 1.2px; color: var(--tx-3);
  font-weight: 700; margin-bottom: 10px;
}
.th-why {
  padding: 18px 20px; background: var(--bg-1);
  border: 1px solid var(--line); border-radius: var(--r-sm);
  margin-bottom: 18px;
  border-left: 3px solid var(--accent);
}
.th-why-body { font-size: 14px; line-height: 1.7; color: var(--tx-1); }

.th-warn {
  margin: 14px 0; padding: 12px 16px;
  background: var(--amber-bg); border: 1px solid rgba(255,181,71,0.3);
  border-radius: var(--r-sm); color: var(--amber);
  font-size: 13px; display: flex; align-items: center; gap: 8px;
}
.th-warn-tag {
  padding: 2px 7px; font-size: 10px; border-radius: 3px;
  border: 1px solid currentColor; letter-spacing: 0.8px;
  background: rgba(255,181,71,0.1);
}

/* Head-to-head Tetsu-style card */
.th-h2h {
  margin: 22px 0;
  background: linear-gradient(135deg, rgba(102,144,255,0.06), var(--bg-1));
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 18px 20px;
}
.th-h2h-head {
  display: flex; flex-wrap: wrap; justify-content: space-between;
  align-items: baseline; gap: 8px 14px; margin-bottom: 10px;
}
.h2h-ai-badge {
  color: var(--accent); background: rgba(102,144,255,0.12);
  padding: 2px 8px; border-radius: 4px;
  border: 1px solid rgba(102,144,255,0.3);
  letter-spacing: 0.3px;
}
.h2h-synth-badge {
  color: var(--tx-3);
  letter-spacing: 0.3px;
}
.th-h2h .th-section-head { margin: 0; }
.h2h-verdict {
  font-size: 15px; line-height: 1.7; color: var(--tx-1);
  padding: 12px 14px; margin-bottom: 14px;
  background: var(--bg-2); border-left: 3px solid var(--accent);
  border-radius: var(--r-sm); font-weight: 500;
}
.h2h-grid {
  display: grid; grid-template-columns: 1fr auto 1fr;
  gap: 14px; align-items: stretch;
}
.h2h-card {
  background: var(--bg-1); border: 1px solid var(--line);
  border-radius: var(--r-sm); padding: 14px 16px;
  display: flex; flex-direction: column; gap: 8px;
}
.h2h-card.h2h-pick { border-color: var(--up); border-left: 3px solid var(--up); }
.h2h-card.h2h-skip { border-color: var(--dn); border-left: 3px solid var(--dn); opacity: 0.85; }
.h2h-card-tag {
  font-size: 10px; letter-spacing: 0.8px;
  font-weight: 700; padding: 3px 9px;
  border-radius: 4px; align-self: flex-start;
}
.h2h-pick .h2h-card-tag { background: rgba(255,59,59,0.12); color: var(--up); }
.h2h-skip .h2h-card-tag { background: rgba(16,185,129,0.12); color: var(--dn); }
.h2h-card-sym { margin: 0; font-size: 18px; }
.h2h-card-sym strong { font-family: var(--font-mono); letter-spacing: 0.5px; }
.h2h-stats {
  display: flex; flex-wrap: wrap; gap: 5px;
}
.h2h-chip {
  font-size: 11px; font-family: var(--font-mono);
  padding: 3px 8px; border-radius: 999px;
  background: var(--bg-2); border: 1px solid var(--line);
  color: var(--tx-2);
}
.h2h-chip.up    { color: var(--up);    border-color: rgba(255,59,59,0.4); background: rgba(255,59,59,0.08); }
.h2h-chip.dn    { color: var(--dn);    border-color: rgba(16,185,129,0.4); background: rgba(16,185,129,0.08); }
.h2h-chip.amber { color: var(--amber); border-color: rgba(255,181,71,0.4); background: rgba(255,181,71,0.08); }
.h2h-card-why { line-height: 1.6; color: var(--tx-2); margin: 2px 0 0; }
.h2h-deeplink {
  color: var(--accent); text-decoration: none;
  margin-top: auto;
}
.h2h-deeplink:hover { text-decoration: underline; }
.h2h-vs {
  align-self: center;
  font-size: 22px; font-weight: 800; letter-spacing: 1px;
  color: var(--tx-3); padding: 0 4px;
}
@media (max-width: 720px) {
  .h2h-grid { grid-template-columns: 1fr; }
  .h2h-vs { transform: rotate(90deg); padding: 4px 0; justify-self: center; }
}

.th-stocks { margin-top: 24px; }
.th-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r-sm); }
.th-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
  min-width: 1100px;
}
.th-table thead th {
  text-align: left; padding: 10px 12px;
  background: var(--bg-2); color: var(--tx-3);
  border-bottom: 1px solid var(--line-2);
  letter-spacing: 0.5px; font-weight: 700;
  position: sticky; top: 0; z-index: 1;
}
.th-table tbody td {
  padding: 11px 12px; border-bottom: 1px solid var(--line);
  vertical-align: middle;
}
.th-table tbody tr:last-child td { border-bottom: none; }
.th-table tbody tr:hover { background: var(--bg-2); }
.th-rank { color: var(--tx-3); font-size: 11px; width: 32px; }
.th-sym { font-weight: 700; font-size: 13px; }
.th-name { margin-top: 2px; }
.th-chain {
  font-size: 10px; letter-spacing: 0.6px; color: var(--accent);
  text-decoration: none; padding: 4px 8px; border-radius: 3px;
  border: 1px solid var(--line-2); transition: all .15s;
}
.th-chain:hover { background: var(--bg-2); border-color: var(--accent); }

.th-temp {
  display: inline-block; padding: 2px 7px; border-radius: 3px;
  font-size: 9.5px; font-weight: 700; letter-spacing: 0.8px;
  border: 1px solid currentColor;
}
.th-temp.th-cold { color: #3b82f6; background: rgba(59,130,246,0.1); }
.th-temp.th-warm { color: var(--amber); background: var(--amber-bg); }
.th-temp.th-hot  { color: var(--up); background: var(--up-bg); }

.th-52w-wrap { position: relative; width: 80px; height: 18px; background: var(--bg-2); border-radius: 2px; overflow: hidden; }
.th-52w-bar {
  position: absolute; top: 0; left: 0; bottom: 0;
  opacity: 0.55;
}
.th-52w-bar.th-cold { background: #3b82f6; }
.th-52w-bar.th-warm { background: var(--amber); }
.th-52w-bar.th-hot  { background: var(--up); }
.th-52w-val {
  position: relative; display: block;
  font-size: 10.5px; text-align: center; line-height: 18px;
  color: var(--tx-1); font-weight: 700;
}

.th-callouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; margin: 24px 0; }
.th-callout {
  padding: 16px 18px; border-radius: var(--r-sm);
  border: 1px solid var(--line-2);
}
.th-cold-box { background: rgba(59,130,246,0.06); border-color: rgba(59,130,246,0.25); }
.th-hot-box  { background: var(--up-bg);          border-color: rgba(255,59,59,0.3); }
.th-callout-tag {
  display: inline-block; padding: 3px 9px; border-radius: 3px;
  font-size: 10px; letter-spacing: 0.8px; font-weight: 700;
  margin-bottom: 8px;
  border: 1px solid currentColor;
}
.th-cold-box .th-callout-tag { color: #3b82f6; background: rgba(59,130,246,0.08); }
.th-hot-box  .th-callout-tag { color: var(--up);   background: rgba(255,59,59,0.08); }
.th-callout-body { font-size: 13.5px; line-height: 1.6; color: var(--tx-1); }
.th-callout-foot { margin-top: 8px; line-height: 1.55; }

.src-list { list-style: disc; padding-left: 22px; }
.src-list li { margin: 6px 0; }
.src-link { color: var(--accent); text-decoration: none; }
.src-link:hover { text-decoration: underline; }

.th-foot {
  margin-top: 32px; padding-top: 16px;
  border-top: 1px solid var(--line);
  display: flex; justify-content: space-between; align-items: center;
  color: var(--tx-3);
}
.th-foot code {
  font-family: var(--font-mono); font-size: 11px;
  background: var(--bg-2); padding: 2px 6px; border-radius: 3px;
}

/* ---------- Screener ---------- */
.sc-wrap { padding: 18px 24px 40px; }
.sc-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  margin-bottom: 14px; flex-wrap: wrap; gap: 12px;
}
.sc-title { font-size: 20px; font-weight: 700; letter-spacing: 0.4px; margin: 0 0 4px; }
.sc-actions { display: flex; gap: 8px; }
.sc-btn {
  padding: 6px 14px; border: 1px solid var(--line-2); background: var(--bg-2);
  color: var(--tx-1); font-family: var(--font-mono); font-size: 11px;
  letter-spacing: 0.5px; cursor: pointer; border-radius: 3px;
  transition: all 0.15s;
}
.sc-btn:hover { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
.sc-btn-ghost { background: transparent; color: var(--tx-3); }

.sc-presets { margin-bottom: 16px; }
.sc-presets-label { letter-spacing: 0.6px; margin-bottom: 8px; }
.sc-preset-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}
.sc-preset {
  padding: 10px 12px; background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--r-sm); cursor: pointer; text-align: left;
  color: var(--tx-1); transition: all 0.15s;
  display: flex; flex-direction: column; gap: 3px;
}
.sc-preset:hover { border-color: var(--accent); background: var(--accent-bg); }
.sc-preset.active { border-color: var(--accent); background: rgba(108,180,255,0.12); }
.sc-preset-label { font-size: 12.5px; font-weight: 700; letter-spacing: 0.3px; }
.sc-preset-desc { line-height: 1.4; }

.sc-filters {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px 12px; padding: 14px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: var(--r-sm);
  margin-bottom: 14px;
}
.sc-filter-group { display: flex; flex-direction: column; gap: 4px; }
.sc-filter-label { letter-spacing: 0.4px; color: var(--tx-3); }
.sc-input, .sc-input-num {
  padding: 6px 8px; background: var(--bg); border: 1px solid var(--line-2);
  color: var(--tx-1); font-family: var(--font-mono); font-size: 12px;
  border-radius: 3px; width: 100%; box-sizing: border-box;
}
.sc-input:focus, .sc-input-num:focus { outline: none; border-color: var(--accent); }
.sc-range { display: flex; align-items: center; gap: 4px; }
.sc-range .sc-input-num { width: 0; flex: 1; }

.sc-count-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 4px; margin-bottom: 6px;
}
.sc-count { color: var(--tx-1); font-weight: 700; font-size: 13px; }

.sc-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r-sm); }
.sc-table {
  width: 100%; border-collapse: collapse; font-size: 12.5px;
  min-width: 1300px;
}
.sc-table thead th {
  position: sticky; top: 0; background: var(--bg-2); z-index: 1;
  padding: 10px 10px; border-bottom: 1px solid var(--line);
  color: var(--tx-3); letter-spacing: 0.5px; text-align: right;
  cursor: pointer; user-select: none;
}
.sc-table thead th:hover { color: var(--accent); }
.sc-table thead th[data-sort]::after {
  content: ' ↕'; opacity: 0.3; font-size: 10px;
}
.sc-table thead th.sc-sort-asc::after  { content: ' ↑'; opacity: 1; color: var(--accent); }
.sc-table thead th.sc-sort-desc::after { content: ' ↓'; opacity: 1; color: var(--accent); }
.sc-table thead th:nth-child(1),
.sc-table thead th:nth-child(2) { text-align: left; }
.sc-table tbody td {
  padding: 8px 10px; border-bottom: 1px solid var(--line);
  text-align: right; white-space: nowrap;
}
.sc-table tbody td:first-child,
.sc-table tbody td:nth-child(2) { text-align: left; }
.sc-table tbody tr:hover { background: var(--bg-2); }

.sc-sym { font-weight: 700; }
.sc-name { max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.sc-src {
  display: inline-block; width: 20px; height: 20px; text-align: center;
  line-height: 20px; border-radius: 3px; font-weight: 700;
  border: 1px solid currentColor;
}
.sc-src-h { color: var(--up);     background: rgba(255,59,59,0.06); }
.sc-src-w { color: var(--accent); background: rgba(108,180,255,0.06); }
.sc-src-u { color: var(--tx-3);   background: var(--bg-2); }

.sc-link {
  color: var(--accent); text-decoration: none; font-size: 11px;
  letter-spacing: 0.4px; font-weight: 700;
}
.sc-link:hover { text-decoration: underline; }

.sc-empty { padding: 40px; text-align: center; font-size: 14px; }
.sc-foot {
  margin-top: 14px; padding: 12px 14px; line-height: 1.65;
  background: var(--bg-2); border-left: 3px solid var(--amber);
  border-radius: 3px;
}
.sc-foot strong { color: var(--tx-1); }

/* ---------- 籌碼 chips (三大法人) ---------- */
.chip-streak {
  display: inline-block; padding: 2px 7px; border-radius: 3px;
  font-weight: 700; letter-spacing: 0.3px; font-size: 10.5px;
  border: 1px solid currentColor;
}
.chip-streak-buy  { color: var(--up); background: rgba(255,59,59,0.08); }
.chip-streak-sell { color: var(--dn); background: rgba(27,217,124,0.08); }

.chip-spark { display: block; }
.chip-spark .chip-spark-buy  { fill: var(--up); opacity: 0.85; }
.chip-spark .chip-spark-sell { fill: var(--dn); opacity: 0.85; }
.chip-spark .chip-spark-mid  { stroke: var(--line-2); stroke-width: 0.8; stroke-dasharray: 2 2; }

.th-chips-cell { text-align: right; white-space: nowrap; padding: 6px 10px; }
.th-chips-legend {
  margin-top: 10px; padding: 10px 14px;
  background: var(--bg-2); border-radius: 4px;
  line-height: 1.6;
}

/* HOLDERS panel */
.dd-chip-panel { padding: 22px 0 40px; }
.dd-chip-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 18px; gap: 16px; flex-wrap: wrap;
}
.dd-chip-title {
  font-size: 13px; letter-spacing: 0.8px; font-weight: 700;
  color: var(--tx-1); margin-bottom: 4px;
}
.dd-chip-summary {
  padding: 6px 12px; background: var(--bg-2);
  border-left: 3px solid var(--accent);
  border-radius: 3px; color: var(--tx-1);
  letter-spacing: 0.3px;
}
.dd-chip-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; margin-bottom: 20px;
}
.dd-chip-card {
  padding: 14px 16px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: var(--r-sm);
}
.dd-chip-card-total { border-color: var(--accent); background: rgba(108,180,255,0.04); }
.dd-chip-card-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 10px;
}
.dd-chip-card-title {
  font-size: 11px; letter-spacing: 0.6px; font-weight: 700;
  color: var(--tx-2);
}
.dd-chip-card-stats {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-bottom: 10px;
}
.dd-chip-stat .val-md { font-size: 15px; font-weight: 700; }
.dd-chip-spark-wrap {
  display: flex; align-items: center; gap: 10px;
  padding-top: 8px; border-top: 1px solid var(--line);
}
.dd-chip-section { margin-top: 18px; }
.dd-chip-section-head {
  letter-spacing: 0.6px; padding: 0 0 8px; border-bottom: 1px solid var(--line);
  margin-bottom: 10px; text-transform: uppercase;
}
.dd-chip-daily-wrap { overflow-x: auto; }
.dd-chip-daily {
  width: 100%; border-collapse: collapse; font-size: 12.5px;
  min-width: 520px;
}
.dd-chip-daily thead th {
  text-align: right; padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  color: var(--tx-3); letter-spacing: 0.4px;
}
.dd-chip-daily thead th:first-child { text-align: left; }
.dd-chip-daily tbody td {
  padding: 8px 10px; border-bottom: 1px solid var(--line);
  text-align: right; white-space: nowrap;
}
.dd-chip-daily tbody td:first-child { text-align: left; color: var(--tx-2); }
.dd-chip-daily tbody tr:hover { background: var(--bg-2); }
.dd-chip-disclaimer {
  margin-top: 18px; padding: 12px 14px;
  background: var(--bg-2); border-left: 3px solid var(--amber);
  border-radius: 3px; line-height: 1.65;
}
.dd-chip-disclaimer strong { color: var(--tx-1); font-weight: 700; }

@media (max-width: 720px) {
  .th-stats { grid-template-columns: 1fr; }
  .th-title { font-size: 22px; }
  .th-table { min-width: 1100px; }
  .dd-chip-grid { grid-template-columns: 1fr; }
  .dd-chip-card-stats { grid-template-columns: 1fr 1fr; }
}

/* ─────────────────────────────────────────
   COVERAGE section (PORT tab)
   主動佈局 — supply_chains.yaml + audit_coverage + Gemini coverage_suggestions
   ───────────────────────────────────────── */
.cov-section { margin-top: 28px; }
.cov-lede {
  padding: 14px 16px; margin-bottom: 22px;
  background: var(--bg-2); border-left: 3px solid var(--accent);
  border-radius: 4px; line-height: 1.7;
}
.cov-sub { margin-bottom: 28px; }
.cov-sub-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
  letter-spacing: 0.6px;
}

/* Atlas */
.cov-chain-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.cov-chain-card {
  padding: 12px 14px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: 4px;
}
.cov-chain-title {
  font-size: 13px; font-weight: 700; color: var(--tx-1);
  letter-spacing: 0.3px; margin-bottom: 6px;
}
.cov-chain-stats {
  display: flex; gap: 6px; flex-wrap: wrap;
  margin-bottom: 8px; color: var(--tx-2);
}
.cov-chain-bar-bg {
  height: 4px; background: var(--bg-0); border-radius: 2px;
  overflow: hidden;
}
.cov-chain-bar-fill {
  height: 100%; background: var(--tx-3); border-radius: 2px;
}
.cov-chain-bar-fill.up     { background: var(--up); }
.cov-chain-bar-fill.amber  { background: var(--amber); }
.cov-chain-bar-fill.muted  { background: var(--line-2); }

/* AI picks */
.cov-pick-intro {
  padding: 10px 12px; margin-bottom: 12px;
  background: var(--bg-2); border-radius: 4px; line-height: 1.7;
}
.cov-pick-intro code {
  background: var(--bg-0); padding: 1px 6px; border-radius: 3px;
  color: var(--tx-1);
}
.cov-pick-list {
  display: grid; grid-template-columns: 1fr; gap: 10px;
}
.cov-pick-row {
  padding: 14px 16px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: 4px;
  border-left: 3px solid var(--accent);
}
.cov-pick-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.cov-pick-sym strong { font-size: 15px; color: var(--tx-1); margin-right: 8px; letter-spacing: 0.4px; }
.cov-pick-priority {
  padding: 2px 10px; border-radius: 12px;
  background: var(--bg-0); border: 1px solid var(--line);
  letter-spacing: 0.6px;
}
.cov-pick-priority.dn    { color: var(--dn); border-color: rgba(255,59,59,0.4); }
.cov-pick-priority.amber { color: var(--amber); border-color: rgba(255,181,71,0.4); }
.cov-pick-chain { margin-bottom: 6px; }
.cov-pick-why {
  font-size: 14px; line-height: 1.75; color: var(--tx-1);
  margin-top: 4px;
}

/* Gap radar */
.cov-gap-intro {
  padding: 10px 12px; margin-bottom: 10px;
  background: var(--bg-2); border-radius: 4px; line-height: 1.7;
}
.cov-gap-table {
  width: 100%; border-collapse: collapse;
  background: var(--bg-2); border-radius: 4px; overflow: hidden;
}
.cov-gap-table thead th {
  padding: 8px 12px; font-size: 10px; letter-spacing: 1px; font-weight: 600;
  color: var(--tx-3); text-transform: uppercase;
  border-bottom: 1px solid var(--line); background: var(--bg-1);
}
.cov-gap-table tbody td {
  padding: 8px 12px; border-bottom: 1px solid var(--line);
  color: var(--tx-2);
}
.cov-gap-table tbody td strong { color: var(--tx-1); margin-right: 8px; }
.cov-gap-table tbody tr:hover { background: var(--bg-1); }
.cov-gap-badge-in {
  padding: 1px 8px; border-radius: 10px;
  background: rgba(27,217,124,0.1); color: var(--up);
  border: 1px solid rgba(27,217,124,0.3);
}
.cov-gap-badge-out {
  padding: 1px 8px; border-radius: 10px;
  background: rgba(255,181,71,0.1); color: var(--amber);
  border: 1px solid rgba(255,181,71,0.3);
}

/* Just added */
.cov-added-intro {
  padding: 10px 12px; margin-bottom: 10px;
  background: var(--bg-2); border-radius: 4px; line-height: 1.7;
}
.cov-added-list {
  display: grid; grid-template-columns: 1fr; gap: 8px;
}
.cov-added-row {
  padding: 10px 14px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: 4px;
  display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap;
}
.cov-added-chain { min-width: 120px; color: var(--tx-3); letter-spacing: 0.4px; }
.cov-added-syms {
  display: flex; gap: 6px; flex-wrap: wrap;
  color: var(--tx-1);
}
.cov-added-syms span { color: var(--tx-1); }

@media (max-width: 720px) {
  .cov-chain-grid { grid-template-columns: 1fr; }
  .cov-added-row { flex-direction: column; gap: 6px; }
  .cov-added-chain { min-width: auto; }
}

/* ─────────────────────────────────────────
   SUPPLY CHAIN MAP (theme page)
   Full vertical ecosystem: 上游 → 中游 → 下游
   ───────────────────────────────────────── */
.sc-map-section { margin-top: 36px; }
.sc-map-intro {
  padding: 16px 18px; margin: 10px 0 20px;
  background: var(--bg-2); border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
}
.sc-map-title {
  font-size: 16px; font-weight: 700; color: var(--tx-1);
  margin-bottom: 10px; letter-spacing: 0.4px;
}
.sc-map-tags {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin-bottom: 12px;
}
.sc-map-tag {
  padding: 2px 10px; border-radius: 12px;
  background: var(--bg-0); border: 1px solid var(--line);
  color: var(--tx-2); letter-spacing: 0.4px;
}
.sc-map-narrative {
  color: var(--tx-1); line-height: 1.75;
  margin-bottom: 10px; font-size: 13px;
}
.sc-map-legend {
  padding: 8px 10px; background: var(--bg-0);
  border-radius: 3px; line-height: 1.65;
}
.sc-map-legend code {
  background: var(--bg-1); padding: 1px 6px; border-radius: 3px;
  color: var(--tx-1);
}

.sc-map-layers {
  display: flex; flex-direction: column; gap: 18px;
}
.sc-map-layer {
  padding: 16px 18px; background: var(--bg-1);
  border: 1px solid var(--line); border-radius: 6px;
  position: relative;
}
.sc-map-layer-head {
  display: flex; gap: 12px; align-items: flex-start;
  margin-bottom: 14px; padding-bottom: 10px;
  border-bottom: 1px dashed var(--line);
}
.sc-map-layer-num {
  flex-shrink: 0;
  width: 28px; height: 28px; border-radius: 50%;
  background: var(--accent); color: var(--bg-0);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 13px; letter-spacing: 0;
}
.sc-map-layer-title { flex: 1; }
.sc-map-layer-name {
  font-size: 15px; font-weight: 700; color: var(--tx-1);
  letter-spacing: 0.3px; margin-bottom: 3px;
}
.sc-map-layer-role {
  line-height: 1.65; color: var(--tx-2);
}
.sc-map-layer-stocks {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.sc-map-stock {
  padding: 10px 12px; background: var(--bg-2);
  border: 1px solid var(--line); border-radius: 4px;
  transition: border-color 0.15s;
}
.sc-map-stock:hover { border-color: var(--accent); }
.sc-map-stock-lead {
  background: linear-gradient(180deg, rgba(255,181,71,0.08), var(--bg-2));
  border-color: rgba(255,181,71,0.3);
}
.sc-map-stock-lead:hover { border-color: var(--amber); }
.sc-map-stock-head {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-bottom: 4px;
}
.sc-map-stock-head strong {
  color: var(--tx-1); font-size: 13px; letter-spacing: 0.3px;
}
.sc-map-sym-link {
  color: inherit; text-decoration: none;
  border-bottom: 1px dotted var(--line-2);
}
.sc-map-sym-link:hover { border-bottom-color: var(--accent); }
.sc-map-name { color: var(--tx-2); font-size: 13px; }
.sc-map-lead-badge {
  padding: 1px 6px; border-radius: 8px;
  background: rgba(255,181,71,0.15); color: var(--amber);
  border: 1px solid rgba(255,181,71,0.35);
  letter-spacing: 0.4px; margin-left: auto;
}
.sc-map-stock-role {
  line-height: 1.55; margin-bottom: 6px;
  color: var(--tx-2);
}
.sc-map-price {
  display: flex; gap: 10px; align-items: baseline;
  padding-top: 4px; border-top: 1px dashed var(--line);
  color: var(--tx-1);
}

/* ---- tier badges & filter (Phase G: hidden-champion bias) ---- */
.sc-map-badges {
  margin-left: auto;
  display: flex; gap: 4px; align-items: center;
}
.sc-map-tier-bar {
  display: flex; gap: 6px; flex-wrap: wrap;
  margin: 10px 0 14px;
}
.sc-map-tier-badge {
  padding: 1px 6px; border-radius: 8px;
  border: 1px solid;
  letter-spacing: 0.4px;
}
.sc-map-tier-badge-mega {
  background: rgba(255,59,59,0.10); color: var(--dn);
  border-color: rgba(255,59,59,0.30);
}
.sc-map-tier-badge-large {
  background: rgba(255,181,71,0.10); color: var(--amber);
  border-color: rgba(255,181,71,0.30);
}
.sc-map-tier-badge-mid {
  background: var(--bg-0); color: var(--tx-2);
  border-color: var(--line);
}
.sc-map-tier-badge-small {
  background: rgba(27,217,124,0.10); color: var(--up);
  border-color: rgba(27,217,124,0.30);
}
.sc-map-tier-badge-hidden {
  background: rgba(27,217,124,0.20); color: var(--up);
  border-color: rgba(27,217,124,0.50);
  font-weight: 700;
}
.sc-map-tier-count { padding: 2px 8px; }
.sc-map-stock-snowball {
  border-color: rgba(27,217,124,0.40);
}
.sc-map-stock-snowball:hover { border-color: var(--up); }
.sc-map-filter {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  margin: 14px 0 18px; padding: 10px 14px;
  background: var(--bg-2); border-radius: 4px;
}
.sc-map-filter-label {
  color: var(--tx-2); letter-spacing: 0.5px;
  margin-right: 4px;
}
.sc-map-filter-btn {
  padding: 4px 12px;
  background: var(--bg-0);
  border: 1px solid var(--line);
  color: var(--tx-2);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  border-radius: 3px;
  letter-spacing: 0.3px;
  transition: border-color 120ms, color 120ms;
}
.sc-map-filter-btn:hover {
  border-color: var(--accent);
  color: var(--tx-1);
}
.sc-map-filter-btn.active {
  background: var(--accent);
  color: var(--bg-0);
  border-color: var(--accent);
  font-weight: 700;
}
.sc-map-stock.is-filtered-out { display: none; }

/* ---- Today's Focus panel (Phase I · 2026-04-19) ---- */
.today-focus {
  margin: 20px 0 28px;
  padding: 22px 24px;
  background: linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 100%);
  border: 1px solid var(--line-2);
  border-radius: 12px;
}
.tf-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 24px; margin-bottom: 18px;
  flex-wrap: wrap;
}
.tf-kicker {
  color: var(--accent-2);
  letter-spacing: 2px;
  margin-bottom: 4px;
}
.tf-title {
  margin: 0;
  font-size: var(--fs-h2);
  font-weight: 700;
  letter-spacing: -0.01em;
}
.tf-lead {
  flex: 1 1 260px;
  max-width: 520px;
  margin: 0;
  line-height: 1.55;
}
.tf-lead strong { color: var(--tx-1); font-weight: 600; }
.tf-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
.tf-col {
  padding: 14px 16px;
  background: var(--bg-2);
  border-radius: 10px;
  border: 1px solid var(--line);
  border-top: 3px solid var(--line-2);
  transition: border-color 120ms;
}
.tf-col-cheap  { border-top-color: var(--dn); }    /* 綠：便宜 */
.tf-col-hot    { border-top-color: var(--up); }    /* 紅：熱門 */
.tf-col-danger { border-top-color: var(--amber); } /* 黃：警戒 */
.tf-col:hover { border-color: var(--line-2); }
.tf-col-head { margin-bottom: 10px; }
.tf-col-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
  margin-bottom: 2px;
}
.tf-col-sub { line-height: 1.45; }
.tf-list {
  margin: 0; padding: 0; list-style: none;
  display: flex; flex-direction: column; gap: 8px;
}
.tf-row {
  padding: 8px 10px;
  background: var(--bg-1);
  border-radius: 6px;
  border: 1px solid transparent;
  transition: border-color 120ms, background 120ms;
}
.tf-row:hover {
  border-color: var(--line-2);
  background: var(--bg-3);
}
.tf-row-top {
  display: flex; align-items: baseline; gap: 8px;
  margin-bottom: 3px;
}
.tf-sym {
  color: var(--tx-1);
  border-bottom: 1px dotted var(--line-2);
}
.tf-sym:hover { border-bottom-color: var(--accent); color: var(--accent-2); }
.tf-sym strong { font-size: 14.5px; font-weight: 600; }
.tf-name { color: var(--tx-2); font-size: 13.5px; }
.tf-price { margin-left: auto; color: var(--tx-1); font-size: 13px; }
.tf-reason { line-height: 1.45; }
.tf-empty { padding: 20px 4px; text-align: center; line-height: 1.5; }
.tf-foot {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--line);
  line-height: 1.6;
}
@media (max-width: 960px) {
  .tf-grid { grid-template-columns: 1fr; }
  .today-focus { padding: 16px 18px; }
}

/* ---- fundamentals row on each supply-chain card (Phase H) ---- */
.sc-map-fund {
  display: flex; flex-wrap: wrap; gap: 10px;
  padding: 6px 0;
  margin-bottom: 6px;
  border-top: 1px dashed var(--line);
  font-size: 11.5px;
  color: var(--tx-2);
}
.sc-map-fund-cell {
  display: inline-flex; gap: 4px; align-items: baseline;
  white-space: nowrap;
}
.sc-map-fund-cell .muted { font-size: 10.5px; letter-spacing: 0.3px; }

/* Eyeball valuation badge on the head row (next to tier badge) */
.sc-map-val-badge {
  padding: 1px 6px; border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--bg-0);
  letter-spacing: 0.3px;
}
.sc-map-val-badge.up     { color: var(--up);    border-color: rgba(27,217,124,0.30); background: rgba(27,217,124,0.08); }
.sc-map-val-badge.amber  { color: var(--amber); border-color: rgba(255,181,71,0.35); background: rgba(255,181,71,0.10); }
.sc-map-val-badge.dn     { color: var(--dn);    border-color: rgba(255,59,59,0.30);  background: rgba(255,59,59,0.08);  }

@media (max-width: 720px) {
  .sc-map-layer-stocks { grid-template-columns: 1fr; }
  .sc-map-layer { padding: 12px 14px; }
  .sc-map-filter { padding: 8px 10px; }
  .sc-map-filter-btn { padding: 3px 8px; font-size: 11px; }
  .sc-map-fund { gap: 6px; font-size: 11px; }
}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    DOCS_DIR.mkdir(exist_ok=True)
    DOCS_BRIEFS_DIR.mkdir(exist_ok=True)
    DOCS_HOLDINGS_DIR.mkdir(exist_ok=True)
    DOCS_THEMES_DIR = DOCS_DIR / "themes"
    DOCS_THEMES_DIR.mkdir(exist_ok=True)

    briefs = load_briefs()
    pf = load_portfolio()
    history = load_history()
    latest_analysis = None
    if briefs:
        latest_analysis = load_analysis(briefs[0]["date"])

    print(f"loaded {len(briefs)} briefs, portfolio={'yes' if pf else 'no'}, "
          f"history={len(history)} tickers, analysis={'yes' if latest_analysis else 'no'}",
          file=sys.stderr)

    (DOCS_DIR / "styles.css").write_text(STYLES_CSS, encoding="utf-8")
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    (DOCS_DIR / "index.html").write_text(render_index(briefs, pf, history), encoding="utf-8")
    print("wrote docs/index.html", file=sys.stderr)

    for brief in briefs:
        out = DOCS_BRIEFS_DIR / f"{brief['date']}.html"
        out.write_text(render_brief_page(brief), encoding="utf-8")
    print(f"wrote {len(briefs)} brief pages", file=sys.stderr)

    # Per-holding + watchlist + universe deep-dive pages
    if pf:
        # Build news index across ALL briefs, keyed by ticker
        all_items = (pf.get("holdings", []) +
                     pf.get("watchlist", []) +
                     pf.get("simulator_universe", []))
        news_by_ticker = build_news_index(briefs, all_items)
        written: set[str] = set()

        def _write_page(stock: dict, kind: str):
            sym = stock["symbol"]
            out = DOCS_HOLDINGS_DIR / f"{sym}.html"
            out.write_text(
                render_holding_page(
                    stock, pf, history, latest_analysis,
                    is_watchlist=(kind != "holding"),
                    news_for_ticker=news_by_ticker.get(sym),
                    page_kind=kind,
                ),
                encoding="utf-8",
            )
            written.add(sym)

        for h in pf.get("holdings", []):
            _write_page(h, "holding")
        for w in pf.get("watchlist", []):
            if w["symbol"] not in written:
                _write_page(w, "watchlist")
        # Universe — generate pages for everything else
        for u in pf.get("simulator_universe", []):
            if u["symbol"] not in written:
                _write_page(u, "universe")

        print(f"wrote {len(written)} stock pages (holdings + watchlist + universe)",
              file=sys.stderr)

    # Theme deep-dive pages (from AI opportunities)
    if pf and latest_analysis:
        theme_count = 0
        for idx, opp in enumerate(latest_analysis.get("opportunities", [])):
            slug = _theme_slug(opp, idx)
            out = DOCS_THEMES_DIR / f"{slug}.html"
            out.write_text(
                render_theme_page(opp, pf, history, latest_analysis, slug),
                encoding="utf-8",
            )
            theme_count += 1
        print(f"wrote {theme_count} theme pages", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

DATE_RE = re.compile(r"^# Daily Brief — (\d{4}-\d{2}-\d{2}) \(週(.)\)", re.MULTILINE)
COUNT_RE = re.compile(r"抓到 (\d+) 則新聞")
SECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
PROMPT_MARKER = "\n---\n\n你是我的"

SENTIMENT_CLS = {"正面": "up", "負面": "dn", "中性": "flat"}
SENTIMENT_ICON = {"正面": "🟢", "負面": "🔴", "中性": "⚪"}
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
        <h2>📊 投資組合</h2>
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
            f'<div class="alert-item alert-red">🔴 <strong>停損觸發</strong> '
            f'{a["symbol"]} {html.escape(a["name"])}：現價 <span class="mono">{a["price"]}</span> ≤ 停損 <span class="mono">{a["stop_loss"]}</span></div>'
        )
    for a in alerts.get("take_profit", []):
        items.append(
            f'<div class="alert-item alert-green">🟢 <strong>停利觸發</strong> '
            f'{a["symbol"]} {html.escape(a["name"])}：現價 <span class="mono">{a["price"]}</span> ≥ 停利 <span class="mono">{a["take_profit"]}</span></div>'
        )
    for a in alerts.get("nearing_stop", []):
        items.append(
            f'<div class="alert-item alert-amber">🟡 <strong>接近停損</strong> '
            f'{a["symbol"]}：距離 {a["stop_loss_dist_pct"]:.1f}%</div>'
        )
    for a in alerts.get("concentration", []):
        items.append(
            f'<div class="alert-item alert-amber">🟠 <strong>單一持股過重</strong> '
            f'{a["symbol"]}：佔比 {a["weight_pct"]:.1f}% &gt; 上限 {a["limit_pct"]:.0f}%</div>'
        )
    for a in alerts.get("pillar", []):
        items.append(
            f'<div class="alert-item alert-purple">🟣 <strong>三柱失衡</strong> '
            f'{PILLAR_LABEL.get(a["pillar"], a["pillar"])}：現 {a["actual_pct"]:.0f}% vs 目標 {a["target_pct"]:.0f}% '
            f'(差 {a["diff_pct"]:+.1f}pp)</div>'
        )
    return f'''
<div class="pf-alerts">
  <div class="pf-sub-head with-badge">
    ⚠️ 組合警報 <span class="badge-count">{total} ACTIVE</span>
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
    <h2>💼 持股明細</h2>
    <span class="muted small">{len(holdings)} 檔</span>
  </div>
  <div class="hgrid">{"".join(cards)}</div>

  <div class="section-head mt">
    <h2>👁️ 追蹤清單</h2>
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
    <h2>🌡️ 市場脈搏</h2>
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
  <div class="section-head"><h2>🌏 總經背景</h2></div>
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
  <div class="section-head"><h2>🩺 組合診斷</h2></div>
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
            f'<div class="action-header">{icon} {label}</div>'
            f'<ul>{li}</ul></div>'
        )

    actions_html = f'''
<section class="a-section">
  <div class="section-head"><h2>🎯 今日行動清單</h2></div>
  <div class="actions-grid">
    {render_actions(actions.get("green", []), "action-green", "可以做", "🟢")}
    {render_actions(actions.get("yellow", []), "action-yellow", "該警戒", "🟡")}
    {render_actions(actions.get("red", []), "action-red", "不要做", "🔴")}
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
        f'<section class="a-section"><div class="section-head"><h2>📊 今日主題</h2>'
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
        f'<section class="a-section"><div class="section-head"><h2>💼 持股分析</h2></div>'
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
        f'<section class="a-section"><div class="section-head"><h2>🔍 值得研究 '
        f'<span class="badge-count">{len(opps)} DETECTED</span></h2></div>'
        f'{"".join(opp_cards)}</section>'
        if opp_cards else ""
    )

    # Budget allocation — also on brief page
    budget_alloc = analysis.get("budget_allocation", {})
    budget_section_html = ""
    if budget_alloc.get("allocations"):
        allocs = budget_alloc.get("allocations", [])
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
            levels_html = " · ".join(row_levels) if row_levels else ""
            rows.append(f'''
            <article class="alloc-full-card {cls}">
              <div class="alloc-full-head">
                <div>
                  <div class="alloc-action-big">{html.escape(action)}</div>
                  <h3>{html.escape(al.get("symbol", ""))} <span class="muted">{html.escape(al.get("name", ""))}</span></h3>
                </div>
                <div class="alloc-conf-big mono">信心度 {al.get("confidence_pct", 0)}%</div>
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
        budget_section_html = f'''
<section class="a-section" id="budget">
  <div class="section-head"><h2>💰 今日 NT${budget_alloc.get("budget_twd", 0):,.0f} 配置建議 <span class="badge-count">SNOWBALL</span></h2></div>
  <div class="budget-plan-big">{html.escape(budget_alloc.get("plan_summary", ""))}</div>
  {"".join(rows)}
  {unalloc_line}
  {why_not_line}
</section>
'''

    # Learning
    learning_html = ""
    if lp:
        learning_html = f'''
<section class="a-section learning-section">
  <div class="section-head"><h2>📚 新手學習點</h2></div>
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
<link rel="stylesheet" href="{css_href}">
</head>
<body>
"""

PAGE_FOOT = """
<footer class="wrap">
  <p>生成於 {now} · <a href="https://github.com/iannn211/stock-daily-brief" target="_blank">source</a></p>
</footer>
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


def render_radar_tab(analysis: dict | None, pf: dict | None) -> str:
    """Opportunity Radar — dedicated tab that surfaces themes to research.

    Shows AI opportunities as big cards with industry grouping, lead stocks
    (clickable to deep page), topic cross-reference, and a 'how it's found'
    explanation.
    """
    if not analysis:
        return '<div class="radar-empty"><p class="muted">AI 分析尚未生成。下次排程後會看到機會雷達。</p></div>'

    opps = analysis.get("opportunities", [])
    topics = analysis.get("topics", [])

    if not opps:
        return '<div class="radar-empty"><p class="muted">今日 AI 未挑出值得研究的新機會（市場條件可能不合適）。</p></div>'

    # Intro
    intro = f'''
<div class="radar-intro">
  <h2 class="radar-title">📡 機會雷達</h2>
  <p class="muted">
    AI 橫掃全市場找出「你可能錯過」的題材。每個都有論點 / 研究切入點 / 風險 / 直接跳到個股分析。
    今天掃出 <strong class="mono">{len(opps)}</strong> 個機會、<strong class="mono">{len(topics)}</strong> 個主題。
  </p>
</div>
'''

    # Opportunity cards — big, with all detail exposed
    opp_cards = []
    for o in opps:
        sym = o.get("symbol", "")
        in_universe = sym in _TICKER_ALIAS
        cta = (
            f'<a href="holdings/{sym}.html" class="btn-link small">→ {sym} 完整分析 / 趨勢圖 / 新聞</a>'
            if in_universe else
            f'<span class="muted small">（尚未在資料庫中：手動到 portfolio.yaml 加入 simulator_universe 即可）</span>'
        )
        opp_cards.append(f'''
        <article class="radar-card">
          <div class="radar-card-head">
            <div>
              <h3>{html.escape(sym)} <span class="muted">{html.escape(o.get("name", ""))}</span></h3>
            </div>
          </div>
          <div class="radar-card-body">
            <div class="radar-row"><span class="radar-label">論點</span>{_link_tickers(o.get("thesis", ""))}</div>
            <div class="radar-row"><span class="radar-label">研究切入</span>{_link_tickers(o.get("research_angle", ""))}</div>
            <div class="radar-row radar-risk-row"><span class="radar-label dn">⚠ 風險</span>{_link_tickers(o.get("risk", ""))}</div>
          </div>
          <div class="radar-card-foot">{cta}</div>
        </article>''')

    # Related topics (mini — linked to brief)
    topics_mini = []
    for t in topics[:6]:
        ticks = "".join(
            (
                f'<a href="holdings/{_TICKER_ALIAS[tk]}.html" class="chip chip-muted small">{html.escape(tk)}</a>'
                if tk in _TICKER_ALIAS else
                f'<span class="chip chip-muted small">{html.escape(tk)}</span>'
            )
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
  <h3 class="radar-subtitle">🔥 今日主題 — {len(topics)} 個族群</h3>
  <div class="radar-topics-grid">{"".join(topics_mini)}</div>
  <div class="tab-footer">
    <a href="briefs/{date}.html" class="btn-link small">→ 看完整主題分析 + 原始新聞</a>
  </div>
</div>
'''

    return f'''
<div class="radar-body">
  {intro}
  <div class="radar-grid">{"".join(opp_cards)}</div>
  {topics_block}
</div>
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
    <span class="cat-title mono">🗓 TODAY & UPCOMING</span>
    <span class="muted small">{len(agenda)} 個事件</span>
  </div>
  <div class="cat-list">{"".join(items)}</div>
</div>
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
          <div class="hero-action-lbl">🟢 今日可以做</div>
          <div class="hero-action-body"><strong>{html.escape(a.get("action", ""))}</strong>
            <div class="hero-action-reason muted small">{html.escape(a.get("reason", ""))}</div>
          </div>
        </div>'''

    # Budget allocation summary
    budget_alloc = analysis.get("budget_allocation", {})
    budget_html = ""
    if budget_alloc.get("allocations"):
        allocs = budget_alloc.get("allocations", [])
        budget_amt = budget_alloc.get("budget_twd", 0)
        plan = budget_alloc.get("plan_summary", "")
        alloc_cards = []
        for al in allocs:
            action = al.get("action", "")
            is_cash = "現金" in action or "不動作" in action
            cls = "alloc-cash" if is_cash else "alloc-buy"
            sym = al.get("symbol", "")
            name = al.get("name", "")
            shares = al.get("target_shares")
            cost = al.get("target_cost_twd")
            sl = al.get("stop_loss_price")
            tp = al.get("take_profit_price")
            conf = al.get("confidence_pct", 0)
            rat = al.get("rationale", "")
            shares_str = f"{shares} 股" if shares else ""
            cost_str = f"≈{_fmt_twd(cost)}" if cost else ""
            sl_str = f"停損 {sl}" if sl else ""
            tp_str = f"停利 {tp}" if tp else ""
            levels = " · ".join(s for s in (shares_str, cost_str, sl_str, tp_str) if s)
            alloc_cards.append(f'''
            <div class="alloc-card {cls}">
              <div class="alloc-head">
                <span class="alloc-action">{html.escape(action)}</span>
                <span class="alloc-conf mono">{conf}%</span>
              </div>
              <div class="alloc-sym"><strong>{html.escape(sym)}</strong> <span class="muted small">{html.escape(name)}</span></div>
              <div class="alloc-levels mono small">{html.escape(levels)}</div>
              <div class="alloc-rat small">{html.escape(rat)[:140]}{"…" if len(rat) > 140 else ""}</div>
            </div>''')
        budget_html = f'''
        <div class="hero-budget">
          <div class="hero-picks-head">
            <span class="hero-action-lbl">💰 下一筆 NT${budget_amt:,.0f} 建議</span>
            <a href="briefs/{latest_brief["date"]}.html#budget" class="btn-link small">看完整下單計畫 →</a>
          </div>
          <p class="budget-plan">{html.escape(plan)}</p>
          <div class="alloc-grid">{"".join(alloc_cards)}</div>
        </div>'''

    # Health badge
    health = diag.get("overall_health", "")
    health_cls = {"良好": "up", "需調整": "amber", "高風險": "dn"}.get(health, "flat")
    diag_pill = (
        f'<span class="badge badge-{health_cls} small">{html.escape(health)}</span>'
        if health else ""
    )

    # Picks strip — symbol + name + thesis one-liner
    picks_html = ""
    if opps:
        picks = []
        for o in opps[:3]:
            sym = o.get("symbol", "")
            pick_href = f"holdings/{sym}.html" if sym in _TICKER_ALIAS else f"briefs/{latest_brief['date']}.html#opportunities"
            picks.append(f'''
            <a class="pick-card" href="{pick_href}">
              <div class="pick-head">
                <strong>{html.escape(sym)}</strong>
                <span class="muted small">{html.escape(o.get("name", ""))}</span>
              </div>
              <div class="pick-thesis small">{html.escape(o.get("thesis", ""))[:80]}{"…" if len(o.get("thesis", "")) > 80 else ""}</div>
              <div class="pick-risk muted small">⚠ {html.escape(o.get("risk", ""))[:60]}{"…" if len(o.get("risk", "")) > 60 else ""}</div>
            </a>''')
        picks_html = f'''
        <div class="hero-picks">
          <div class="hero-picks-head">
            <span class="hero-action-lbl">🔍 今日值得研究</span>
            <span class="muted small">{len(opps)} 檔</span>
          </div>
          <div class="pick-grid">{"".join(picks)}</div>
        </div>'''

    date_str = latest_brief["date"]
    weekday = latest_brief["weekday"]

    # GUSHI-style BriefHero: greeting + headline + one-liner + highlights + agenda
    greeting = mb.get("greeting") or "早安"
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
  <h2 class="bh-headline">{html.escape(greeting)}，{headline_html}</h2>
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
        return f'<div class="action-col {cls}"><div class="action-header">{icon} {label}</div><ul>{li}</ul></div>'

    actions_html = (
        '<div class="actions-grid">'
        + action_col(actions.get("green", []), "action-green", "🟢", "可以做")
        + action_col(actions.get("yellow", []), "action-yellow", "🟡", "該警戒")
        + action_col(actions.get("red", []), "action-red", "🔴", "不要做")
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
  <div class="tab-subhead">🌏 總經背景</div>
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
        f'<div class="ai-block"><div class="tab-subhead">📊 今日主題 '
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
        f'<div class="ai-block"><div class="tab-subhead">💼 持股分析</div>{"".join(holding_cards)}</div>'
        if holding_cards else ""
    )

    # Budget allocation — full detail
    budget_alloc = analysis.get("budget_allocation", {})
    budget_full_html = ""
    if budget_alloc.get("allocations"):
        allocs = budget_alloc.get("allocations", [])
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
  <div class="tab-subhead">💰 今日 NT${budget_alloc.get("budget_twd", 0):,.0f} 配置建議 <span class="badge-count">SNOWBALL</span></div>
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
            f'<div class="ai-block" id="opportunities"><div class="tab-subhead">🔍 值得研究的個股 '
            f'<span class="badge-count">{len(opps)} DETECTED</span> <span class="muted small">· 點代碼看深度</span></div>'
            f'{"".join(rows)}</div>'
        )

    # Learning
    learning_html = ""
    if lp:
        learning_html = f'''
<div class="ai-block">
  <div class="tab-subhead">📚 新手學習點</div>
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
    <div class="tab-subhead">🎯 今日行動清單</div>
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
            "⭐ 我的持股", h.get("pct_52w"), h.get("high_52w"), h.get("low_52w"),
            h.get("pillar"))

    # Watchlist
    for w in pf.get("watchlist", []):
        add(w["symbol"], w["name"], w.get("price"), w.get("market", "TW"),
            "👁 追蹤中", w.get("pct_52w"), w.get("high_52w"), w.get("low_52w"),
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
                        u_match["market"], "🔍 AI 今日機會",
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
      <label class="sim-lbl">💰 預算</label>
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
      <label class="sim-lbl">📊 標的 <span id="sim-count" class="muted small mono"></span></label>
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
      <label class="sim-lbl">🔴 停損 −<span id="sim-sl-val" class="mono">10</span>%</label>
      <input type="range" id="sim-sl" min="3" max="25" value="10" step="1" class="sim-range">
      <div class="sim-range-labels muted small mono"><span>−3%</span><span>−25%</span></div>
    </div>

    <div class="sim-field">
      <label class="sim-lbl">🟢 停利 +<span id="sim-tp-val" class="mono">30</span>%</label>
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
  const priorityGroups = ['⭐ 我的持股', '👁 追蹤中', '🔍 AI 今日機會'];
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
      if (it.pct_52w >= 90) advice = '⚠️ 52週位階 ' + it.pct_52w.toFixed(0) + '%（高檔），建議限價等拉回';
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


def render_index(briefs: list[dict], pf: dict | None) -> str:
    if not pf:
        # Fallback for no portfolio data
        return (
            PAGE_HEAD.format(title="Stock AI Desk", css_href="styles.css")
            + '<div class="empty-state wrap"><h1>📈 Stock AI Desk</h1><p>無組合資料</p></div>'
            + PAGE_FOOT.format(now=datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"))
        )

    latest_brief = briefs[0] if briefs else None
    latest_analysis = load_analysis(latest_brief["date"]) if latest_brief else None
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
    hero = render_daily_hero(latest_brief, latest_analysis, pf)
    mood_panel = render_market_mood(pf, latest_analysis)
    catalyst_panel = render_catalyst_timeline(latest_analysis)
    chart = render_big_chart(pf)
    macro_strip = render_macro_strip(pf)
    positions = render_positions_table(pf)
    briefs_table = render_briefs_table(briefs)
    ai_tab = render_ai_tab(latest_brief, latest_analysis)
    radar_tab = render_radar_tab(latest_analysis, pf)
    sim_html, _ = render_simulator(pf, latest_analysis)

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
<header class="top-header wrap">
  <div class="top-brand">
    <h1 class="top-title">📈 Stock AI Desk</h1>
    <span class="top-date mono">{date_str} · 週{weekday_zh}</span>
    <span class="live-dot accent"></span>
  </div>

  <div class="top-search-wrap">
    <span class="top-search-icon">🔍</span>
    <input type="text" id="top-search" class="top-search-input" placeholder="搜尋股票代號或名稱（2330、台積電、NVDA…）" autocomplete="off">
    <div class="top-search-results" id="top-search-results"></div>
  </div>

  <div class="summary-strip">
    <div class="ss-cell ss-main">
      <span class="ss-lbl">組合市值</span>
      <span class="ss-val mono tnum">{_fmt_twd(total_value)}</span>
    </div>
    <div class="ss-cell">
      <span class="ss-lbl">今日</span>
      <span class="ss-val mono tnum {_cls(day_pnl)}">{_fmt_twd(day_pnl, sign=True)} ({_fmt_pct(day_pct)})</span>
    </div>
    <div class="ss-cell">
      <span class="ss-lbl">總損益</span>
      <span class="ss-val mono tnum {_cls(total_pnl)}">{_fmt_twd(total_pnl, sign=True)} ({_fmt_pct(total_pct)})</span>
    </div>
    <div class="ss-cell">
      <span class="ss-lbl">α vs {html.escape(bench.get("symbol", "—"))}</span>
      <span class="ss-val mono tnum {_cls(alpha)}">{_fmt_pct(alpha)}</span>
    </div>
    {f'<div class="ss-cell ss-alert"><span class="ss-lbl">⚠ 警報</span><span class="ss-val mono tnum amber">{alert_count} 個</span></div>' if alert_count > 0 else ''}
  </div>
</header>

<nav class="main-tabs wrap">
  <button class="mt-btn active" data-tab="ai">
    <span class="mt-icon">🤖</span>
    <span class="mt-label">今日 AI 建議</span>
  </button>
  <button class="mt-btn" data-tab="radar">
    <span class="mt-icon">📡</span>
    <span class="mt-label">機會雷達</span>
  </button>
  <button class="mt-btn" data-tab="sim">
    <span class="mt-icon">🧮</span>
    <span class="mt-label">試算看看</span>
  </button>
  <button class="mt-btn" data-tab="portfolio">
    <span class="mt-icon">📊</span>
    <span class="mt-label">組合 & 風險</span>
  </button>
  <button class="mt-btn" data-tab="positions">
    <span class="mt-icon">💼</span>
    <span class="mt-label">持股明細</span>
  </button>
  <button class="mt-btn" data-tab="briefs">
    <span class="mt-icon">📰</span>
    <span class="mt-label">歷史 Brief</span>
    <span class="mt-count">{len(briefs)}</span>
  </button>
</nav>

<main class="main-panel wrap">
  <div class="tab-panel active" data-panel="ai">
    {hero}
    <div class="mood-cat-row">
      {mood_panel}
      {catalyst_panel}
    </div>
    {ai_tab}
  </div>
  <div class="tab-panel" data-panel="radar">
    {radar_tab}
  </div>
  <div class="tab-panel" data-panel="sim">
    {sim_html}
  </div>
  <div class="tab-panel" data-panel="portfolio">
    {macro_strip}
    {chart}
    <section class="portfolio-detail">
      {sidebar}
    </section>
  </div>
  <div class="tab-panel" data-panel="positions">
    {positions}
  </div>
  <div class="tab-panel" data-panel="briefs">
    {briefs_table}
  </div>
</main>

<script>
// Tab switching with URL hash persistence
function setTab(t) {{
  document.querySelectorAll('.mt-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === t));
  if (location.hash !== '#' + t) history.replaceState(null, '', '#' + t);
}}
document.querySelectorAll('.mt-btn').forEach(btn => {{
  btn.addEventListener('click', () => setTab(btn.dataset.tab));
}});
// Restore from hash (only if matches a tab)
const initTab = (location.hash || '').replace('#', '');
if (initTab && document.querySelector(`.mt-btn[data-tab="${{initTab}}"]`)) setTab(initTab);

// --- Search: autocomplete any stock, navigate to /holdings/<sym>.html ---
(function() {{
  const INDEX = {json.dumps([
      {"symbol": it.get("symbol"), "name": it.get("name", ""),
       "category": it.get("category", ""), "price": it.get("price"),
       "group": "持股" if it.get("is_held") else "追蹤/全部"}
      for it in (pf.get("simulator_universe") or []) + pf.get("holdings", []) + pf.get("watchlist", [])
      if it.get("symbol")
  ], ensure_ascii=False)};
  const seen = new Set();
  const uniq = INDEX.filter(x => !seen.has(x.symbol) && seen.add(x.symbol));
  const input = document.getElementById('top-search');
  const results = document.getElementById('top-search-results');
  if (!input) return;
  let activeIdx = -1;

  function render(matches) {{
    if (!matches.length) {{ results.classList.remove('open'); results.innerHTML=''; return; }}
    results.innerHTML = matches.slice(0, 10).map((m, i) => `
      <a class="search-result${{i === activeIdx ? ' active' : ''}}" href="holdings/${{m.symbol}}.html">
        <span class="search-result-sym">${{m.symbol}}</span>
        <span class="search-result-name">${{m.name}}</span>
        <span class="search-result-cat">${{m.category || m.group}}</span>
      </a>`).join('');
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

    # Stop / Take-profit
    sl = data.get("stop_loss")
    tp = data.get("take_profit")
    rules_html = ""
    if sl or tp:
        rows = []
        if sl:
            d = data.get("stop_loss_dist_pct") or 0
            rows.append(f'<div class="rule-row"><span class="dn">🔴 停損</span><span class="mono tnum">{sl}</span><span class="muted mono tnum small">距離 {d:+.1f}%</span></div>')
        if tp:
            d = data.get("take_profit_dist_pct") or 0
            rows.append(f'<div class="rule-row"><span class="up">🟢 停利</span><span class="mono tnum">{tp}</span><span class="muted mono tnum small">距離 {d:+.1f}%</span></div>')
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
  <div class="section-head"><h2>🤖 AI Verdict</h2>{_sentiment_badge(ha.get("outlook", "中性"))}</div>
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
    <h2>📰 近期相關新聞 <span class="muted small">({len(news_for_ticker)} 則)</span></h2>
  </div>
  <ul class="dd-news-list">{"".join(rows)}</ul>
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
{ai_html}
{news_html}
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
:root {
  --bg-0: #07090d;
  --bg-1: #0d1118;
  --bg-2: #141923;
  --bg-3: #1c2230;
  --bg-4: #262d3d;
  --line: rgba(255,255,255,0.06);
  --line-2: rgba(255,255,255,0.10);
  --tx-1: #f5f7fa;
  --tx-2: #b6bdc9;
  --tx-3: #717786;
  --tx-4: #4a4f5b;
  --up:    #ff3b3b;
  --up-bg: rgba(255,59,59,0.12);
  --up-soft: #ff7a7a;
  --dn:    #1bd97c;
  --dn-bg: rgba(27,217,124,0.12);
  --dn-soft:#5fe5a3;
  --amber:  #ffb547;
  --amber-bg: rgba(255,181,71,0.12);
  --purple: #b584ff;
  --purple-bg: rgba(181,132,255,0.12);
  --accent: #5b8dff;
  --accent-2: #82a8ff;
  --accent-glow: rgba(91,141,255,0.35);
  --accent-soft: rgba(91,141,255,0.14);
  --pillar-growth: #5b8dff;
  --pillar-defense: #ffb547;
  --pillar-flex: #b584ff;
  --font-sans: -apple-system, BlinkMacSystemFont, "PingFang TC", "Noto Sans TC", "Helvetica Neue", Helvetica, Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace;
  --pad: 16px; --pad-sm: 12px; --gap: 12px; --r: 14px; --r-sm: 10px;
}

* { box-sizing: border-box; }
html, body {
  margin: 0; background: var(--bg-0); color: var(--tx-1);
  font-family: var(--font-sans); -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility; line-height: 1.6;
}
body { overflow-x: hidden; }
a { color: var(--accent-2); text-decoration: none; }
a:hover { color: #b8d0ff; }

/* ── Layout ── */
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px; }
.mono { font-family: var(--font-mono); font-feature-settings: "tnum"; }
.tnum { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
.muted { color: var(--tx-3); }
.small { font-size: 12px; }
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
.action-header { font-weight: 700; font-size: 13px; margin-bottom: 10px; letter-spacing: 0.3px; }
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
   CLEAN TOP LAYOUT — main index.html (focus on clarity)
   ──────────────────────────────────────────────────────────── */
.top-header {
  padding: 20px 24px 0;
  max-width: 1200px;
  margin: 0 auto;
}
.top-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.top-title { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.2px; }
.top-date {
  font-size: 11px; color: var(--tx-3);
  letter-spacing: 0.5px; text-transform: uppercase;
}

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
}
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

/* ── Radar tab ── */
.radar-empty { padding: 40px 20px; text-align: center; }
.radar-body { padding: 0; }
.radar-intro { padding: 20px 22px 14px; border-bottom: 1px solid var(--line); }
.radar-title { margin: 0 0 6px; font-size: 20px; font-weight: 700; }
.radar-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
  padding: 18px 22px;
}
.radar-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: var(--r);
  padding: 16px 18px 14px;
  display: flex; flex-direction: column; gap: 10px;
  transition: border-color 0.15s;
}
.radar-card:hover { border-left-color: var(--accent-2); }
.radar-card-head h3 { margin: 0; font-size: 17px; }
.radar-card-head h3 .muted { font-weight: 500; font-size: 14px; }
.radar-card-body { display: flex; flex-direction: column; gap: 8px; }
.radar-row { font-size: 13px; line-height: 1.65; }
.radar-row.radar-risk-row { color: var(--tx-2); }
.radar-label {
  display: inline-block;
  font-size: 11px; padding: 2px 8px; margin-right: 8px;
  background: var(--bg-3); color: var(--tx-2);
  border-radius: 4px; font-weight: 600;
  font-family: var(--font-mono);
}
.radar-label.dn { color: var(--up-soft); background: var(--up-bg); }
.radar-card-foot { padding-top: 6px; border-top: 1px solid var(--line); }
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
.alloc-levels-row { background: var(--bg-2); padding: 8px 12px; border-radius: var(--r-sm); font-size: 12px; margin: 8px 0 10px; line-height: 1.7; }
.alloc-sources { margin: 6px 0; display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }

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
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    DOCS_DIR.mkdir(exist_ok=True)
    DOCS_BRIEFS_DIR.mkdir(exist_ok=True)
    DOCS_HOLDINGS_DIR.mkdir(exist_ok=True)

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

    (DOCS_DIR / "index.html").write_text(render_index(briefs, pf), encoding="utf-8")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

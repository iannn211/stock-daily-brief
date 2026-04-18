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


def render_index(briefs: list[dict], pf: dict | None) -> str:
    macro_html = render_macro_ribbon(pf) if pf else ""
    portfolio_html = render_portfolio_card(pf) if pf else ""
    holdings_html = render_holdings_grid(pf) if pf else ""

    if briefs:
        latest = briefs[0]["date"]
        total = len(briefs)
        subtitle = f"台股+美股每日情報簡報 · 共 {total} 份 · 最新 {latest}"
    else:
        subtitle = "台股+美股每日情報簡報"

    weekday_map = {"一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu",
                   "五": "Fri", "六": "Sat", "日": "Sun"}
    cards = []
    for b in briefs:
        tags_html = "".join(
            f'<span class="chip chip-muted small">{html.escape(t)}</span>'
            for t in b["tags"][:5]
        )
        day_en = weekday_map.get(b["weekday"], "")
        has_ai = (ANALYSES_DIR / f'{b["date"]}.json').exists()
        ai_badge = '<span class="ai-badge">🤖 AI 已分析</span>' if has_ai else ''
        cards.append(f'''
        <a class="brief-card" href="briefs/{b["date"]}.html">
          <div class="bc-top">
            <div>
              <div class="bc-date mono">{b["date"]}</div>
              <div class="bc-day muted small">週{b["weekday"]} · {day_en}</div>
            </div>
            {ai_badge}
          </div>
          <div class="bc-count muted small">{b["count"]} 則新聞</div>
          <div class="bc-tags">{tags_html}</div>
          <div class="bc-link">看完整分析 →</div>
        </a>''')
    briefs_html = "".join(cards) if cards else '<p class="empty">還沒有 brief。</p>'

    body = f'''
<header class="site-header">
  <div class="wrap">
    <div class="title-row">
      <h1>📈 Stock AI Desk</h1>
      <span class="live-dot accent"></span>
    </div>
    <p class="subtitle muted">{html.escape(subtitle)}</p>
  </div>
</header>

{macro_html}
{portfolio_html}
{holdings_html}

<section class="wrap briefs-section">
  <div class="section-head">
    <h2>🗞️ 歷史 Brief</h2>
    <span class="muted small">{len(briefs)} 份</span>
  </div>
  <input type="search" id="search" placeholder="🔍 搜尋日期、產業標籤..." autocomplete="off" class="search-box">
  <div class="briefs-grid" id="brief-list">{briefs_html}</div>
</section>

<script>
const q = document.getElementById('search');
const cards = document.querySelectorAll('.brief-card');
if (q) {{
  q.addEventListener('input', () => {{
    const t = q.value.toLowerCase().trim();
    cards.forEach(c => {{
      c.style.display = !t || c.textContent.toLowerCase().includes(t) ? '' : 'none';
    }});
  }});
}}
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
                        is_watchlist: bool = False) -> str:
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

    price_str = f"{price:.2f}" if price is not None else "—"
    status_str = "觀察中" if is_watchlist else "持有中"
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

{ai_html}
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

    # Per-holding deep-dive pages
    if pf:
        holdings_count = 0
        for h in pf.get("holdings", []):
            out = DOCS_HOLDINGS_DIR / f"{h['symbol']}.html"
            out.write_text(
                render_holding_page(h, pf, history, latest_analysis, is_watchlist=False),
                encoding="utf-8",
            )
            holdings_count += 1
        for w in pf.get("watchlist", []):
            out = DOCS_HOLDINGS_DIR / f"{w['symbol']}.html"
            out.write_text(
                render_holding_page(w, pf, history, latest_analysis, is_watchlist=True),
                encoding="utf-8",
            )
            holdings_count += 1
        print(f"wrote {holdings_count} holding pages", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

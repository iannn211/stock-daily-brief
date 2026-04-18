"""
Build the static HTML dashboard from briefs/*.md.

Generates:
  docs/index.html            — list of all briefs with search
  docs/briefs/<date>.html    — each brief rendered as a page
  docs/styles.css            — shared stylesheet

Designed to be served from GitHub Pages (source: main branch, /docs folder).
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown as md

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
BRIEFS_DIR = ROOT / "briefs"
DOCS_DIR = ROOT / "docs"
DOCS_BRIEFS_DIR = DOCS_DIR / "briefs"
PORTFOLIO_JSON = ROOT / "portfolio.json"

DATE_RE = re.compile(r"^# Daily Brief — (\d{4}-\d{2}-\d{2}) \(週(.)\)", re.MULTILINE)
COUNT_RE = re.compile(r"抓到 (\d+) 則新聞")
SECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)
PROMPT_MARKER = "\n---\n\n你是我的"


# ---------------------------------------------------------------------------
# Load & parse
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
        # Deduplicate while preserving order (Python 3.7+ dict ordering).
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
    """Return (preamble, copyable_prompt+news).

    The copyable part is everything from '你是我的股市研究助理' onwards — that's
    what the user pastes into Claude.ai.
    """
    idx = content.find(PROMPT_MARKER)
    if idx < 0:
        return "", content
    split_at = idx + len("\n---\n\n")
    return content[:split_at], content[split_at:]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
"""

PAGE_FOOT = """
<footer>
  <p>生成於 {now} · <a href="https://github.com/iannn211/stock-daily-brief" target="_blank">原始碼</a></p>
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Portfolio card
# ---------------------------------------------------------------------------

def _fmt_twd(n: float, sign: bool = False) -> str:
    sign_ch = "+" if (sign and n > 0) else ("-" if n < 0 else "")
    return f"{sign_ch}NT${abs(n):,.0f}"


def _fmt_pct(n: float) -> str:
    return f"{n:+.2f}%"


def _cls(n: float) -> str:
    """CSS class for red (negative) / green (positive) — Taiwan convention."""
    if n > 0:
        return "up"
    if n < 0:
        return "down"
    return "flat"


def render_portfolio_card() -> str:
    if not PORTFOLIO_JSON.exists():
        return ""
    try:
        pf = json.loads(PORTFOLIO_JSON.read_text(encoding="utf-8"))
    except Exception:
        return ""

    s = pf.get("summary", {})
    bench = pf.get("benchmark", {})
    attr = pf.get("attribution", {})
    alerts = pf.get("alerts", {})
    holdings = pf.get("holdings", [])
    watchlist = pf.get("watchlist", [])

    as_of = pf.get("as_of", "")
    try:
        dt = datetime.fromisoformat(as_of)
        as_of_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        as_of_str = as_of

    day_pnl = s.get("day_pnl_twd", 0)
    day_pct = s.get("day_pnl_pct", 0)
    total_pnl = s.get("total_pnl_twd", 0)
    total_pct = s.get("total_pnl_pct", 0)
    total_value = s.get("total_value_twd", 0)
    alpha = s.get("alpha_vs_benchmark_pct", 0)
    bench_pct = bench.get("day_change_pct", 0)
    bench_sym = bench.get("symbol", "")

    # Attribution chips
    pos_chips = "".join(
        f'<span class="chip up">{h["symbol"]} {_fmt_twd(h["day_contribution"], sign=True)}</span>'
        for h in attr.get("positive", [])
    ) or '<span class="chip muted">無</span>'
    neg_chips = "".join(
        f'<span class="chip down">{h["symbol"]} {_fmt_twd(h["day_contribution"], sign=True)}</span>'
        for h in attr.get("negative", [])
    ) or '<span class="chip muted">無</span>'

    # Alerts
    alerts_html = ""
    if alerts.get("stop_loss"):
        items = "".join(
            f'<li><strong>{h["symbol"]} {h["name"]}</strong> '
            f'現價 <span class="down">{h["price"]}</span> ≤ 停損價 {h["stop_loss"]}</li>'
            for h in alerts["stop_loss"]
        )
        alerts_html += f'<div class="alert-box alert-stop"><div class="alert-title">🔴 停損警告</div><ul>{items}</ul></div>'
    if alerts.get("take_profit"):
        items = "".join(
            f'<li><strong>{h["symbol"]} {h["name"]}</strong> '
            f'現價 <span class="up">{h["price"]}</span> ≥ 停利目標 {h["take_profit"]}</li>'
            for h in alerts["take_profit"]
        )
        alerts_html += f'<div class="alert-box alert-target"><div class="alert-title">🟢 停利訊號</div><ul>{items}</ul></div>'

    # Holdings table
    holding_rows = "".join(
        f'<tr>'
        f'<td><strong>{h["symbol"]}</strong> {html.escape(h["name"])}</td>'
        f'<td class="num">{h["shares"]:,}</td>'
        f'<td class="num">{h["cost_basis"]:.2f}</td>'
        f'<td class="num">{h["price"]:.2f}</td>'
        f'<td class="num {_cls(h["day_change_pct"])}">{_fmt_pct(h["day_change_pct"])}</td>'
        f'<td class="num">{_fmt_twd(h["value"])}</td>'
        f'<td class="num {_cls(h["pnl"])}">{_fmt_twd(h["pnl"], sign=True)}</td>'
        f'<td class="num {_cls(h["pnl_pct"])}">{_fmt_pct(h["pnl_pct"])}</td>'
        f'</tr>'
        for h in holdings
    )

    # Watchlist row (compact)
    watch_rows = "".join(
        f'<tr>'
        f'<td><strong>{w["symbol"]}</strong> {html.escape(w["name"])}</td>'
        f'<td class="num">{w["price"]:.2f}</td>'
        f'<td class="num {_cls(w["day_change_pct"])}">{_fmt_pct(w["day_change_pct"])}</td>'
        f'<td class="num muted">{w["currency"]}</td>'
        f'</tr>'
        for w in watchlist
    )

    return f'''
<section class="pf-card wrap">
  <div class="pf-head">
    <h2>📊 投資組合</h2>
    <div class="pf-asof">{html.escape(as_of_str)}</div>
  </div>

  <div class="pf-metrics">
    <div class="pf-metric">
      <div class="pf-label">總市值</div>
      <div class="pf-val">{_fmt_twd(total_value)}</div>
    </div>
    <div class="pf-metric">
      <div class="pf-label">今日損益</div>
      <div class="pf-val {_cls(day_pnl)}">{_fmt_twd(day_pnl, sign=True)} <span class="pf-sub">({_fmt_pct(day_pct)})</span></div>
    </div>
    <div class="pf-metric">
      <div class="pf-label">總損益</div>
      <div class="pf-val {_cls(total_pnl)}">{_fmt_twd(total_pnl, sign=True)} <span class="pf-sub">({_fmt_pct(total_pct)})</span></div>
    </div>
  </div>

  <div class="pf-alpha">
    vs <strong>{bench_sym}</strong> <span class="{_cls(bench_pct)}">({_fmt_pct(bench_pct)})</span>
    → alpha <span class="{_cls(alpha)}"><strong>{_fmt_pct(alpha)}</strong></span>
  </div>

  <div class="pf-attr">
    <div class="pf-attr-row"><span class="pf-attr-lbl">正貢獻</span>{pos_chips}</div>
    <div class="pf-attr-row"><span class="pf-attr-lbl">負貢獻</span>{neg_chips}</div>
  </div>

  {alerts_html}

  <details class="pf-details">
    <summary>持股明細 ({len(holdings)})</summary>
    <table>
      <thead><tr><th>標的</th><th>股數</th><th>成本</th><th>現價</th><th>今日</th><th>市值</th><th>損益</th><th>%</th></tr></thead>
      <tbody>{holding_rows}</tbody>
    </table>
  </details>

  <details class="pf-details">
    <summary>追蹤清單 ({len(watchlist)})</summary>
    <table>
      <thead><tr><th>標的</th><th>現價</th><th>今日</th><th>幣別</th></tr></thead>
      <tbody>{watch_rows}</tbody>
    </table>
  </details>
</section>
'''


def render_index(briefs: list[dict]) -> str:
    if briefs:
        latest = briefs[0]["date"]
        total = len(briefs)
        subtitle = f"台股+美股每日情報簡報 · 共 {total} 份 · 最新 {latest}"
    else:
        subtitle = "台股+美股每日情報簡報 · 尚未產生任何 brief"

    # Card per brief
    cards = []
    for b in briefs:
        tags_html = "".join(
            f'<span class="tag">{html.escape(t)}</span>' for t in b["tags"][:6]
        )
        weekday_map = {"一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu",
                       "五": "Fri", "六": "Sat", "日": "Sun"}
        day_en = weekday_map.get(b["weekday"], "")
        cards.append(f'''
        <a class="card" href="briefs/{b["date"]}.html">
          <div class="card-top">
            <div class="card-date">{b["date"]}</div>
            <div class="card-day">週{b["weekday"]} · {day_en}</div>
          </div>
          <div class="card-count">{b["count"]} 則新聞</div>
          <div class="card-tags">{tags_html}</div>
          <div class="card-link">看完整分析 →</div>
        </a>''')
    cards_html = "\n".join(cards) if cards else '<p class="empty">還沒有 brief，等第一次排程跑完就會出現。</p>'

    portfolio_html = render_portfolio_card()

    body = f'''
<header class="site-header">
  <div class="wrap">
    <h1>📈 Stock Daily Brief</h1>
    <p class="subtitle">{html.escape(subtitle)}</p>
  </div>
</header>
{portfolio_html}
<div class="search-wrap wrap">
  <input type="search" id="search" placeholder="🔍 搜尋日期、產業標籤..." autocomplete="off">
</div>
<main id="brief-list" class="grid wrap">
{cards_html}
</main>
<script>
const q = document.getElementById('search');
const cards = document.querySelectorAll('.card');
q.addEventListener('input', () => {{
  const t = q.value.toLowerCase().trim();
  cards.forEach(c => {{
    c.style.display = !t || c.textContent.toLowerCase().includes(t) ? '' : 'none';
  }});
}});
</script>
'''
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    return (
        PAGE_HEAD.format(title="Stock Daily Brief", css_href="styles.css")
        + body
        + PAGE_FOOT.format(now=now)
    )


def render_brief_page(brief: dict) -> str:
    _, copyable = split_prompt(brief["content"])
    html_body = md.markdown(
        copyable,
        extensions=["tables", "fenced_code", "sane_lists"],
    )

    weekday_map = {"一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu",
                   "五": "Fri", "六": "Sat", "日": "Sun"}
    day_en = weekday_map.get(brief["weekday"], "")

    # The prompt text to put on clipboard — full copyable section.
    prompt_for_clipboard = copyable

    body = f'''
<header class="brief-header wrap">
  <a href="../index.html" class="back">← 回首頁</a>
  <h1>Daily Brief · {brief["date"]}</h1>
  <p class="meta">週{brief["weekday"]} · {day_en} · {brief["count"]} 則新聞</p>
  <div class="actions">
    <button id="copy-btn" class="btn-primary">📋 複製完整 Prompt 貼到 Claude.ai</button>
    <a href="https://claude.ai/new" target="_blank" class="btn-secondary">🚀 開 Claude.ai</a>
  </div>
  <p class="hint">流程：① 按複製按鈕 → ② 開 Claude.ai 新對話 → ③ 貼上送出 → 拿到 7 點分析（含今日行動清單）</p>
</header>
<main class="brief-body wrap">
{html_body}
</main>
<div id="prompt-source" hidden>{html.escape(prompt_for_clipboard)}</div>
<script>
const btn = document.getElementById('copy-btn');
const src = document.getElementById('prompt-source');
btn.addEventListener('click', async () => {{
  const text = src.textContent;
  try {{
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = '✅ 已複製！去 Claude.ai 貼上（Cmd+V）';
    btn.classList.add('ok');
    setTimeout(() => {{ btn.textContent = orig; btn.classList.remove('ok'); }}, 4000);
  }} catch (e) {{
    alert('複製失敗：' + e.message + '\\n可以手動全選下方內容複製。');
  }}
}});
</script>
'''
    now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    return (
        PAGE_HEAD.format(title=f'{brief["date"]} · Stock Daily Brief', css_href="../styles.css")
        + body
        + PAGE_FOOT.format(now=now)
    )


# ---------------------------------------------------------------------------
# CSS (dark theme, mobile friendly)
# ---------------------------------------------------------------------------

STYLES_CSS = """
/* ---- reset + base ---- */
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang TC",
               "Microsoft JhengHei", "Noto Sans TC", sans-serif;
  background: #0b0d10;
  color: #e6e6e6;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
a { color: #2ab687; text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 960px; margin: 0 auto; padding: 0 20px; }

/* ---- site header ---- */
.site-header {
  background: linear-gradient(180deg, #0f141a 0%, #0b0d10 100%);
  padding: 36px 0 28px;
  border-bottom: 1px solid #1f2530;
}
.site-header h1 { margin: 0 0 6px; font-size: 28px; letter-spacing: 0.5px; }
.site-header .subtitle { margin: 0; color: #8a95a5; font-size: 14px; }

/* ---- search ---- */
.search-wrap { margin: 24px auto 18px; }
#search {
  width: 100%;
  padding: 12px 16px;
  background: #141920;
  color: #e6e6e6;
  border: 1px solid #222a36;
  border-radius: 10px;
  font-size: 15px;
  outline: none;
  transition: border-color 0.15s, background 0.15s;
}
#search:focus { border-color: #2ab687; background: #151c26; }

/* ---- card grid ---- */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  padding-bottom: 40px;
}
.card {
  display: block;
  background: #141920;
  border: 1px solid #1f2530;
  border-radius: 12px;
  padding: 18px 18px 16px;
  color: inherit;
  text-decoration: none !important;
  transition: transform 0.1s, border-color 0.15s, background 0.15s;
}
.card:hover {
  border-color: #2ab687;
  background: #161e26;
  transform: translateY(-1px);
}
.card-top { display: flex; justify-content: space-between; align-items: baseline; }
.card-date { font-size: 18px; font-weight: 600; letter-spacing: 0.3px; }
.card-day { font-size: 12px; color: #8a95a5; }
.card-count { font-size: 13px; color: #8a95a5; margin: 6px 0 10px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; min-height: 24px; }
.tag {
  display: inline-block;
  font-size: 11px;
  padding: 3px 8px;
  background: #1b2733;
  color: #9fd8c1;
  border-radius: 999px;
  border: 1px solid #233041;
  white-space: nowrap;
}
.card-link { font-size: 13px; color: #2ab687; font-weight: 500; }
.empty { color: #8a95a5; text-align: center; padding: 60px 0; grid-column: 1 / -1; }

/* ---- brief page ---- */
.brief-header {
  padding: 28px 20px 20px;
  border-bottom: 1px solid #1f2530;
  background: #0f141a;
}
.brief-header .back {
  display: inline-block;
  color: #8a95a5;
  font-size: 14px;
  margin-bottom: 10px;
}
.brief-header h1 { margin: 0 0 4px; font-size: 26px; }
.brief-header .meta { color: #8a95a5; font-size: 13px; margin: 0 0 16px; }
.brief-header .hint { color: #8a95a5; font-size: 12px; margin-top: 10px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.btn-primary, .btn-secondary {
  padding: 10px 16px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: transform 0.1s, background 0.15s;
  border: none;
  font-family: inherit;
}
.btn-primary {
  background: #2ab687;
  color: #04140d;
}
.btn-primary:hover { background: #34c898; transform: translateY(-1px); }
.btn-primary.ok { background: #1d8f6b; }
.btn-secondary {
  background: #1b2733;
  color: #e6e6e6;
  border: 1px solid #233041;
  text-decoration: none !important;
  display: inline-flex;
  align-items: center;
}
.btn-secondary:hover { background: #233041; }

/* ---- rendered markdown body ---- */
.brief-body {
  padding: 28px 20px 60px;
  font-size: 15px;
}
.brief-body h2 {
  margin-top: 36px;
  padding-top: 18px;
  border-top: 1px solid #1f2530;
  font-size: 20px;
}
.brief-body h2:first-child {
  border-top: none;
  padding-top: 0;
  margin-top: 0;
}
.brief-body h3 {
  margin-top: 24px;
  font-size: 16px;
  color: #9fd8c1;
}
.brief-body p { margin: 10px 0; }
.brief-body ul { padding-left: 22px; }
.brief-body li { margin: 8px 0; }
.brief-body li > p { margin: 4px 0; }
.brief-body a {
  color: #6ac8ff;
  word-break: break-word;
}
.brief-body blockquote {
  margin: 6px 0 10px;
  padding: 6px 0 6px 14px;
  border-left: 3px solid #233041;
  color: #b4bcc7;
  font-size: 14px;
}
.brief-body code {
  background: #1a2028;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 13px;
}
.brief-body hr {
  border: none;
  border-top: 1px solid #1f2530;
  margin: 28px 0;
}
.brief-body strong { color: #f4c669; }

/* ---- footer ---- */
footer {
  padding: 24px 20px 36px;
  text-align: center;
  color: #5a6374;
  font-size: 12px;
  border-top: 1px solid #1f2530;
  margin-top: 40px;
}
footer a { color: #8a95a5; }

/* ---- portfolio card ---- */
.pf-card {
  background: #141920;
  border: 1px solid #1f2530;
  border-radius: 14px;
  padding: 22px 22px 16px;
  margin: 20px auto;
}
.pf-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 16px;
}
.pf-head h2 { margin: 0; font-size: 18px; letter-spacing: 0.3px; }
.pf-asof { color: #8a95a5; font-size: 12px; }
.pf-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid #1f2530;
}
.pf-metric { }
.pf-label { color: #8a95a5; font-size: 12px; margin-bottom: 4px; }
.pf-val { font-size: 20px; font-weight: 600; letter-spacing: 0.3px; }
.pf-sub { font-size: 13px; font-weight: 500; color: inherit; opacity: 0.85; }

/* Taiwan convention: up = red 🔴 / down = green 🟢 */
.up   { color: #ff5b5b; }
.down { color: #2ab687; }
.flat { color: #b4bcc7; }

.pf-alpha {
  font-size: 14px;
  color: #b4bcc7;
  margin: 6px 0 16px;
  padding: 10px 12px;
  background: #0f141a;
  border-radius: 8px;
}

.pf-attr { margin-bottom: 14px; }
.pf-attr-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 13px;
}
.pf-attr-lbl {
  color: #8a95a5;
  min-width: 60px;
  font-size: 12px;
}
.chip {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid;
}
.chip.up    { color: #ff8585; background: #2a1414; border-color: #4a2020; }
.chip.down  { color: #40c99d; background: #0f2620; border-color: #20443a; }
.chip.muted { color: #5a6374; background: #141920; border-color: #222a36; }

.alert-box {
  border-radius: 10px;
  padding: 12px 14px;
  margin: 10px 0;
  border: 1px solid;
}
.alert-stop   { background: #2a1414; border-color: #5a2020; }
.alert-target { background: #0f2620; border-color: #20443a; }
.alert-title  { font-weight: 600; margin-bottom: 6px; font-size: 13px; }
.alert-box ul { margin: 4px 0 0; padding-left: 20px; font-size: 13px; }
.alert-box li { margin: 4px 0; }

.pf-details {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #1f2530;
}
.pf-details summary {
  cursor: pointer;
  color: #9fd8c1;
  font-size: 13px;
  padding: 4px 0;
  user-select: none;
}
.pf-details summary:hover { color: #2ab687; }
.pf-details table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
  font-size: 13px;
}
.pf-details th {
  text-align: left;
  padding: 8px 6px;
  color: #8a95a5;
  border-bottom: 1px solid #1f2530;
  font-weight: 500;
  font-size: 12px;
}
.pf-details td {
  padding: 8px 6px;
  border-bottom: 1px solid #1a1f27;
}
.pf-details td.num { text-align: right; font-variant-numeric: tabular-nums; }
.pf-details td.muted { color: #5a6374; font-size: 11px; }

/* ---- mobile ---- */
@media (max-width: 540px) {
  .site-header { padding: 24px 0 20px; }
  .site-header h1 { font-size: 22px; }
  .brief-header h1 { font-size: 20px; }
  .brief-body { font-size: 14px; padding: 20px 16px 50px; }
  .brief-body h2 { font-size: 17px; }
  .btn-primary, .btn-secondary { width: 100%; text-align: center; justify-content: center; }
  .pf-card { padding: 16px 14px 12px; margin: 14px 12px; border-radius: 12px; }
  .pf-val { font-size: 17px; }
  .pf-details table { font-size: 11px; }
  .pf-details th, .pf-details td { padding: 6px 3px; }
}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    DOCS_DIR.mkdir(exist_ok=True)
    DOCS_BRIEFS_DIR.mkdir(exist_ok=True)

    briefs = load_briefs()
    print(f"loaded {len(briefs)} briefs", file=sys.stderr)

    # Write CSS
    (DOCS_DIR / "styles.css").write_text(STYLES_CSS, encoding="utf-8")

    # Write a .nojekyll so Pages doesn't try to run Jekyll
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    # Write index
    (DOCS_DIR / "index.html").write_text(render_index(briefs), encoding="utf-8")
    print("wrote docs/index.html", file=sys.stderr)

    # Write each brief page
    for brief in briefs:
        out_path = DOCS_BRIEFS_DIR / f"{brief['date']}.html"
        out_path.write_text(render_brief_page(brief), encoding="utf-8")
    print(f"wrote {len(briefs)} brief pages", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

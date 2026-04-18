"""
Daily stock brief generator.

Fetches news from Taiwan + US financial RSS feeds, classifies articles by
sector and by the user's holdings, and writes a Markdown "prompt" to
briefs/YYYY-MM-DD.md. The user pastes that file into Claude.ai for analysis.

Runs locally or inside GitHub Actions. No Claude API calls in v1.
"""
from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
BRIEFS_DIR = ROOT / "briefs"

# ---------------------------------------------------------------------------
# Configuration — edit these to tune sources / watchlist
# ---------------------------------------------------------------------------

def _gnews(query: str) -> str:
    """Google News RSS query (zh-TW). Accepts Chinese; handles URL-encoding."""
    from urllib.parse import quote
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


FEEDS: list[dict] = [
    # Taiwan — direct newspaper feeds
    {"name": "經濟日報 證券",     "url": "https://money.udn.com/rssfeed/news/1001/5590?ch=news", "market": "TW"},
    {"name": "經濟日報 股市要聞", "url": "https://money.udn.com/rssfeed/news/1001/5591?ch=news", "market": "TW"},
    {"name": "Yahoo 股市 TW",     "url": "https://tw.stock.yahoo.com/rss",                       "market": "TW"},
    # Taiwan — Google News aggregated queries (catches 鉅亨、工商、商周、天下、Anue 等)
    {"name": "GNews 台股",        "url": _gnews("台股"),                                         "market": "TW"},
    {"name": "GNews 半導體",      "url": _gnews("台積電 OR 半導體 OR 晶圓"),                     "market": "TW"},
    {"name": "GNews AI光通訊",    "url": _gnews("AI伺服器 OR CoWoS OR 光通訊 OR CPO"),            "market": "TW"},
    {"name": "GNews 產業",        "url": _gnews("PCB OR 載板 OR 被動元件 OR 散熱 OR MLCC"),       "market": "TW"},
    # US
    {"name": "Yahoo Finance",     "url": "https://finance.yahoo.com/news/rssindex",              "market": "US"},
    {"name": "MarketWatch Top",   "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "market": "US"},
    {"name": "GNews S&P",         "url": _gnews("S&P 500 OR VOO OR Federal Reserve"),            "market": "US"},
]

# User's current holdings — articles matching these are pulled to the top.
HOLDINGS: dict[str, list[str]] = {
    "2330 台積電":  ["台積電", "2330", "TSMC"],
    "0050 元大台灣50": ["0050", "元大台灣50", "台灣50", "台灣五十"],
    "VOO (關注中)": ["VOO", "S&P 500", "S&P500", "SP500", "標普500", "標普 500"],
}

# Sector watchlist — 8 themes the user wants to track.
SECTORS: dict[str, list[str]] = {
    "半導體代工": [
        "台積電", "2330", "TSMC", "晶圓代工", "聯電", "2303", "UMC", "世界先進", "5347",
    ],
    "IC 設計": [
        "IC 設計", "IC設計", "fabless", "聯發科", "2454", "聯詠", "3034", "瑞昱", "2379",
        "群聯", "8299", "信驊", "5274", "世芯", "3443", "創意", "3443",
    ],
    "AI 伺服器 / CoWoS": [
        "AI 伺服器", "AI伺服器", "CoWoS", "輝達", "NVIDIA", "HBM", "GB200", "GB300",
        "鴻海", "2317", "廣達", "2382", "緯創", "3231", "緯穎", "6669", "技嘉", "2376",
    ],
    "光通訊": [
        "光通訊", "光模組", "矽光子", "CPO", "co-packaged optics", "光收發",
        "華星光", "4979", "波若威", "3163", "聯亞", "3081", "眾達", "4977",
        "光環", "3234", "上詮", "3363",
    ],
    "PCB / 載板": [
        "PCB", "電路板", "載板", "ABF", "臻鼎", "4958", "金像電", "2368", "欣興", "3037",
        "南電", "8046", "景碩", "3189",
    ],
    "被動元件": [
        "被動元件", "MLCC", "國巨", "2327", "華新科", "2492", "禾伸堂", "3026", "奇力新", "2456",
    ],
    "半導體設備": [
        "半導體設備", "ASML", "應用材料", "東京威力", "家登", "3680", "辛耘", "3583",
        "京鼎", "3413", "帆宣", "6196", "弘塑", "3131",
    ],
    "散熱 / 液冷": [
        "散熱", "液冷", "均熱片", "熱導管", "雙鴻", "3324", "奇鋐", "3017", "建準", "2421",
        "泰碩", "3338", "超眾", "6230",
    ],
}

# Articles older than this are dropped.
MAX_AGE = timedelta(hours=36)

# Per-bucket cap — avoids drowning the prompt in near-duplicates.
MAX_PER_HOLDING = 12
MAX_PER_SECTOR = 8
MAX_UNTAGGED = 12  # per market (TW/US)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Article:
    title: str
    link: str
    summary: str
    source: str
    market: str
    published: datetime | None
    matched_holdings: list[str] = field(default_factory=list)
    matched_sectors: list[str] = field(default_factory=list)

    @property
    def is_tagged(self) -> bool:
        return bool(self.matched_holdings or self.matched_sectors)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(html.unescape(text), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _parse_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return datetime(*tm[:6], tzinfo=ZoneInfo("UTC")).astimezone(TAIPEI)
            except Exception:
                continue
    return None


def fetch_feed(feed: dict) -> list[Article]:
    print(f"  fetching {feed['name']} …", file=sys.stderr)
    try:
        parsed = feedparser.parse(feed["url"])
    except Exception as exc:
        print(f"    !! failed: {exc}", file=sys.stderr)
        return []

    if parsed.bozo and not parsed.entries:
        print(f"    !! no entries ({parsed.bozo_exception})", file=sys.stderr)
        return []

    now = datetime.now(TAIPEI)
    articles: list[Article] = []
    for entry in parsed.entries:
        published = _parse_published(entry)
        if published and (now - published) > MAX_AGE:
            continue
        articles.append(Article(
            title=_clean(entry.get("title", "")),
            link=entry.get("link", ""),
            summary=_clean(entry.get("summary", "") or entry.get("description", ""))[:400],
            source=feed["name"],
            market=feed["market"],
            published=published,
        ))
    print(f"    got {len(articles)} fresh articles", file=sys.stderr)
    return articles


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(articles: Iterable[Article]) -> None:
    for article in articles:
        haystack = f"{article.title} {article.summary}"
        for holding, keywords in HOLDINGS.items():
            if any(kw in haystack for kw in keywords):
                article.matched_holdings.append(holding)
        for sector, keywords in SECTORS.items():
            if any(kw in haystack for kw in keywords):
                article.matched_sectors.append(sector)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_article(article: Article) -> str:
    when = article.published.strftime("%m-%d %H:%M") if article.published else "—"
    line = f"- [{article.title}]({article.link}) · {article.source} · {when}"
    if article.summary:
        line += f"\n  > {article.summary}"
    return line


def render_brief(articles: list[Article], date: datetime) -> str:
    date_str = date.strftime("%Y-%m-%d")
    weekday = "一二三四五六日"[date.weekday()]
    total = len(articles)

    by_holding: dict[str, list[Article]] = {k: [] for k in HOLDINGS}
    by_sector: dict[str, list[Article]] = {k: [] for k in SECTORS}
    untagged: list[Article] = []

    for article in articles:
        if article.matched_holdings:
            for holding in article.matched_holdings:
                by_holding[holding].append(article)
        elif article.matched_sectors:
            for sector in article.matched_sectors:
                by_sector[sector].append(article)
        else:
            untagged.append(article)

    lines: list[str] = []
    lines.append(f"# Daily Brief — {date_str} (週{weekday})")
    lines.append("")
    lines.append(f"抓到 {total} 則新聞，以下是幫 Claude.ai 準備的分析 prompt。")
    lines.append("**使用方式**：複製下面整段（從「你是我的股市研究助理」開始）貼到 Claude.ai。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- The actual prompt starts here ---
    lines.append("你是我的股市研究助理。我是台灣散戶，目前持有：")
    lines.append("- 元大台灣50 (0050) × 1,200 股")
    lines.append("- 台積電 (2330) × 20 股")
    lines.append("- 還在考慮要不要定期定額 VOO (S&P 500 ETF)")
    lines.append("")
    lines.append("我想追蹤的 8 個產業題材：**半導體代工、IC 設計、AI 伺服器/CoWoS、光通訊、PCB/載板、被動元件、半導體設備、散熱/液冷**。")
    lines.append("")
    lines.append("以下是今天抓到的新聞，已經按「跟我持股相關」和「分產業」分好。請你：")
    lines.append("")
    lines.append("1. **今日重點**：挑出 3–5 則最值得我注意的新聞，解釋為什麼重要（用我聽得懂的白話）")
    lines.append("2. **對我持股的潛在影響**：逐一看 2330 / 0050 / VOO，有沒有該關心的事")
    lines.append("3. **題材溫度計**：哪個產業題材今天最熱？為什麼熱？是延續性還是一次性事件？")
    lines.append("4. **值得研究的個股**：如果有具體公司我該去了解（**不是叫我買**），列出來並給我研究的切入點（營收 YoY/QoQ、本益比位置、法人連續買賣超）")
    lines.append("5. **風險提醒**：有沒有利空、總經風險、或我可能忽略的事")
    lines.append("6. **新手學習點**：挑一個今天新聞裡的名詞/概念，用 3 句話教我（每天累積）")
    lines.append("7. **📋 今日行動清單（三色燈）**：給我明確、可執行的結論——")
    lines.append("   - 🟢 **可以做**：具體的觀察動作或可下單的條件單（例：「盤前觀察聯亞 3081 是否站上前高 XXX 元，站上代表光通訊行情續航」）")
    lines.append("   - 🟡 **該警戒**：持股的預警線（例：「2330 跌破 2000 要警覺，法說利多完全失效」）")
    lines.append("   - 🔴 **不要做**：明確的不建議（例：「不要現在追高任何光通訊股，已漲到歷史新高」）")
    lines.append("   每個燈號至少給 1 項、最多 3 項。給具體數字和條件，不要含糊。")
    lines.append("")
    lines.append("請用繁體中文、條列清楚，避免過度樂觀或催促我行動。最後提醒我：所有判斷僅供參考，決策責任在我。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- News sections ---
    lines.append("## 🎯 跟我持股 / 關注清單相關")
    lines.append("")
    had_holding = False
    for holding, items in by_holding.items():
        if not items:
            continue
        had_holding = True
        lines.append(f"### {holding}")
        for article in _dedupe(items)[:MAX_PER_HOLDING]:
            lines.append(_render_article(article))
        lines.append("")
    if not had_holding:
        lines.append("_今天沒抓到直接相關的新聞。_")
        lines.append("")

    lines.append("## 📊 分產業新聞")
    lines.append("")
    had_sector = False
    for sector, items in by_sector.items():
        items = _dedupe(items)
        if not items:
            continue
        had_sector = True
        lines.append(f"### {sector}")
        for article in items[:MAX_PER_SECTOR]:
            lines.append(_render_article(article))
        lines.append("")
    if not had_sector:
        lines.append("_今天沒抓到分產業相關新聞。_")
        lines.append("")

    if untagged:
        lines.append("## 📰 其他頭條（未分類）")
        lines.append("")
        lines.append("_這些沒對上任何關鍵字，但可能是總經/大盤/海外重要消息。Claude 可以瞄一眼，判斷有沒有漏網之魚。_")
        lines.append("")
        tw_others = [a for a in untagged if a.market == "TW"][:MAX_UNTAGGED]
        us_others = [a for a in untagged if a.market == "US"][:MAX_UNTAGGED]
        if tw_others:
            lines.append("**台股/台灣總經**")
            for article in tw_others:
                lines.append(_render_article(article))
            lines.append("")
        if us_others:
            lines.append("**美股/國際**")
            for article in us_others:
                lines.append(_render_article(article))
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Brief generated at {datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')} Taipei time._")
    return "\n".join(lines)


def _dedupe(articles: list[Article]) -> list[Article]:
    seen = set()
    out = []
    for article in articles:
        key = article.link or article.title
        if key in seen:
            continue
        seen.add(key)
        out.append(article)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    now = datetime.now(TAIPEI)
    print(f"[{now:%Y-%m-%d %H:%M}] generating daily brief…", file=sys.stderr)

    all_articles: list[Article] = []
    for feed in FEEDS:
        all_articles.extend(fetch_feed(feed))

    # Global dedupe by link
    seen = set()
    unique: list[Article] = []
    for article in all_articles:
        key = article.link or article.title
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(article)

    print(f"  {len(unique)} unique articles after dedupe", file=sys.stderr)

    classify(unique)
    tagged = sum(1 for a in unique if a.is_tagged)
    print(f"  {tagged} tagged, {len(unique) - tagged} untagged", file=sys.stderr)

    brief_md = render_brief(unique, now)

    BRIEFS_DIR.mkdir(exist_ok=True)
    out_path = BRIEFS_DIR / f"{now:%Y-%m-%d}.md"
    out_path.write_text(brief_md, encoding="utf-8")
    latest = BRIEFS_DIR / "latest.md"
    latest.write_text(brief_md, encoding="utf-8")

    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

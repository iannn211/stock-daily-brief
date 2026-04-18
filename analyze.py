"""
Generate narrative analysis from the daily brief using Gemini 2.0 Flash.

Inputs:
  briefs/<date>.md        (news)
  portfolio.yaml          (holdings config)
  portfolio.json          (live P&L snapshot)

Output:
  analyses/<date>.json    (structured analysis)

Expects env var GEMINI_API_KEY. Skips gracefully if unset or if the API fails —
the dashboard falls back to the copy-prompt flow.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
PORTFOLIO_YAML = ROOT / "portfolio.yaml"
PORTFOLIO_JSON = ROOT / "portfolio.json"
BRIEFS_DIR = ROOT / "briefs"
ANALYSES_DIR = ROOT / "analyses"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Approx. max news-section bytes to include in the prompt.
# Gemini 2.0 Flash has 1M token context, but smaller = faster + cheaper + less drift.
MAX_NEWS_BYTES = 80_000


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "market_pulse": {
            "type": "object",
            "properties": {
                "tw_sentiment": {"type": "string", "enum": ["正面", "中性", "負面"]},
                "us_sentiment": {"type": "string", "enum": ["正面", "中性", "負面"]},
                "summary": {"type": "string"},
            },
            "required": ["tw_sentiment", "us_sentiment", "summary"],
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sentiment": {"type": "string", "enum": ["正面", "中性", "負面"]},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "narrative": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "sentiment", "tickers", "narrative", "key_points"],
            },
        },
        "holdings_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "name": {"type": "string"},
                    "commentary": {"type": "string"},
                    "outlook": {"type": "string", "enum": ["正面", "中性", "負面"]},
                },
                "required": ["symbol", "name", "commentary", "outlook"],
            },
        },
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "name": {"type": "string"},
                    "thesis": {"type": "string"},
                    "research_angle": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "required": ["symbol", "name", "thesis", "research_angle", "risk"],
            },
        },
        "action_checklist": {
            "type": "object",
            "properties": {
                "green": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["action", "reason"],
                    },
                },
                "yellow": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["action", "reason"],
                    },
                },
                "red": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["action", "reason"],
                    },
                },
            },
            "required": ["green", "yellow", "red"],
        },
        "learning_point": {
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "explanation": {"type": "string"},
            },
            "required": ["term", "explanation"],
        },
    },
    "required": ["market_pulse", "topics", "action_checklist", "learning_point"],
}


def load_latest_brief() -> tuple[str, str] | None:
    """Return (date, markdown_content) of the most recent brief file."""
    candidates = sorted(
        (p for p in BRIEFS_DIR.glob("*.md") if p.stem != "latest"),
        reverse=True,
    )
    if not candidates:
        return None
    path = candidates[0]
    return path.stem, path.read_text(encoding="utf-8")


def trim_brief(content: str, limit: int = MAX_NEWS_BYTES) -> str:
    if len(content.encode("utf-8")) <= limit:
        return content
    # Walk lines until we exceed the byte budget; preserve sections
    out_lines: list[str] = []
    total = 0
    for line in content.splitlines():
        total += len(line.encode("utf-8")) + 1
        if total > limit:
            out_lines.append("…（新聞過長已截斷）")
            break
        out_lines.append(line)
    return "\n".join(out_lines)


def build_portfolio_context() -> str:
    cfg = yaml.safe_load(PORTFOLIO_YAML.read_text(encoding="utf-8"))
    lines: list[str] = []
    lines.append("## 使用者檔案")
    lines.append("- 身份：台灣散戶、新手（小白），缺時間盯盤")
    lines.append("- 目標：用新聞+產業趨勢掌握投資機會，建立資產配置紀律")
    lines.append("")
    lines.append("### 現有持股")
    for h in cfg.get("holdings", []):
        sl = f"，停損價 {h['stop_loss']}" if h.get("stop_loss") else ""
        tp = f"，停利目標 {h['take_profit']}" if h.get("take_profit") else ""
        lines.append(
            f"- **{h['symbol']} {h['name']}** × {h['shares']:,} 股，"
            f"成本均價 {h['cost_basis']}（{h['market']} 市場）{sl}{tp}"
        )
    if cfg.get("watchlist"):
        lines.append("")
        lines.append("### 追蹤清單（尚未持有）")
        for w in cfg["watchlist"]:
            lines.append(f"- {w['symbol']} {w['name']}（{w['market']}）")

    # Live portfolio snapshot
    if PORTFOLIO_JSON.exists():
        try:
            pf = json.loads(PORTFOLIO_JSON.read_text(encoding="utf-8"))
            s = pf.get("summary", {})
            bench = pf.get("benchmark", {})
            lines.append("")
            lines.append("### 組合即時快照（台股收盤後／盤前）")
            lines.append(f"- 總市值 NT${s.get('total_value_twd', 0):,.0f}")
            lines.append(
                f"- 今日損益 {s.get('day_pnl_twd', 0):+,.0f} "
                f"({s.get('day_pnl_pct', 0):+.2f}%)"
            )
            lines.append(
                f"- 總損益 {s.get('total_pnl_twd', 0):+,.0f} "
                f"({s.get('total_pnl_pct', 0):+.2f}%)"
            )
            lines.append(
                f"- vs 基準 {bench.get('symbol', '0050')} "
                f"({bench.get('day_change_pct', 0):+.2f}%)，"
                f"alpha {s.get('alpha_vs_benchmark_pct', 0):+.2f}%"
            )
            alerts = pf.get("alerts", {})
            if alerts.get("stop_loss") or alerts.get("take_profit"):
                lines.append("")
                lines.append("**⚠️ 價格警報觸發**")
                for h in alerts.get("stop_loss", []):
                    lines.append(
                        f"- 🔴 {h['symbol']} {h['name']} 現價 {h['price']} ≤ 停損 {h['stop_loss']}"
                    )
                for h in alerts.get("take_profit", []):
                    lines.append(
                        f"- 🟢 {h['symbol']} {h['name']} 現價 {h['price']} ≥ 停利 {h['take_profit']}"
                    )
        except Exception:
            pass

    # Sector watch
    sectors = (
        "半導體代工 / IC 設計 / AI 伺服器·CoWoS / 光通訊·CPO / "
        "PCB·載板 / 被動元件 / 半導體設備 / 散熱·液冷"
    )
    lines.append("")
    lines.append(f"### 追蹤題材\n{sectors}")
    return "\n".join(lines)


SYSTEM_INSTRUCTIONS = """你是一位專業投資研究助理，服務於一位台灣散戶新手。你的任務是從今日新聞中產生結構化分析，而非只是摘要。

你必須嚴格遵守以下原則：
1. **繁體中文**，語氣專業但平易，像在教朋友
2. **敘事段落** 而非條列，Narrative 欄位寫 3-5 句有邏輯的分析，像財經雜誌的專題段落
3. **具體引用數字**（股價、營收增減、法人買賣超金額），不要說「大漲」「重挫」等模糊詞
4. **考量使用者持倉**：每個段落都要連結到「這對他 2330/0050/VOO 是好是壞」
5. **三柱配置觀**：不斷提醒 Growth（成長核心）/ Defense（防禦對沖）/ Flexibility（機動倉位）的平衡
6. **誠實**：不過度樂觀、不催促行動、承認不確定性
7. **禁止**：買賣具體建議（「買 X」「賣 Y」），改用「值得研究」「值得觀察」
8. **行動清單**必須具體可執行（含價位、日期、條件），不要給空話

題材範例：「科技巨頭」、「光通訊 / CPO」、「半導體代工漲價」、「被動元件」、「總經 / 美股」等。請依今日新聞實際熱度選 3-6 個主題，每題至少 3 則新聞支撐。

關於「今日行動清單」：
- 🟢 **可以做**：具體觀察動作或條件單（例：「盤前看聯亞 3081 是否站上 2700，站上代表光通訊行情續航」）
- 🟡 **該警戒**：持股的預警線（例：「2330 跌破 2000 要警覺，法說利多失效訊號」）
- 🔴 **不要做**：明確的不建議（例：「不追高任何光通訊股，已達歷史新高」）
每區至少 1 項、最多 3 項。

關於「新手學習點」：每天挑一個今日新聞裡出現的財經/投資名詞（例：利多出盡、乖離率、CoWoS、本益比倍數），用 3-4 句話教會使用者。目標是一年內累積一套投資辭典。"""


def build_prompt(brief_markdown: str) -> str:
    trimmed = trim_brief(brief_markdown)
    portfolio_ctx = build_portfolio_context()
    return f"""{portfolio_ctx}

---

## 今日新聞彙整（從 RSS 抓取，已按產業/持股分類）

{trimmed}

---

請輸出符合 schema 的 JSON。記住：敘事要深、要具體、要連結到使用者的持股。"""


def call_gemini(prompt: str) -> dict | None:
    if not GEMINI_API_KEY:
        print("!! GEMINI_API_KEY not set — skipping", file=sys.stderr)
        return None

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTIONS}],
        },
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]},
        ],
        "generationConfig": {
            "temperature": 0.5,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "maxOutputTokens": 8192,
        },
    }

    params = {"key": GEMINI_API_KEY}
    for attempt in range(3):
        try:
            r = requests.post(GEMINI_URL, params=params, json=payload, timeout=90)
            if r.status_code == 429:
                wait = 2 ** attempt * 5
                print(f"!! rate-limited, retrying in {wait}s…", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except requests.HTTPError as e:
            body = r.text[:500] if r is not None else "no body"
            print(f"!! HTTP {r.status_code}: {body}", file=sys.stderr)
            if r.status_code in (400, 401, 403):
                return None  # won't recover by retrying
        except json.JSONDecodeError as e:
            print(f"!! gemini returned non-JSON: {e}", file=sys.stderr)
            print(f"    text was: {text[:500]}", file=sys.stderr)
        except Exception as e:
            print(f"!! gemini call failed (attempt {attempt + 1}): {e}", file=sys.stderr)
        time.sleep(2 ** attempt)
    return None


def main() -> int:
    ANALYSES_DIR.mkdir(exist_ok=True)

    loaded = load_latest_brief()
    if not loaded:
        print("no brief found, nothing to analyze", file=sys.stderr)
        return 0
    date, brief_md = loaded

    out_path = ANALYSES_DIR / f"{date}.json"

    # Skip if analysis already exists for today unless FORCE set
    if out_path.exists() and not os.environ.get("FORCE_ANALYZE"):
        print(f"analysis already exists: {out_path.relative_to(ROOT)} "
              f"(set FORCE_ANALYZE=1 to regenerate)", file=sys.stderr)
        return 0

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set — skipping analysis "
              "(dashboard will fall back to copy-prompt flow)", file=sys.stderr)
        return 0

    prompt = build_prompt(brief_md)
    print(f"[{datetime.now(TAIPEI):%H:%M:%S}] calling gemini "
          f"({len(prompt)} chars prompt)…", file=sys.stderr)

    result = call_gemini(prompt)
    if not result:
        print("analysis failed — brief page will fall back to copy-prompt flow",
              file=sys.stderr)
        return 0

    result["generated_at"] = datetime.now(TAIPEI).isoformat()
    result["date"] = date
    result["model"] = GEMINI_MODEL

    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"→ {out_path.relative_to(ROOT)}  "
          f"topics={len(result.get('topics', []))} "
          f"holdings={len(result.get('holdings_analysis', []))} "
          f"opps={len(result.get('opportunities', []))}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

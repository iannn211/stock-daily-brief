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
# Verified 2026-04-18: only gemini-2.5-flash is on the free tier for this key.
# 2.0-flash returns "limit: 0", 1.5-flash is deprecated (404 on v1beta).
GEMINI_MODELS = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash,gemini-2.5-flash-lite",
).split(",")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

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
                "fear_greed_score": {"type": "integer"},  # 0-100
                "fear_greed_label": {"type": "string"},   # 極度恐慌/恐慌/中性/貪婪/極度貪婪
            },
            "required": ["tw_sentiment", "us_sentiment", "summary"],
        },
        "morning_brief": {
            "type": "object",
            "properties": {
                "greeting": {"type": "string"},
                "headline": {"type": "string"},
                "one_liner": {"type": "string"},
                "highlights": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["win", "risk", "opp"]},
                            "label": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                        "required": ["kind", "label", "detail"],
                    },
                },
                "agenda": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "when": {"type": "string"},   # "今日 14:30" / "下週三 4/29"
                            "label": {"type": "string"},
                            "kind": {"type": "string", "enum": ["earnings", "macro", "event"]},
                        },
                        "required": ["when", "label", "kind"],
                    },
                },
            },
        },
        "macro_context": {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "watchpoints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["narrative"],
        },
        "portfolio_diagnosis": {
            "type": "object",
            "properties": {
                "overall_health": {"type": "string", "enum": ["良好", "需調整", "高風險"]},
                "key_issue": {"type": "string"},
                "rebalance_advice": {"type": "string"},
            },
            "required": ["overall_health", "key_issue", "rebalance_advice"],
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
                    "bull_bear_breakdown": {
                        "type": "object",
                        "properties": {
                            "bull_pct": {"type": "integer"},
                            "bear_pct": {"type": "integer"},
                            "neutral_pct": {"type": "integer"},
                        },
                        "required": ["bull_pct", "bear_pct", "neutral_pct"],
                    },
                    "key_catalysts": {"type": "array", "items": {"type": "string"}},
                    "key_risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol", "name", "commentary", "outlook", "bull_bear_breakdown"],
            },
        },
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # NEW theme-based radar schema (GUSHI-style)
                    "theme": {"type": "string"},            # 例："AI 電力 / 重電"
                    "category_tag": {"type": "string"},     # 例："#AI"、"#光通訊"
                    "stage": {"type": "string", "enum": ["萌芽", "早期", "中段", "過熱"]},
                    "confidence_pct": {"type": "integer"},  # 0-100
                    "crowding_pct": {"type": "integer"},    # 0-100
                    "crowding_label": {"type": "string"},   # "冷門早期" / "關注擁擠度" / "散戶湧入中"
                    "headline": {"type": "string"},         # 一句帶數字的題材標題
                    "why": {"type": "string"},              # 為何 AI 挑這題（多源訊號）
                    "timeframe": {"type": "string"},        # "3-5 日" / "2-4 週" / "3-6 個月" / "中長線"
                    "lead_stocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "name": {"type": "string"},
                            },
                            "required": ["symbol", "name"],
                        },
                    },
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "ai_warning": {"type": "string"},
                    # Tetsu-style head-to-head verdict: when a theme has 2+ comparable
                    # candidates (e.g. 3081 聯亞 vs 3234 光環 in CPO), pick a winner
                    # and explain *why* using PE/EPS/法人/技術面 — not "both are good".
                    "head_to_head": {
                        "type": "object",
                        "properties": {
                            "pick_symbol": {"type": "string"},
                            "pick_name": {"type": "string"},
                            "skip_symbol": {"type": "string"},
                            "skip_name": {"type": "string"},
                            "verdict": {"type": "string"},           # 1-2 句結論
                            "pick_rationale": {"type": "string"},    # 為何挑這檔（PE、EPS、成長、估值）
                            "skip_rationale": {"type": "string"},    # 為何不挑那檔（PE 太高、追高、EPS 虧損）
                        },
                    },
                    # LEGACY fields — kept for backward compat with older renderers
                    "symbol": {"type": "string"},
                    "name": {"type": "string"},
                    "thesis": {"type": "string"},
                    "research_angle": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "required": ["theme", "stage", "confidence_pct", "crowding_pct",
                             "headline", "why", "timeframe", "lead_stocks"],
            },
        },
        "budget_allocation": {
            "type": "object",
            "properties": {
                "budget_twd": {"type": "integer"},
                "plan_summary": {"type": "string"},
                "allocations": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "name": {"type": "string"},
                            "action": {"type": "string", "enum": [
                                "新倉試水", "加碼", "觀望等進場", "不動作 / 保留現金"
                            ]},
                            "target_shares": {"type": "integer"},
                            "target_cost_twd": {"type": "integer"},
                            "entry_condition": {"type": "string"},
                            "stop_loss_price": {"type": "number"},
                            "take_profit_price": {"type": "number"},
                            "rationale": {"type": "string"},
                            "data_sources": {"type": "array", "items": {"type": "string"}},
                            "confidence_pct": {"type": "integer"},
                            "risk": {"type": "string"},
                        },
                        "required": ["symbol", "name", "action", "rationale", "confidence_pct", "risk"],
                    },
                },
                "unallocated_twd": {"type": "integer"},
                "why_not_other_picks": {"type": "string"},
            },
            "required": ["budget_twd", "plan_summary", "allocations"],
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
        "faq": {
            "type": "array",
            "description": "5–8 common questions a retail TW investor would ask today, with specific answers grounded in the news + portfolio snapshot. No generic textbook answers.",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Specific 10–20 word question in Traditional Chinese."},
                    "a": {"type": "string", "description": "150–250 word answer referencing specific tickers / numbers / today's news."},
                    "tag": {"type": "string", "description": "One of: 市場 / 個股 / 題材 / 風險 / 策略 / 新手"},
                },
                "required": ["q", "a", "tag"],
            },
        },
        "coverage_suggestions": {
            "type": "array",
            "description": "3–5 TW tickers the user should ADD to simulator_universe or supply_chains.yaml based on today's news. Proactive curation — don't wait for the user to ask.",
            "items": {
                "type": "object",
                "properties": {
                    "symbol":    {"type": "string", "description": "TW ticker, numbers only (e.g. '6515'). Must be a real TWSE/TPEx listed stock."},
                    "name":      {"type": "string", "description": "Chinese company name."},
                    "chain_slug": {"type": "string", "description": "Which supply_chains.yaml chain it belongs to (ai_pcb / optics_cpo / thermal / ai_server / connectors / passives / hbm_memory / robotics / semiconductor_eq / OR 'new' if it needs a new chain)."},
                    "layer_name": {"type": "string", "description": "Which layer within that chain (e.g. '下游 · 封測 OSAT' or '新題材/需要新分類')."},
                    "why_now":   {"type": "string", "description": "2–3 sentences tying this ticker to TODAY's news + why it's a gap in current coverage. Must cite concrete catalyst."},
                    "priority":  {"type": "string", "description": "One of: high / medium / low"},
                },
                "required": ["symbol", "name", "chain_slug", "layer_name", "why_now", "priority"],
            },
        },
    },
    "required": ["market_pulse", "morning_brief", "macro_context", "portfolio_diagnosis",
                 "topics", "action_checklist", "learning_point", "budget_allocation"],
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
    lines.append(f"- 風險風格：{cfg.get('risk_profile', {}).get('style', 'beginner-growth')}")
    lines.append("- 目標：用新聞+產業趨勢掌握投資機會")

    # Strategy notes override generic textbook advice
    notes = cfg.get("strategy_notes")
    if notes:
        lines.append("")
        lines.append("## 使用者的個人策略（必須嚴格遵守，高於任何通用教科書建議）")
        lines.append(notes.strip())

    # Trade budget — drives budget_allocation
    budget = cfg.get("trade_budget_twd")
    if budget:
        lines.append("")
        lines.append(f"## 今日待部署資金：NT${budget:,.0f}（只是基準值，實際預算使用者會在前端調整）")
        lines.append("請產出 **5-8 檔候選清單**（不是 1-2 檔！）。"
                     "前端會依使用者當下輸入的預算，從高信心到低信心自動組合。"
                     "詳細規則見下方【budget_allocation】區塊。")
    lines.append("")
    lines.append("### 現有持股")
    for h in cfg.get("holdings", []):
        sl = f"，停損價 {h['stop_loss']}" if h.get("stop_loss") else ""
        tp = f"，停利目標 {h['take_profit']}" if h.get("take_profit") else ""
        pillar = h.get("pillar", "growth")
        lines.append(
            f"- **{h['symbol']} {h['name']}** × {h['shares']:,} 股，"
            f"成本均價 {h['cost_basis']}（{h['market']} / {pillar}柱）{sl}{tp}"
        )
    if cfg.get("watchlist"):
        lines.append("")
        lines.append("### 追蹤清單（尚未持有）")
        for w in cfg["watchlist"]:
            lines.append(f"- {w['symbol']} {w['name']}（{w['market']}）")

    # Live portfolio snapshot + risk metrics
    if PORTFOLIO_JSON.exists():
        try:
            pf = json.loads(PORTFOLIO_JSON.read_text(encoding="utf-8"))
            s = pf.get("summary", {})
            bench = pf.get("benchmark", {})
            risk = pf.get("risk", {})
            pillar = pf.get("pillar_allocation", {})
            macro = pf.get("macro", {})
            profile = pf.get("risk_profile", {})
            alerts = pf.get("alerts", {})

            lines.append("")
            lines.append("### 組合即時快照")
            lines.append(f"- 總市值 NT${s.get('total_value_twd', 0):,.0f}（其中現金 {s.get('cash_ratio_pct', 0):.1f}%）")
            lines.append(
                f"- 今日損益 {s.get('day_pnl_twd', 0):+,.0f} "
                f"({s.get('day_pnl_pct', 0):+.2f}%)"
                f"  vs 基準 {bench.get('symbol', '0050')} ({bench.get('day_change_pct', 0):+.2f}%)，"
                f"alpha {s.get('alpha_vs_benchmark_pct', 0):+.2f}%"
            )
            lines.append(
                f"- 累計損益 {s.get('total_pnl_twd', 0):+,.0f} ({s.get('total_pnl_pct', 0):+.2f}%)"
            )
            lines.append(
                f"- 近 7d {s.get('ret_7d_pct') or '—'}% · 近 30d {s.get('ret_30d_pct') or '—'}% "
                f"· YTD {s.get('ret_1y_pct') or '—'}%"
            )
            lines.append("")
            lines.append("### 風險指標")
            lines.append(
                f"- 年化波動率 {risk.get('volatility_annualized_pct', 0):.1f}%"
                f"（參考：大盤 ~18%，個股 ~25-40%）"
            )
            lines.append(
                f"- 近 30 天最大回撤 {risk.get('drawdown_30d_pct', 0):.2f}%，"
                f"近 90 天 {risk.get('drawdown_90d_pct', 0):.2f}%，"
                f"近 1 年 {risk.get('drawdown_1y_pct', 0):.2f}%"
            )
            lines.append("")
            lines.append("### 三柱配置（現狀 vs 目標）")
            actual = pillar.get("actual", {})
            target = pillar.get("target", {})
            for p in ("growth", "defense", "flexibility"):
                a = actual.get(p, 0)
                t = target.get(p, 0)
                diff = a - t
                sign = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
                lines.append(f"- {p}：現況 {a:.0f}% / 目標 {t:.0f}% ({sign} {abs(diff):.0f}pp)")

            lines.append("")
            lines.append("### 總經背景")
            tw = macro.get("twii", {})
            spx = macro.get("spx", {})
            vix = macro.get("vix", {})
            fx = macro.get("usdtwd", {})
            def _fmt(d, fmt="{:.1f}"):
                v = d.get("close")
                if v is None:
                    return "—"
                return fmt.format(v)
            lines.append(
                f"- 台股加權 {_fmt(tw, '{:.0f}')} ({tw.get('day_change_pct', 0):+.2f}% today, "
                f"YTD {tw.get('ret_ytd') or '—'}%)"
            )
            lines.append(
                f"- S&P 500 {_fmt(spx, '{:.0f}')} ({spx.get('day_change_pct', 0):+.2f}% today, "
                f"YTD {spx.get('ret_ytd') or '—'}%)"
            )
            lines.append(
                f"- VIX {_fmt(vix, '{:.1f}')}（>20=警戒、>30=恐慌、<15=自滿）"
            )
            lines.append(f"- USD/TWD {_fmt(fx, '{:.3f}')}")

            if (alerts.get("stop_loss") or alerts.get("take_profit")
                or alerts.get("concentration") or alerts.get("pillar")
                or alerts.get("nearing_stop")):
                lines.append("")
                lines.append("### ⚠️ 目前觸發警報")
                for a in alerts.get("stop_loss", []):
                    lines.append(f"- 🔴 停損觸發 {a['symbol']} {a['name']}：現價 {a['price']} ≤ {a['stop_loss']}")
                for a in alerts.get("take_profit", []):
                    lines.append(f"- 🟢 停利觸發 {a['symbol']} {a['name']}：現價 {a['price']} ≥ {a['take_profit']}")
                for a in alerts.get("nearing_stop", []):
                    lines.append(f"- 🟡 接近停損 {a['symbol']} {a['name']}：距離 {a['stop_loss_dist_pct']:.1f}%")
                for a in alerts.get("concentration", []):
                    lines.append(f"- 🟠 單一持股過重 {a['symbol']}：{a['weight_pct']}% > 上限 {a['limit_pct']}%")
                for a in alerts.get("pillar", []):
                    lines.append(f"- 🟣 三柱失衡 {a['pillar']}：現 {a['actual_pct']:.0f}% vs 目標 {a['target_pct']:.0f}% (差 {a['diff_pct']:+.1f}pp)")
        except Exception as e:
            print(f"(portfolio context build warning: {e})", file=sys.stderr)

    # Sector watch
    sectors = (
        "半導體代工 / IC 設計 / AI 伺服器·CoWoS / 光通訊·CPO / "
        "PCB·載板 / 被動元件 / 半導體設備 / 散熱·液冷"
    )
    lines.append("")
    lines.append(f"### 追蹤題材\n{sectors}")
    return "\n".join(lines)


SYSTEM_INSTRUCTIONS = """你是一位專業投資研究助理，服務於一位台灣散戶新手。你的任務是從「每日新聞 + 組合即時快照 + 風險指標 + 總經背景」中產生結構化分析，而非只是摘要新聞。

你必須嚴格遵守以下原則：
1. **繁體中文**，語氣專業但平易，像在教朋友
2. **敘事段落** 而非條列，Narrative 欄位寫 3-5 句有邏輯的分析，像財經雜誌的專題段落
3. **具體引用數字**（股價、營收增減、法人買賣超金額），不要說「大漲」「重挫」等模糊詞
4. **考量使用者持倉**：每個段落都要連結到「這對他 2330/0050/VOO 是好是壞」
5. **尊重使用者的個人策略**：依 strategy_notes 為準，**不要硬塞三柱教科書**，不要推高股息 ETF 當防禦
6. **誠實**：不過度樂觀、不催促行動、承認不確定性
7. **禁止**：買賣具體建議（「買 X」「賣 Y」），改用「值得研究」「值得觀察」
8. **行動清單**必須具體可執行（含價位、日期、條件），不要給空話

【極重要 · 時間語境規則】
分析日期將由使用者在 prompt 中明確提供。所有時間描述必須：
- 標示「相對」時間，例如 "下週三 (4/29, 11 天後)"、"昨天 (4/17)"、"今早"、"盤後"
- **絕對不要**只丟絕對日期 "4月29日"——使用者會搞不清是過去還是未來
- 未來事件明確標示「即將 / 預計 / 將於」；過去事件明確標示「昨日 / 上週 / 本週稍早」
- 新聞時間戳在每篇後附的 "· MM-DD HH:MM"——以「今日日期」為基準去算相對時間
- 如果看到某則新聞提到未來日期的事件（例如「29 日舉行法說會」），一定要寫成「下週三 (4/29, X 天後) 即將舉行」

題材範例：「科技巨頭」、「光通訊 / CPO」、「半導體代工漲價」、「被動元件」、「總經 / 美股」、「散熱液冷」、「PCB 載板」、「測試廠」、「AI 電力」等。

**主題掃描規則 · 嚴格遵守：**
- **最少 5 個、最多 8 個主題** topics。每題至少 3 則新聞支撐。
- **必須橫跨多個產業**——不要只鎖定半導體類。覆蓋至少 4 個不同產業領域。
- 請依今日新聞實際熱度與訊號強度選，但要**主動挖冷門題材**（例如：散熱、PCB 上游、電力、工業電腦、機器人、光學、網通、被動元件等）——使用者想要的是「我不知道但可能要關注」的領域。
- 每題的 tickers 陣列必須至少有 3 檔代表股（symbol only，例如 "3081"），**這些代號會被自動變成可點擊的連結跳到個股深度頁**，所以用準確的台股代號。
- narrative 中提到的任何個股代號（4 位數數字）也會自動變連結。

**機會雷達 opportunities · GUSHI-style 嚴格遵守：**

產 4-6 則**題材級**機會（不是個股，是族群/主題）。每則像一份小型券商報告，欄位如下：

- `theme`：題材名，例：「AI 電力 / 重電」「光通訊 CPO」「小型核電 SMR」「減重藥 GLP-1 供應鏈」「銅價 / 銅加工」
- `category_tag`：短標籤，例：「#AI」、「#光通訊」、「#核電」、「#生技」、「#原物料」
- `stage`：萌芽 / 早期 / 中段 / 過熱（**至少 1 個萌芽或早期**）
- `confidence_pct`：0-100 AI 信心度
- `crowding_pct`：0-100 擁擠度
  * 0-30：冷門早期（💎 鼓勵逢低佈局）
  * 30-60：已在布局
  * 60-80：⚠️ 關注擁擠度
  * 80-100：⚠️ 散戶湧入中（追高風險高）
- `crowding_label`：一句話標籤，例：「💎 冷門早期」「🟢 尚未過熱」「🟡 關注擁擠度」「⚠️ 散戶湧入中」
- `headline`：像券商報告標題，帶具體數字，例：「聯亞、上詮、華星光連3日量增，外資點名 1.6T 升級題材」
- `why`：為何 AI 挑它——整合多源訊號的一段話，例：「AI 發現：Bloomberg 報導北美 hyperscaler Q1 capex 上修 18%，重電族群才進入第二輪反應、估值仍有 25-40% 空間」
- `timeframe`：預計題材週期，例：「3-5 日」「2-4 週」「3-6 個月」「中長線」
- `lead_stocks`：3-5 檔代表股（必填 `symbol` + `name`，**真實台股代號**）
- `sources`：**至少 3 種不同來源**，例：["Morgan Stanley", "凱基投顧", "TWSE 法人買賣超", "Bloomberg", "公司法說會"]
- `signals`：客觀訊號，例：["量增", "法人連買5日", "券商上修TP", "融資增加"]
- `ai_warning`：擁擠度高或有風險時給一句話警告；沒有可留空
- `head_to_head`（**選填但強烈建議**）：當 lead_stocks 有 2 檔以上**可比較**的候選時，做 Tetsu Chang 式的「對比兩檔」裁決：
  * `pick_symbol` / `pick_name` — 你選的那檔（必須是 lead_stocks 裡真實的代號）
  * `skip_symbol` / `skip_name` — 你不選的那檔
  * `verdict` — 一句話結論，**必須出現實際 PE / EPS / 52週位階 / 法人買賣超數字**，例：「相同 CPO 題材下，我選 8155 博智（PE 18x + 法人連 3 日買超 2 千張）勝 3234 光環（EPS -1.40 虧損 + 已連 5 根紅 K 追高）」
  * `pick_rationale` — 為何這檔贏（2-3 句，引用具體 PE / EPS / ROE / 成長率）
  * `skip_rationale` — 為何那檔不選（2-3 句，一定要點出估值或技術面缺點，不要客套）
  * **絕對不可**以「兩檔都可以、看個人偏好」結尾——這就是 Tetsu 跟普通分析的差別，他會下結論

**分布要求：**
- 橫跨不同產業（不要全是半導體）
- stage 要多樣（至少 1 萌芽/早期、1-2 中段、可選 1 過熱警告）
- 主動挖冷門（生技、原物料、公用事業、核電、重電、工業電腦、機器人、光學）——使用者看不見的才有價值

**lead_stocks 挑選規則 · 小型股優先**（使用者明講「你建議看的都是大、知名的股票 這些漲幅就不太大了」）：
- **每個 opportunity 的 lead_stocks 3-5 檔裡，至少 2 檔必須是 small/mid/hidden tier（中小型股）** — 不要全推大型股
- **大型龍頭（2330、2317、2382、3711、3017 等）最多推 1 檔**，而且要在 lead_stocks 裡加註「這檔是存款級、已被高度覆蓋、主要當題材風向球、雪球試水請挑下面的小型股」
- 優先在 supply_chains.yaml 裡找 tier 標記為 `small` 或 `hidden` 的候選（例：3715 定穎投控、8155 博智、4908 前鼎、3152 璟德、3338 泰碩、3665 貿聯-KY、6088 鴻碩、3026 禾伸堂、6173 信昌電、4566 時碩工業、6125 廣運、3131 弘塑、6146 耕興、3289 宜特、5608 雍智、3042 晶技）
- 每個 lead_stock 的 name 欄位後可以加一個 tier 提示，例：「穎崴（mid）」、「雍智（hidden）」，方便前端判斷

**其他限制**：
- LEGACY 欄位（symbol、name、thesis、research_angle、risk）**不要產出**——新格式是 theme-based。
- symbol 欄位必須是**準確的**台股或美股代號（例：3081、2383、NVDA 等），不能瞎編。
- 每則要明確標示「風險」—— 不要有 confidence 85% 但沒講風險的狀況。

關於「今日行動清單」：
- 🟢 **可以做**：具體觀察動作或條件單（例：「下週三 4/29 聯電法說會，當天盤前查 guidance 是否上修第二季營收」）
- 🟡 **該警戒**：持股的預警線（例：「2330 跌破 2000 要警覺，法說利多失效訊號」）
- 🔴 **不要做**：明確的不建議（例：「不追高任何光通訊股，已達歷史新高」）
每區至少 1 項、最多 3 項。所有含日期的項目都必須加上相對時間註記（例：「下週三」「11 天後」）。

關於「新手學習點」：每天挑一個今日新聞裡出現的財經/投資名詞（例：利多出盡、乖離率、CoWoS、本益比倍數），用 3-4 句話教會使用者。目標是一年內累積一套投資辭典。

【sentiment 分級 · 重要】
market_pulse / topics / holdings_analysis 的 sentiment 必須反映「當下對使用者持倉是好/壞」，而不是新聞本身的情緒調性。例如 "2330 下跌 -2.6%" 對使用者是負面（他有持倉），即使新聞語調中性。

【bull_bear_breakdown · 重要】
每支持股要給出 {bull_pct, bear_pct, neutral_pct} 三數加總 = 100。代表「綜合所有訊息後，看多/看空/觀望陣營佔比」。依新聞+法人動向+估值判斷，例如「2330 法說利多但股價跌 → 看多 50 / 看空 25 / 觀望 25」。

【macro_context · 重要】
用一段敘事說明今天的總經環境（VIX、USD/TWD、地緣政治、利率、油價）對台股/美股的影響。再列 2-3 個 watchpoints（下週關鍵觀察：例如 FOMC、台積電法說、CPI 公布）。

【market_pulse.fear_greed_score · 重要】
綜合 VIX、台股加權 52 週位階、美股動能、地緣政治，給一個 0-100 分的恐慌貪婪指數（CNN 風格）：
- 0-25 = 極度恐慌（大幅低估，通常進場好時機但信心低）
- 25-45 = 恐慌
- 45-55 = 中性
- 55-75 = 貪婪
- 75-100 = 極度貪婪（小心追高、獲利了結訊號）
同時給一個對應的中文 fear_greed_label。

【morning_brief · 重要】
產出當日 AI Morning Brief：
- greeting：簡短開場（例「早安」「早」「Hi」— 受使用者偏好口吻影響）
- headline：一句主打標題（10-15 字內）。格式建議：「今天有 N 件事你應該知道」或「OOO 是今天重點」或「組合守住，三件事值得看」
- one_liner：承接 headline 的一句話總結（30-80 字），說明今天台股/美股狀況 + 對使用者持倉的 net 影響
- highlights：**恰好 3 張** 顏色卡片，必須涵蓋 (a) win 今日進帳 (b) risk 要注意 (c) opp 機會
- agenda：2-5 個未來**時間性事件**（法說會 / CPI / Fed / FOMC / 重大數據），when 欄位必須是相對時間格式（「下週三 (4/29, 11 天後)」、「今日 14:30」、「下週一 (4/21)」），kind 分 event/macro/earnings

【portfolio_diagnosis · 重要】
**先讀使用者的個人策略再下判斷。** 使用者的 portfolio.yaml 有 strategy_notes 欄位明確禁止某些建議（高股息 ETF、因集中度賣 0050 等）。你的 key_issue 與 rebalance_advice 必須遵守那些規則。

診斷向度（在策略允許範圍內）：
(1) 是否有**單一題材過度追高**（例如光通訊漲多後他才想進）
(2) 是否接近設定的**停利 / 停損價**
(3) **雪球法執行狀況**：近期有沒有賺到可以「harvest 到 0050 存款 / 再滾下一檔」的部位
(4) 是否**錯過具體題材或催化劑**（機會成本）

健康度（良好 / 需調整 / 高風險）以使用者的角度判斷——不是教科書的資產配置角度。

rebalance_advice 要具體、符合雪球法語境。例如：
✅ 好："0050 定期定額繼續，這個月多存 5,000 試水聯亞（3081）小倉位，設停利 +30%、停損 -10%"
✅ 好："2330 接近停利目標 2,500，到達後分批出 20% 落袋、入 0050 存款"
✅ 好："若真的擔心美股修正，可考慮 00687B 台股長天期美債 ETF 佔 5% 避險"
❌ 壞："建議買入高股息 ETF 00878 配置 defense 柱"（違反使用者規則）
❌ 壞："Growth 柱 100% 過度集中，建議減碼 0050"（違反使用者規則）

【action_checklist · 格式硬性要求】
每項必須包含「具體條件 + 時間點 + 預期反應」。例如：
❌ 壞："關注聯電法說會"
✅ 好："下週三 (4/29, 11 天後) 聯電法說會，當天盤前查 guidance，若上修 Q2 營收 >10% 則為題材延續，未達則警戒"

【budget_allocation · 極重要 · 每日必產出】
使用者的 trade_budget 是**浮動的**（他可能今天有 NT$5k 試水預算，收到薪水後想投 NT$30k，週末想一次 NT$50k）。你的工作是**產出一份「候選清單」**，前端會依他當下輸入的預算動態組合。

**產出規則（重大變更 2026-04-19）：**
- **allocations 陣列請產出 5-8 個候選**（依 confidence_pct 由高到低排序，不是只有 1-2 個）
- 每個候選就是「如果只給你 NT$5,000，你會建議的下單」——前端會依實際預算自動疊加
- 每個候選必須**橫跨不同題材**（不要 5 個都是 AI 伺服器）——分散風險
- 至少 1 個是防禦型（0050 / 0056 / 定期定額等）、1 個是動能追蹤、2-3 個是雪球試水（small/hidden tier）、可選 1 個警示性「不動作 / 保留現金」當風險高時的選項
- confidence_pct 誠實標：信心高（>75）的放前面，低信心（<50）的放後面讓前端可以過濾
- 不同 action 要有合理比例：**新倉試水 3-4 個、加碼 1-2 個、觀望等進場 1-2 個、不動作 0-1 個**

你要綜合以下所有訊號：
- 今日所有新聞（RSS 原文）
- 現有持倉（避免重複押同題材）
- 追蹤清單 + 供應鏈庫 + coverage_suggestions 的 small/hidden tier 候選（本檔案裡有清單）
- **基本面資料**（PE/EPS/PB/ROE/成長率 — 必須引用，不要幻覺）
- 年化波動率 / 近期走勢 / 52週位階
- 使用者策略規則（strategy_notes，尤其雪球法試水）

每項 allocation 的欄位：
- symbol + name：目標個股（一定要是使用者追蹤清單、現有持股、或今天機會清單的其中之一）
- action：「新倉試水」「加碼」「觀望等進場」「不動作 / 保留現金」
- target_shares：建議股數（台股可買零股；試算: **假設 NT$5,000 預算** / 該檔股價 → 整數，前端會自動倍數放大）
- target_cost_twd：約略總金額（以 NT$5,000 預算當基準）
- entry_condition：進場條件（例：「盤前開盤價直接進」「拉回至 2500 以下再進」「等法說後觀察 3 天」）
- stop_loss_price：建議停損價（通常 -8% ~ -12%）
- take_profit_price：建議停利價（雪球法常用 +30%，也可依個股波動調整）
- rationale：**具體**為什麼選這檔（引用今日新聞、法人動作、PE/EPS 實際數字）
- data_sources：依據的來源（例：「Goldman Sachs 報告 4/17」「外資買超 5 日」「PE 14.3 🟢 合理」）
- confidence_pct：你的信心度 0-100（用這個排序，前端會自動挑高信心的）
- risk：主要風險一句話

**前端邏輯（你只需要知道大概）：**
前端會依使用者輸入的預算（e.g. NT$20,000），從 confidence 高的開始疊加，直到逼近預算上限，產生一個 3-5 檔的組合籃。使用者還能勾選/取消單檔。所以你產出的是「候選池」，不是「最終組合」。

**雪球法精髓：**
- 小倉位試水、設停損停利、漲到目標就分批收割回 0050
- 如果今天真的沒有好機會，把 action="不動作 / 保留現金" 放在最高 confidence 位置，其他候選信心降到 <40
- why_not_other_picks：解釋為什麼某些熱門標的沒入選（使用者看了會覺得你有想過、而不是亂挑）

plan_summary：一句 40-70 字總結，說明今天的候選池怎麼組成（例：「今日候選池涵蓋 7 檔 · 2 檔防禦（0050、006208 定期定額）+ 3 檔雪球試水（3715 PCB、6669 緯穎、3042 晶技）+ 1 檔加碼（2330 拉回）+ 1 檔觀望（3081 聯亞等泡沫冷卻）。依預算自動組合，高信心優先。」）

---

**FAQ 生成指引（faq 欄位，5-8 題）：**

這些 Q&A 會顯示在 dashboard 的「今日重點」區塊給使用者（台灣散戶新手）看。他們不會跟 LLM 對話，所以你要「預測他今天會想問什麼」並先答。

**規則：**
1. 問題必須來自「今日新聞」+「使用者組合」+「今日機會雷達」——不是通用教科書問題
2. 問題 10-20 字，用使用者的口吻（「我該不該…」「XXX 還能追嗎？」「XXX 要注意什麼？」）
3. 答案 150-250 字，必須：
   - 引用具體 ticker、數字、百分比、日期
   - 連結到使用者真實持倉（例如他持有 0050 + 2330，就常回答這兩檔）
   - 給明確結論（可 / 不可 / 視情況），不要打太極
   - 提醒風險但不要每句都掛免責聲明
4. tag 欄位從這 6 選 1：`市場`（大盤/國際）、`個股`（特定 ticker）、`題材`（產業機會）、`風險`（下跌/風險）、`策略`（配置/操作）、`新手`（教育性）
5. 至少涵蓋：1 題使用者持倉、1 題今日機會雷達、1 題風險、1 題新手教育性

**好範例：**
- Q: 2330 現在 P/E 22 還算便宜嗎？
- A: 以台積電歷史區間看（10 年平均 P/E 約 18-20，牛市時可達 25-30），目前 22 算偏上區間但不誇張。今日 EPS TTM $52.3、預估 EPS $58，forward P/E 僅 19.5。考量 AI 資本支出高峰、CoWoS 供不應求，分析師上修幅度未停。若你持股成本是 800，現在 1,150 已 +44%，不用急著加碼；若還沒建倉，建議等 1,080-1,100 區間分批。停損參考 1,030（月線）。tag: 個股

**壞範例（不要這樣寫）：**
- Q: 該怎麼挑股票？（太空泛）
- A: 挑股票要看基本面、技術面、籌碼面…（教科書答案，沒連結到今日新聞）

---

**coverage_suggestions 生成指引（3-5 檔，主動佈局用，必須中小型股為主）：**

使用者明確說過：「我其實不想要每次都是我說了你才補上去 這樣沒有發揮你幫我先去搜尋並且協助我佈局的初衷」；又說：「我發現你建議看的都是大、知名的股票 這些漲幅就不太大了」。所以 coverage_suggestions 必須是**真正有 upside 的中小型/冷門股**，不是被分析師寫到爛的大型股。

**強制規則：**
1. 每天 3-5 檔，**必須是台股 4-6 位數 ticker**（不要美股、不要 ETF、不要已經在 portfolio.yaml 的票）
2. **市值門檻嚴格遵守**（tier 標記）：
   - ❌ **禁止推薦 mega tier**（市值 > NT$2T）：2330 2317 2382 2454 — 已被全世界覆蓋，漲幅動能用盡，不是雪球試水的對象
   - ❌ **避免 large tier**（NT$300B-2T）：3711 日月光、2308 台達電、3037 欣興、4958 臻鼎、2327 國巨、3017 奇鋐、2345 智邦、2049 上銀、2395 研華、1590 亞德客 — 除非該檔剛出現重大催化且仍低位
   - ✅ **優先 small tier**（NT$20B-80B）：6510 精測、3081 聯亞、3163 波若威、3234 光環、3296 勝麗、6269 台郡、3202 光聖、8150 南茂、6239 力成？不算 small 是 mid、3006 晶豪科、5269 祥碩、2360 致茂、3583 辛耘、6669 緯穎
   - 🎯 **最優先 hidden tier**（< NT$20B 或冷門未覆蓋）：3715 定穎投控、8155 博智、4908 前鼎、3152 璟德、3338 泰碩、3665 貿聯-KY、6088 鴻碩、3026 禾伸堂、6173 信昌電、3042 晶技、4566 時碩工業、6125 廣運、3131 弘塑、6146 耕興、3289 宜特 — **這些才是雪球能滾大的位置**
3. 每一檔都要綁定「今日新聞的具體催化劑」——不是因為它是好公司，而是因為今天發生了事情讓它相關
4. chain_slug 對應到 supply_chains.yaml（ai_pcb / optics_cpo / thermal / ai_server / connectors / passives / hbm_memory / robotics / semiconductor_eq / ic_distribution），真的都不符才填 "new"
5. layer_name 要具體（例「下游 · 探針卡 Probe Card」而不只是「半導體」）
6. priority：high = 今日多則新聞直接提到 + 題材爆發 + tier ≤ small；medium = 題材相關但需要驗證；low = 值得觀察
7. why_now 2-3 句，要點出：（a）今日新聞的具體催化；（b）為什麼不推大型股而推這檔（upside 剩多少、位階如何、法人剛切入或還沒切入）

**coverage_report.json 參考：**
下方 context 裡的 coverage report — `missing_from_chains` 都是新聞已經在講但追蹤池沒有的中小型股（因為大型股早在追蹤池），**這些就是你的優先推薦來源**。大型股的新聞熱度不是理由，漲不動才是事實。

**好範例 (hidden tier)：**
- symbol: "3715", name: "定穎投控", chain_slug: "ai_pcb", layer_name: "中游 · PCB 製造", priority: "high"
  why_now: "今日經濟日報提到 AI load board 需求超預期，NVIDIA Blackwell 認證名單擴張；定穎是少數切入 AI load board 的小型 PCB 廠（市值僅 NT$30B vs 金像電 NT$200B），今年 EPS 預估+80%，52週位階只在 65% 還沒過熱。不推金像電是因為它今年已經漲 120%，剩下空間有限；定穎才是雪球適合試水的位置。"

- symbol: "3665", name: "貿聯-KY", chain_slug: "connectors", layer_name: "連接器 / 纜線", priority: "high"
  why_now: "M 平方今早報告指出 GB200 機櫃內高速銅纜線束是新瓶頸，NVIDIA 預估 2026 出貨量翻倍；貿聯是直接供 NVIDIA 的高速線束小型冠軍（市值 NT$35B），追蹤池原本沒有。audit 也顯示它在近 7 日新聞出現 2 次但還沒被分類。"

**壞範例：**
- symbol: "2330" ❌（mega tier，禁止）
- symbol: "2317" ❌（mega tier，禁止）
- symbol: "3711" ❌（large tier 且已被過度覆蓋，除非有異常催化）
- symbol: "VOO" ❌（美股）
- symbol: "AI 受惠股" ❌（沒具體 ticker）
- why_now: "AI 受惠股、長線看好" ❌（沒有今日催化、沒有相對估值論述）

---

**估值守則 · EPS / P/E / P/B / ROE 納入判斷（使用者明講「需要納入考慮 EPS P/E P/B 這些」）**

所有的 lead_stocks / coverage_suggestions / budget_allocation / holdings_analysis 都**必須引用 prompt 裡「基本面資料」區塊的實際數字**（PE_TTM、EPS_TTM、P/B、ROE、earnings_growth）。不要編造數字。

**硬規則：**
1. why_now / rationale 必須點名 PE 或 EPS 的**實際數字與標籤**（🟢 合理 / 🟡 偏高 / 🟠 昂貴 / 🔴 泡沫），讓使用者看得懂這是便宜還貴。
2. **禁止推薦** PE > 50 的中小型股（hidden / small tier）**除非** earnings_growth > +50% 且 why_now 明確解釋「為何貴也要買」。
3. **禁止推薦** EPS < 0（虧損）的公司**除非**明確標註「此為轉機股、催化為 ___」並給出虧損轉盈的量化預估。
4. **優先推薦** 條件：PE 在 🟢 合理區（<20）+ earnings_growth 🟢 成長（>+10%）+ ROE > 10%。這三者齊備是「雪球夢幻配」。
5. 若基本面資料沒有該檔（fundamentals 無數據），why_now 要坦白寫「基本面資料尚待 yfinance 補齊，建議先小倉位試水」，不要裝作有數據。
6. holdings_analysis 的 bull_bear_breakdown 要把估值納入考慮：例如 2330 PE 22 + EPS 成長 34% → bull 65 / bear 15 / neutral 20（估值合理+成長佳）。

**產業估值基準（粗略、Gemini 要判斷）：**
- 半導體 / AI：PE < 25 合理，> 40 昂貴
- 電子代工 / PCB：PE < 18 合理，> 28 昂貴
- 金融 / 金控：PE < 12 合理，> 18 昂貴；**P/B 更關鍵**（< 1.2 便宜）
- 傳產 / 金屬 / 航運：PE < 15 合理（景氣循環要同時看 P/B）
- 生技 / 未獲利：看 P/S 或 EPS 趨勢，PE 數字可能失真

**好範例（why_now 引用估值）：**
- why_now: "今日 M 平方報告點名 GB200 機櫃內部高速銅纜線需求翻倍；3665 貿聯-KY 是直接供 NVIDIA 的小型冠軍（PE 18.5 🟢 合理、EPS TTM 12.3、ROE 22% 🟢、earnings_growth +35% 🟢），估值尚未反映 2026 訂單能見度，52 週位階僅 60% 還有空間。"

**壞範例：**
- why_now: "PE 合理、獲利成長" ❌（沒數字）
- why_now: "長線看好 AI 題材" ❌（沒估值論述、沒今日催化）
- why_now: "estimated PE 25" ❌（不是估計的，你有 prompt 裡實際的 PE_TTM）

"""


def build_coverage_context() -> str:
    """Inject audit_coverage.py output so Gemini can propose coverage_suggestions
    grounded in actual gaps rather than hallucinating tickers."""
    path = ROOT / "coverage_report.json"
    if not path.exists():
        return ""
    try:
        rpt = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    lines = ["## 追蹤池覆蓋現況（audit_coverage.py 的輸出）", ""]

    gaps = rpt.get("missing_from_chains") or []
    if gaps:
        lines.append("### 🔥 新聞提到但追蹤池缺的票（最近 7 日）")
        lines.append("這些是 Gemini 應該優先納入 coverage_suggestions 的候選：")
        for g in gaps[:15]:
            lines.append(f"- {g['symbol']} {g.get('name', '')} — 最近 7 日提到 {g.get('mentions', 0)} 次")
        lines.append("")

    freq = rpt.get("news_frequency") or {}
    if freq:
        lines.append("### 新聞提及次數 TOP 15（供佐證熱度）")
        items = list(freq.items())[:15]
        lines.append(" · ".join(f"{s} ×{c}" for s, c in items))
        lines.append("")

    totals = rpt.get("chain_totals") or {}
    if totals:
        lines.append("### 目前追蹤的供應鏈（supply_chains.yaml）")
        for slug, t in totals.items():
            lines.append(f"- {slug} ({t.get('title', slug)}): "
                         f"{t.get('unique_count', 0)} 檔 × {t.get('layer_count', 0)} 層 · "
                         f"最近 7 日有 {t.get('mentioned_in_window', 0)} 檔被新聞提及")
        lines.append("")

    # Tier context (Phase G) — show Gemini which tiers are already covered per
    # chain, plus a list of small/hidden names we already track (so it knows
    # the "right shape" for new coverage_suggestions).
    try:
        import yaml as _yaml
        yp = ROOT / "supply_chains.yaml"
        if yp.exists():
            sc = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
            chains = sc.get("chains") or {}
            hidden_names: list[str] = []
            small_names: list[str] = []
            per_chain_tier: list[str] = []
            for slug, chain in chains.items():
                counts = {"mega": 0, "large": 0, "mid": 0, "small": 0, "hidden": 0}
                for layer in chain.get("layers") or []:
                    for s in layer.get("stocks") or []:
                        tier = (s.get("tier") or "").strip().lower()
                        if tier in counts:
                            counts[tier] += 1
                        sym = s.get("symbol")
                        name = s.get("name") or ""
                        if tier == "hidden" and sym:
                            hidden_names.append(f"{sym} {name}")
                        elif tier == "small" and sym:
                            small_names.append(f"{sym} {name}")
                dist = " · ".join(f"{k}:{v}" for k, v in counts.items() if v)
                per_chain_tier.append(f"- {slug}: {dist}")

            if per_chain_tier:
                lines.append("### 各鏈的市值分佈（mega/large 偏重 = 需要補 small/hidden）")
                lines.extend(per_chain_tier)
                lines.append("")

            if hidden_names or small_names:
                lines.append("### 目前已追蹤的中小型/隱形冠軍（提示『正確的樣子』）")
                lines.append(
                    "⚠️ 這些已經在追蹤池裡了，不要再「建議」一次；"
                    "但新提案的 coverage_suggestions 應該長這個樣子（小型、具體角色、有 catalyst）："
                )
                if hidden_names:
                    lines.append("**HIDDEN tier**（最優先複製此模式）：")
                    lines.append(" · ".join(hidden_names[:25]))
                if small_names:
                    lines.append("**SMALL tier**：")
                    lines.append(" · ".join(small_names[:25]))
                lines.append("")
    except Exception as exc:
        print(f"[analyze] coverage tier context skipped: {exc}")

    return "\n".join(lines)


def _pe_flag(pe: float | None) -> str:
    """Color-word flag for a P/E ratio — tells Gemini 'is this cheap?'."""
    if pe is None:
        return "—"
    if pe < 0:
        return "🔴 負值（虧損）"
    if pe < 12:
        return "🟢 便宜"
    if pe < 20:
        return "🟢 合理"
    if pe < 30:
        return "🟡 偏高"
    if pe < 50:
        return "🟠 昂貴"
    return "🔴 泡沫"


def _growth_flag(g: float | None) -> str:
    """EPS / 營收 YoY 成長標籤。yfinance 回傳 0.18 = 18%。"""
    if g is None:
        return "—"
    pct = g * 100
    if pct < -10:
        return f"🔴 {pct:+.0f}%（衰退）"
    if pct < 0:
        return f"🟠 {pct:+.0f}%（微幅下滑）"
    if pct < 10:
        return f"🟡 {pct:+.0f}%（持平）"
    if pct < 30:
        return f"🟢 {pct:+.0f}%（成長）"
    return f"🟢 {pct:+.0f}%（高速成長）"


def build_valuation_context() -> str:
    """Inject EPS / P/E / P/B / ROE / growth so Gemini can gate recommendations
    on actual valuation instead of hallucinating 'P/E 22 合理' when it doesn't
    know the real number.

    User critique (2026-04-18): 「你在分析的時候需要納入考慮 EPS P/E P/B 這些誒」

    Data flow: fetch_prices.py already pulls fundamentals from yfinance into
    prices.json. We just need to surface them in the prompt.
    """
    prices_path = ROOT / "prices.json"
    if not prices_path.exists():
        return ""

    try:
        prices_blob = json.loads(prices_path.read_text(encoding="utf-8"))
        prices = prices_blob.get("prices") or {}
    except Exception:
        return ""

    # Map symbol -> fundamentals (ignoring yf ticker suffix).
    sym_to_fund: dict[str, dict] = {}
    sym_to_meta: dict[str, dict] = {}
    for yf_ticker, rec in prices.items():
        sym = rec.get("symbol") or yf_ticker
        fund = rec.get("fundamentals") or {}
        if fund:
            sym_to_fund[str(sym)] = fund
            sym_to_meta[str(sym)] = {
                "close": rec.get("close"),
                "pct_52w": rec.get("pct_52w"),
                "ret_30d": rec.get("ret_30d"),
            }

    if not sym_to_fund:
        return ""

    # Load portfolio + watchlist symbols
    watch_syms: list[str] = []
    hold_syms: list[str] = []
    try:
        cfg = yaml.safe_load(PORTFOLIO_YAML.read_text(encoding="utf-8"))
        hold_syms = [str(h["symbol"]) for h in (cfg.get("holdings") or [])]
        watch_syms = [str(w["symbol"]) for w in (cfg.get("watchlist") or [])]
    except Exception:
        pass

    # Load supply_chains.yaml stocks grouped by tier
    chain_syms_by_tier: dict[str, list[tuple[str, str, str]]] = {
        "hidden": [], "small": [], "mid": [], "large": [], "mega": [],
    }
    try:
        sc = yaml.safe_load((ROOT / "supply_chains.yaml").read_text(encoding="utf-8")) or {}
        for slug, chain in (sc.get("chains") or {}).items():
            for layer in chain.get("layers") or []:
                for s in layer.get("stocks") or []:
                    tier = (s.get("tier") or "").strip().lower()
                    sym = str(s.get("symbol") or "").strip()
                    name = s.get("name") or ""
                    if sym and tier in chain_syms_by_tier:
                        chain_syms_by_tier[tier].append((sym, name, slug))
    except Exception:
        pass

    def _row(sym: str, name: str = "", extra: str = "") -> str | None:
        f = sym_to_fund.get(sym)
        if not f:
            return None
        pe = f.get("pe_ttm")
        pe_f = f.get("pe_forward")
        eps = f.get("eps_ttm")
        pb = f.get("pb")
        roe = f.get("roe")
        eg = f.get("earnings_growth")
        rg = f.get("rev_growth")
        margin = f.get("profit_margin")
        meta = sym_to_meta.get(sym, {})
        bits: list[str] = []
        if pe is not None:
            bits.append(f"PE={pe:.1f} {_pe_flag(pe)}")
        if pe_f is not None and pe_f != pe:
            bits.append(f"fwdPE={pe_f:.1f}")
        if eps is not None:
            bits.append(f"EPS={eps:.2f}")
        if pb is not None:
            bits.append(f"PB={pb:.2f}")
        if roe is not None:
            bits.append(f"ROE={roe*100:.0f}%")
        if margin is not None:
            bits.append(f"淨利率={margin*100:.0f}%")
        if eg is not None:
            bits.append(f"EPS成長={_growth_flag(eg)}")
        elif rg is not None:
            bits.append(f"營收成長={_growth_flag(rg)}")
        if meta.get("pct_52w") is not None:
            bits.append(f"52週位階={meta['pct_52w']:.0f}%")
        if not bits:
            return None
        label = f"{sym} {name}".strip()
        suffix = f" {extra}" if extra else ""
        return f"- {label}：{' · '.join(bits)}{suffix}"

    out: list[str] = ["## 基本面資料（yfinance · 每個 ticker 的 EPS / P/E / P/B / ROE / 成長率）", ""]
    out.append("⚠️ **極重要規則 · Gemini 必讀**：")
    out.append("- 任何 lead_stocks / coverage_suggestions / budget_allocation 的 why_now / rationale **必須引用下方的實際 PE、EPS 或 ROE 數字**，不要自己編造「PE 22 合理」這種幻覺數字。")
    out.append("- 若某檔 PE > 50 卻要推薦，why_now 必須明確解釋「為何昂貴估值仍合理」（例：EPS 年增 > 80%、產能擴張、題材剛啟動）。")
    out.append("- 若某檔 EPS 為負（虧損）卻要推薦，必須寫「此為轉機股、主要催化是___」。")
    out.append("- 若下方找不到某 ticker 的基本面，就直接寫「基本面資料待補」，不要瞎編。")
    out.append("- 便宜不代表買、貴不代表賣：要結合題材動能 + 52 週位階 + 法人籌碼一起判斷。")
    out.append("")

    # Holdings section
    hold_rows = [r for s in hold_syms for r in [_row(s)] if r]
    if hold_rows:
        out.append("### 使用者持股的估值現況")
        out.extend(hold_rows)
        out.append("")

    # Watchlist section
    watch_rows = [r for s in watch_syms for r in [_row(s)] if r]
    if watch_rows:
        out.append("### 追蹤清單的估值現況")
        out.extend(watch_rows)
        out.append("")

    # Hidden / small tier — highlight "雪球級" valuation picture
    for tier_key, tier_label in [
        ("hidden", "🎯 HIDDEN tier（隱形冠軍）· 最優先的雪球候選"),
        ("small",  "SMALL tier · 次優先雪球候選"),
        ("mid",    "MID tier · 波段"),
    ]:
        rows = []
        seen: set[str] = set()
        for sym, name, slug in chain_syms_by_tier.get(tier_key, []):
            if sym in seen:
                continue
            seen.add(sym)
            r = _row(sym, name, extra=f"[{slug}]")
            if r:
                rows.append(r)
        if rows:
            out.append(f"### {tier_label}")
            out.extend(rows[:20])  # cap each tier at 20 to keep prompt size sane
            out.append("")

    # Legend
    out.append("### P/E 分級（粗略標準，細看產業）")
    out.append("🟢 <12 便宜 · 🟢 12-20 合理 · 🟡 20-30 偏高 · 🟠 30-50 昂貴 · 🔴 >50 泡沫 · 🔴 負值 虧損")
    out.append("（金融/傳產 PE<15 才算便宜；半導體/AI PE<25 才算便宜；生技/未獲利公司看 P/S 或 EPS 趨勢）")
    out.append("")

    return "\n".join(out)


def build_prompt(brief_markdown: str) -> str:
    trimmed = trim_brief(brief_markdown)
    portfolio_ctx = build_portfolio_context()
    coverage_ctx = build_coverage_context()
    valuation_ctx = build_valuation_context()

    # Today / key upcoming dates — prevents AI from misreading forward dates as past.
    now = datetime.now(TAIPEI)
    weekday_zh = "一二三四五六日"[now.weekday()]
    today_str = f"{now:%Y-%m-%d} (週{weekday_zh})"

    coverage_block = f"\n---\n\n{coverage_ctx}\n" if coverage_ctx else ""
    valuation_block = f"\n---\n\n{valuation_ctx}\n" if valuation_ctx else ""

    return f"""【今日日期】{today_str}

所有日期必須以今日為基準計算相對時間。如果新聞提到「4/29 法說會」，而今天是 4/18，那就寫「下週三 4/29 即將舉行的法說會（11 天後）」——**絕對不要只丟 4/29 給讀者猜是過去還是未來**。

{portfolio_ctx}

---

## 今日新聞彙整（從 RSS 抓取，已按產業/持股分類）

{trimmed}
{coverage_block}{valuation_block}---

請輸出符合 schema 的 JSON。檢核清單：
- [ ] 敘事段落有 3-5 句，不是條列
- [ ] 每個提到的日期都加了相對時間（昨日/今早/下週三/N 天後）
- [ ] 引用了具體數字（股價、%、金額），不用模糊詞
- [ ] 每個 topic 都連結到使用者持倉的影響
- [ ] action_checklist 的每項都是具體可執行的觀察/條件單
- [ ] learning_point 是今天新聞裡真的出現過的名詞
- [ ] faq 有 5-8 題，每題都連結到今日新聞或使用者持倉，答案有具體 ticker/數字
- [ ] coverage_suggestions 有 3-5 檔台股新票，每檔綁定今日新聞催化劑 + 對應 supply_chains.yaml 的某條鏈某層
- [ ] **每個 lead_stocks / coverage_suggestions / budget_allocation 的 why_now 都引用了基本面實際數字**（上方「基本面資料」區塊的 PE / EPS / ROE / 成長率），不是瞎猜「PE 22 合理」"""


def call_gemini(prompt: str) -> tuple[dict | None, str | None]:
    """Try each model in GEMINI_MODELS. Return (analysis_dict, model_used)."""
    if not GEMINI_API_KEY:
        print("!! GEMINI_API_KEY not set — skipping", file=sys.stderr)
        return None, None

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
            # 2.5-flash supports up to 65k output. Give plenty of headroom so
            # verbose narratives + action checklist + topics + learning never truncate.
            "maxOutputTokens": 32768,
            # Disable "thinking" mode — we want deterministic JSON out, not chain-of-thought.
            # Thinking tokens consume part of the output budget and don't help here.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    params = {"key": GEMINI_API_KEY}

    for model in GEMINI_MODELS:
        model = model.strip()
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        print(f"   trying model: {model}", file=sys.stderr)
        r = None
        text = None
        for attempt in range(3):
            try:
                r = requests.post(url, params=params, json=payload, timeout=120)
                if r.status_code == 200:
                    data = r.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(text), model
                body = r.text[:700]
                print(f"   !! HTTP {r.status_code}: {body}", file=sys.stderr)
                # Permanent failures: skip to next model immediately
                if r.status_code in (400, 401, 403, 404):
                    break
                # Transient (429, 500, 503): retry with backoff
                if r.status_code in (429, 500, 503):
                    wait = 2 ** attempt * 5
                    print(f"   …retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                break
            except json.JSONDecodeError as e:
                # Likely truncation — dump enough to diagnose, then try next model
                # (doesn't help to retry same model with same max_tokens).
                print(f"   !! non-JSON (likely truncated): {e}", file=sys.stderr)
                if text:
                    print(f"       output length: {len(text)} chars", file=sys.stderr)
                    print(f"       last 300 chars: …{text[-300:]}", file=sys.stderr)
                break
            except Exception as e:
                print(f"   !! request exception: {e}", file=sys.stderr)
                time.sleep(2 ** attempt)
        print(f"   ✗ model {model} failed, trying next", file=sys.stderr)
    return None, None


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

    result, model_used = call_gemini(prompt)
    if not result:
        print("analysis failed — brief page will fall back to copy-prompt flow",
              file=sys.stderr)
        return 0

    result["generated_at"] = datetime.now(TAIPEI).isoformat()
    result["date"] = date
    result["model"] = model_used

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

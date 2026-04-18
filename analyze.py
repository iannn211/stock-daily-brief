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
                    "symbol": {"type": "string"},
                    "name": {"type": "string"},
                    "thesis": {"type": "string"},
                    "research_angle": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "required": ["symbol", "name", "thesis", "research_angle", "risk"],
            },
        },
        "budget_allocation": {
            "type": "object",
            "properties": {
                "budget_twd": {"type": "integer"},
                "plan_summary": {"type": "string"},
                "allocations": {
                    "type": "array",
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
        lines.append(f"## 今日待部署資金：NT${budget:,.0f}")
        lines.append("請依此金額產生 budget_allocation（1-2 檔具體下單建議或不動作保留現金）。")
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

**機會清單 opportunities · 嚴格遵守：**
- **最少 5 個、最多 8 個**機會。
- 必須涵蓋**不同產業**，讓使用者看到他可能錯過的題材（橫掃所有 13 個產業：半導體代工、IC 設計、AI 伺服器、網通、散熱、光通訊、PCB、被動元件、測試、ETF、美債、美股 ETF、美股個股等，以及本土電力、機器人、工業電腦等）。
- 優先挑出：(a) 連續量能放大、(b) 法人連續買超、(c) 券商上修目標價、(d) 處於題材早中段（未擁擠）的個股。
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
使用者有一筆「trade_budget」（從 portfolio context 取得，通常 NT$5,000-10,000）要**今日或下個交易日**部署。你要綜合以下所有訊號產生具體下單計畫：
- 今日所有新聞（RSS 原文）
- 現有持倉（避免重複押同題材）
- 追蹤清單現價 + 52 週位階
- 年化波動率 / 近期走勢
- 使用者策略規則（strategy_notes，尤其雪球法試水）

產出 allocations 陣列，每項是一個具體下單建議：
- symbol + name：目標個股（一定要是使用者追蹤清單、現有持股、或今天機會清單的其中之一）
- action：「新倉試水」「加碼」「觀望等進場」「不動作 / 保留現金」
- target_shares：建議股數（台股可買零股；試算: budget/price → 整數）
- target_cost_twd：約略總金額
- entry_condition：進場條件（例：「盤前開盤價直接進」「拉回至 2500 以下再進」「等法說後觀察 3 天」）
- stop_loss_price：建議停損價（通常 -8% ~ -12%）
- take_profit_price：建議停利價（雪球法常用 +30%，也可依個股波動調整）
- rationale：**具體**為什麼選這檔（引用今日新聞、法人動作、歷史數據）
- data_sources：依據的來源（例：「Goldman Sachs 報告 4/17」「外資買超 5 日」「52 週位階 98%」「高盛上修聯亞目標價」）
- confidence_pct：你的信心度 0-100
- risk：主要風險一句話

**分配邏輯準則：**
- 通常 1-2 檔即可（NT$5k 買 1 檔零股試水最乾淨）
- 不要把預算分得太碎（< NT$2k 的倉位沒意義）
- 如果今日沒有好機會，可以 unallocated_twd = 全額、action = 「不動作 / 保留現金」並在 plan_summary 解釋為什麼
- 雪球法精髓：小倉位試水、設停損停利、漲到目標就分批收割回 0050
- why_not_other_picks：解釋為什麼不選 opportunities 其他檔或 watchlist 其他檔（使用者看了會覺得你有想過、而不是亂挑）

plan_summary：一句 30-50 字總結（例：「今日建議用 NT$5,000 試水聯亞 1 股，理由：光通訊受高盛上修 + 52 週位階雖高但法人連續買超；停損 2340、停利 3380 分批。」）

"""


def build_prompt(brief_markdown: str) -> str:
    trimmed = trim_brief(brief_markdown)
    portfolio_ctx = build_portfolio_context()

    # Today / key upcoming dates — prevents AI from misreading forward dates as past.
    now = datetime.now(TAIPEI)
    weekday_zh = "一二三四五六日"[now.weekday()]
    today_str = f"{now:%Y-%m-%d} (週{weekday_zh})"

    return f"""【今日日期】{today_str}

所有日期必須以今日為基準計算相對時間。如果新聞提到「4/29 法說會」，而今天是 4/18，那就寫「下週三 4/29 即將舉行的法說會（11 天後）」——**絕對不要只丟 4/29 給讀者猜是過去還是未來**。

{portfolio_ctx}

---

## 今日新聞彙整（從 RSS 抓取，已按產業/持股分類）

{trimmed}

---

請輸出符合 schema 的 JSON。檢核清單：
- [ ] 敘事段落有 3-5 句，不是條列
- [ ] 每個提到的日期都加了相對時間（昨日/今早/下週三/N 天後）
- [ ] 引用了具體數字（股價、%、金額），不用模糊詞
- [ ] 每個 topic 都連結到使用者持倉的影響
- [ ] action_checklist 的每項都是具體可執行的觀察/條件單
- [ ] learning_point 是今天新聞裡真的出現過的名詞"""


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

"""
Compute portfolio metrics: P&L, alpha, attribution, risk, sparklines, pillar
allocation. Reads portfolio.yaml + prices.json + price_history.json.

Writes portfolio.json consumed by build_dashboard.py and analyze.py.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
PORTFOLIO_PATH = ROOT / "portfolio.yaml"
PRICES_PATH = ROOT / "prices.json"
HISTORY_PATH = ROOT / "price_history.json"
OUTPUT_PATH = ROOT / "portfolio.json"

# Default pillar allocation targets by profile
TARGET_ALLOCATIONS: dict[str, dict[str, float]] = {
    "beginner-growth": {"growth": 0.70, "defense": 0.20, "flexibility": 0.10},
    "balanced":        {"growth": 0.55, "defense": 0.30, "flexibility": 0.15},
    "aggressive":      {"growth": 0.80, "defense": 0.05, "flexibility": 0.15},
    "defensive":       {"growth": 0.40, "defense": 0.45, "flexibility": 0.15},
    # Custom: snowball-growth — 0050 is savings, no dividend ETFs, bonds optional
    "snowball-growth": {"growth": 1.00, "defense": 0.00, "flexibility": 0.00},
}


def to_yf_ticker(symbol: str, market: str) -> str:
    return f"{symbol}.TW" if market == "TW" else symbol


def get_usdtwd(prices: dict) -> float:
    for key, p in prices.items():
        if key.upper().startswith("TWD=X"):
            return p.get("close", 32.0)
    return 32.0


def _daily_returns(closes: list[float]) -> list[float]:
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


def _max_drawdown(values: list[float]) -> float:
    """Return max drawdown as negative percent (e.g. -12.3)."""
    if len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values[1:]:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100 if peak else 0
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _volatility(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    std = statistics.stdev(daily_returns)
    return round(std * math.sqrt(252) * 100, 2)  # annualized %


def _pillar_allocation(holdings: list[dict], cash_twd: float, total_value: float) -> dict:
    pillars: dict[str, float] = {"growth": 0.0, "defense": 0.0, "flexibility": 0.0}
    for h in holdings:
        pillars[h.get("pillar", "growth")] = pillars.get(h.get("pillar", "growth"), 0) + h["value"]
    # Cash goes to "defense"
    pillars["defense"] += cash_twd
    return {k: round(v / total_value * 100, 2) if total_value else 0 for k, v in pillars.items()}


def _sparkline(history: list[dict], days: int = 30) -> list[dict]:
    tail = history[-days:]
    return [{"d": r["date"], "c": r["close"]} for r in tail]


def compute_recommendation(stock: dict, is_holding: bool = False,
                           pnl_pct: float | None = None,
                           stop_dist: float | None = None,
                           tp_dist: float | None = None) -> dict:
    """Rule-based buy/sell/hold recommendation based on 52w position + holding state.

    Returns {action, suggested_price, reason, source, tone}.
    tone: 'up' (good), 'dn' (bad), 'flat' (neutral), 'amber' (warn).
    """
    price = stock.get("price") or stock.get("price_twd") or 0
    pct52 = stock.get("pct_52w")

    # For holdings — different logic (focus on hold/sell/add)
    if is_holding and pnl_pct is not None:
        # Near stop-loss
        if stop_dist is not None and 0 < stop_dist < 5:
            return {
                "action": "接近停損 · 警戒",
                "suggested_price": stock.get("stop_loss") or price * 0.92,
                "reason": f"距停損 {stop_dist:.1f}%，跌破應執行",
                "source": "規則", "tone": "dn",
            }
        # Hit take-profit
        if tp_dist is not None and -5 < tp_dist < 0:
            return {
                "action": "接近停利 · 考慮分批",
                "suggested_price": stock.get("take_profit") or price * 1.02,
                "reason": f"接近停利目標（距 {abs(tp_dist):.1f}%），雪球法應收割部分",
                "source": "規則", "tone": "up",
            }
        # High profit — snowball harvest
        if pnl_pct > 50:
            return {
                "action": "分批獲利了結",
                "suggested_price": price,
                "reason": f"已獲利 {pnl_pct:.0f}%，雪球法建議分批收割入 0050",
                "source": "規則", "tone": "up",
            }
        # Red zone: 52w high, no TP set, already up
        if pct52 and pct52 >= 90 and pnl_pct > 20:
            return {
                "action": "鎖利 · 考慮設停利",
                "suggested_price": price * 1.05,
                "reason": f"52週位階 {pct52:.0f}%（高檔），已獲利 {pnl_pct:.0f}%",
                "source": "規則", "tone": "amber",
            }
        # Otherwise — continue holding
        return {
            "action": "續抱",
            "suggested_price": price,
            "reason": f"損益 {pnl_pct:+.1f}%，未觸發調整訊號",
            "source": "規則", "tone": "flat",
        }

    # Not a holding — buy/watch decision by 52w position
    if pct52 is None:
        return {
            "action": "觀望",
            "suggested_price": price,
            "reason": "資料不足，先觀察",
            "source": "規則", "tone": "flat",
        }

    if pct52 < 20:
        return {
            "action": "積極買入",
            "suggested_price": round(price, 2),
            "reason": f"52週位階 {pct52:.0f}%（低檔），逢低布局好時機",
            "source": "規則", "tone": "up",
        }
    if pct52 < 50:
        return {
            "action": "可以買入",
            "suggested_price": round(price * 0.98, 2),
            "reason": f"52週位階 {pct52:.0f}%（中低），限價 −2% 建倉",
            "source": "規則", "tone": "up",
        }
    if pct52 < 75:
        return {
            "action": "觀望 · 等拉回",
            "suggested_price": round(price * 0.95, 2),
            "reason": f"52週位階 {pct52:.0f}%（中段），−5% 以下考慮",
            "source": "規則", "tone": "flat",
        }
    if pct52 < 92:
        return {
            "action": "警戒擁擠 · 耐心等",
            "suggested_price": round(price * 0.92, 2),
            "reason": f"52週位階 {pct52:.0f}%（偏高），需拉回 8% 以上",
            "source": "規則", "tone": "amber",
        }
    return {
        "action": "避開追高",
        "suggested_price": round(price * 0.88, 2),
        "reason": f"52週位階 {pct52:.0f}%（歷史高檔），絕不追高",
        "source": "規則", "tone": "dn",
    }


def main() -> int:
    cfg = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))["prices"]
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["history"]

    usdtwd = get_usdtwd(prices)
    cash_twd = float(cfg.get("cash_twd", 0) or 0)
    risk_profile = cfg.get("risk_profile", {}) or {}
    style = risk_profile.get("style", "beginner-growth")
    target_alloc = TARGET_ALLOCATIONS.get(style, TARGET_ALLOCATIONS["beginner-growth"])

    # -------------------- per-holding --------------------
    holdings_out: list[dict] = []
    total_value = 0.0
    total_cost = 0.0
    day_pnl = 0.0

    for h in cfg.get("holdings", []):
        yf_ticker = to_yf_ticker(h["symbol"], h["market"])
        p = prices.get(yf_ticker)
        if not p:
            print(f"  ⚠️ no price for {yf_ticker}", file=sys.stderr)
            continue
        fx = 1.0 if h["market"] == "TW" else usdtwd
        price_twd = p["close"] * fx
        cost_twd = h["cost_basis"] * fx
        value = price_twd * h["shares"]
        cost = cost_twd * h["shares"]
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0.0
        day_change_twd = p["day_change"] * fx
        day_contribution = day_change_twd * h["shares"]
        day_pnl += day_contribution
        total_value += value
        total_cost += cost

        stop_loss_hit = bool(h.get("stop_loss") and p["close"] <= h["stop_loss"])
        take_profit_hit = bool(h.get("take_profit") and p["close"] >= h["take_profit"])

        # Distance to stop/target (for "nearing" warnings)
        def _pct_distance(level: float | None) -> float | None:
            if not level:
                return None
            return round((p["close"] - level) / level * 100, 2)

        hist = history.get(yf_ticker, [])
        spark = _sparkline(hist, 30)

        entry = {
            "symbol": h["symbol"],
            "name": h["name"],
            "market": h["market"],
            "pillar": h.get("pillar", "growth"),
            "shares": h["shares"],
            "cost_basis": h["cost_basis"],
            "price": p["close"],
            "price_twd": round(price_twd, 2),
            "value": round(value, 0),
            "cost": round(cost, 0),
            "pnl": round(pnl, 0),
            "pnl_pct": round(pnl_pct, 2),
            "day_change": p["day_change"],
            "day_change_pct": p["day_change_pct"],
            "day_contribution": round(day_contribution, 0),
            "high_52w": p.get("high_52w"),
            "low_52w": p.get("low_52w"),
            "pct_52w": p.get("pct_52w"),
            "ret_7d": p.get("ret_7d"),
            "ret_30d": p.get("ret_30d"),
            "ret_90d": p.get("ret_90d"),
            "ret_ytd": p.get("ret_ytd"),
            "stop_loss": h.get("stop_loss"),
            "take_profit": h.get("take_profit"),
            "stop_loss_hit": stop_loss_hit,
            "take_profit_hit": take_profit_hit,
            "stop_loss_dist_pct": _pct_distance(h.get("stop_loss")),
            "take_profit_dist_pct": _pct_distance(h.get("take_profit")),
            "sparkline": spark,
            "yf_ticker": p.get("yf_ticker", yf_ticker),
            "fundamentals": p.get("fundamentals") or {},
        }
        entry["recommendation"] = compute_recommendation(
            entry, is_holding=True, pnl_pct=entry["pnl_pct"],
            stop_dist=entry["stop_loss_dist_pct"],
            tp_dist=entry["take_profit_dist_pct"],
        )
        holdings_out.append(entry)

    # -------------------- portfolio-level --------------------
    total_value_incl_cash = total_value + cash_twd
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    prev_total_value = total_value - day_pnl
    day_pnl_pct = (day_pnl / prev_total_value * 100) if prev_total_value else 0.0

    # Reconstruct historical portfolio value series using current share counts.
    # This is not "true" historical value (since user may not have held same shares)
    # but it shows how the current basket has moved over the past year.
    dates_set: set[str] = set()
    for h in cfg.get("holdings", []):
        key = to_yf_ticker(h["symbol"], h["market"])
        for row in history.get(key, []):
            dates_set.add(row["date"])
    dates_sorted = sorted(dates_set)

    portfolio_series: list[dict] = []
    for d in dates_sorted:
        day_value = cash_twd
        ok = True
        for h in cfg.get("holdings", []):
            key = to_yf_ticker(h["symbol"], h["market"])
            rows = history.get(key, [])
            # Find close on date (linear; datasets are small)
            close_on = None
            for r in rows:
                if r["date"] == d:
                    close_on = r["close"]
                    break
            if close_on is None:
                ok = False
                break
            fx = 1.0 if h["market"] == "TW" else usdtwd
            day_value += close_on * fx * h["shares"]
        if ok:
            portfolio_series.append({"d": d, "v": round(day_value, 0)})

    # Risk metrics
    values = [r["v"] for r in portfolio_series]
    daily_rets = _daily_returns(values)
    vol_annualized = _volatility(daily_rets[-60:]) if len(daily_rets) >= 20 else 0.0
    drawdown_30d = _max_drawdown(values[-22:]) if len(values) >= 22 else 0.0
    drawdown_90d = _max_drawdown(values[-66:]) if len(values) >= 66 else 0.0
    drawdown_1y = _max_drawdown(values) if len(values) >= 30 else 0.0

    # Portfolio return windows
    def _window_ret(window: int) -> float | None:
        if len(values) <= window:
            return None
        v0 = values[-window - 1]
        return round((values[-1] - v0) / v0 * 100, 2) if v0 else None
    ret_7d = _window_ret(5)
    ret_30d = _window_ret(20)
    ret_90d = _window_ret(60)
    ret_1y = round((values[-1] - values[0]) / values[0] * 100, 2) if len(values) > 1 and values[0] else None

    # Benchmark
    bench_sym = cfg.get("benchmark_tw", "0050")
    bench_yf = to_yf_ticker(bench_sym, "TW")
    bench_price = prices.get(bench_yf, {})
    bench_day_pct = bench_price.get("day_change_pct", 0)
    bench_ret_30d = bench_price.get("ret_30d")
    bench_ret_ytd = bench_price.get("ret_ytd")
    alpha_day = round(day_pnl_pct - bench_day_pct, 2)

    # Attribution
    sorted_contrib = sorted(holdings_out, key=lambda h: h["day_contribution"], reverse=True)
    attribution = {
        "positive": [h for h in sorted_contrib if h["day_contribution"] > 0][:3],
        "negative": [h for h in reversed(sorted_contrib) if h["day_contribution"] < 0][:3],
    }

    # Weekly attribution — last 5 trading days of P&L (daily diffs on portfolio_series)
    weekly_attribution: list[dict] = []
    if len(portfolio_series) >= 2:
        tail = portfolio_series[-6:]  # 6 points → 5 diffs
        for i in range(1, len(tail)):
            prev_v = tail[i - 1]["v"]
            cur_v = tail[i]["v"]
            pnl_d = cur_v - prev_v
            pct_d = (pnl_d / prev_v * 100) if prev_v else 0
            weekday_zh = "一二三四五六日"[datetime.strptime(tail[i]["d"], "%Y-%m-%d").weekday()]
            weekly_attribution.append({
                "date": tail[i]["d"],
                "weekday": weekday_zh,
                "pnl": round(pnl_d, 0),
                "pct": round(pct_d, 2),
            })

    # Alerts: stop-loss / take-profit / concentration / pillar imbalance
    concentration_alerts = []
    if total_value_incl_cash:
        for h in holdings_out:
            weight = h["value"] / total_value_incl_cash
            if weight > risk_profile.get("max_single_position", 0.40):
                concentration_alerts.append({
                    "symbol": h["symbol"], "name": h["name"],
                    "weight_pct": round(weight * 100, 2),
                    "limit_pct": round(risk_profile.get("max_single_position", 0.40) * 100, 2),
                })

    actual_alloc = _pillar_allocation(holdings_out, cash_twd, total_value_incl_cash)
    pillar_alerts: list[dict] = []
    threshold = risk_profile.get("rebalance_threshold", 0.05) * 100
    for pillar, target in target_alloc.items():
        target_pct = target * 100
        diff = actual_alloc.get(pillar, 0) - target_pct
        if abs(diff) > threshold:
            pillar_alerts.append({
                "pillar": pillar,
                "actual_pct": actual_alloc.get(pillar, 0),
                "target_pct": target_pct,
                "diff_pct": round(diff, 2),
            })

    alerts = {
        "stop_loss":     [h for h in holdings_out if h["stop_loss_hit"]],
        "take_profit":   [h for h in holdings_out if h["take_profit_hit"]],
        "concentration": concentration_alerts,
        "pillar":        pillar_alerts,
        "nearing_stop":  [
            h for h in holdings_out
            if h.get("stop_loss_dist_pct") is not None
            and 0 < h["stop_loss_dist_pct"] < 5
        ],
    }
    alert_count = (len(alerts["stop_loss"]) + len(alerts["take_profit"])
                   + len(alerts["concentration"]) + len(alerts["pillar"])
                   + len(alerts["nearing_stop"]))

    # Macro snapshot
    def _macro(key: str) -> dict:
        p = prices.get(key, {})
        return {
            "close": p.get("close"),
            "day_change_pct": p.get("day_change_pct"),
            "ret_30d": p.get("ret_30d"),
            "ret_ytd": p.get("ret_ytd"),
            "pct_52w": p.get("pct_52w"),
        }
    macro = {
        "twii":   _macro("^TWII"),
        "spx":    _macro("^GSPC"),
        "ndx":    _macro("^IXIC"),
        "sox":    _macro("^SOX"),
        "n225":   _macro("^N225"),
        "hsi":    _macro("^HSI"),
        "vix":    _macro("^VIX"),
        "usdtwd": _macro("TWD=X"),
        "dxy":    _macro("DX-Y.NYB"),
        "us10y":  _macro("^TNX"),
        "gold":   _macro("GC=F"),
        "oil":    _macro("CL=F"),
        "btc":    _macro("BTC-USD"),
    }

    # Simulator universe (light: price + 52w only, no history)
    universe_out: list[dict] = []
    existing_syms = {h["symbol"] for h in holdings_out} | {w["symbol"] for w in cfg.get("watchlist", [])}
    for u in cfg.get("simulator_universe", []):
        yf_ticker = to_yf_ticker(u["symbol"], u["market"])
        p = prices.get(yf_ticker)
        if not p:
            continue
        entry = {
            "symbol": u["symbol"],
            "name": u["name"],
            "market": u["market"],
            "category": u.get("category", "其他"),
            "price": p["close"],
            "day_change": p.get("day_change"),
            "day_change_pct": p["day_change_pct"],
            "pct_52w": p.get("pct_52w"),
            "high_52w": p.get("high_52w"),
            "low_52w": p.get("low_52w"),
            "ret_7d": p.get("ret_7d"),
            "ret_30d": p.get("ret_30d"),
            "ret_90d": p.get("ret_90d"),
            "ret_ytd": p.get("ret_ytd"),
            "currency": p.get("currency"),
            "is_held": u["symbol"] in existing_syms,
            "yf_ticker": p.get("yf_ticker", yf_ticker),
            "fundamentals": p.get("fundamentals") or {},
        }
        entry["recommendation"] = compute_recommendation(entry, is_holding=False)
        universe_out.append(entry)

    # Watchlist enrichment
    watchlist_out: list[dict] = []
    for w in cfg.get("watchlist", []):
        yf_ticker = to_yf_ticker(w["symbol"], w["market"])
        p = prices.get(yf_ticker)
        if not p:
            continue
        entry = {
            "symbol": w["symbol"],
            "name": w["name"],
            "market": w["market"],
            "pillar": w.get("pillar"),
            "price": p["close"],
            "day_change": p.get("day_change"),
            "day_change_pct": p["day_change_pct"],
            "ret_7d": p.get("ret_7d"),
            "ret_30d": p.get("ret_30d"),
            "ret_90d": p.get("ret_90d"),
            "ret_ytd": p.get("ret_ytd"),
            "pct_52w": p.get("pct_52w"),
            "high_52w": p.get("high_52w"),
            "low_52w": p.get("low_52w"),
            "currency": p.get("currency"),
            "sparkline": _sparkline(history.get(yf_ticker, []), 30),
            "yf_ticker": p.get("yf_ticker", yf_ticker),
            "fundamentals": p.get("fundamentals") or {},
        }
        entry["recommendation"] = compute_recommendation(entry, is_holding=False)
        watchlist_out.append(entry)

    out = {
        "as_of": datetime.now(TAIPEI).isoformat(),
        "fx_usdtwd": usdtwd,
        "risk_profile": {
            "style": style,
            "target_allocation": {k: round(v * 100, 1) for k, v in target_alloc.items()},
            "max_single_position_pct": round(risk_profile.get("max_single_position", 0.40) * 100, 1),
            "target_cash_ratio_pct": round(risk_profile.get("target_cash_ratio", 0.10) * 100, 1),
            "rebalance_threshold_pct": round(risk_profile.get("rebalance_threshold", 0.05) * 100, 1),
        },
        "summary": {
            "total_value_twd": round(total_value_incl_cash, 0),
            "equity_value_twd": round(total_value, 0),
            "cash_twd": round(cash_twd, 0),
            "cash_ratio_pct": round(cash_twd / total_value_incl_cash * 100, 2) if total_value_incl_cash else 0,
            "total_cost_twd": round(total_cost, 0),
            "total_pnl_twd": round(total_pnl, 0),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "day_pnl_twd": round(day_pnl, 0),
            "day_pnl_pct": round(day_pnl_pct, 2),
            "alpha_vs_benchmark_pct": alpha_day,
            "ret_7d_pct": ret_7d,
            "ret_30d_pct": ret_30d,
            "ret_90d_pct": ret_90d,
            "ret_1y_pct": ret_1y,
        },
        "benchmark": {
            "symbol": bench_sym,
            "day_change_pct": bench_day_pct,
            "ret_30d_pct": bench_ret_30d,
            "ret_ytd_pct": bench_ret_ytd,
        },
        "risk": {
            "volatility_annualized_pct": vol_annualized,
            "drawdown_30d_pct": drawdown_30d,
            "drawdown_90d_pct": drawdown_90d,
            "drawdown_1y_pct": drawdown_1y,
        },
        "pillar_allocation": {
            "actual": actual_alloc,
            "target": {k: round(v * 100, 1) for k, v in target_alloc.items()},
        },
        "macro": macro,
        "holdings": holdings_out,
        "attribution": attribution,
        "weekly_attribution": weekly_attribution,
        "alerts": alerts,
        "alert_count": alert_count,
        "watchlist": watchlist_out,
        "simulator_universe": universe_out,
        "portfolio_series": portfolio_series[-90:],  # last 90 trading days for sparkline
    }
    OUTPUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    s = out["summary"]
    print(
        f"→ {OUTPUT_PATH.relative_to(ROOT)}  "
        f"value=NT${s['total_value_twd']:,.0f} "
        f"pnl={s['total_pnl_pct']:+.2f}% "
        f"day={s['day_pnl_pct']:+.2f}% "
        f"30d={s['ret_30d_pct']}% "
        f"vol={out['risk']['volatility_annualized_pct']:.1f}% "
        f"dd30d={out['risk']['drawdown_30d_pct']:.2f}%",
        file=sys.stderr,
    )
    print(f"  pillar actual={actual_alloc}  target={out['risk_profile']['target_allocation']}",
          file=sys.stderr)
    print(f"  alerts: {alert_count} active "
          f"(stop={len(alerts['stop_loss'])} tp={len(alerts['take_profit'])} "
          f"conc={len(alerts['concentration'])} pillar={len(alerts['pillar'])} "
          f"near={len(alerts['nearing_stop'])})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

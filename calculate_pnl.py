"""
Calculate portfolio metrics from portfolio.yaml + prices.json.

Writes portfolio.json with:
  {
    "as_of": ISO,
    "summary": { total_value, total_cost, total_pnl, total_pnl_pct,
                 day_pnl, day_pnl_pct, alpha_vs_benchmark },
    "holdings": [ {symbol, name, shares, cost_basis, price, value, cost,
                   pnl, pnl_pct, day_change, day_contribution,
                   stop_loss_hit, take_profit_hit}, ... ],
    "attribution": { positive: [top 3], negative: [top 3] },
    "alerts": { stop_loss: [...], take_profit: [...] },
    "benchmark": { symbol, day_change_pct },
    "watchlist": [ {symbol, price, day_change_pct}, ... ]
  }

All amounts in TWD (US stocks converted via USD/TWD from yfinance).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
PORTFOLIO_PATH = ROOT / "portfolio.yaml"
PRICES_PATH = ROOT / "prices.json"
OUTPUT_PATH = ROOT / "portfolio.json"
USDTWD_DEFAULT = 32.0  # Fallback if fx lookup fails


def to_yf_ticker(symbol: str, market: str) -> str:
    return f"{symbol}.TW" if market == "TW" else symbol


def get_usdtwd(prices: dict) -> float:
    """Look for TWD=X in prices, else fallback."""
    for key, p in prices.items():
        if key.upper().startswith("TWD=X") or key == "USDTWD":
            return p.get("close", USDTWD_DEFAULT)
    return USDTWD_DEFAULT


def main() -> int:
    cfg = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))["prices"]

    usdtwd = get_usdtwd(prices)

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

        holdings_out.append({
            "symbol": h["symbol"],
            "name": h["name"],
            "market": h["market"],
            "shares": h["shares"],
            "cost_basis": h["cost_basis"],
            "price": p["close"],
            "price_twd": round(price_twd, 2),
            "day_change": p["day_change"],
            "day_change_pct": p["day_change_pct"],
            "day_contribution": round(day_contribution, 0),
            "value": round(value, 0),
            "cost": round(cost, 0),
            "pnl": round(pnl, 0),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss": h.get("stop_loss"),
            "take_profit": h.get("take_profit"),
            "stop_loss_hit": stop_loss_hit,
            "take_profit_hit": take_profit_hit,
        })

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    day_pnl_pct = (day_pnl / (total_value - day_pnl) * 100) if total_value - day_pnl else 0.0

    # Benchmark
    bench_sym = cfg.get("benchmark_tw", "0050")
    bench_yf = to_yf_ticker(bench_sym, "TW")
    bench_price = prices.get(bench_yf)
    bench_day_pct = bench_price["day_change_pct"] if bench_price else 0.0
    alpha = round(day_pnl_pct - bench_day_pct, 2)

    # Attribution: top +/- contributors
    sorted_contrib = sorted(holdings_out, key=lambda h: h["day_contribution"], reverse=True)
    attribution = {
        "positive": [h for h in sorted_contrib if h["day_contribution"] > 0][:3],
        "negative": [h for h in reversed(sorted_contrib) if h["day_contribution"] < 0][:3],
    }

    # Alerts
    alerts = {
        "stop_loss": [h for h in holdings_out if h["stop_loss_hit"]],
        "take_profit": [h for h in holdings_out if h["take_profit_hit"]],
    }

    # Watchlist
    watchlist_out: list[dict] = []
    for w in cfg.get("watchlist", []):
        yf_ticker = to_yf_ticker(w["symbol"], w["market"])
        p = prices.get(yf_ticker)
        if not p:
            continue
        watchlist_out.append({
            "symbol": w["symbol"],
            "name": w["name"],
            "market": w["market"],
            "price": p["close"],
            "day_change_pct": p["day_change_pct"],
            "currency": p["currency"],
        })

    out = {
        "as_of": datetime.now(TAIPEI).isoformat(),
        "fx_usdtwd": usdtwd,
        "summary": {
            "total_value_twd": round(total_value, 0),
            "total_cost_twd": round(total_cost, 0),
            "total_pnl_twd": round(total_pnl, 0),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "day_pnl_twd": round(day_pnl, 0),
            "day_pnl_pct": round(day_pnl_pct, 2),
            "alpha_vs_benchmark_pct": alpha,
        },
        "benchmark": {
            "symbol": bench_sym,
            "day_change_pct": bench_day_pct,
        },
        "holdings": holdings_out,
        "attribution": attribution,
        "alerts": alerts,
        "watchlist": watchlist_out,
    }
    OUTPUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    s = out["summary"]
    print(f"→ {OUTPUT_PATH.relative_to(ROOT)}  "
          f"value=NT${s['total_value_twd']:,.0f} "
          f"pnl={s['total_pnl_pct']:+.2f}% "
          f"day={s['day_pnl_pct']:+.2f}% "
          f"alpha={s['alpha_vs_benchmark_pct']:+.2f}%",
          file=sys.stderr)
    if alerts["stop_loss"]:
        print(f"  🔴 stop-loss hit: {[a['symbol'] for a in alerts['stop_loss']]}", file=sys.stderr)
    if alerts["take_profit"]:
        print(f"  🟢 take-profit hit: {[a['symbol'] for a in alerts['take_profit']]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

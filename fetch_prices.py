"""
Fetch daily prices + 1-year history for portfolio + watchlist + benchmarks + macro.

Writes:
  prices.json         — latest close / prev close / day change for all tickers
  price_history.json  — 1-year daily closes keyed by ticker

Uses yfinance for TW (.TW, fallback .TWO), US, and macro tickers (^VIX, ^TWII, etc.).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
import yfinance as yf

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
PORTFOLIO_PATH = ROOT / "portfolio.yaml"
PRICES_PATH = ROOT / "prices.json"
HISTORY_PATH = ROOT / "price_history.json"

HISTORY_PERIOD = "1y"  # 1-year daily


def to_yf_ticker(symbol: str, market: str) -> str:
    """TW = .TW default (TPEx fallback handled later)."""
    if market == "TW":
        return f"{symbol}.TW"
    return symbol


def _fetch_history(yf_ticker: str) -> pd.DataFrame | None:
    try:
        hist = yf.Ticker(yf_ticker).history(period=HISTORY_PERIOD, auto_adjust=False)
        if hist.empty:
            return None
        return hist
    except Exception:
        return None


def _safe_num(v) -> float | None:
    """Coerce yfinance info value to float; drop None/NaN/inf."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return f


def _fetch_fundamentals(yf_ticker: str) -> dict:
    """Pull the subset of .info we render on theme/stock pages.

    Returns a dict with keys (any may be None if yfinance doesn't have it):
      pe_ttm, pe_forward, eps_ttm, eps_forward, pb, roe, profit_margin,
      rev_growth, earnings_growth, market_cap, dividend_yield, beta
    """
    try:
        info = yf.Ticker(yf_ticker).info or {}
    except Exception:
        return {}

    return {
        "pe_ttm": _safe_num(info.get("trailingPE")),
        "pe_forward": _safe_num(info.get("forwardPE")),
        "eps_ttm": _safe_num(info.get("trailingEps")),
        "eps_forward": _safe_num(info.get("forwardEps")),
        "pb": _safe_num(info.get("priceToBook")),
        "roe": _safe_num(info.get("returnOnEquity")),          # 0.24 = 24%
        "profit_margin": _safe_num(info.get("profitMargins")),  # 0.24 = 24%
        "rev_growth": _safe_num(info.get("revenueGrowth")),     # yoy
        "earnings_growth": _safe_num(info.get("earningsGrowth")),  # yoy
        "market_cap": _safe_num(info.get("marketCap")),
        "dividend_yield": _safe_num(info.get("dividendYield")),
        "beta": _safe_num(info.get("beta")),
        "sector": info.get("sector") or None,
        "industry": info.get("industry") or None,
    }


def _is_equity(yf_ticker: str) -> bool:
    """Fundamentals make sense only for real equities — skip macro/FX/commodity tickers."""
    if yf_ticker.startswith("^"):       # ^VIX ^TWII ^SPX etc
        return False
    if "=" in yf_ticker:                # TWD=X, GC=F, CL=F, DX-Y.NYB
        return False
    if yf_ticker.endswith("-USD"):      # BTC-USD
        return False
    return True


def fetch_one(yf_ticker: str, with_fundamentals: bool = True) -> tuple[dict | None, pd.DataFrame | None]:
    """Fetch latest + 1y history. Fall back to .TWO for TW tickers if .TW fails."""
    hist = _fetch_history(yf_ticker)
    actual = yf_ticker

    if (hist is None or hist.empty) and yf_ticker.endswith(".TW"):
        alt = yf_ticker[:-3] + ".TWO"
        hist = _fetch_history(alt)
        if hist is not None and not hist.empty:
            actual = alt

    if hist is None or hist.empty:
        print(f"  ⚠️  {yf_ticker}: no history", file=sys.stderr)
        return None, None

    close = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close
    day_change = close - prev_close
    day_change_pct = (day_change / prev_close * 100) if prev_close else 0.0

    currency = "TWD" if actual.endswith((".TW", ".TWO")) else "USD"
    if actual == "TWD=X":
        currency = "TWD"
    elif actual == "^VIX":
        currency = "PT"  # points

    latest = {
        "close": round(close, 4),
        "prev_close": round(prev_close, 4),
        "day_change": round(day_change, 4),
        "day_change_pct": round(day_change_pct, 4),
        "currency": currency,
        "as_of": hist.index[-1].strftime("%Y-%m-%d"),
        "yf_ticker": actual,
    }

    # 52-week stats + sparkline (last 60 trading days)
    year = hist.tail(252)
    latest["high_52w"] = round(float(year["High"].max()), 4)
    latest["low_52w"] = round(float(year["Low"].min()), 4)
    rng = latest["high_52w"] - latest["low_52w"]
    latest["pct_52w"] = round((close - latest["low_52w"]) / rng * 100, 2) if rng else 50.0

    # Tail returns (trading days)
    def _ret(n: int) -> float | None:
        if len(hist) <= n:
            return None
        past = float(hist["Close"].iloc[-n - 1])
        return round((close - past) / past * 100, 2) if past else None
    latest["ret_7d"] = _ret(5)
    latest["ret_30d"] = _ret(20)
    latest["ret_90d"] = _ret(60)
    latest["ret_ytd"] = _ret(len(hist) - 1) if len(hist) > 1 else None  # placeholder
    # Proper YTD
    this_year = datetime.now(TAIPEI).year
    ytd = hist[hist.index.year == this_year]
    if len(ytd) > 1:
        y0 = float(ytd["Close"].iloc[0])
        latest["ret_ytd"] = round((close - y0) / y0 * 100, 2) if y0 else None

    # Fundamentals (equities only — skip indices/FX/commodity)
    if with_fundamentals and _is_equity(actual):
        fund = _fetch_fundamentals(actual)
        if fund:
            latest["fundamentals"] = fund

    return latest, hist


def main() -> int:
    cfg = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))

    # Collect tickers in order: holdings, watchlist, benchmarks, macro.
    tickers: dict[str, str] = {}  # yf_ticker -> display symbol
    for h in cfg.get("holdings", []):
        tickers[to_yf_ticker(h["symbol"], h["market"])] = h["symbol"]
    for w in cfg.get("watchlist", []):
        tickers[to_yf_ticker(w["symbol"], w["market"])] = w["symbol"]
    for key in ("benchmark_tw", "benchmark_us"):
        sym = cfg.get(key)
        if not sym:
            continue
        market = "TW" if key == "benchmark_tw" else "US"
        tickers.setdefault(to_yf_ticker(sym, market), sym)
    for m in cfg.get("macro_tickers", []):
        tickers.setdefault(m, m)
    # Simulator universe — fetched lightly (just for price + 52w range)
    for u in cfg.get("simulator_universe", []):
        tickers.setdefault(to_yf_ticker(u["symbol"], u["market"]), u["symbol"])

    print(f"[{datetime.now(TAIPEI):%Y-%m-%d %H:%M}] fetching {len(tickers)} tickers + 1y history…",
          file=sys.stderr)

    prices: dict[str, dict] = {}
    history_out: dict[str, list[dict]] = {}
    for yf_ticker, sym in tickers.items():
        latest, hist = fetch_one(yf_ticker)
        if not latest:
            continue
        prices[yf_ticker] = {**latest, "symbol": sym}
        # Downsample history: save last 252 trading days, daily close only
        tail = hist.tail(252)
        history_out[yf_ticker] = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 4)}
            for idx, row in tail.iterrows()
        ]
        actual = latest.get("yf_ticker", yf_ticker)
        hint = f" (via {actual})" if actual != yf_ticker else ""
        fund = latest.get("fundamentals") or {}
        fund_bits = []
        if fund.get("pe_ttm"):
            fund_bits.append(f"PE={fund['pe_ttm']:.1f}")
        if fund.get("eps_ttm"):
            fund_bits.append(f"EPS={fund['eps_ttm']:.2f}")
        if fund.get("roe"):
            fund_bits.append(f"ROE={fund['roe']*100:.0f}%")
        fund_str = (" " + " ".join(fund_bits)) if fund_bits else ""
        print(
            f"  ✓ {yf_ticker}: {latest['close']} ({latest['day_change_pct']:+.2f}%) "
            f"52w pct={latest['pct_52w']:.0f}% ytd={latest.get('ret_ytd') or '—'}{fund_str}{hint}",
            file=sys.stderr,
        )

    PRICES_PATH.write_text(
        json.dumps({"fetched_at": datetime.now(TAIPEI).isoformat(), "prices": prices},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    HISTORY_PATH.write_text(
        json.dumps({"fetched_at": datetime.now(TAIPEI).isoformat(), "history": history_out},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"→ prices.json ({len(prices)} tickers), "
        f"price_history.json ({sum(len(v) for v in history_out.values())} rows)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

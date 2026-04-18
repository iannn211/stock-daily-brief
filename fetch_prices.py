"""
Fetch daily prices for portfolio + watchlist + benchmark.

Writes prices.json with:
  {
    "fetched_at": ISO timestamp,
    "prices": {
        "2330.TW": {"close": 2030.0, "prev_close": 2085.0, "currency": "TWD", ...},
        ...
    }
  }

Uses yfinance for both TW (append .TW) and US stocks. Free, no auth.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
import yfinance as yf

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
PORTFOLIO_PATH = ROOT / "portfolio.yaml"
PRICES_PATH = ROOT / "prices.json"


def to_yf_ticker(symbol: str, market: str) -> str:
    """TW tickers: .TW for TWSE-listed, .TWO for TPEx-listed. Default to .TW."""
    if market == "TW":
        return f"{symbol}.TW"
    return symbol


def _try_history(yf_ticker: str):
    t = yf.Ticker(yf_ticker)
    try:
        hist = t.history(period="5d", auto_adjust=False)
        if hist.empty:
            return None, None
        return t, hist
    except Exception:
        return None, None


def fetch_one(yf_ticker: str) -> dict | None:
    """Fetch close + prev close. For .TW tickers, fall back to .TWO (TPEx)."""
    t, hist = _try_history(yf_ticker)

    if (hist is None or hist.empty) and yf_ticker.endswith(".TW"):
        # Try TPEx (上櫃) suffix.
        alt = yf_ticker[:-3] + ".TWO"
        t, hist = _try_history(alt)
        if hist is not None and not hist.empty:
            yf_ticker = alt  # record the one that worked

    if hist is None or hist.empty:
        print(f"  ⚠️  {yf_ticker}: no history", file=sys.stderr)
        return None

    try:
        close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close
        # Currency from fast_info if available
        currency = "TWD" if yf_ticker.endswith((".TW", ".TWO")) else "USD"
        try:
            fi = t.fast_info
            currency = getattr(fi, "currency", currency) or currency
        except Exception:
            pass
        day_change = close - prev_close
        day_change_pct = (day_change / prev_close * 100) if prev_close else 0.0
        return {
            "close": round(close, 4),
            "prev_close": round(prev_close, 4),
            "day_change": round(day_change, 4),
            "day_change_pct": round(day_change_pct, 4),
            "currency": currency,
            "as_of": hist.index[-1].strftime("%Y-%m-%d"),
            "yf_ticker": yf_ticker,  # record actual ticker used (may have .TWO fallback)
        }
    except Exception as exc:
        print(f"  ❌  {yf_ticker}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    cfg = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))
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

    print(f"[{datetime.now(TAIPEI):%Y-%m-%d %H:%M}] fetching {len(tickers)} tickers…", file=sys.stderr)
    prices: dict[str, dict] = {}
    for yf_ticker, sym in tickers.items():
        data = fetch_one(yf_ticker)
        if data:
            # Always store under the originally-requested ticker so lookups work,
            # even if we had to fall back to .TWO.
            prices[yf_ticker] = {**data, "symbol": sym}
            actual = data.get("yf_ticker", yf_ticker)
            suffix_hint = f" (via {actual})" if actual != yf_ticker else ""
            print(f"  ✓ {yf_ticker}: {data['close']} ({data['day_change_pct']:+.2f}%){suffix_hint}", file=sys.stderr)

    out = {
        "fetched_at": datetime.now(TAIPEI).isoformat(),
        "prices": prices,
    }
    PRICES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ wrote {PRICES_PATH.relative_to(ROOT)} with {len(prices)} tickers", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

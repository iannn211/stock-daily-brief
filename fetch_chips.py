"""
Fetch last 20 trading days of 三大法人買賣超 from TWSE (上市) + TPEx (上櫃).

Writes chips.json:
  {
    "fetched_at": "2026-04-18T..",
    "trading_days": ["2026-04-17", "2026-04-16", ...],
    "by_symbol": {
      "2330": {
        "foreign_5d":   1234567,   # 股
        "foreign_20d":  5678901,
        "trust_5d":     ...,
        "trust_20d":    ...,
        "dealer_5d":    ...,
        "dealer_20d":   ...,
        "total_5d":     ...,
        "total_20d":    ...,
        "foreign_streak": 3,       # consecutive days of net buy (+) or net sell (−)
        "daily": [                 # last 5 trading days, newest first
          {"d": "2026-04-17", "foreign": 123, "trust": 45, "dealer": 6, "total": 174},
          ...
        ]
      },
      ...
    }
  }

股 (shares), not 張 (lots of 1000). Convert downstream if needed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
CHIPS_PATH = ROOT / "chips.json"
PORTFOLIO_PATH = ROOT / "portfolio.yaml"

# How many trading days to retrieve
MAX_TRADING_DAYS = 20
CALENDAR_LOOKBACK = 40  # walk back this many calendar days to guarantee 20 trading days

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={date}&selectType=ALL&response=json"
TPEX_URL = ("https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            "3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={date_roc}&_=")

UA = {"User-Agent": "Mozilla/5.0 (stock-daily-brief; ianmong520@gmail.com)"}


def _to_int(s) -> int:
    if s is None:
        return 0
    s = str(s).replace(",", "").strip()
    if not s or s == "--":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _fetch_twse_day(date_str: str) -> dict[str, dict]:
    """Fetch TWSE (上市) 三大法人 for one day. Date format: 20260417."""
    url = TWSE_URL.format(date=date_str)
    try:
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        print(f"  TWSE {date_str}: {e}", file=sys.stderr)
        return {}

    if data.get("stat") != "OK":
        return {}

    out: dict[str, dict] = {}
    # Column map (TWSE T86 as of 2024-2026):
    #   0=證券代號, 4=外陸資買賣超(不含外資自營商), 10=投信買賣超,
    #   11=自營商買賣超(合計), 18=三大法人買賣超合計
    for row in data.get("data", []):
        try:
            sym = row[0].strip()
            if not sym:
                continue
            out[sym] = {
                "foreign": _to_int(row[4]),
                "trust":   _to_int(row[10]),
                "dealer":  _to_int(row[11]),
                "total":   _to_int(row[18]),
            }
        except (IndexError, AttributeError):
            continue
    return out


def _fetch_tpex_day(date_iso: str) -> dict[str, dict]:
    """Fetch TPEx (上櫃) 三大法人 for one day. date_iso: 2026-04-17."""
    y, m, d = date_iso.split("-")
    roc_date = f"{int(y) - 1911}/{m}/{d}"
    url = TPEX_URL.format(date_roc=roc_date)
    try:
        req = urllib.request.Request(url, headers=UA)
        payload = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        print(f"  TPEx {date_iso}: {e}", file=sys.stderr)
        return {}

    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return {}

    out: dict[str, dict] = {}
    # Column map (TPEx daily 3itrade_hedge_result as of 2024-2026):
    #   0=代號, 10=外資+陸資合計買賣超, 13=投信買賣超,
    #   22=自營商合計買賣超, 23=三大法人買賣超合計
    for row in tables[0]["data"]:
        try:
            sym = row[0].strip()
            if not sym:
                continue
            out[sym] = {
                "foreign": _to_int(row[10]),
                "trust":   _to_int(row[13]),
                "dealer":  _to_int(row[22]),
                "total":   _to_int(row[23]),
            }
        except (IndexError, AttributeError):
            continue
    return out


def _fetch_day(date_iso: str) -> dict[str, dict]:
    """Merged TWSE + TPEx for a single date."""
    ymd = date_iso.replace("-", "")
    twse = _fetch_twse_day(ymd)
    # Polite delay between requests — TWSE rate-limits aggressively
    time.sleep(1.0)
    tpex = _fetch_tpex_day(date_iso)
    merged = {**twse, **tpex}  # TPEx shouldn't collide with TWSE but upsert anyway
    return merged


def _walk_trading_days(max_days: int = MAX_TRADING_DAYS) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """Yield up to max_days of valid trading-day data, walking back from today.
    Returns (ordered_date_list_newest_first, {date: {sym: chips}})."""
    today = datetime.now(TAIPEI).date()
    got: dict[str, dict[str, dict]] = {}
    order: list[str] = []
    for back in range(CALENDAR_LOOKBACK):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # Sat/Sun skip immediately
            continue
        iso = d.isoformat()
        print(f"  fetching {iso} ...", file=sys.stderr)
        day_data = _fetch_day(iso)
        if not day_data:
            print(f"    → empty (holiday?)", file=sys.stderr)
            continue
        got[iso] = day_data
        order.append(iso)
        if len(order) >= max_days:
            break
    return order, got


def _streak(daily_values: list[int]) -> int:
    """How many consecutive days (from newest) the sign is consistent.
    Positive = net-buy streak, negative = net-sell streak. 0 = zero/mixed."""
    if not daily_values or daily_values[0] == 0:
        return 0
    sign = 1 if daily_values[0] > 0 else -1
    streak = 0
    for v in daily_values:
        if v == 0:
            break
        if (v > 0) != (sign > 0):
            break
        streak += 1
    return streak * sign


def _portfolio_symbols() -> set[str]:
    """Symbols from portfolio.yaml (holdings + watchlist + simulator_universe)
    plus any 4-digit TW tickers in today's AI analysis opportunities."""
    import re
    import yaml
    syms: set[str] = set()
    try:
        cfg = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        for coll in ("holdings", "watchlist", "simulator_universe"):
            for it in cfg.get(coll, []) or []:
                if it.get("market") == "TW" and it.get("symbol"):
                    syms.add(it["symbol"])
    except Exception as e:
        print(f"  portfolio.yaml read failed: {e}", file=sys.stderr)

    # Pull symbols mentioned in latest AI analysis (if present)
    analyses_dir = ROOT / "analyses"
    if analyses_dir.exists():
        latest = sorted(analyses_dir.glob("*.json"))[-1:] if any(analyses_dir.iterdir()) else []
        for p in latest:
            try:
                a = json.loads(p.read_text(encoding="utf-8"))
                for o in a.get("opportunities", []) or []:
                    for ls in o.get("lead_stocks", []) or []:
                        s = (ls.get("symbol") or "").strip()
                        if s and re.fullmatch(r"\d{4,6}[A-Z]*", s):
                            syms.add(s)
            except Exception:
                continue
    return syms


def main() -> int:
    print(f"[{datetime.now(TAIPEI):%Y-%m-%d %H:%M}] scraping TWSE + TPEx 三大法人 "
          f"(up to {MAX_TRADING_DAYS} trading days)…", file=sys.stderr)

    order, day_map = _walk_trading_days()
    if not order:
        print("no trading-day data fetched — TWSE/TPEx may be down", file=sys.stderr)
        return 1
    print(f"→ got {len(order)} trading days: {order[0]} (newest) … {order[-1]}", file=sys.stderr)

    # Filter to symbols we actually render on the dashboard
    wanted = _portfolio_symbols()
    print(f"  filtering to {len(wanted)} portfolio + AI opportunity symbols", file=sys.stderr)

    # Collect matching symbols only
    all_syms: set[str] = set()
    for d in order:
        for sym in day_map[d].keys():
            if sym in wanted:
                all_syms.add(sym)

    by_symbol: dict[str, dict] = {}
    for sym in all_syms:
        daily: list[dict] = []
        foreign_list = []
        trust_list = []
        dealer_list = []
        total_list = []
        for d in order:
            row = day_map[d].get(sym) or {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}
            foreign_list.append(row["foreign"])
            trust_list.append(row["trust"])
            dealer_list.append(row["dealer"])
            total_list.append(row["total"])
            if len(daily) < 5:  # Keep only last 5 days for UI sparkline
                daily.append({
                    "d": d,
                    "foreign": row["foreign"],
                    "trust": row["trust"],
                    "dealer": row["dealer"],
                    "total": row["total"],
                })

        n5 = min(5, len(order))
        n20 = len(order)
        by_symbol[sym] = {
            "foreign_5d":  sum(foreign_list[:n5]),
            "foreign_20d": sum(foreign_list[:n20]),
            "trust_5d":    sum(trust_list[:n5]),
            "trust_20d":   sum(trust_list[:n20]),
            "dealer_5d":   sum(dealer_list[:n5]),
            "dealer_20d":  sum(dealer_list[:n20]),
            "total_5d":    sum(total_list[:n5]),
            "total_20d":   sum(total_list[:n20]),
            "foreign_streak": _streak(foreign_list),
            "trust_streak":   _streak(trust_list),
            "days_included": n20,
            "daily": daily,
        }

    out = {
        "fetched_at": datetime.now(TAIPEI).isoformat(),
        "trading_days": order,
        "by_symbol": by_symbol,
    }
    CHIPS_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"→ chips.json: {len(by_symbol)} symbols, {len(order)} days "
          f"({CHIPS_PATH.stat().st_size // 1024} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

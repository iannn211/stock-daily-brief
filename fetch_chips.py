"""
Fetch last 20 trading days of 三大法人買賣超 from TWSE (上市) + TPEx (上櫃),
plus market-wide 籌碼 signals (外資期貨未平倉、融資餘額) needed for the
"媽媽模式" narrative analyses — every data point has a 代表什麼 + 下一步.

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
    },
    "market_chips": {
      "foreign_futures": {        # 外資台指期未平倉多空淨額口數
        "latest": {"date": "2026-04-17", "net_oi": -41151},
        "prev":   {"date": "2026-04-16", "net_oi": -39683},
        "change_1d": -1468,        # 今日 - 昨日 (變空越多 = 避險加重)
        "trend_5d": [{"d": "2026-04-17", "v": -41151}, ...]
      },
      "margin_total": {           # 融資餘額 (市場總額, NT$億)
        "latest":  {"date": "2026-04-17", "balance_yi": 1620.18, "short_lots": 26081},
        "prev":    {"date": "2026-04-16", "balance_yi": 1609.71, "short_lots": 24864},
        "change_1d_yi": 10.47      # 當日變化 (+ = 散戶進場加碼融資)
      }
    }
  }

股 (shares), not 張 (lots of 1000). Convert downstream if needed.
口 = futures contract, not share. 億 = 100M, balance_yi is already in 億.
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
# Market-wide 融資融券: TWSE MI_MARGN (aggregate row) - date format YYYYMMDD.
# selectType=MS returns the overall market balance (not per-stock).
TWSE_MARGIN_URL = ("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?"
                   "date={date}&selectType=MS&response=json")
# TAIFEX 三大法人 open interest by contract type. date format YYYY/MM/DD.
# We filter rows to commodity=TX (台指期) and 身份別=外資 for the narrative number.
TAIFEX_FOREIGN_URL = ("https://www.taifex.com.tw/cht/3/futContractsDateDown"
                      "?queryStartDate={date}&queryEndDate={date}&commodityId=TXF")

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


# --------------------------------------------------------------------------- #
#                         Market-wide chips (futures + margin)                #
# --------------------------------------------------------------------------- #
# These are the single-number headlines mom quotes: "外資期貨空單 4.1 萬口",
# "融資餘額 1620 億". Not per-stock — market state. Feeds the narrative prompt.

def _fetch_foreign_futures_net_oi(date_iso: str) -> int | None:
    """Fetch 外資 台指期 多空淨額口數 (net open-interest futures position).

    Returns positive = net long, negative = net short, None = fetch failed.
    TAIFEX serves a CSV with one row per (契約, 身份別) × (交易+未平倉). We
    want the 外資 row on 臺股期貨 contract, "未平倉" part — the net OI column.
    """
    ymd_slash = date_iso.replace("-", "/")
    url = TAIFEX_FOREIGN_URL.format(date=ymd_slash)
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"  TAIFEX futures {date_iso}: {e}", file=sys.stderr)
        return None

    # TAIFEX returns CSV in big5 (sometimes utf-8) — try both.
    text = None
    for enc in ("utf-8", "big5", "ms950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        print(f"  TAIFEX futures {date_iso}: could not decode CSV", file=sys.stderr)
        return None

    # CSV rows look like (columns vary slightly across contract types):
    #   日期,商品名稱,身份別,多方交易口數,...,空方交易口數,...,多空交易淨額口數,...,
    #         多方未平倉口數,...,空方未平倉口數,...,多空未平倉淨額口數,...
    # We grep for lines containing 外資 AND 臺股期貨, and look at the LAST
    # numeric column that represents the 多空未平倉淨額口數.
    import csv
    from io import StringIO
    try:
        rows = list(csv.reader(StringIO(text)))
    except Exception as e:
        print(f"  TAIFEX futures {date_iso}: csv parse err {e}", file=sys.stderr)
        return None

    header = None
    target_idx = None
    for row in rows:
        if not row:
            continue
        # Header row — identify the 多空未平倉淨額口數 column index.
        if header is None and "身份別" in row and any("多空" in c and "未平倉" in c for c in row):
            header = row
            # Pick the column that has BOTH 多空淨額 and 未平倉 and 口數 in its name
            for i, col in enumerate(row):
                col_clean = col.replace(" ", "").replace("\r", "").replace("\n", "")
                if "多空" in col_clean and "未平倉" in col_clean and "口數" in col_clean:
                    target_idx = i
                    break
            continue
        if header is None or target_idx is None:
            continue
        # Data row: look for 外資 on 臺股期貨 (standard TX contract).
        joined = " ".join(row)
        if ("外資" in joined) and ("臺股期貨" in joined or "台指期" in joined or "TX" in joined):
            # The 多空未平倉淨額 could be at the exact target_idx; try a few adjacent
            # columns to tolerate format drift.
            for probe in (target_idx, target_idx - 1, target_idx + 1):
                try:
                    val = _to_int(row[probe])
                    # Sanity: net OI typically ranges ±100k contracts
                    if -200000 < val < 200000 and val != 0:
                        return val
                except (IndexError, ValueError):
                    continue
    return None


def _fetch_margin_total(date_iso: str) -> dict | None:
    """Fetch market-wide 融資餘額 + 融券張數 from TWSE MI_MARGN.

    Returns {"balance_yi": 1620.18, "short_lots": 26081} — balance in 億元,
    short_lots in 張 (1000 shares per lot).
    """
    ymd = date_iso.replace("-", "")
    url = TWSE_MARGIN_URL.format(date=ymd)
    try:
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        print(f"  TWSE margin {date_iso}: {e}", file=sys.stderr)
        return None

    if data.get("stat") != "OK":
        return None

    # MI_MARGN (selectType=MS) returns:
    #   tables[0].fields = ['項目', '買進', '賣出', '現金(券)償還', '前日餘額', '今日餘額']
    #   tables[0].data rows include:
    #     ['融資(交易單位)', ...]          — shares (張) for margin buy
    #     ['融券(交易單位)', ...]          — shares (張) for short sell, col 5 = today's balance in 張
    #     ['融資金額(仟元)', ...]          — NT$ 千元, col 5 = today's 融資餘額 in 千元
    # We pull 融資金額 (money) + 融券(交易單位) (lots), convert to 億 and 張.
    tables = data.get("tables") or []
    all_rows: list = []
    if tables:
        for tbl in tables:
            all_rows.extend(tbl.get("data") or [])
    else:
        all_rows = data.get("data") or []

    margin_yi = None
    short_lots = None
    for row in all_rows:
        if not row:
            continue
        label = str(row[0] or "").strip()
        try:
            if ("融資金額" in label) and margin_yi is None:
                bal_qian = _to_int(row[5]) if len(row) > 5 else 0  # 千元
                margin_yi = round(bal_qian / 100_000, 2)           # 千元 → 億
            elif ("融券" in label) and ("交易單位" in label) and short_lots is None:
                short_lots = _to_int(row[5]) if len(row) > 5 else 0  # 張
        except Exception:
            continue

    if margin_yi is None and short_lots is None:
        return None
    return {"balance_yi": margin_yi, "short_lots": short_lots}


def _build_market_chips(trading_days: list[str]) -> dict:
    """Collect futures + margin for the most recent 5 trading days so the
    prompt can narrate "今日 vs 昨日 vs 本週" comparisons. Best-effort:
    anything that 404s or returns null just gets omitted."""
    futures_trend: list[dict] = []
    margin_trend: list[dict] = []
    for d in trading_days[:5]:  # newest first
        time.sleep(0.8)  # be polite — 3 endpoints per day
        fx = _fetch_foreign_futures_net_oi(d)
        if fx is not None:
            futures_trend.append({"d": d, "v": fx})
        mg = _fetch_margin_total(d)
        if mg is not None:
            margin_trend.append({"d": d, **mg})

    market: dict = {}

    if futures_trend:
        latest = futures_trend[0]
        prev = futures_trend[1] if len(futures_trend) > 1 else None
        block = {
            "latest": {"date": latest["d"], "net_oi": latest["v"]},
            "trend_5d": futures_trend,
        }
        if prev:
            block["prev"] = {"date": prev["d"], "net_oi": prev["v"]}
            block["change_1d"] = latest["v"] - prev["v"]
        market["foreign_futures"] = block

    if margin_trend:
        latest = margin_trend[0]
        prev = margin_trend[1] if len(margin_trend) > 1 else None
        block = {
            "latest": {
                "date": latest["d"],
                "balance_yi": latest.get("balance_yi"),
                "short_lots": latest.get("short_lots"),
            }
        }
        if prev:
            block["prev"] = {
                "date": prev["d"],
                "balance_yi": prev.get("balance_yi"),
                "short_lots": prev.get("short_lots"),
            }
            if latest.get("balance_yi") is not None and prev.get("balance_yi") is not None:
                block["change_1d_yi"] = round(latest["balance_yi"] - prev["balance_yi"], 2)
        market["margin_total"] = block

    return market


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

    # Market-wide headline 籌碼 numbers (外資期貨、融資餘額). Best-effort —
    # if TAIFEX/TWSE aggregate endpoints are down the whole file still saves.
    print("  fetching market-wide futures + margin (外資期貨未平倉、融資餘額)…",
          file=sys.stderr)
    try:
        market_chips = _build_market_chips(order)
    except Exception as e:
        print(f"  market_chips build failed: {e}", file=sys.stderr)
        market_chips = {}

    out = {
        "fetched_at": datetime.now(TAIPEI).isoformat(),
        "trading_days": order,
        "by_symbol": by_symbol,
        "market_chips": market_chips,
    }
    CHIPS_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    # Summary line
    mc_bits = []
    fx = (market_chips.get("foreign_futures") or {}).get("latest") or {}
    mg = (market_chips.get("margin_total") or {}).get("latest") or {}
    if fx.get("net_oi") is not None:
        mc_bits.append(f"外資期貨淨 OI={fx['net_oi']:+,}口")
    if mg.get("balance_yi") is not None:
        mc_bits.append(f"融資餘額={mg['balance_yi']:.1f}億")
    mc_str = f" · {' / '.join(mc_bits)}" if mc_bits else ""

    print(f"→ chips.json: {len(by_symbol)} symbols, {len(order)} days "
          f"({CHIPS_PATH.stat().st_size // 1024} KB){mc_str}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

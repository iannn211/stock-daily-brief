"""
validate_analysis.py — Sanity-check Gemini's analysis output against ground-truth data.

Runs after analyze.py and before build_dashboard.py in the daily workflow.
Produces validation_report.json which build_dashboard.py reads to render a
warning banner at the top of the dashboard if any errors were found.

The philosophy: LLMs hallucinate, especially with numbers and ticker-to-fact
mappings. A Python validator catches the mechanical mistakes (~80% of errors)
without any LLM cost. Semantic / contextual errors that Python can't see
should be flagged separately; we can add a Claude layer later if needed.

Non-blocking: always exits 0. The report gets surfaced in the UI instead of
failing the build (the user wants to see a warning banner, not a broken site).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
ANALYSES_DIR = ROOT / "analyses"
PRICES_PATH = ROOT / "prices.json"
PORTFOLIO_YAML = ROOT / "portfolio.yaml"
REPORT_PATH = ROOT / "validation_report.json"


# --------------------------------------------------------------------------- #
#                               Ground truth load                             #
# --------------------------------------------------------------------------- #

def load_ground_truth() -> dict[str, dict]:
    """Build a dict: symbol → {name, price, pe, eps, pb, roe, rev_growth,
    earnings_growth, industry, sector, pct_52w}."""
    gt: dict[str, dict] = {}

    # Prices + fundamentals from prices.json (this is the primary source)
    try:
        data = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
        for yft, rec in (data.get("prices") or {}).items():
            sym = str(rec.get("symbol") or yft.split(".")[0]).strip()
            if not sym:
                continue
            f = rec.get("fundamentals") or {}
            gt[sym] = {
                "price": rec.get("close"),
                "pe": f.get("pe_ttm"),
                "eps": f.get("eps_ttm"),
                "pb": f.get("pb"),
                "roe": f.get("roe"),
                "rev_growth": f.get("rev_growth"),
                "earnings_growth": f.get("earnings_growth"),
                "profit_margin": f.get("profit_margin"),
                "industry": f.get("industry") or "",
                "sector": f.get("sector") or "",
                "pct_52w": rec.get("pct_52w"),
            }
    except Exception as e:
        print(f"  ! couldn't load prices.json: {e}", file=sys.stderr)

    # Names from portfolio.yaml (fills gaps where we have the ticker but no name)
    try:
        import yaml
        pf = yaml.safe_load(PORTFOLIO_YAML.read_text(encoding="utf-8")) or {}
        for coll in ("holdings", "watchlist", "simulator_universe"):
            for it in pf.get(coll) or []:
                sym = str(it.get("symbol") or "").strip()
                if not sym:
                    continue
                if sym not in gt:
                    gt[sym] = {}
                if it.get("name") and not gt[sym].get("name"):
                    gt[sym]["name"] = it["name"]
    except Exception:
        pass

    # Names + roles from supply_chains.yaml (more coverage for hidden tiers;
    # role text is useful as a second-opinion industry check when yfinance
    # mislabels a small-cap TPEx stock)
    try:
        import yaml
        sc = yaml.safe_load((ROOT / "supply_chains.yaml").read_text(encoding="utf-8")) or {}
        for chain_key, chain in (sc.get("chains") or {}).items():
            chain_theme = chain.get("theme") or chain_key or ""
            for layer in chain.get("layers") or []:
                layer_name = layer.get("name") or ""
                for s in layer.get("stocks") or []:
                    sym = str(s.get("symbol") or "").strip()
                    if not sym:
                        continue
                    if sym not in gt:
                        gt[sym] = {}
                    if s.get("name") and not gt[sym].get("name"):
                        gt[sym]["name"] = s["name"]
                    # Accumulate role hints: chain theme + layer + role text
                    role_bits = [chain_theme, layer_name, s.get("role") or ""]
                    blob = " ".join(b for b in role_bits if b)
                    prev = gt[sym].get("sc_roles") or ""
                    gt[sym]["sc_roles"] = (prev + " " + blob).strip()
    except Exception:
        pass

    return gt


# --------------------------------------------------------------------------- #
#                                Check helpers                                #
# --------------------------------------------------------------------------- #

class Issue:
    __slots__ = ("severity", "category", "location", "message", "context")

    def __init__(self, severity: str, category: str, location: str, message: str,
                 context: dict | None = None) -> None:
        self.severity = severity   # "error" | "warning" | "info"
        self.category = category
        self.location = location
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity,
            "category": self.category,
            "location": self.location,
            "message": self.message,
        }
        if self.context:
            d["context"] = self.context
        return d


def _pe_tier(pe: float | None) -> str:
    """Our canonical PE tier labels, matching inspect.html."""
    if pe is None or pe <= 0:
        return "loss"
    if pe <= 15:
        return "cheap"
    if pe <= 30:
        return "fair"
    if pe <= 50:
        return "rich"
    if pe <= 100:
        return "expensive"
    return "bubble"


# Words Gemini might use → what they IMPLY about the stock. We flag hard
# contradictions only — mild disagreements stay silent to avoid noise.
PE_CHEAP_WORDS = ("便宜", "低估", "低本益比", "價值股")
PE_RICH_WORDS = ("昂貴", "泡沫", "高檔", "估值偏高", "過熱")
GROWTH_POSITIVE_WORDS = ("高成長", "營收大增", "獲利大幅成長", "營收創高", "獲利暴衝",
                         "爆發成長", "強勁成長", "盈餘翻倍")
QUALITY_WORDS = ("品質股", "獲利穩健", "優質", "績優")
MOMENTUM_HIGH_WORDS = ("股價創高", "強勢股", "漲勢凌厲", "飆漲", "連漲")


# --------------------------------------------------------------------------- #
#                                   Checks                                    #
# --------------------------------------------------------------------------- #

def check_ticker_existence(analysis: dict, gt: dict) -> list[Issue]:
    """1. Every ticker mentioned in opportunities/holdings/allocations exists."""
    issues = []
    seen_syms: set[str] = set()

    for i, opp in enumerate(analysis.get("opportunities") or []):
        for j, ls in enumerate(opp.get("lead_stocks") or []):
            sym = str(ls.get("symbol") or "").strip()
            if not sym:
                continue
            seen_syms.add(sym)
            if sym not in gt:
                issues.append(Issue(
                    "warning", "ticker-not-in-data",
                    f"opportunities[{i}] «{opp.get('theme','?')}» → lead_stocks[{j}]",
                    f"代號 {sym} ({ls.get('name','')}) 本站無資料，無法驗證數字是否正確",
                ))

    for i, al in enumerate(analysis.get("budget_allocation", {}).get("allocations") or []):
        sym = str(al.get("symbol") or "").strip()
        if sym and sym not in gt:
            issues.append(Issue(
                "error", "ticker-not-in-data",
                f"budget_allocation.allocations[{i}]",
                f"配置建議的代號 {sym} ({al.get('name','')}) 本站無資料，價格與基本面無法核對",
            ))

    for i, h in enumerate(analysis.get("holdings_analysis") or []):
        sym = str(h.get("symbol") or "").strip()
        if sym and sym not in gt:
            issues.append(Issue(
                "info", "ticker-not-in-data",
                f"holdings_analysis[{i}]",
                f"持倉 {sym} ({h.get('name','')}) 本站無資料",
            ))

    return issues


def check_ticker_name_match(analysis: dict, gt: dict) -> list[Issue]:
    """2. If Gemini gives both symbol + name, check against ground truth name."""
    issues = []

    def _compare(sym: str, claimed_name: str, loc: str) -> None:
        if not sym or not claimed_name or sym not in gt:
            return
        gt_name = (gt[sym].get("name") or "").strip()
        if not gt_name:
            return
        claimed = claimed_name.strip()
        # Allow partial match: one is substring of the other (covers
        # "2330 台積電" vs "2330 TSMC" or abbreviations like "大聯大" vs "大聯大投控")
        if claimed in gt_name or gt_name in claimed:
            return
        # Also allow small edit distance (1 char off for typos)
        if abs(len(claimed) - len(gt_name)) <= 1 and sum(
            a != b for a, b in zip(claimed, gt_name)
        ) <= 1:
            return
        issues.append(Issue(
            "error", "ticker-name-mismatch",
            loc,
            f"代號 {sym} 本站記錄為「{gt_name}」，Gemini 標成「{claimed}」— 可能 Gemini 記錯了",
            {"symbol": sym, "claimed": claimed, "actual": gt_name},
        ))

    for i, opp in enumerate(analysis.get("opportunities") or []):
        for j, ls in enumerate(opp.get("lead_stocks") or []):
            _compare(str(ls.get("symbol", "")).strip(), ls.get("name", ""),
                     f"opportunities[{i}].lead_stocks[{j}]")

    for i, al in enumerate(analysis.get("budget_allocation", {}).get("allocations") or []):
        _compare(str(al.get("symbol", "")).strip(), al.get("name", ""),
                 f"budget_allocation.allocations[{i}]")

    for i, h in enumerate(analysis.get("holdings_analysis") or []):
        _compare(str(h.get("symbol", "")).strip(), h.get("name", ""),
                 f"holdings_analysis[{i}]")

    return issues


def check_budget_math(analysis: dict) -> list[Issue]:
    """8. Sum of allocations must not exceed the budget (with small tolerance)."""
    issues = []
    ba = analysis.get("budget_allocation") or {}
    budget = ba.get("budget_twd")
    allocations = ba.get("allocations") or []
    if not budget or not allocations:
        return issues

    total_cost = 0.0
    for al in allocations:
        cost = al.get("target_cost_twd") or 0
        try:
            total_cost += float(cost)
        except (TypeError, ValueError):
            pass

    unalloc_claimed = ba.get("unallocated_twd", 0) or 0
    tolerance = 10  # NT$10 rounding

    if total_cost > budget + tolerance:
        issues.append(Issue(
            "error", "budget-overflow",
            "budget_allocation",
            f"配置加總 NT${total_cost:,.0f} 超過預算 NT${budget:,.0f}（差 NT${total_cost - budget:,.0f}）",
            {"budget": budget, "total_cost": total_cost},
        ))

    expected_unalloc = budget - total_cost
    if abs(expected_unalloc - unalloc_claimed) > tolerance:
        issues.append(Issue(
            "warning", "budget-unalloc-mismatch",
            "budget_allocation.unallocated_twd",
            f"unallocated_twd 標 NT${unalloc_claimed:,.0f}，但實際預算 − 配置 = NT${expected_unalloc:,.0f}",
            {"claimed": unalloc_claimed, "expected": expected_unalloc},
        ))

    # Check target_cost_twd ≈ target_shares × price
    for i, al in enumerate(allocations):
        shares = al.get("target_shares")
        cost = al.get("target_cost_twd")
        sym = str(al.get("symbol") or "").strip()
        # Need the actual price — we use data from prices.json (loaded in gt)
        # but gt isn't passed here. Skip this check for now; it'd need a param.

    return issues


def check_pe_claims(analysis: dict, gt: dict) -> list[Issue]:
    """4. When narrative says "便宜/低估" check PE is actually low;
          when it says "泡沫/昂貴" check PE is actually high."""
    issues = []

    def _check_narrative(text: str, symbols: list[str], loc: str, theme: str | None = None) -> None:
        if not text or not symbols:
            return
        text_lower = text
        claims_cheap = any(w in text_lower for w in PE_CHEAP_WORDS)
        claims_rich = any(w in text_lower for w in PE_RICH_WORDS)

        for sym in symbols:
            if sym not in gt:
                continue
            pe = gt[sym].get("pe")
            if pe is None or pe <= 0:
                continue
            tier = _pe_tier(pe)

            # Egregious: says "便宜" but PE is "expensive/bubble"
            if claims_cheap and tier in ("expensive", "bubble"):
                issues.append(Issue(
                    "error", "pe-claim-contradicts-data",
                    loc,
                    f"{sym} 描述提到「便宜/低估」，但實際 PE={pe:.1f}（屬於昂貴/泡沫區）"
                    + (f" — 題材「{theme}」" if theme else ""),
                    {"symbol": sym, "pe": pe, "tier": tier},
                ))
            # Egregious: says "泡沫/昂貴" but PE is "cheap/fair"
            if claims_rich and tier in ("cheap", "fair") and pe < 25:
                issues.append(Issue(
                    "warning", "pe-claim-contradicts-data",
                    loc,
                    f"{sym} 描述提到「昂貴/泡沫」，但實際 PE={pe:.1f}（屬於合理區）"
                    + (f" — 題材「{theme}」" if theme else ""),
                    {"symbol": sym, "pe": pe, "tier": tier},
                ))

    # Topics
    for i, t in enumerate(analysis.get("topics") or []):
        syms = [str(s) for s in (t.get("tickers") or []) if s]
        _check_narrative(t.get("narrative") or "", syms, f"topics[{i}]", t.get("title"))

    # Opportunities
    for i, o in enumerate(analysis.get("opportunities") or []):
        syms = [str(ls.get("symbol") or "") for ls in (o.get("lead_stocks") or [])]
        syms = [s for s in syms if s]
        _check_narrative(o.get("why") or "", syms, f"opportunities[{i}].why", o.get("theme"))
        _check_narrative(o.get("headline") or "", syms, f"opportunities[{i}].headline", o.get("theme"))

    return issues


def check_growth_claims(analysis: dict, gt: dict) -> list[Issue]:
    """6/7. If narrative claims 高成長 / 營收創高, the lead stocks' actual
    rev_growth or earnings_growth should be positive (allow one of them
    being unknown)."""
    issues = []

    def _check(text: str, symbols: list[str], loc: str, theme: str | None = None) -> None:
        if not text or not symbols:
            return
        if not any(w in text for w in GROWTH_POSITIVE_WORDS):
            return
        for sym in symbols:
            if sym not in gt:
                continue
            rev = gt[sym].get("rev_growth")
            eps_g = gt[sym].get("earnings_growth")
            # If BOTH are known and BOTH are negative, flag
            if rev is not None and eps_g is not None and rev < 0 and eps_g < 0:
                issues.append(Issue(
                    "error", "growth-claim-contradicts-data",
                    loc,
                    f"{sym} 描述提到「高成長/營收創高」，但實際營收 YoY {rev*100:+.1f}% 且 EPS YoY {eps_g*100:+.1f}% — 都在衰退"
                    + (f" — 題材「{theme}」" if theme else ""),
                    {"symbol": sym, "rev_growth": rev, "earnings_growth": eps_g},
                ))
            # Soft: only rev known and rev < -5%, flag as warning
            elif rev is not None and rev < -0.05 and eps_g is None:
                issues.append(Issue(
                    "warning", "growth-claim-contradicts-data",
                    loc,
                    f"{sym} 描述提到「高成長」，但營收 YoY {rev*100:+.1f}% — 可能是錯判"
                    + (f" — 題材「{theme}」" if theme else ""),
                    {"symbol": sym, "rev_growth": rev},
                ))

    for i, o in enumerate(analysis.get("opportunities") or []):
        syms = [str(ls.get("symbol") or "") for ls in (o.get("lead_stocks") or [])]
        syms = [s for s in syms if s]
        _check(o.get("why") or "", syms, f"opportunities[{i}].why", o.get("theme"))

    for i, t in enumerate(analysis.get("topics") or []):
        syms = [str(s) for s in (t.get("tickers") or []) if s]
        _check(t.get("narrative") or "", syms, f"topics[{i}]", t.get("title"))

    return issues


def check_quality_claims(analysis: dict, gt: dict) -> list[Issue]:
    """7. If narrative calls a stock 品質股/獲利穩健, ROE should be > 8%
    and EPS should be positive."""
    issues = []

    def _check(text: str, symbols: list[str], loc: str, theme: str | None = None) -> None:
        if not text or not symbols:
            return
        if not any(w in text for w in QUALITY_WORDS):
            return
        for sym in symbols:
            if sym not in gt:
                continue
            eps = gt[sym].get("eps")
            roe = gt[sym].get("roe")
            bad = []
            if eps is not None and eps < 0:
                bad.append(f"EPS {eps:.2f}（虧損）")
            if roe is not None and roe < 0.05:
                bad.append(f"ROE {roe*100:.1f}%（平庸）")
            if bad:
                issues.append(Issue(
                    "warning", "quality-claim-contradicts-data",
                    loc,
                    f"{sym} 描述提到「品質股/獲利穩健」，但實際 " + "、".join(bad)
                    + (f" — 題材「{theme}」" if theme else ""),
                    {"symbol": sym, "eps": eps, "roe": roe},
                ))

    for i, o in enumerate(analysis.get("opportunities") or []):
        syms = [str(ls.get("symbol") or "") for ls in (o.get("lead_stocks") or [])]
        syms = [s for s in syms if s]
        _check(o.get("why") or "", syms, f"opportunities[{i}].why", o.get("theme"))

    return issues


def check_head_to_head_no_cop_out(analysis: dict) -> list[Issue]:
    """9. No "兩檔都可以" conclusions. We explicitly forbid that in the prompt;
    this catches if Gemini slips one through anyway."""
    issues = []
    cop_out_phrases = ("兩檔都可以", "兩檔都值得", "兩檔都買", "都可以買", "任一檔")

    for i, o in enumerate(analysis.get("opportunities") or []):
        h2h = o.get("head_to_head") or {}
        verdict = (h2h.get("verdict") or "") + " " + (h2h.get("pick_rationale") or "")
        for phrase in cop_out_phrases:
            if phrase in verdict:
                issues.append(Issue(
                    "error", "h2h-cop-out-conclusion",
                    f"opportunities[{i}].head_to_head",
                    f"對比卡結論出現「{phrase}」— 這個題材提示要 pick 一個，不能兩檔都推",
                ))
                break

    return issues


def check_theme_industry_sanity(analysis: dict, gt: dict) -> list[Issue]:
    """3 (softened). For each opportunity, check lead stocks' industry/sector
    isn't obviously unrelated to theme. Uses rough keyword matching; this is
    not precise but should catch "food company in AI theme" type errors.

    Anti-false-positive: if the stock is listed in supply_chains.yaml with
    a role text that matches theme keywords, trust that over yfinance (which
    often mislabels TPEx small caps — e.g. 5475 德宏 is labelled "Textile
    Manufacturing" by yfinance but is actually in CCL materials). Include
    the supply_chains role in the warning for human verification."""
    issues = []

    # Theme keywords → industry/sector words that should appear (loosely).
    # Each value also includes TW-specific keywords that might appear in the
    # supply_chains role text (e.g. "散熱" / "液冷" / "CCL") so the cross-check
    # below can find agreement.
    THEME_HINTS = {
        ("AI", "伺服器", "算力"): ("technology", "semiconductor", "electronic", "computer",
                                   "半導體", "電子", "資訊", "通訊", "伺服器", "AI"),
        ("半導體", "晶片", "IC"): ("semiconductor", "technology", "electronic",
                                   "半導體", "電子", "晶圓", "IC"),
        ("PCB", "載板", "CCL"): ("technology", "electronic", "materials",
                                 "電子", "電子零組件", "半導體", "PCB", "載板", "CCL",
                                 "玻纖", "銅箔", "樹脂"),
        ("光通訊", "CPO", "矽光子"): ("technology", "electronic", "communication",
                                     "光電", "電子", "通訊", "光纖", "CPO", "矽光子"),
        ("散熱", "液冷"): ("technology", "electronic", "industrial",
                           "電子", "電機", "冷卻", "熱", "散熱", "液冷", "均溫板", "水冷"),
        ("被動元件", "MLCC", "電容"): ("technology", "electronic", "electrical",
                                      "電子", "電機", "被動", "MLCC", "電容", "電阻"),
        ("工業電腦",): ("technology", "computer", "industrial", "電腦", "工業", "電子", "IPC"),
        ("航太", "太空"): ("aerospace", "industrial", "航太", "國防", "工業", "衛星"),
        ("生技", "醫療"): ("healthcare", "pharmaceutical", "biotech", "醫", "生技", "製藥"),
        ("金融",): ("financial", "bank", "insurance", "金融", "銀行", "證券"),
    }

    for i, o in enumerate(analysis.get("opportunities") or []):
        theme = o.get("theme") or ""
        # Match theme keywords
        matched_industries = None
        for keys, industries in THEME_HINTS.items():
            if any(k in theme for k in keys):
                matched_industries = industries
                break
        if matched_industries is None:
            continue  # unknown theme — skip industry check

        for j, ls in enumerate(o.get("lead_stocks") or []):
            sym = str(ls.get("symbol") or "").strip()
            if not sym or sym not in gt:
                continue

            yf_blob = (gt[sym].get("industry", "") + " " + gt[sym].get("sector", "")).lower()
            sc_blob = (gt[sym].get("sc_roles") or "").lower()

            if not yf_blob.strip() and not sc_blob.strip():
                continue  # no data at all — can't check

            hints_lower = tuple(w.lower() for w in matched_industries)
            yf_hit = bool(yf_blob.strip()) and any(w in yf_blob for w in hints_lower)
            sc_hit = bool(sc_blob.strip()) and any(w in sc_blob for w in hints_lower)

            # supply_chains.yaml is our editorial source of truth; if IT agrees
            # with the theme, trust it and suppress the warning regardless of
            # yfinance's (often stale) industry label.
            if sc_hit:
                continue
            if yf_hit:
                continue

            # Neither source agrees — flag. Include supply_chains role text
            # when available so user can judge quickly.
            sc_role_hint = gt[sym].get("sc_roles") or ""
            sc_role_hint = sc_role_hint.strip()
            if len(sc_role_hint) > 60:
                sc_role_hint = sc_role_hint[:60] + "…"

            extra = ""
            if sc_role_hint:
                extra = f"；supply_chains 登記的角色為「{sc_role_hint}」"

            issues.append(Issue(
                "warning", "theme-industry-mismatch",
                f"opportunities[{i}].lead_stocks[{j}]",
                f"題材「{theme}」的 lead stock {sym} ({ls.get('name','')}) "
                f"產業標為「{gt[sym].get('industry') or gt[sym].get('sector') or '未知'}」"
                f"{extra}— 跟題材看起來不符，請人工確認（可能 Gemini 劃錯，也可能資料源標籤過時）",
                {"symbol": sym, "theme": theme, "industry": gt[sym].get("industry"),
                 "sector": gt[sym].get("sector"), "sc_roles": gt[sym].get("sc_roles")},
            ))

    return issues


def check_empty_narratives(analysis: dict) -> list[Issue]:
    """Soft: flag empty / placeholder narrative strings."""
    issues = []

    def _flag(text: str, loc: str) -> None:
        if not text:
            issues.append(Issue("warning", "empty-field", loc, "此欄位為空"))
            return
        if len(text.strip()) < 10:
            issues.append(Issue("warning", "empty-field", loc, f"內容過短（{len(text.strip())} 字）：{text[:20]}"))

    for i, o in enumerate(analysis.get("opportunities") or []):
        _flag(o.get("why") or "", f"opportunities[{i}].why")
        _flag(o.get("headline") or "", f"opportunities[{i}].headline")

    return issues


# --------------------------------------------------------------------------- #
#                                   Runner                                    #
# --------------------------------------------------------------------------- #

def validate(analysis: dict, gt: dict) -> list[Issue]:
    """Run all checks."""
    issues: list[Issue] = []
    for fn in (
        check_ticker_existence,
        check_ticker_name_match,
        check_budget_math,
        check_pe_claims,
        check_growth_claims,
        check_quality_claims,
        check_head_to_head_no_cop_out,
        check_theme_industry_sanity,
        check_empty_narratives,
    ):
        try:
            # Some checks need gt, some don't
            if fn.__code__.co_argcount == 2:
                issues.extend(fn(analysis, gt))
            else:
                issues.extend(fn(analysis))
        except Exception as e:
            issues.append(Issue(
                "info", "validator-internal-error",
                fn.__name__,
                f"內部檢查失敗（不影響 Gemini 輸出）：{e}",
            ))
    return issues


def pick_analysis_path() -> Path | None:
    """Return the latest analysis JSON file, or None."""
    if not ANALYSES_DIR.exists():
        return None
    candidates = sorted(ANALYSES_DIR.glob("*.json"))
    return candidates[-1] if candidates else None


def main() -> int:
    path = pick_analysis_path()
    if not path:
        print("  ! no analyses/*.json found; nothing to validate", file=sys.stderr)
        # Still write an empty report so build_dashboard can read it
        REPORT_PATH.write_text(json.dumps({
            "validated_at": datetime.now(TAIPEI).isoformat(),
            "analysis_file": None,
            "issues": [],
            "summary": {"errors": 0, "warnings": 0, "infos": 0, "total": 0},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    analysis = json.loads(path.read_text(encoding="utf-8"))
    gt = load_ground_truth()

    issues = validate(analysis, gt)

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")

    report = {
        "validated_at": datetime.now(TAIPEI).isoformat(),
        "analysis_file": str(path.relative_to(ROOT)),
        "analysis_date": analysis.get("date"),
        "analysis_model": analysis.get("model"),
        "issues": [i.to_dict() for i in issues],
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "total": len(issues),
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print a crisp summary
    print(f"[validate] {path.name} → {errors} errors, {warnings} warnings, {infos} infos",
          file=sys.stderr)
    for i in issues:
        icon = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(i.severity, "•")
        print(f"  {icon} [{i.category}] {i.location}: {i.message}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Industry speed classification for provenance staleness.

Classifies every (sector, industry) pair in the portfolio into three speed
classes — fast / medium / slow — each with its own staleness thresholds
defined in `provenance.STALENESS_THRESHOLDS`.

Rationale (see Spec fix-08 §Staleness)
--------------------------------------
    fast    — semi / software / biotech: news & analyst updates every few days.
              A target price from 14 days ago is already stale.

    medium  — PC/server tech, electronic components, chemicals, industrial
              machinery, autos: quarterly product / order cycles. 30-60 days
              before we worry.

    slow    — banks, insurance, utilities, food/grocery, traditional goods:
              report once a quarter; news flow is sparse. 60-120 days OK.

The classifier accepts BOTH:
    - English yfinance labels (e.g. "Semiconductors")
    - Chinese theme strings from Gemini (e.g. "AI 伺服器散熱 / 液冷")

When given a Chinese theme, we substring-match against `_CN_KEYWORDS` to
infer speed. This is a best-effort fallback — exact English labels from
yfinance are preferred.

Unknown inputs default to `medium`.
"""
from __future__ import annotations

from typing import Literal

Speed = Literal["fast", "medium", "slow"]

# yfinance English "industry" labels → speed class.
# Covers the 23 unique labels in the current portfolio universe + a few
# forward-looking additions (biotech, drug manufacturers, etc) in case the
# universe grows.
_INDUSTRY_SPEED: dict[str, Speed] = {
    # --- fast: semiconductors, software, biotech
    "Semiconductors":                     "fast",
    "Semiconductor Equipment & Materials":"fast",
    "Software - Infrastructure":          "fast",
    "Software - Application":             "fast",
    "Biotechnology":                      "fast",
    "Drug Manufacturers - General":       "fast",
    "Drug Manufacturers - Specialty & Generic": "fast",
    "Information Technology Services":    "fast",

    # --- medium: 2nd-tier tech, industrial cyclicals, consumer cyclicals
    "Electronic Components":              "medium",
    "Computer Hardware":                  "medium",
    "Communication Equipment":            "medium",
    "Consumer Electronics":               "medium",
    "Specialty Industrial Machinery":     "medium",
    "Electrical Equipment & Parts":       "medium",
    "Tools & Accessories":                "medium",
    "Metal Fabrication":                  "medium",
    "Specialty Chemicals":                "medium",
    "Chemicals":                          "medium",
    "Auto Manufacturers":                 "medium",
    "Auto Parts":                         "medium",
    "Auto & Truck Dealerships":           "medium",
    "Airlines":                           "medium",
    "Marine Shipping":                    "medium",
    "Steel":                              "medium",
    "Aluminum":                           "medium",
    "Building Materials":                 "medium",
    "Construction":                       "medium",
    "Aerospace & Defense":                "medium",

    # --- slow: financials, staples, utilities, property, traditional goods
    "Banks - Regional":                   "slow",
    "Banks - Diversified":                "slow",
    "Insurance - Life":                   "slow",
    "Insurance - Property & Casualty":    "slow",
    "Insurance - Diversified":            "slow",
    "Asset Management":                   "slow",
    "Credit Services":                    "slow",
    "Packaged Foods":                     "slow",
    "Grocery Stores":                     "slow",
    "Beverages - Non-Alcoholic":          "slow",
    "Beverages - Brewers":                "slow",
    "Tobacco":                            "slow",
    "Household & Personal Products":      "slow",
    "Utilities - Regulated Electric":     "slow",
    "Utilities - Regulated Gas":          "slow",
    "REIT - Industrial":                  "slow",
    "REIT - Residential":                 "slow",
    "REIT - Office":                      "slow",
    "Apparel Manufacturing":              "slow",
    "Footwear & Accessories":             "slow",
    "Leisure":                            "slow",
    "Restaurants":                        "slow",
    "Lodging":                            "slow",
    "Farm Products":                      "slow",
}

# Chinese keyword → speed class (fallback for Gemini theme strings).
# Checked as substring against the input. First match wins, so order by
# specificity.
_CN_KEYWORDS: list[tuple[str, Speed]] = [
    # fast
    ("半導體", "fast"),
    ("晶圓", "fast"),
    ("IC 設計", "fast"),
    ("矽光子", "fast"),
    ("CoWoS", "fast"),
    ("AI 晶片", "fast"),
    ("AI晶片", "fast"),
    ("AI 伺服器", "fast"),  # server cycle is fast
    ("伺服器", "fast"),
    ("CPO", "fast"),
    ("光通訊", "fast"),
    ("生技", "fast"),
    ("新藥", "fast"),
    ("軟體", "fast"),
    ("雲端", "fast"),

    # medium (2nd-tier tech & industrials)
    ("PCB", "medium"),
    ("CCL", "medium"),
    ("載板", "medium"),
    ("被動元件", "medium"),
    ("封測", "medium"),
    ("電子零組件", "medium"),
    ("散熱", "medium"),        # 2nd-order AI beneficiary
    ("液冷", "medium"),
    ("機器人", "medium"),
    ("電動車", "medium"),
    ("汽車", "medium"),
    ("航運", "medium"),
    ("鋼鐵", "medium"),
    ("化工", "medium"),
    ("太陽能", "medium"),
    ("風電", "medium"),

    # slow
    ("金融", "slow"),
    ("銀行", "slow"),
    ("保險", "slow"),
    ("壽險", "slow"),
    ("食品", "slow"),
    ("生活必需", "slow"),
    ("民生", "slow"),
    ("公用事業", "slow"),
    ("電信", "slow"),
    ("REIT", "slow"),
    ("觀光", "slow"),
    ("餐飲", "slow"),
    ("紡織", "slow"),
]


def speed_of(industry: str | None = None,
             theme_hint: str | None = None,
             sector: str | None = None) -> Speed:
    """Classify an industry/theme into fast/medium/slow.

    Resolution order:
      1. Exact English industry label (most reliable).
      2. Chinese keyword match against theme_hint.
      3. Sector-level heuristic (fallback).
      4. Default "medium".

    Never raises — unknown always falls back to medium.
    """
    # 1. yfinance English industry
    if industry and industry in _INDUSTRY_SPEED:
        return _INDUSTRY_SPEED[industry]

    # 2. Chinese theme keyword
    if theme_hint:
        for kw, speed in _CN_KEYWORDS:
            if kw in theme_hint:
                return speed

    # 2b. Chinese keyword against industry (sometimes Gemini mixes zh/en)
    if industry:
        for kw, speed in _CN_KEYWORDS:
            if kw in industry:
                return speed

    # 3. Sector-level fallback
    if sector:
        s = sector.lower()
        if "technology" in s or "semi" in s:
            return "fast"
        if "financial" in s or "utilities" in s or "consumer defensive" in s \
           or "real estate" in s:
            return "slow"
        # Industrials, Materials, Cyclicals → medium
        return "medium"

    # 4. Default
    return "medium"


# ---------------------------------------------------------- self-test driver --

if __name__ == "__main__":
    # Known mappings
    assert speed_of("Semiconductors") == "fast"
    assert speed_of("Electronic Components") == "medium"
    assert speed_of("Banks - Regional") == "slow"
    assert speed_of("Packaged Foods") == "slow"

    # Chinese theme fallback
    assert speed_of(theme_hint="AI 伺服器散熱 / 液冷") == "fast"
    # (伺服器 matches first — fast, which is correct for AI-server theme)
    assert speed_of(theme_hint="PCB / CCL 材料上游漲價") == "medium"
    assert speed_of(theme_hint="金融股比價行情") == "slow"

    # Sector fallback
    assert speed_of(sector="Technology") == "fast"
    assert speed_of(sector="Financial Services") == "slow"
    assert speed_of(sector="Industrials") == "medium"

    # Unknown → medium
    assert speed_of("Unknown Industry 2026") == "medium"
    assert speed_of() == "medium"

    # yfinance Eng wins over zh theme
    # (a semi stock forced into food theme — should still be fast)
    assert speed_of("Semiconductors", theme_hint="食品") == "fast"

    print("provenance_speed_map.py: ✓ smoke tests passed")

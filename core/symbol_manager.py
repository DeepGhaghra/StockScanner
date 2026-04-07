"""
core/symbol_manager.py — Auto-fetch & cache NSE index constituents (7-day TTL)

Data source: NSE India public archive CSVs (no auth required)
Fyers symbol format: NSE:{SYMBOL}-EQ
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
META_FILE = os.path.join(CACHE_DIR, "index_meta.json")

CACHE_TTL_DAYS = 7

# ─── NSE Index CSV Sources ────────────────────────────────────────────────────
# All URLs from NSE India public archives — no authentication required
NSE_BASE = "https://archives.nseindia.com/content/indices"

INDEX_SOURCES: dict[str, str] = {
    # Broad Market
    "Nifty 50":                f"{NSE_BASE}/ind_nifty50list.csv",
    "Nifty Next 50":           f"{NSE_BASE}/ind_niftynext50list.csv",
    "Nifty 100":               f"{NSE_BASE}/ind_nifty100list.csv",
    "Nifty 200":               f"{NSE_BASE}/ind_nifty200list.csv",
    "Nifty 500":               f"{NSE_BASE}/ind_nifty500list.csv",
    # Midcap / Smallcap
    "Nifty Midcap Select":     f"{NSE_BASE}/ind_niftymidcapselect.csv",      # falls back to static
    "Nifty Midcap 50":         f"{NSE_BASE}/ind_niftymidcap50list.csv",
    "Nifty Midcap 100":        f"{NSE_BASE}/ind_niftymidcap100list.csv",
    "Nifty Midcap 150":        f"{NSE_BASE}/ind_niftymidcap150list.csv",
    "Nifty Smallcap 50":       f"{NSE_BASE}/ind_niftysmallcap50list.csv",
    "Nifty Smallcap 100":      f"{NSE_BASE}/ind_niftysmallcap100list.csv",
    "Nifty Smallcap 250":      f"{NSE_BASE}/ind_niftysmallcap250list.csv",
    "Nifty MidSmallcap 400":   f"{NSE_BASE}/ind_niftymidsmallcap400list.csv",
    # Sectoral — Banking
    "Bank Nifty":              f"{NSE_BASE}/ind_niftybanklist.csv",
    "Fin Nifty":               f"{NSE_BASE}/ind_niftyfinancialserviceslist.csv",
    "Nifty PSU Bank":          f"{NSE_BASE}/ind_niftypsubanklist.csv",
    "Nifty Private Bank":      f"{NSE_BASE}/ind_niftyprivatebankindex.csv",   # falls back to static
    # Sectoral — Others
    "Nifty IT":                f"{NSE_BASE}/ind_niftyitlist.csv",
    "Nifty Pharma":            f"{NSE_BASE}/ind_niftypharmalist.csv",
    "Nifty Auto":              f"{NSE_BASE}/ind_niftyautolist.csv",
    "Nifty FMCG":              f"{NSE_BASE}/ind_niftyfmcglist.csv",
    "Nifty Metal":             f"{NSE_BASE}/ind_niftymetallist.csv",
    "Nifty Realty":            f"{NSE_BASE}/ind_niftyrealtylist.csv",
    "Nifty Energy":            f"{NSE_BASE}/ind_niftyenergylist.csv",
    "Nifty Healthcare":        f"{NSE_BASE}/ind_niftyhealthcarelist.csv",
    "Nifty Infrastructure":    f"{NSE_BASE}/ind_niftyinfralist.csv",
    "Nifty India Defence":     f"{NSE_BASE}/ind_niftyindiadefence.csv",       # falls back to static
    "Nifty CPSE":              f"{NSE_BASE}/ind_niftycpselist.csv",
    "Nifty MNC":               f"{NSE_BASE}/ind_niftymnclist.csv",
}

# Fallback static data for indices where NSE archive CSV is not publicly available.
# These are kept up-to-date with official NSE index composition (semi-annual review).
_STATIC_FALLBACK: dict[str, list[str]] = {
    # ── Fin Nifty (25 stocks) ───────────────────────────────────────────────
    "Fin Nifty": [
        "NSE:AXISBANK-EQ", "NSE:BAJFINANCE-EQ", "NSE:BAJAJFINSV-EQ",
        "NSE:CHOLAFIN-EQ", "NSE:HDFCBANK-EQ", "NSE:HDFCLIFE-EQ",
        "NSE:ICICIBANK-EQ", "NSE:ICICIGI-EQ", "NSE:KOTAKBANK-EQ",
        "NSE:MUTHOOTFIN-EQ", "NSE:RECLTD-EQ", "NSE:SBIN-EQ",
        "NSE:SBICARD-EQ", "NSE:SBILIFE-EQ", "NSE:SHRIRAMFIN-EQ",
        "NSE:PFC-EQ", "NSE:LIC-EQ", "NSE:JIOFIN-EQ",
        "NSE:ABCAPITAL-EQ", "NSE:IRFC-EQ",
        "NSE:M&MFIN-EQ", "NSE:MANAPPURAM-EQ", "NSE:POONAWALLA-EQ",
        "NSE:ANGELONE-EQ", "NSE:360ONE-EQ",
    ],
    # ── Nifty Private Bank (10 stocks) ──────────────────────────────────────
    "Nifty Private Bank": [
        "NSE:AXISBANK-EQ", "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ",
        "NSE:KOTAKBANK-EQ", "NSE:INDUSINDBK-EQ", "NSE:FEDERALBNK-EQ",
        "NSE:YESBANK-EQ", "NSE:BANDHANBNK-EQ", "NSE:RBLBANK-EQ",
        "NSE:IDFCFIRSTB-EQ",
    ],
    # ── Nifty Midcap Select (25 stocks) ─────────────────────────────────────
    "Nifty Midcap Select": [
        "NSE:AUBANK-EQ", "NSE:ABCAPITAL-EQ", "NSE:ALKEM-EQ",
        "NSE:ASTRAL-EQ", "NSE:BHEL-EQ", "NSE:COFORGE-EQ",
        "NSE:CUMMINSIND-EQ", "NSE:DELHIVERY-EQ", "NSE:FEDERALBNK-EQ",
        "NSE:GLENMARK-EQ", "NSE:GODREJPROP-EQ", "NSE:IDFCFIRSTB-EQ",
        "NSE:INDHOTEL-EQ", "NSE:KALYANKJIL-EQ", "NSE:KPITTECH-EQ",
        "NSE:LICI-EQ", "NSE:LUPIN-EQ", "NSE:MAXHEALTH-EQ",
        "NSE:MPHASIS-EQ", "NSE:NMDC-EQ", "NSE:OBEROIRLTY-EQ",
        "NSE:PERSISTENT-EQ", "NSE:PHOENIXLTD-EQ", "NSE:SUPREMEIND-EQ",
        "NSE:TORNTPHARM-EQ",
    ],
    # ── Nifty India Defence (thematic, ~20 stocks) ──────────────────────────
    "Nifty India Defence": [
        "NSE:BEL-EQ", "NSE:HAL-EQ", "NSE:BHARATFORG-EQ",
        "NSE:SOLARINDS-EQ", "NSE:MAZDOCK-EQ", "NSE:BDL-EQ",
        "NSE:COCHINSHIP-EQ", "NSE:DATAPATTNS-EQ", "NSE:MTARTECH-EQ",
        "NSE:ASTRAMICRO-EQ", "NSE:BEML-EQ", "NSE:DYNAMATECH-EQ",
        "NSE:AXISCADES-EQ", "NSE:ZENTEC-EQ", "NSE:GRSE-EQ",
        "NSE:MIDHANI-EQ", "NSE:APOLLOMICRO-EQ", "NSE:PARASDEF-EQ",
        "NSE:IDEAFORGE-EQ", "NSE:ELCID-EQ",
    ],
}

# HTTP headers mimicking a browser (NSE sometimes blocks bare Python requests)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# ─── Cache Utilities ──────────────────────────────────────────────────────────

def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_file(index_name: str) -> str:
    safe = index_name.replace(" ", "_").replace("/", "_").lower()
    return os.path.join(CACHE_DIR, f"{safe}.json")


def _load_meta() -> dict:
    if os.path.exists(META_FILE):
        with open(META_FILE) as f:
            return json.load(f)
    return {}


def _save_meta(meta: dict):
    _ensure_cache_dir()
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)


def _is_cache_valid(index_name: str) -> bool:
    meta = _load_meta()
    if index_name not in meta:
        return False
    fetched_at = datetime.fromisoformat(meta[index_name]["fetched_at"])
    return datetime.now() < fetched_at + timedelta(days=CACHE_TTL_DAYS)


def _read_cache(index_name: str) -> Optional[list[str]]:
    path = _cache_file(index_name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    return data.get("symbols", [])


def _write_cache(index_name: str, symbols: list[str]):
    _ensure_cache_dir()
    path = _cache_file(index_name)
    with open(path, "w") as f:
        json.dump({
            "index": index_name,
            "count": len(symbols),
            "fetched_at": datetime.now().isoformat(),
            "symbols": symbols,
        }, f, indent=2)

    meta = _load_meta()
    meta[index_name] = {
        "fetched_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(days=CACHE_TTL_DAYS)).isoformat(),
        "count": len(symbols),
    }
    _save_meta(meta)


# ─── NSE Fetch Logic ──────────────────────────────────────────────────────────

def _fetch_nse_csv(url: str, timeout: int = 15) -> Optional[pd.DataFrame]:
    """Download NSE CSV and return as DataFrame"""
    try:
        # Use a session with retries
        session = requests.Session()
        # First hit NSE homepage to get cookies
        try:
            session.get("https://www.nseindia.com", headers=_HEADERS, timeout=5)
        except Exception:
            pass  # cookie fetch may fail, continue anyway

        resp = session.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()

        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        df.columns = df.columns.str.strip()
        return df

    except requests.RequestException as e:
        logger.warning(f"[SymbolManager] Failed to fetch {url}: {e}")
        return None


def _nse_symbols_to_fyers(nse_symbols: list[str]) -> list[str]:
    """Convert NSE symbol list to Fyers format: RELIANCE → NSE:RELIANCE-EQ"""
    fyers_symbols = []
    for sym in nse_symbols:
        sym = sym.strip().upper()
        if sym:
            fyers_symbols.append(f"NSE:{sym}-EQ")
    return fyers_symbols


def _parse_nse_csv(df: pd.DataFrame) -> Optional[list[str]]:
    """Extract symbol list from NSE index CSV"""
    # NSE CSVs have a 'Symbol' column (strip whitespace)
    symbol_col = None
    for col in df.columns:
        if col.strip().lower() == "symbol":
            symbol_col = col
            break

    if symbol_col is None:
        logger.warning(f"[SymbolManager] 'Symbol' column not found. Columns: {list(df.columns)}")
        return None

    symbols = df[symbol_col].dropna().str.strip().tolist()
    return [s for s in symbols if s]


# ─── Public API ───────────────────────────────────────────────────────────────

def get_available_indices() -> list[str]:
    """Returns list of all supported index names"""
    return list(INDEX_SOURCES.keys())


def get_symbols(index_name: str, force_refresh: bool = False) -> list[str]:
    """
    Get Fyers-format symbols for an index.
    Priority:
      1. Valid cache (< 7 days old) — served immediately, no network call
      2. Fresh fetch from NSE archive CSV
      3. Stale cache (expired but exists)
      4. Static fallback data (for indices without a public CSV)

    Returns: list of symbols like ['NSE:RELIANCE-EQ', 'NSE:TCS-EQ', ...]
    """
    if index_name not in INDEX_SOURCES:
        logger.error(f"[SymbolManager] Unknown index: {index_name}")
        return []

    # 1. Serve from valid cache
    if not force_refresh and _is_cache_valid(index_name):
        cached = _read_cache(index_name)
        if cached:
            return cached

    # 2. Fetch fresh from NSE
    url = INDEX_SOURCES[index_name]
    logger.info(f"[SymbolManager] Fetching {index_name} from NSE...")

    df = _fetch_nse_csv(url)
    if df is not None:
        nse_symbols = _parse_nse_csv(df)
        if nse_symbols:
            fyers_symbols = _nse_symbols_to_fyers(nse_symbols)
            _write_cache(index_name, fyers_symbols)
            logger.info(f"[SymbolManager] Cached {len(fyers_symbols)} symbols for {index_name}")
            return fyers_symbols

    logger.warning(f"[SymbolManager] NSE fetch failed for '{index_name}'.")

    # 3. Stale cache fallback
    cached = _read_cache(index_name)
    if cached:
        logger.warning(f"[SymbolManager] Using stale cache ({len(cached)} symbols)")
        return cached

    # 4. Static fallback
    if index_name in _STATIC_FALLBACK:
        static = _STATIC_FALLBACK[index_name]
        logger.warning(f"[SymbolManager] Using static fallback ({len(static)} symbols)")
        # Save to cache so it gets a TTL too
        _write_cache(index_name, static)
        return static

    return []



def refresh_all_indices(progress_callback=None) -> dict[str, int]:
    """
    Force-refresh all index symbol lists from NSE.
    Returns dict of {index_name: symbol_count}
    """
    results = {}
    total = len(INDEX_SOURCES)
    for i, index_name in enumerate(INDEX_SOURCES.keys()):
        if progress_callback:
            progress_callback(i + 1, total, index_name)
        symbols = get_symbols(index_name, force_refresh=True)
        results[index_name] = len(symbols)
        time.sleep(0.5)  # polite delay between NSE requests
    return results


def get_cache_status() -> list[dict]:
    """
    Returns status info for all indices.
    """
    meta = _load_meta()
    rows = []
    for index_name in INDEX_SOURCES.keys():
        if index_name in meta:
            fetched = datetime.fromisoformat(meta[index_name]["fetched_at"])
            expires = datetime.fromisoformat(meta[index_name]["expires_at"])
            is_valid = datetime.now() < expires
            days_left = max(0, (expires - datetime.now()).days)
            rows.append({
                "Index": index_name,
                "Stocks": meta[index_name].get("count", 0),
                "Last Fetched": fetched.strftime("%Y-%m-%d %H:%M"),
                "Expires": expires.strftime("%Y-%m-%d"),
                "Status": f"✅ Valid ({days_left}d left)" if is_valid else "⚠️ Expired",
            })
        else:
            rows.append({
                "Index": index_name,
                "Stocks": 0,
                "Last Fetched": "Never",
                "Expires": "—",
                "Status": "❌ Not fetched",
            })
    return rows


def get_custom_watchlist_symbols() -> list[str]:
    """Load from custom watchlist CSV"""
    path = os.path.join(BASE_DIR, "data", "custom_watchlist.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        return []
    return df["symbol"].dropna().str.strip().tolist()

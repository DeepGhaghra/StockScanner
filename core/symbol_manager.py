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

CACHE_TTL_DAYS = 90

# ─── NSE Index CSV Sources ────────────────────────────────────────────────────
# All URLs from NSE India public archives — no authentication required
NSE_BASE = "https://archives.nseindia.com/content/indices"

INDEX_SOURCES: dict[str, str] = {
    # ─── Broad Market ──────────────────────────────────────────────────────────
    "Nifty 50":                f"{NSE_BASE}/ind_nifty50list.csv",
    "Nifty Next 50":           f"{NSE_BASE}/ind_niftynext50list.csv",
    "Nifty 100":               f"{NSE_BASE}/ind_nifty100list.csv",
    "Nifty 200":               f"{NSE_BASE}/ind_nifty200list.csv",
    "Nifty 500":               f"{NSE_BASE}/ind_nifty500list.csv",
    "Nifty Midcap 50":         f"{NSE_BASE}/ind_niftymidcap50list.csv",
    "Nifty Midcap 100":        f"{NSE_BASE}/ind_niftymidcap100list.csv",
    "Nifty Midcap 150":        f"{NSE_BASE}/ind_niftymidcap150list.csv",
    "Nifty Smallcap 50":       f"{NSE_BASE}/ind_niftysmallcap50list.csv",
    "Nifty Smallcap 100":      f"{NSE_BASE}/ind_niftysmallcap100list.csv",
    "Nifty Smallcap 250":      f"{NSE_BASE}/ind_niftysmallcap250list.csv",
    "Nifty Microcap 250":      f"{NSE_BASE}/ind_niftymicrocap250_list.csv",
    "Nifty MidSmallcap 400":   f"{NSE_BASE}/ind_niftymidsmallcap400list.csv",
    "Nifty Total Market":      f"{NSE_BASE}/ind_niftytotalmarket_list.csv",
    "NIFTY FNO":               "API:SECURITIES IN F&O",

    # ─── Sectoral Indices ──────────────────────────────────────────────────────
    "Bank Nifty":              f"{NSE_BASE}/ind_niftybanklist.csv",
    "Fin Nifty":               f"{NSE_BASE}/ind_niftyfinancialserviceslist.csv",
    "Nifty IT":                f"{NSE_BASE}/ind_niftyitlist.csv",
    "Nifty Pharma":            f"{NSE_BASE}/ind_niftypharmalist.csv",
    "Nifty Auto":              f"{NSE_BASE}/ind_niftyautolist.csv",
    "Nifty FMCG":              f"{NSE_BASE}/ind_niftyfmcglist.csv",
    "Nifty Metal":             f"{NSE_BASE}/ind_niftymetallist.csv",
    "Nifty Realty":            f"{NSE_BASE}/ind_niftyrealtylist.csv",
    "Nifty Media":             f"{NSE_BASE}/ind_niftymedialist.csv",
    "Nifty Energy":            f"{NSE_BASE}/ind_niftyenergylist.csv",
    "Nifty Healthcare":        f"{NSE_BASE}/ind_niftyhealthcarelist.csv",
    "Nifty Consumer Durables": f"{NSE_BASE}/ind_niftyconsumerdurableslist.csv",
    "Nifty Oil & Gas":         f"{NSE_BASE}/ind_niftyoilgaslist.csv",
    "Nifty Infrastructure":    f"{NSE_BASE}/ind_niftyinfralist.csv",
    "Nifty PSU Bank":          f"{NSE_BASE}/ind_niftypsubanklist.csv",
    "Nifty Private Bank":      f"{NSE_BASE}/ind_niftyprivatebankindex.csv",

    # ─── Thematic & Strategy ───────────────────────────────────────────────────
    "Nifty India Defence":     f"{NSE_BASE}/ind_niftyindiadefence.csv",
    "Nifty CPSE":              f"{NSE_BASE}/ind_niftycpselist.csv",
    "Nifty MNC":               f"{NSE_BASE}/ind_niftymnclist.csv",
    "Nifty PSE":               f"{NSE_BASE}/ind_niftypselist.csv",
    "Nifty Services Sector":   f"{NSE_BASE}/ind_niftyserviceslist.csv",
    "Nifty Commodities":       f"{NSE_BASE}/ind_niftycommoditieslist.csv",
    "Nifty India Consumption": f"{NSE_BASE}/ind_niftyindiaconsumptionlist.csv",
    "Nifty Digital India":     f"{NSE_BASE}/ind_niftydigitalindialist.csv",
    "Nifty India Manufacturing": f"{NSE_BASE}/ind_niftyindiamanufacturinglist.csv",
    
    # ─── Tracking: Index Spot Prices (Prices, NOT constituent lists) ──────────
    "MAJOR INDICES (Spot)":    "STATIC:MAJOR_INDICES",

    # ─── Full Markets ──────────────────────────────────────────────────────────
    "ALL NSE":                 "https://public.fyers.in/sym_details/NSE_CM.csv",
    "ALL BSE":                 "https://public.fyers.in/sym_details/BSE_CM.csv",
}

# Fallback static data for indices where NSE archive CSV is not publicly available 
# OR for custom tracker lists like "MAJOR INDICES".
_STATIC_FALLBACK: dict[str, list[str]] = {
    # ── Major Indices (Price Symbols for Fyers) ───────────────────────────
    "MAJOR INDICES (Spot)": [
        "NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "NSE:FINNIFTY-INDEX",
        "NSE:MIDCPNIFTY-INDEX", "NSE:NIFTYNEXT50-INDEX", "NSE:NIFTY100-INDEX",
        "NSE:NIFTY500-INDEX", "NSE:NIFTYIT-INDEX", "NSE:NIFTYAUTO-INDEX",
        "NSE:NIFTYPHARMA-INDEX", "NSE:NIFTYMETAL-INDEX", "NSE:NIFTYREALTY-INDEX",
        "NSE:NIFTYFMCG-INDEX", "NSE:NIFTYENERGY-INDEX", "NSE:NIFTYINFRA-INDEX",
        "NSE:NIFTYPSE-INDEX", "NSE:NIFTYCPSE-INDEX", "NSE:NIFTYCOMMODITIES-INDEX",
        "NSE:NIFTYCONSUMPTION-INDEX", "NSE:NIFTYMNC-INDEX", "NSE:NIFTYSERVSECTOR-INDEX",
        "NSE:NIFTYMID150-INDEX", "NSE:NIFTYSMLCAP250-INDEX", "NSE:NIFTYMIDSML400-INDEX",
        "NSE:NIFTYMEDIA-INDEX", "NSE:NIFTYPSUBANK-INDEX", "NSE:NIFTYPVTBANK-INDEX",
        "NSE:NIFTYHEALTHCARE-INDEX", "NSE:NIFTYOILANDGAS-INDEX", "NSE:NIFTYCONSRDURBL-INDEX",
        "NSE:NIFTYMICROCAP250-INDEX", "NSE:NIFTYINDDEFENCE-INDEX"
    ],
    # ── Fin Nifty (Constituents) ───────────────────────────────────────────────
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
    # ── Nifty Private Bank (Constituents) ──────────────────────────────────────
    "Nifty Private Bank": [
        "NSE:AXISBANK-EQ", "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ",
        "NSE:KOTAKBANK-EQ", "NSE:INDUSINDBK-EQ", "NSE:FEDERALBNK-EQ",
        "NSE:YESBANK-EQ", "NSE:BANDHANBNK-EQ", "NSE:RBLBANK-EQ",
        "NSE:IDFCFIRSTB-EQ",
    ],
    # ── Nifty Midcap Select (Constituents) ─────────────────────────────────────
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
    # ── Nifty India Defence ──────────────────────────────────────────────────
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


def _fetch_nse_json(index_name_api: str) -> Optional[list[str]]:
    """Fetch symbols from NSE API JSON"""
    try:
        session = requests.Session()
        # Hit NSE homepage to get fresh cookies
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)
        
        # Format the API URL
        import urllib.parse
        encoded_name = urllib.parse.quote(index_name_api)
        url = f"https://www.nseindia.com/api/equity-stockIndices?index={encoded_name}"
        
        logger.info(f"[SymbolManager] Fetching JSON from {url}...")
        resp = session.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        
        data = resp.json()
        if 'data' not in data:
            logger.warning(f"[SymbolManager] JSON response missing 'data' key for {index_name_api}")
            return None
            
        symbols = [item['symbol'] for item in data['data'] if item.get('symbol')]
        # Filter out obvious indices if any (safety)
        symbols = [s for s in symbols if s not in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]]
        
        return symbols

    except Exception as e:
        logger.warning(f"[SymbolManager] Failed to fetch JSON for {index_name_api}: {e}")
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


# ─── Fyers Master Fetch Logic ───────────────────────────────────────────────

def _fetch_fyers_master(url: str, exchange_prefix: str) -> list[str]:
    """Download Fyers Symbol Master CSV and return formatted symbols"""
    try:
        logger.info(f"[SymbolManager] Downloading Fyers master from {url}...")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        # Fyers CSV format (NSE_CM/BSE_CM): 
        # Token, Description, Symbol, Type, Lot, Tick, ISIN...
        # We need the 3rd column (Symbol) and filter for Type 10 (Equity)
        from io import StringIO
        import csv
        
        fyers_symbols = []
        f = StringIO(resp.text)
        reader = csv.reader(f)
        
        for row in reader:
            if len(row) < 11: continue
            
            # Column mapping from inspection:
            # 9: Standard Fyers Symbol (e.g., NSE:RELIANCE-EQ)
            # 10: Instrument Type (10 = Equity on NSE, 12 on BSE CM)
            raw_symbol = row[9].strip()
            # inst_type = row[10].strip() # No longer strictly needed if we filter by suffix
            
            # Filter for Equity Stocks: Regular, T2T, and common BSE groups/SME
            # This handles both NSE and BSE correctly and excludes Debt, Bonds, MF, etc.
            if ":" in raw_symbol:
                equity_suffixes = ["-EQ", "-BE", "-A", "-B", "-X", "-XT", "-T", "-Z", "-P", "-SM", "-ST", "-M", "-MT"]
                if any(raw_symbol.endswith(sfx) for sfx in equity_suffixes):
                    fyers_symbols.append(raw_symbol)
            else:
                # In case prefix is missing (safety)
                clean_sym = raw_symbol
                equity_suffixes = ["-EQ", "-BE", "-A", "-B", "-X", "-XT", "-T", "-Z", "-P", "-SM", "-ST", "-M", "-MT"]
                if any(clean_sym.endswith(sfx) for sfx in equity_suffixes):
                    fyers_symbols.append(f"{exchange_prefix}:{clean_sym}")
        
        return sorted(list(set(fyers_symbols)))

    except Exception as e:
        logger.error(f"[SymbolManager] Fyers Master fetch failed: {e}")
        return []


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

    # 2. Fetch fresh from source
    url = INDEX_SOURCES[index_name]
    
    # Case A: Static mapping
    if url.startswith("STATIC:"):
        key = url.split(":")[1]
        static_data = _STATIC_FALLBACK.get(index_name) or _STATIC_FALLBACK.get(key, [])
        if static_data:
            _write_cache(index_name, static_data)
            return static_data

    # Case B: ALL Exchange (Large Master)
    elif index_name.startswith("ALL "):
        prefix = "NSE" if "NSE" in index_name else "BSE"
        fyers_symbols = _fetch_fyers_master(url, prefix)
        if fyers_symbols:
            _write_cache(index_name, fyers_symbols)
            logger.info(f"[SymbolManager] Cached {len(fyers_symbols)} symbols for {index_name}")
            return fyers_symbols
    
    # Case C: NSE API JSON
    elif url.startswith("API:"):
        api_index = url.split(":")[1]
        logger.info(f"[SymbolManager] Fetching {index_name} via NSE API...")
        nse_symbols = _fetch_nse_json(api_index)
        if nse_symbols:
            fyers_symbols = _nse_symbols_to_fyers(nse_symbols)
            _write_cache(index_name, fyers_symbols)
            logger.info(f"[SymbolManager] Cached {len(fyers_symbols)} symbols for {index_name}")
            return fyers_symbols

    # Case D: Standard NSE Index CSV
    else:
        logger.info(f"[SymbolManager] Fetching {index_name} from NSE...")
        df = _fetch_nse_csv(url)
        if df is not None:
            nse_symbols = _parse_nse_csv(df)
            if nse_symbols:
                fyers_symbols = _nse_symbols_to_fyers(nse_symbols)
                _write_cache(index_name, fyers_symbols)
                logger.info(f"[SymbolManager] Cached {len(fyers_symbols)} symbols for {index_name}")
                return fyers_symbols

    logger.warning(f"[SymbolManager] Fetch failed for '{index_name}'.")

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


# ─── Sectoral Intelligence ───────────────────────────────────────────────────

SECTOR_INDICES = [
    "Bank Nifty", "Nifty IT", "Nifty Pharma", "Nifty Auto", 
    "Nifty FMCG", "Nifty Metal", "Nifty Realty", "Nifty Energy", 
    "Nifty Media", "Nifty Healthcare", "Nifty Infrastructure", 
    "Nifty Consumer Durables", "Nifty Oil & Gas", "Nifty India Defence",
    "Nifty PSU Bank", "Nifty Private Bank", "Fin Nifty"
]

INDUSTRY_MASTER_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

def _fetch_industry_master() -> dict[str, str]:
    """
    Fetch Nifty 500 list and extract {Symbol: Industry} mapping.
    """
    cache_name = "industry_master"
    cache_path = os.path.join(CACHE_DIR, f"{cache_name}.json")
    
    # Try valid cache first (TTL 30 days for industry)
    if os.path.exists(cache_path):
        meta = _load_meta()
        if cache_name in meta:
            fetched_at = datetime.fromisoformat(meta[cache_name]["fetched_at"])
            if datetime.now() < fetched_at + timedelta(days=30):
                with open(cache_path) as f:
                    return json.load(f).get("mapping", {})

    logger.info("[SymbolManager] Fetching Nifty 500 Industry Master from NSE...")
    df = _fetch_nse_csv(INDUSTRY_MASTER_URL)
    if df is not None:
        mapping = {}
        for _, row in df.iterrows():
            symbol = str(row.get('Symbol', '')).strip()
            industry = str(row.get('Industry', '')).strip()
            if symbol and industry:
                fyers_sym = f"NSE:{symbol}-EQ"
                mapping[fyers_sym] = industry
        
        # Save to cache
        _ensure_cache_dir()
        with open(cache_path, "w") as f:
            json.dump({"fetched_at": datetime.now().isoformat(), "mapping": mapping}, f, indent=2)
        
        meta = _load_meta()
        meta[cache_name] = {
            "fetched_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
        }
        _save_meta(meta)
        return mapping
        
    return {}

def get_sector_map() -> dict[str, str]:
    """
    Build a map of {Symbol: SectorName} automatically.
    1. Primary: Detailed Screener-level Industrials from SQLite Database.
    2. Fallback: Nifty 500 Industry Master.
    3. Specific groupings from Nifty Sectoral Indices (Bank, IT, etc.)
    """
    # 1. Load the deep Screener map (1800+ stocks) from DB
    from core.database import StockDatabase
    try:
        db = StockDatabase()
        sector_map = db.get_all_sectors()
    except Exception as e:
        logger.warning(f"[SymbolManager] Failed to load sectors from DB: {e}")
        sector_map = {}

    # 2. Fallback/Supplement with Nifty 500 Industry Master
    master = _fetch_industry_master()
    for k, v in master.items():
        if k not in sector_map:
            sector_map[k] = v
    
    # 3. Specific groupings from official indices (Bank Nifty, IT, etc.)
    for index in SECTOR_INDICES:
        symbols = get_symbols(index)
        clean_name = index.replace("Nifty ", "").replace(" Index", "")
        for s in symbols:
            sector_map[s] = clean_name
            
    return sector_map


def get_custom_watchlist_symbols() -> list[str]:
    """Load from custom watchlist CSV"""
    path = os.path.join(BASE_DIR, "data", "custom_watchlist.csv")
    if not os.path.exists(path):
        return []
    try:
        df = pd.read_csv(path)
        if "symbol" not in df.columns:
            return []
        return df["symbol"].dropna().str.strip().tolist()
    except Exception as e:
        logger.error(f"[SymbolManager] Error loading custom watchlist: {e}")
        return []

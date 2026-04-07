"""
core/data_fetcher.py — Fyers API wrapper with in-session caching
"""
import os
import time
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
import pandas as pd
from fyers_apiv3 import fyersModel
from core.database import StockDatabase

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db = StockDatabase()

# Global Start Date for full history scans (ATH)
START_1994 = date(1994, 1, 1)

# In-memory cache: key → DataFrame
_cache: dict[str, pd.DataFrame] = {}

# Cached Fyers session
_fyers_session: fyersModel.FyersModel | None = None

# Smart Symbol Resolver Cache: user_input -> working_fyers_symbol
_symbol_resolver: dict[str, str] = {}


def _make_cache_key(symbol: str, resolution: str, range_from: str, range_to: str) -> str:
    return f"{symbol}|{resolution}|{range_from}|{range_to}"


def get_fyers_client(token: str) -> fyersModel.FyersModel:
    global _fyers_session
    client_id = os.getenv("FYERS_CLIENT_ID", "")
    _fyers_session = fyersModel.FyersModel(
        client_id=client_id,
        is_async=False,
        token=token,
        log_path="",
    )
    return _fyers_session


def validate_session(fyers: fyersModel.FyersModel) -> bool:
    """Quick profile check to validate session is active"""
    try:
        profile = fyers.get_profile()
        return profile.get("s") == "ok"
    except Exception:
        return False


def fetch_ohlcv(
    fyers: fyersModel.FyersModel,
    symbol: str,
    resolution: str,
    scan_date: date,
    lookback_days: int = 120,
    retry_count: int = 3,
) -> pd.DataFrame | None:
    """
    Fetch OHLCV data using a Hybrid approach: SQLite Cache + Fyers Sync.
    If resolution is 'D', it ensures history from 1994 is synced.
    """
    res_map = {"1D": "D", "1W": "W", "1M": "M"}
    api_res = res_map.get(resolution, resolution)
    symbol = symbol.strip().upper()

    # 1. Resolve Symbol Suffix if missing
    if "-" not in symbol:
        if symbol in _symbol_resolver:
            symbol = _symbol_resolver[symbol]
        else:
            exchange = symbol.split(":")[0] if ":" in symbol else "NSE"
            suffixes = ["-EQ", "-BE"] if exchange == "NSE" else ["-EQ", "-B", "-T", "-X", "-XT", "-A", "-Z", "-P"]
            found = False
            for sfx in [None] + suffixes:
                target = f"{symbol}{sfx}" if sfx else symbol
                df_disc = fetch_ohlcv_direct(fyers, target, api_res, scan_date, lookback_days=5, retry_count=1, discovery_mode=True)
                if df_disc is not None and len(df_disc) > 0:
                    _symbol_resolver[symbol] = target
                    symbol = target
                    found = True
                    break
            if not found: return None

    # 2. Daily Sync Logic (1994 to Today)
    if api_res == "D":
        last_stored = db.get_last_date(symbol, api_res)
        start_sync = START_1994 if last_stored is None else (last_stored + timedelta(days=1)).date()
        end_sync = date.today()

        if start_sync < end_sync:
            print(f"[Sync] Fetching {symbol} from {start_sync} to {end_sync}...")
            current_start = start_sync
            while current_start < end_sync:
                # Fyers 360 days limit
                current_end = min(current_start + timedelta(days=360), end_sync)
                
                # Use fetch_ohlcv_direct but with custom range (we need to bypass its internal day calculation)
                df_chunk = _fetch_range_direct(fyers, symbol, api_res, current_start, current_end)
                if df_chunk is not None and not df_chunk.empty:
                    db.save_candles(symbol, api_res, df_chunk)
                
                current_start = current_end + timedelta(days=1)
                time.sleep(0.5) # Prevent rate limits

    # 3. Load from Database
    # For ATH we need all, for regular we might need only lookback. 
    # But loading all Daily data into memory is fine (~7000 rows for 30 years).
    df = db.get_history(symbol, api_res)
    
    if df.empty and api_res != "D":
        # Fallback for non-daily which we don't sync fully yet
        df = fetch_ohlcv_direct(fyers, symbol, api_res, scan_date, lookback_days, retry_count)
        if df is not None:
             db.save_candles(symbol, api_res, df)
             return df
    
    return df if not df.empty else None


def _fetch_range_direct(fyers, symbol, resolution, start_date, end_date):
    """Internal helper to fetch a specific date range with retry logic"""
    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start_date.strftime("%Y-%m-%d"),
        "range_to": end_date.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    for attempt in range(3):
        try:
            response = fyers.history(data)
            if response.get("s") == "ok":
                candles = response.get("candles", [])
                if not candles: return None
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
                df["datetime"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                return df.drop(columns=["timestamp"])
            elif response.get("code") == 429:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                return None
        except Exception as e:
            time.sleep(2)
            continue
    return None


def fetch_ohlcv_direct(
    fyers: fyersModel.FyersModel,
    symbol: str,
    resolution: str,
    scan_date: date,
    lookback_days: int = 120,
    retry_count: int = 5,
    discovery_mode: bool = False,
) -> pd.DataFrame | None:
    """
    Fetch OHLCV data for a symbol up to scan_date.
    Returns a DataFrame with columns: datetime, open, high, low, close, volume
    Returns None on error.
    """
    # Mapping already done in wrapper, use as-is
    api_res = resolution 
    
    intraday_resolutions = {"1", "5", "15", "30", "60"}
    if not discovery_mode:
        if api_res in intraday_resolutions:
            lookback_days = min(lookback_days, 30)
        else:
            # Increase lookback for larger TFs to ensure indicator calculation
            # CAP at 360 for D/W/M because Fyers API limit is 366 days
            if resolution == "M":
                lookback_days = min(max(lookback_days, 3650), 360)
            elif resolution == "W":
                lookback_days = min(max(lookback_days, 730), 360)
            else:
                lookback_days = min(max(lookback_days, 300), 360)
    else:
        # Discovery mode: use exact lookback
        pass
    
    range_from = (scan_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    range_to = scan_date.strftime("%Y-%m-%d")

    cache_key = _make_cache_key(symbol, resolution, range_from, range_to)
    if cache_key in _cache:
        return _cache[cache_key].copy()

    data = {
        "symbol": symbol,
        "resolution": api_res,
        "date_format": "1",  # range_from and range_to as yyyy-mm-dd strings
        "range_from": range_from,
        "range_to": range_to,
        "cont_flag": "1",
    }

    for attempt in range(retry_count):
        try:
            # Preventative delay to avoid hitting limits in the first place
            time.sleep(0.2) 
            
            response = fyers.history(data)

            if response.get("s") == "ok":
                candles = response.get("candles", [])
                if not candles:
                    return None

                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
                df["datetime"] = (
                    df["datetime"]
                    .dt.tz_localize("UTC")
                    .dt.tz_convert("Asia/Kolkata")
                    .dt.tz_localize(None)
                )
                df = df.drop(columns=["timestamp"])
                df = df.sort_values("datetime").reset_index(drop=True)

                _cache[cache_key] = df.copy()
                return df

            # 429 = Too Many Requests
            elif response.get("code") == 429 or "exceeded" in str(response.get("message", "")).lower():
                # Exponential backoff: 3s, 6s, 9s...
                wait = (attempt + 1) * 3 
                print(f"[DataFetcher] Rate limit hit for {symbol}. Backing off for {wait}s...")
                time.sleep(wait)
                continue

            elif response.get("code") in [401, -16, -17]:
                print(f"[DataFetcher] Auth error for {symbol}: {response.get('message')}")
                return None

            else:
                msg = str(response.get("message", "")).lower()
                if "invalid symbol" in msg or "not found" in msg:
                    return None
                return None

        except Exception as e:
            if "too many requests" in str(e).lower():
                time.sleep((attempt + 1) * 3)
                continue
            
            print(f"[DataFetcher] Error fetching {symbol} (attempt {attempt+1}): {e}")
            if attempt < retry_count - 1:
                time.sleep(2)

    return None


def clear_cache():
    """Clear in-session data cache"""
    _cache.clear()

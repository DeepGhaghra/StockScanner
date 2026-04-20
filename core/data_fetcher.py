"""
core/data_fetcher.py — Fyers API wrapper with Native Multi-Segment Fallback
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
_RESOLVER_FILE = os.path.join(BASE_DIR, "data", "cache", "symbol_map.json")

def _load_resolver():
    global _symbol_resolver
    if os.path.exists(_RESOLVER_FILE):
        try:
            with open(_RESOLVER_FILE, "r") as f:
                _symbol_resolver = json.load(f)
        except Exception:
            _symbol_resolver = {}

def _save_resolver():
    try:
        os.makedirs(os.path.dirname(_RESOLVER_FILE), exist_ok=True)
        with open(_RESOLVER_FILE, "w") as f:
            json.dump(_symbol_resolver, f)
    except Exception:
        pass

# Initial load
import json
_load_resolver()


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
    Includes automated segment switching if restricted.
    """
    api_res = resolution 
    
    # Safety cap for single API calls (Fyers limit is ~366 days for D/W/M)
    lookback_days = min(max(lookback_days, 1), 360)
    
    # Range calculation
    range_from = (scan_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    range_to = scan_date.strftime("%Y-%m-%d")

    cache_key = _make_cache_key(symbol, resolution, range_from, range_to)
    if cache_key in _cache:
        return _cache[cache_key].copy()

    # Core request payload
    data = {
        "symbol": symbol,
        "resolution": api_res,
        "date_format": "1",
        "range_from": range_from,
        "range_to": range_to,
        "cont_flag": "1",
    }

    for attempt in range(retry_count):
        try:
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

            # Handle Rate Limiting
            elif response.get("code") == 429 or "exceeded" in str(response.get("message", "")).lower():
                wait = (attempt + 1) * 3 
                print(f"[DataFetcher] Rate limit {symbol}. Waiting {wait}s...")
                time.sleep(wait)
                continue

            # Handle Auth Error (Code -16/-17) - DO NOT RETRY, return None to trigger wrapper-level fallback
            elif response.get("code") in [401, -16, -17]:
                if not discovery_mode:
                    print(f"[DataFetcher] Fyers Restricted {symbol}: {response.get('message')}")
                return None

            else:
                return None

        except Exception as e:
            print(f"[DataFetcher] Error fetching {symbol}: {e}")
            if attempt < retry_count - 1:
                time.sleep(2)

    return None


def fetch_ohlcv(
    fyers: fyersModel.FyersModel,
    symbol: str,
    resolution: str,
    scan_date: date,
    lookback_days: int = 120,
    retry_count: int = 3,
) -> pd.DataFrame | None:
    """
    Hybrid Fetch with Multi-Segment Discovery.
    Tries NSE-EQ -> NSE-BE -> BSE Group A/B/T natively.
    """
    res_map = {"1D": "D", "1W": "W", "1M": "M"}
    api_res = res_map.get(resolution, resolution)
    symbol = symbol.strip().upper()

    # 1. Multi-Segment Discovery
    if symbol in _symbol_resolver:
        resolved_symbol = _symbol_resolver[symbol]
    else:
        clean_sym = symbol.split(":")[-1].split("-")[0]
        # Segments to try in order of preference
        # NSE Equity -> NSE Trade-for-Trade -> BSE Quality A/B -> BSE Restricted T/XT
        test_segments = [
            f"NSE:{clean_sym}-EQ",
            f"NSE:{clean_sym}-BE",
            f"BSE:{clean_sym}-A",
            f"BSE:{clean_sym}-B",
            f"BSE:{clean_sym}-T",
            f"BSE:{clean_sym}-XT"
        ]
        
        resolved_symbol = None
        for target in test_segments:
            # Quick 20-day check to see if we have access
            df_check = fetch_ohlcv_direct(fyers, target, api_res, scan_date, lookback_days=20, retry_count=1, discovery_mode=True)
            if df_check is not None and len(df_check) > 0:
                print(f"[Discovery] Found working Fyers segment: {target}")
                _symbol_resolver[symbol] = target
                resolved_symbol = target
                _save_resolver()
                break
        
        if not resolved_symbol:
            # Try original input as last resort
            resolved_symbol = symbol 

    # 2. Universal Sync (D, W, M - 1994 to Today)
    if api_res in ["D", "W", "M"]:
        last_stored = db.get_last_date(resolved_symbol, api_res)
        end_sync = date.today()

        if last_stored is None:
            start_sync = START_1994
        else:
            last_stored_date = last_stored.date() if hasattr(last_stored, 'date') else last_stored
            if last_stored_date >= end_sync:
                # Already synced today — re-fetch ONLY today's candle to get latest running price
                start_sync = end_sync
            else:
                start_sync = last_stored_date + timedelta(days=1)

        if start_sync <= end_sync:
            print(f"[Sync] {resolved_symbol} ({api_res}) from {start_sync}...")
            current_start = start_sync
            while current_start <= end_sync:
                # Chunk size for W/M can be 360 days (same limit applies)
                current_end = min(current_start + timedelta(days=360), end_sync)
                df_chunk = _fetch_range_direct(fyers, resolved_symbol, api_res, current_start, current_end)
                if df_chunk is not None and not df_chunk.empty:
                    db.save_candles(resolved_symbol, api_res, df_chunk)
                current_start = current_end + timedelta(days=1)
                time.sleep(0.3)

    # 3. Load from Database
    df = db.get_history(resolved_symbol, api_res)
    
    # Last ditch attempt for non-daily or if sync was skipped
    if df is None or df.empty:
        df = fetch_ohlcv_direct(fyers, resolved_symbol, api_res, scan_date, lookback_days, retry_count)
        if df is not None and not df.empty:
            db.save_candles(resolved_symbol, api_res, df)
            
    return df if (df is not None and not df.empty) else None


def _fetch_range_direct(fyers, symbol, resolution, start_date, end_date):
    """Internal helper to fetch a specific date range with segment support"""
    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start_date.strftime("%Y-%m-%d"),
        "range_to": end_date.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    for attempt in range(2):
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
                time.sleep(3)
                continue
            else:
                return None
        except Exception:
            time.sleep(1)
            continue
    return None


def clear_cache():
    """Clear in-session data cache"""
    _cache.clear()

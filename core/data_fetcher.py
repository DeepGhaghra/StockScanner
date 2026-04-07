"""
core/data_fetcher.py — Fyers API wrapper with in-session caching
"""
import os
import time
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
import pandas as pd
from fyers_apiv3 import fyersModel

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# In-memory cache: key → DataFrame
_cache: dict[str, pd.DataFrame] = {}

# Cached Fyers session
_fyers_session: fyersModel.FyersModel | None = None


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
    retry_count: int = 5,
) -> pd.DataFrame | None:
    """
    Fetch OHLCV data for a symbol up to scan_date.
    Returns a DataFrame with columns: datetime, open, high, low, close, volume
    Returns None on error.
    """
    # For intraday, limit lookback to stay within API limits
    intraday_resolutions = {"1", "5", "15", "30", "60"}
    if resolution in intraday_resolutions:
        lookback_days = min(lookback_days, 30)
    else:
        # Increase lookback for larger TFs to ensure indicator calculation
        if resolution == "M":
            lookback_days = max(lookback_days, 3650) # 10 years
        elif resolution == "W":
            lookback_days = max(lookback_days, 730)  # 2 years
        else:
            lookback_days = max(lookback_days, 300)  # ~1 year for Daily
    
    range_from = (scan_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    range_to = scan_date.strftime("%Y-%m-%d")

    cache_key = _make_cache_key(symbol, resolution, range_from, range_to)
    if cache_key in _cache:
        return _cache[cache_key].copy()

    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",  # epoch timestamps
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

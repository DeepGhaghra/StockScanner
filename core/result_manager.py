"""
core/result_manager.py — Cache and retrieve scanner results (JSON-based)
"""
import os
import json
import hashlib
from datetime import date, datetime
import pandas as pd
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache", "results")

def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)

def _generate_cache_key(
    symbols: list[str],
    scan_date: date,
    resolution: str,
    strategies: list[str],
    logic: str,
    params: dict
) -> str:
    """Generate a unique hash key for a scan configuration"""
    # Sort strategies and params for consistency
    strategies = sorted(strategies)
    # We'll use a hash of the symbols list if it's very long
    symbols_str = ",".join(sorted(symbols))
    params_str = json.dumps(params, sort_keys=True)
    
    key_input = f"{symbols_str}|{scan_date.isoformat()}|{resolution}|{','.join(strategies)}|{logic}|{params_str}"
    return hashlib.md5(key_input.encode()).hexdigest()

def get_cached_results(
    symbols: list[str],
    scan_date: date,
    resolution: str,
    strategies: list[str],
    logic: str,
    params: dict
) -> Optional[pd.DataFrame]:
    """Retrieve cached scan results if they exist and are valid"""
    # If scanning for today, check if it's during market hours (simplistic check)
    # We might want to avoid caching today's results until EOD or use a TTL
    if scan_date == date.today():
        # For today, we could use a short TTL or skip cache
        # Let's skip cache for today for now to ensure freshness
        # return None
        pass

    key = _generate_cache_key(symbols, scan_date, resolution, strategies, logic, params)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    
    if not os.path.exists(cache_path):
        return None
    
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
            
        # Optional: Check TTL for today's results
        if scan_date == date.today():
            cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
            # 15-minute TTL for today's scans
            if (datetime.now() - cached_at).total_seconds() > 900:
                return None

        results = data.get("results", [])
        if not results:
            return pd.DataFrame()
            
        return pd.DataFrame(results)
    except Exception as e:
        print(f"[ResultManager] Error reading cache: {e}")
        return None

def save_results_to_cache(
    symbols: list[str],
    scan_date: date,
    resolution: str,
    strategies: list[str],
    logic: str,
    params: dict,
    results_df: pd.DataFrame
):
    """Save scan results to cache"""
    _ensure_cache_dir()
    key = _generate_cache_key(symbols, scan_date, resolution, strategies, logic, params)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    
    try:
        # Convert DataFrame to list of dicts for JSON storage
        # Filter out heavy columns if necessary, but we need most for the UI
        results_list = results_df.to_dict("records") if not results_df.empty else []
        
        data = {
            "cached_at": datetime.now().isoformat(),
            "scan_date": scan_date.isoformat(),
            "params": {
                "resolution": resolution,
                "strategies": strategies,
                "logic": logic,
                "strategy_params": params
            },
            "results": results_list
        }
        
        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2)
            
    except Exception as e:
        print(f"[ResultManager] Error saving cache: {e}")

def clear_results_cache():
    """Clear all cached scan results"""
    if os.path.exists(CACHE_DIR):
        import shutil
        shutil.rmtree(CACHE_DIR)
        _ensure_cache_dir()

def _generate_momentum_cache_key(symbols: list[str], scan_date: date, weights: dict) -> str:
    symbols_str = ",".join(sorted(symbols))
    weights_str = json.dumps(weights, sort_keys=True)
    key_input = f"momentum|{symbols_str}|{scan_date.isoformat()}|{weights_str}"
    return hashlib.md5(key_input.encode()).hexdigest()

def get_cached_momentum(symbols: list[str], scan_date: date, weights: dict):
    if scan_date == date.today():
        pass
    key = _generate_momentum_cache_key(symbols, scan_date, weights)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(cache_path):
        return None, None
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        if scan_date == date.today():
            cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
            if (datetime.now() - cached_at).total_seconds() > 900:
                return None, None
        df = pd.DataFrame(data.get("results", []))
        stats = data.get("stats", {})
        return df, stats
    except Exception as e:
        print(f"[ResultManager] Error reading momentum cache: {e}")
        return None, None

def save_momentum_to_cache(symbols: list[str], scan_date: date, weights: dict, df: pd.DataFrame, stats: dict):
    _ensure_cache_dir()
    key = _generate_momentum_cache_key(symbols, scan_date, weights)
    cache_path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        results_list = df.to_dict("records") if not df.empty else []
        data = {
            "cached_at": datetime.now().isoformat(),
            "scan_date": scan_date.isoformat(),
            "weights": weights,
            "results": results_list,
            "stats": stats
        }
        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[ResultManager] Error saving momentum cache: {e}")

"""
core/momentum.py — Meaningful Momentum Engine (Multi-Horizon)
============================================================
Ranking Strategy:
  1. 1-Year Return    (50%)
  2. 100-EMA distance (35%)
  3. Relative Volume  (15%)
"""
import time
import pandas as pd
import numpy as np
from datetime import date, timedelta
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ─── Stale threshold ──────────────────────────────────────────────────────────
# 0 = always sync if ANY gap exists (ensures today's candle is fetched)
STALE_DAYS = 0

# ─── Meaningful Weights ────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "return_12m":  0.50,
    "ema100_dist": 0.35,
    "rel_volume":  0.15,
}

def _pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True) * 100

def _resolve_symbol(raw_sym: str) -> str:
    from core.data_fetcher import _symbol_resolver
    resolved = _symbol_resolver.get(raw_sym)
    if resolved: return resolved
    clean = raw_sym.strip().upper().split(":")[-1].split("-")[0]
    return f"NSE:{clean}-EQ"

def _needs_sync(last_dt, scan_date: date) -> bool:
    """Returns True if we need to sync data up to scan_date.
    For backtests (past dates) we skip – DB already has history.
    For today, ALWAYS re-sync to get the latest running daily candle.
    """
    if scan_date < date.today(): return False  # backtest: use DB as-is
    if last_dt is None: return True             # no data at all → must sync
    # Always re-sync today's data — the daily candle updates during market hours
    return True

def _incremental_sync(fyers, resolved_sym: str, last_date: date, scan_date: date):
    from core.data_fetcher import _fetch_range_direct
    from core.database import StockDatabase
    db = StockDatabase()
    start, end = last_date + timedelta(days=1), scan_date
    if start > end: return
    df_gap = _fetch_range_direct(fyers, resolved_sym, "D", start, end)
    if df_gap is not None and not df_gap.empty:
        db.save_candles(resolved_sym, "D", df_gap)
    time.sleep(0.12)

def _load_from_db(resolved_sym: str) -> pd.DataFrame | None:
    from core.database import StockDatabase
    db = StockDatabase()
    df = db.get_history(resolved_sym, "D")
    if df is None or df.empty: return None
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.sort_values("datetime").reset_index(drop=True)

def _compute_metrics(df: pd.DataFrame, scan_date: date) -> dict | None:
    # Use end of the scan_date to include daily candles from that day
    cutoff = pd.Timestamp(scan_date) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    df = df[df["datetime"] <= cutoff].copy()
    if len(df) < 210: return None

    last = df.iloc[-1]
    close = last["close"]

    # Hard Filter: SMA 200
    sma_200 = df["close"].rolling(200).mean().iloc[-1]
    if pd.isna(sma_200) or close <= sma_200: return None

    # Helper for returns
    def get_ret(days):
        dt_then = last["datetime"] - pd.Timedelta(days=days)
        df_target = df[df["datetime"] >= dt_then]
        if df_target.empty: return 0
        start_price = df_target.iloc[0]["close"]
        return ((close - start_price) / start_price) * 100 if start_price > 0 else 0

    ret_12m = get_ret(365)

    # 100 EMA Distance
    ema_100 = df["close"].ewm(span=100, adjust=False).mean().iloc[-1]
    ema100_dist = ((close - ema_100) / ema_100) * 100 if ema_100 > 0 else 0

    # Relative Volume (10d avg vs 90d avg)
    vol_10 = df["volume"].iloc[-10:].mean()
    vol_90 = df["volume"].iloc[-90:].mean()
    rel_vol = vol_10 / vol_90 if vol_90 > 0 else 1.0

    return {
        "Date": last["datetime"].strftime("%d-%m-%Y"),
        "Close": round(close, 2),
        "SMA200": round(sma_200, 2),
        "12M Return %":    round(ret_12m, 2),
        "EMA100 Dist %":   round(ema100_dist, 2),
        "EMA100":          round(ema_100, 2),
        "Rel Volume":      round(rel_vol, 2),
    }

def run_momentum_scan(fyers, symbols: list, scan_date: date, weights: dict = None, progress_callback=None, max_workers: int = 10, use_cache: bool = True):
    from core.database import StockDatabase
    from core.data_fetcher import fetch_ohlcv
    from core.result_manager import get_cached_momentum, save_momentum_to_cache
    db = StockDatabase()
    if weights is None: weights = DEFAULT_WEIGHTS

    if use_cache:
        cached_df, cached_stats = get_cached_momentum(symbols, scan_date, weights)
        if cached_df is not None and cached_stats is not None:
            if progress_callback:
                progress_callback(len(symbols), len(symbols), "Serving from cache...")
            return cached_df, cached_stats

    raw_results, sma_filtered, failed_data, synced_count = [], 0, 0, 0
    total = len(symbols)
    is_backtest = scan_date < date.today()
    
    progress_lock = threading.Lock()
    processed_count = 0
    synced_count_lock = threading.Lock()
    
    def momentum_worker(raw_sym: str):
        nonlocal synced_count
        try:
            resolved = _resolve_symbol(raw_sym)
            synced_this_time = False
            if not is_backtest:
                last_dt = db.get_last_date(resolved, "D")
                if _needs_sync(last_dt, scan_date):
                    try:
                        fetch_ohlcv(fyers, raw_sym, "D", scan_date)
                        with synced_count_lock:
                            synced_count += 1
                        synced_this_time = True
                    except Exception as e:
                        logger.warning(f"Sync failed for {raw_sym}: {e}")

            df = _load_from_db(resolved)
            
            if df is None or len(df) < 210:
                return {"status": "failed", "sym": raw_sym}

            metrics = _compute_metrics(df, scan_date)
            if metrics is None:
                return {"status": "filtered", "sym": raw_sym}

            metrics["Name"] = raw_sym
            return {"status": "success", "data": metrics, "sym": raw_sym}
        except Exception as e:
            logger.error(f"Error in momentum worker for {raw_sym}: {e}")
            return {"status": "failed", "sym": raw_sym}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Map futures to symbols to track progress
        future_to_sym = {executor.submit(momentum_worker, sym): sym for sym in symbols}
        
        for future in as_completed(future_to_sym):
            processed_count += 1
            sym = future_to_sym[future]
            
            if progress_callback:
                progress_callback(processed_count, total, sym)

            try:
                res = future.result()
                if res:
                    status = res.get("status")
                    if status == "success":
                        raw_results.append(res["data"])
                    elif status == "filtered":
                        sma_filtered += 1
                    elif status == "failed":
                        failed_data += 1
            except Exception as e:
                logger.error(f"Future error for {sym}: {e}")
                failed_data += 1

    if not raw_results: return pd.DataFrame(), {}

    res_df = pd.DataFrame(raw_results)
    res_df["f1_12m"] = _pct_rank(res_df["12M Return %"])
    res_df["f3_ema100"] = _pct_rank(res_df["EMA100 Dist %"])
    res_df["f4_vol"] = _pct_rank(res_df["Rel Volume"])

    w = weights
    res_df["Composite Score"] = (
        w.get("return_12m", 0.50) * res_df["f1_12m"] +
        w.get("ema100_dist", 0.35) * res_df["f3_ema100"] +
        w.get("rel_volume", 0.15) * res_df["f4_vol"]
    )

    res_df = res_df.sort_values("Composite Score", ascending=False).head(100).reset_index(drop=True)
    res_df.insert(0, "Rank", res_df.index + 1)
    
    cols = ["Rank", "Name", "Date", "Close", "SMA200", "EMA100", "Composite Score", 
            "12M Return %", "EMA100 Dist %", "Rel Volume",
            "f1_12m", "f3_ema100", "f4_vol"]
    final_df = res_df[[c for c in cols if c in res_df.columns]]
    stats = {
        "total": total, "failed": failed_data, "filtered": sma_filtered, "synced": synced_count, "passed": len(raw_results), "is_backtest": is_backtest
    }
    
    if use_cache:
        save_momentum_to_cache(symbols, scan_date, weights, final_df, stats)
        
    return final_df, stats

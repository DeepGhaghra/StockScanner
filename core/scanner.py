"""
core/scanner.py — Main scanner orchestrator
"""
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable

import pandas as pd
from fyers_apiv3 import fyersModel

from core.data_fetcher import fetch_ohlcv, clear_cache
from core.indicators import add_all_indicators
from core.strategy_engine import run_strategies, STRATEGIES
from core.symbol_manager import get_sector_map
from core.result_manager import get_cached_results, save_results_to_cache
from utils.helpers import symbol_to_display_name


def run_scan(
    fyers: fyersModel.FyersModel,
    symbols: list[str],
    scan_date: date,
    resolution: str,
    selected_strategies: list[str],
    logic: str = "OR",
    strategy_params: dict = {},
    progress_callback: Callable[[int, int, str], None] | None = None,
    use_cache: bool = True,
    max_workers: int = 10,
) -> pd.DataFrame:
    """
    Scan a list of symbols and return matched stocks.

    Args:
        fyers: Authenticated FyersModel instance
        symbols: List of Fyers symbols e.g. ['NSE:RELIANCE-EQ']
        scan_date: The date to scan as-of
        resolution: Candle resolution ('1','5','15','30','60','D','W')
        selected_strategies: List of strategy names
        logic: 'AND' or 'OR' for combining strategies
        strategy_params: Per-strategy parameter overrides
        progress_callback: Optional fn(current, total, symbol) for progress reporting

    Returns:
        DataFrame with results
    """
    total = len(symbols)
    
    # 0. Check Cache
    if use_cache:
        cached_df = get_cached_results(
            symbols=symbols,
            scan_date=scan_date,
            resolution=resolution,
            strategies=selected_strategies,
            logic=logic,
            params=strategy_params
        )
        if cached_df is not None:
            if progress_callback:
                progress_callback(total, total, "Serving from cache...")
            return cached_df

    # Pre-fetch sector map once
    sector_map = get_sector_map()
    results = []
    
    progress_lock = threading.Lock()
    processed_count = 0

    def scan_worker(symbol: str):
        try:
            display_name = symbol_to_display_name(symbol)

            # 1. Fetch data
            df = fetch_ohlcv(fyers, symbol, resolution, scan_date)
            if df is None or len(df) < 5:
                return None

            # 2. Cut to only data up to scan_date (inclusive)
            df = df[df["datetime"].dt.date <= scan_date].copy()
            if len(df) < 5:
                return None

            # 3. Add indicators
            df = add_all_indicators(df)

            # 4. Run strategies
            matched, strategy_results = run_strategies(df, selected_strategies, logic, strategy_params)

            if not matched:
                return None

            # 5. Build result row
            last = df.iloc[-1]
            sector = sector_map.get(symbol, "Others")

            matched_names = [r.strategy_name for r in strategy_results if r.matched]
            details = {}
            for r in strategy_results:
                if r.matched:
                    details[r.strategy_name] = r.details

            return {
                "Symbol": symbol,
                "Name": display_name,
                "Sector": sector,
                "Strategies Matched": ", ".join(matched_names),
                "Close": round(float(last["close"]), 2),
                "Open": round(float(last["open"]), 2),
                "High": round(float(last["high"]), 2),
                "Low": round(float(last["low"]), 2),
                "SMA50": round(float(last["sma_50"]), 2) if pd.notna(last["sma_50"]) else None,
                "RSI": round(float(last["rsi_14"]), 1) if pd.notna(last["rsi_14"]) else None,
                "Volume": int(last["volume"]),
                "Vol Ratio": round(float(last["vol_ratio"]), 2) if pd.notna(last["vol_ratio"]) else None,
                "Candle Date": last["datetime"].strftime("%d-%m-%Y %H:%M:%S"),
                "Signal": "BUY",
                "_details": details,
                "_df": df.tail(60).to_dict("records"),
            }
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
            return None

    # Use ThreadPoolExecutor for concurrent scanning
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sym = {executor.submit(scan_worker, sym): sym for sym in symbols}
        
        for future in as_completed(future_to_sym):
            processed_count += 1
            sym = future_to_sym[future]
            
            if progress_callback:
                try:
                    progress_callback(processed_count, total, symbol_to_display_name(sym))
                except Exception:
                    pass

            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                print(f"Future error for {sym}: {e}")

    if not results:
        df_results = pd.DataFrame()
    else:
        df_results = pd.DataFrame(results)

    # 6. Save to Cache
    if use_cache:
        save_results_to_cache(
            symbols=symbols,
            scan_date=scan_date,
            resolution=resolution,
            strategies=selected_strategies,
            logic=logic,
            params=strategy_params,
            results_df=df_results
        )

    return df_results


def get_indicator_snapshot(symbol_row: dict) -> pd.DataFrame:
    """Extract indicator snapshot from a scanner result row"""
    details = symbol_row.get("_details", {})
    rows = []
    for strategy, d in details.items():
        if isinstance(d, dict):
            for k, v in d.items():
                rows.append({"Strategy": str(strategy), "Parameter": str(k), "Value": str(v)})
    return pd.DataFrame(rows)

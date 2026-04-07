"""
core/strategy_engine.py — 5 built-in strategies + multi-strategy combiner

Each strategy function:
  - Accepts a DataFrame with all indicators already added (via add_all_indicators)
  - Evaluates the LAST COMPLETED CANDLE (df.iloc[-1])
  - Returns (matched: bool, details: dict)
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class StrategyResult:
    matched: bool
    strategy_name: str
    details: dict = field(default_factory=dict)


# ─── Individual Strategies ───────────────────────────────────────────────────

def strategy_higher_high(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    Higher High Strategy:
    - Current candle close > Previous candle high
    - Current candle is green
    """
    name = "Higher High"
    if len(df) < 2:
        return StrategyResult(False, name, {"error": "Not enough data"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close_above_prev_high = last["close"] > prev["high"]
    is_green = last["is_green"]

    matched = close_above_prev_high and is_green
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "close": round(last["close"], 2),
            "prev_high": round(prev["high"], 2),
            "is_green": bool(is_green),
            "close_above_prev_high": close_above_prev_high,
        },
    )


def strategy_strong_bullish_candle(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    Strong Bullish Candle:
    - Last candle is green
    - Body > min_body_pct% of total candle range (default: 60%)
    """
    name = "Strong Bullish Candle"
    min_body_pct = params.get("min_body_pct", 60.0)

    if len(df) < 1:
        return StrategyResult(False, name, {"error": "Not enough data"})

    last = df.iloc[-1]
    is_green = bool(last["is_green"])
    body_pct = last["body_pct"] if not np.isnan(last["body_pct"]) else 0.0
    strong_body = body_pct >= min_body_pct

    matched = is_green and strong_body
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "close": round(last["close"], 2),
            "open": round(last["open"], 2),
            "body_pct": round(body_pct, 1),
            "min_body_pct": min_body_pct,
            "is_green": is_green,
        },
    )


def strategy_sma50_support_bounce(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    50 SMA Support Bounce:
    - Price within ±X% of SMA50 (default: 2%)
    - Previous candle was red
    - Current candle is green (bounce confirmation)
    """
    name = "50 SMA Support Bounce"
    proximity_pct = params.get("proximity_pct", 2.0)

    if len(df) < 51:
        return StrategyResult(False, name, {"error": "Not enough data for SMA50"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    sma50 = last["sma_50"]
    if np.isnan(sma50):
        return StrategyResult(False, name, {"error": "SMA50 is NaN"})

    price_to_sma_pct = abs(last["close"] - sma50) / sma50 * 100
    near_sma = price_to_sma_pct <= proximity_pct
    prev_red = bool(prev["is_red"])
    curr_green = bool(last["is_green"])

    matched = near_sma and prev_red and curr_green
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "close": round(last["close"], 2),
            "sma_50": round(sma50, 2),
            "price_to_sma_pct": round(price_to_sma_pct, 2),
            "proximity_pct": proximity_pct,
            "prev_red": prev_red,
            "curr_green": curr_green,
        },
    )


def strategy_rsi_momentum(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    RSI Momentum:
    - RSI > threshold (default: 60)
    - RSI rising (RSI > prev RSI)
    """
    name = "RSI Momentum"
    rsi_threshold = params.get("rsi_threshold", 60.0)

    if len(df) < 16:
        return StrategyResult(False, name, {"error": "Not enough data for RSI"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    rsi_val = last["rsi_14"]
    rsi_prev = prev["rsi_14"]

    if np.isnan(rsi_val) or np.isnan(rsi_prev):
        return StrategyResult(False, name, {"error": "RSI is NaN"})

    above_threshold = rsi_val > rsi_threshold
    rsi_rising = rsi_val > rsi_prev

    matched = above_threshold and rsi_rising
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "close": round(last["close"], 2),
            "rsi": round(rsi_val, 2),
            "rsi_prev": round(rsi_prev, 2),
            "rsi_threshold": rsi_threshold,
            "rsi_rising": rsi_rising,
        },
    )


def strategy_volume_spike_breakout(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    Volume Spike Breakout:
    - Volume > X× average volume (default: 2x)
    - Current candle is green
    - Close > previous close
    """
    name = "Volume Spike Breakout"
    vol_multiplier = params.get("vol_multiplier", 2.0)

    if len(df) < 22:
        return StrategyResult(False, name, {"error": "Not enough data for volume average"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    vol_ratio = last["vol_ratio"]
    if np.isnan(vol_ratio):
        return StrategyResult(False, name, {"error": "Volume ratio is NaN"})

    volume_spike = vol_ratio >= vol_multiplier
    is_green = bool(last["is_green"])
    close_above_prev = last["close"] > prev["close"]

    matched = volume_spike and is_green and close_above_prev
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "close": round(last["close"], 2),
            "volume": int(last["volume"]),
            "vol_avg_20": round(last["vol_avg_20"], 0),
            "vol_ratio": round(vol_ratio, 2),
            "vol_multiplier": vol_multiplier,
            "is_green": is_green,
            "close_above_prev": close_above_prev,
        },
    )


# ─── Strategy Registry ───────────────────────────────────────────────────────

STRATEGIES: dict[str, Callable] = {
    "Higher High": strategy_higher_high,
    "Strong Bullish Candle": strategy_strong_bullish_candle,
    "50 SMA Support Bounce": strategy_sma50_support_bounce,
    "RSI Momentum": strategy_rsi_momentum,
    "Volume Spike Breakout": strategy_volume_spike_breakout,
}

STRATEGY_PARAMS: dict[str, dict] = {
    "Higher High": {},
    "Strong Bullish Candle": {"min_body_pct": 60.0},
    "50 SMA Support Bounce": {"proximity_pct": 2.0},
    "RSI Momentum": {"rsi_threshold": 60.0},
    "Volume Spike Breakout": {"vol_multiplier": 2.0},
}

STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "Higher High": "🚀 Bullish Continuation: Current candle closed above the previous high. Signals that buyers are aggressively pushing to new territory.",
    "Strong Bullish Candle": "💪 Buying Conviction: Large body with tiny wicks. Indicates that the stock opened low and closed near its high with zero selling pressure.",
    "50 SMA Support Bounce": "🔥 Trend Support: Price touched or neared the 50 SMA and bounced back with a green candle. Classic 'Buy the Dip' setup in an uptrend.",
    "RSI Momentum": "⚡ High Velocity: RSI is above your threshold (default 60) and heading higher. Best for catching stocks in a 'Runaway' phase.",
    "Volume Spike Breakout": "📊 Institutional Entry: Volume is at least 2x higher than the 20-day average. Combined with a green close, it signals major smart money activity.",
}


def run_strategies(
    df: pd.DataFrame,
    selected_strategies: list[str],
    logic: str = "OR",  # "AND" or "OR"
    params: dict = {},
) -> tuple[bool, list[StrategyResult]]:
    """
    Run multiple strategies on a DataFrame.
    Returns (overall_match: bool, results: list[StrategyResult])
    """
    results = []
    for name in selected_strategies:
        fn = STRATEGIES.get(name)
        if fn is None:
            continue
        strategy_params = {**STRATEGY_PARAMS.get(name, {}), **params.get(name, {})}
        result = fn(df, strategy_params)
        results.append(result)

    if not results:
        return False, []

    if logic == "AND":
        overall = all(r.matched for r in results)
    else:
        overall = any(r.matched for r in results)

    return overall, results

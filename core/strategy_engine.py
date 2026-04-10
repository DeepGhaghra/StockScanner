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

def find_pivots(df: pd.DataFrame, strength: int = 5):
    """
    Find major swing highs and swing lows (Pivot Points).
    A high is a pivot high if it is higher than 'strength' candles before and after.
    """
    highs = []
    lows = []
    
    for i in range(strength, len(df) - strength):
        # Pivot High
        is_pivot_high = True
        for j in range(1, strength + 1):
            if df.iloc[i]["high"] <= df.iloc[i-j]["high"] or df.iloc[i]["high"] <= df.iloc[i+j]["high"]:
                is_pivot_high = False
                break
        if is_pivot_high:
            highs.append({"index": i, "price": df.iloc[i]["high"], "date": df.iloc[i]["datetime"]})

        # Pivot Low
        is_pivot_low = True
        for j in range(1, strength + 1):
            if df.iloc[i]["low"] >= df.iloc[i-j]["low"] or df.iloc[i]["low"] >= df.iloc[i+j]["low"]:
                is_pivot_low = False
                break
        if is_pivot_low:
            lows.append({"index": i, "price": df.iloc[i]["low"], "date": df.iloc[i]["datetime"]})
            
    return highs, lows


def strategy_dow_trend(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    Dow Theory Structural Trend:
    - Higher High (HH): Latest major peak > previous major peak.
    - Higher Low (HL): Latest major trough > previous major trough.
    - Confirmation: Price is currently trending upwards.
    """
    name = "Dow Trend (HH/HL)"
    strength = params.get("pivot_strength", 5)
    
    if len(df) < (strength * 4):
        return StrategyResult(False, name, {"error": "Not enough data for pivots"})

    peaks, troughs = find_pivots(df, strength=strength)
    
    if len(peaks) < 2 or len(troughs) < 2:
        return StrategyResult(False, name, {"error": "Structure not fully formed"})

    # Latest and Previous PIVOTS
    last_peak = peaks[-1]
    prev_peak = peaks[-2]
    last_trough = troughs[-1]
    prev_trough = troughs[-2]

    is_hh = last_peak["price"] > prev_peak["price"]
    is_hl = last_trough["price"] > prev_trough["price"]
    
    # Confirmation: We are in an active uptrend structure
    # Most basic: Current close is above the latest trough
    last_close = df.iloc[-1]["close"]
    above_structure = last_close > last_trough["price"]

    matched = is_hh and is_hl and above_structure
    
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "structure": "HH + HL" if is_hh and is_hl else "Broken",
            "last_hh": round(last_peak["price"], 2),
            "prev_peak": round(prev_peak["price"], 2),
            "last_hl": round(last_trough["price"], 2),
            "prev_trough": round(prev_trough["price"], 2),
            "strength": strength,
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


def strategy_abc_long(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    ABC Long:
    - 50 SMA is rising (Trend check)
    - Price at confluence of 50 SMA and Lower Bollinger Band (within proximity)
    - Trigger: Green candle (Bullish) after a touch (Today or Yesterday)
    """
    name = "ABC Long"
    proximity_pct = params.get("abc_proximity_pct", 1.0)

    if len(df) < 53:
        return StrategyResult(False, name, {"error": "Not enough data for SMA50"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # 1. Trend: SMA 50 must be rising
    sma50_curr = last["sma_50"]
    sma50_prev = prev["sma_50"]
    is_rising = sma50_curr > sma50_prev

    # 2. Confluence Zone Logic (Function to reuse for last and prev)
    def check_near_confluence(candle, proximity):
        sma = candle["sma_50"]
        bb_low = candle["bb_lower"]
        if np.isnan(sma) or np.isnan(bb_low): return False
        
        # Confluence: SMA and BB Lower are close
        dist_sma_bb = abs(sma - bb_low) / sma * 100
        # Price: Low of candle is near the zone
        zone_mid = (sma + bb_low) / 2
        dist_to_zone = abs(candle["low"] - zone_mid) / zone_mid * 100
        
        return (dist_sma_bb <= 2.5) and (dist_to_zone <= proximity)

    # 3. Check for Confluence (Today OR Yesterday)
    near_conf_today = check_near_confluence(last, proximity_pct)
    near_conf_yesterday = check_near_confluence(prev, proximity_pct)
    
    # 4. Trigger: Green candle (Bullish confirmation)
    is_bullish = bool(last["is_green"])

    matched = is_rising and (near_conf_today or near_conf_yesterday) and is_bullish
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "price": round(last["close"], 2),
            "sma50": round(sma50_curr, 2),
            "conf_today": near_conf_today,
            "conf_yest": near_conf_yesterday,
            "is_rising": bool(is_rising),
        },
    )


def strategy_abc_short(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    ABC Short:
    - 50 SMA is falling (Trend check)
    - Price at confluence of 50 SMA and Upper Bollinger Band
    - Trigger: Red candle (Bearish) after a touch (Today or Yesterday)
    """
    name = "ABC Short"
    proximity_pct = params.get("abc_proximity_pct", 1.0)

    if len(df) < 53:
        return StrategyResult(False, name, {"error": "Not enough data for SMA50"})

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # 1. Trend: SMA 50 must be falling
    sma50_curr = last["sma_50"]
    sma50_prev = prev["sma_50"]
    is_falling = sma50_curr < sma50_prev

    # 2. Confluence Zone Logic (Function to reuse for last and prev)
    def check_near_confluence_short(candle, proximity):
        sma = candle["sma_50"]
        bb_up = candle["bb_upper"]
        if np.isnan(sma) or np.isnan(bb_up): return False
        
        # Confluence: SMA and BB Upper are close
        dist_sma_bb = abs(sma - bb_up) / sma * 100
        # Price: High of candle is near the zone
        zone_mid = (sma + bb_up) / 2
        dist_to_zone = abs(candle["high"] - zone_mid) / zone_mid * 100
        
        return (dist_sma_bb <= 2.5) and (dist_to_zone <= proximity)

    # 3. Check for Confluence (Today OR Yesterday)
    near_conf_today = check_near_confluence_short(last, proximity_pct)
    near_conf_yesterday = check_near_confluence_short(prev, proximity_pct)
    
    # 4. Trigger: Red candle (Bearish confirmation)
    is_bearish = bool(last["is_red"])

    matched = is_falling and (near_conf_today or near_conf_yesterday) and is_bearish
    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "price": round(last["close"], 2),
            "sma50": round(sma50_curr, 2),
            "conf_today": near_conf_today,
            "conf_yest": near_conf_yesterday,
            "is_falling": is_falling,
        },
    )


def strategy_ath_proximity(df: pd.DataFrame, params: dict = {}) -> StrategyResult:
    """
    All Time High (ATH) Proximity:
    - Finds the highest 'high' in the entire provided history.
    - Matches if current 'close' is within X% of that ATH.
    """
    name = "All Time High Proximity"
    threshold_pct = params.get("ath_threshold_pct", 5.0)

    if len(df) < 10:
        return StrategyResult(False, name, {"error": "Not enough data"})

    # Calculate ATH from full available history
    ath_price = df["high"].max()
    current_price = df.iloc[-1]["close"]
    
    if ath_price == 0:
        return StrategyResult(False, name, {"error": "ATH is 0"})

    distance_pct = ((ath_price - current_price) / ath_price) * 100
    matched = distance_pct <= threshold_pct

    return StrategyResult(
        matched=matched,
        strategy_name=name,
        details={
            "current": round(current_price, 2),
            "ath_price": round(ath_price, 2),
            "dist_pct": f"{round(distance_pct, 2)}%",
            "threshold": f"{threshold_pct}%",
        },
    )


# ─── Strategy Registry ───────────────────────────────────────────────────────

STRATEGIES: dict[str, Callable] = {
    "Dow Trend (HH/HL)": strategy_dow_trend,
    "Strong Bullish Candle": strategy_strong_bullish_candle,
    "50 SMA Support Bounce": strategy_sma50_support_bounce,
    "RSI Momentum": strategy_rsi_momentum,
    "Volume Spike Breakout": strategy_volume_spike_breakout,
    "ABC Long": strategy_abc_long,
    "ABC Short": strategy_abc_short,
    "All Time High Proximity": strategy_ath_proximity,
}

STRATEGY_PARAMS: dict[str, dict] = {
    "Dow Trend (HH/HL)": {"pivot_strength": 5},
    "Strong Bullish Candle": {"min_body_pct": 60.0},
    "50 SMA Support Bounce": {"proximity_pct": 2.0},
    "RSI Momentum": {"rsi_threshold": 60.0},
    "Volume Spike Breakout": {"vol_multiplier": 2.0},
    "ABC Long": {"abc_proximity_pct": 1.0},
    "ABC Short": {"abc_proximity_pct": 1.0},
    "All Time High Proximity": {"ath_threshold_pct": 5.0},
}

STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "Dow Trend (HH/HL)": "📈 Structural Uptrend: Detects a sequence of Higher Highs and Higher Lows according to Dow Theory. Confirms the stock is in a healthy structural bull trend.",
    "Strong Bullish Candle": "💪 Buying Conviction: Large body with tiny wicks. Indicates that the stock opened low and closed near its high with zero selling pressure.",
    "50 SMA Support Bounce": "🔥 Trend Support: Price touched or neared the 50 SMA and bounced back with a green candle. Classic 'Buy the Dip' setup in an uptrend.",
    "RSI Momentum": "⚡ High Velocity: RSI is above your threshold (default 60) and heading higher. Best for catching stocks in a 'Runaway' phase.",
    "Volume Spike Breakout": "📊 Institutional Entry: Volume is at least 2x higher than the 20-day average. Combined with a green close, it signals major smart money activity.",
    "ABC Long": "🅰️ Bullish Confluence: Green candle at the exact point where 50 SMA (rising) meets the Lower Bollinger Band.",
    "ABC Short": "🅾️ Bearish Confluence: Red candle at the exact point where 50 SMA (falling) meets the Upper Bollinger Band.",
    "All Time High Proximity": "🏔️ Multi-Year Peak: Detects stocks currently trading near their absolute historical high (since 1994). Essential for checking Blue-Sky breakouts.",
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

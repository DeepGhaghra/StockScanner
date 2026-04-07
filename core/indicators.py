"""
core/indicators.py — Technical indicator calculations (pure pandas/numpy)
"""
import numpy as np
import pandas as pd


def sma(df: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    """Simple Moving Average"""
    return df[col].rolling(window=period, min_periods=period).mean()


def ema(df: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    """Exponential Moving Average"""
    return df[col].ewm(span=period, adjust=False).mean()


def rsi(df: pd.DataFrame, period: int = 14, col: str = "close") -> pd.Series:
    """Relative Strength Index"""
    delta = df[col].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series


def bollinger_bands(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0, col: str = "close"
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower) Bollinger Bands"""
    middle = df[col].rolling(window=period, min_periods=period).mean()
    std = df[col].rolling(window=period, min_periods=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Rolling average volume"""
    return df["volume"].rolling(window=period, min_periods=period).mean()


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds all standard indicator columns to DataFrame.
    Returns modified DataFrame.
    """
    df = df.copy()

    df["sma_20"] = sma(df, 20)
    df["sma_50"] = sma(df, 50)
    df["sma_200"] = sma(df, 200)
    df["ema_9"] = ema(df, 9)
    df["ema_21"] = ema(df, 21)
    df["rsi_14"] = rsi(df, 14)

    bb_upper, bb_mid, bb_lower = bollinger_bands(df, 20, 2.0)
    df["bb_upper"] = bb_upper
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_lower

    df["vol_avg_20"] = volume_sma(df, 20)
    df["vol_ratio"] = df["volume"] / df["vol_avg_20"].replace(0, np.nan)

    # Candle metrics
    df["body_size"] = abs(df["close"] - df["open"])
    df["candle_range"] = df["high"] - df["low"]
    df["body_pct"] = (df["body_size"] / df["candle_range"].replace(0, np.nan)) * 100
    df["is_green"] = df["close"] > df["open"]
    df["is_red"] = df["close"] < df["open"]

    return df

"""utils/helpers.py — Utility functions"""
import re
from datetime import datetime, date


def symbol_to_display_name(symbol: str) -> str:
    """Convert 'NSE:RELIANCE-EQ' → 'RELIANCE'"""
    match = re.search(r":([^-]+)", symbol)
    return match.group(1) if match else symbol


def format_date_for_fyers(d) -> str:
    """Convert date/datetime to 'YYYY-MM-DD' string"""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return str(d)


def resolution_label(res: str) -> str:
    mapping = {
        "1": "1 Min", "5": "5 Min", "15": "15 Min",
        "30": "30 Min", "60": "1 Hour", "D": "Daily", "W": "Weekly"
    }
    return mapping.get(res, res)


def candle_body_pct(open_: float, close: float, high: float, low: float) -> float:
    """Body size as percentage of total candle range"""
    total_range = high - low
    if total_range == 0:
        return 0.0
    body = abs(close - open_)
    return (body / total_range) * 100

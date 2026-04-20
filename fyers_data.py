import time
import os
import pandas as pd
from fyers_apiv3 import fyersModel
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

# BASE_DIR for portability
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
DATA_DIR = os.getenv("DATA_INPUT_PATH", os.path.join(BASE_DIR, 'data', 'daily'))

from fyers_auth import get_access_token

_fyers_session_cache = None

def get_session(force_refresh=False):
    """Reads the saved access token and caches the session in memory."""
    global _fyers_session_cache
    
    if _fyers_session_cache is not None and not force_refresh:
        return _fyers_session_cache

    token_file = os.path.join(BASE_DIR, "access_token.txt")
    
    if not os.path.exists(token_file) or force_refresh:
        print("Wait: Access Token is missing or refresh requested. Starting generation...")
        token = get_access_token()
        if not token:
            return None
    else:
        try:
            with open(token_file, "r") as f:
                token = f.read().strip()
        except Exception as e:
            print(f"Error reading access_token.txt: {e}")
            token = get_access_token()
    
    if not token:
        return None

    try:
        _fyers_session_cache = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path="")
        # Minimal validation call
        profile = _fyers_session_cache.get_profile()
        if profile.get("s") != "ok":
            print(f"⚠️ Token might be expired. {profile.get('message', 'Retrying...')}")
            token = get_access_token()
            if token:
                _fyers_session_cache = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path="")
            else:
                _fyers_session_cache = None
        
        return _fyers_session_cache
    except Exception as e:
        print(f"Error initializing Fyers session: {e}")
        return None


# Common NSE symbols can be in different segments
def update_stock_data(symbol, fyers=None, retry_count=3):
    if not fyers:
        fyers = get_session()
    if not fyers: return False

    symbol_upper = symbol.upper().replace("&", "_") # Fyers treats & as _ in some symbols
    
    # Try different segments if the first one fails
    # -EQ (Regular), -BE (Trade-for-Trade), -BZ (Restricted), empty
    segments = ["-EQ", "-BE", "-BZ", ""] 
    
    file_path = os.path.join(DATA_DIR, f"{symbol_upper}.csv")
    
    # Determine date range
    to_date = datetime.now().strftime("%Y-%m-%d")
    try:
        if os.path.exists(file_path):
            existing_df = pd.read_csv(file_path)
            if existing_df.empty or 'Date' not in existing_df.columns:
                from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            else:
                last_date = pd.to_datetime(existing_df['Date']).max()
                from_date = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    except:
        from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    if from_date >= to_date:
        return True

    # Try each segment until success
    for segment in segments:
        fyers_symbol = f"NSE:{symbol_upper}{segment}"
        
        data = {
            "symbol": fyers_symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": from_date,
            "range_to": to_date,
            "cont_flag": "1"
        }

        success = False
        for attempt in range(retry_count):
            try:
                response = fyers.history(data=data)
                
                if response.get("s") == "ok":
                    candles = response.get("candles")
                    if candles:
                        new_df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                        new_df['Date'] = pd.to_datetime(new_df['Timestamp'], unit='s').dt.strftime('%Y-%m-%d')
                        new_df = new_df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
                        
                        if os.path.exists(file_path):
                            final_df = pd.concat([pd.read_csv(file_path), new_df]).drop_duplicates(subset=['Date']).sort_values(by='Date')
                        else:
                            final_df = new_df
                            
                        final_df.to_csv(file_path, index=False)
                    
                    print(f"✅ {symbol} updated successfully using {fyers_symbol}.")
                    time.sleep(0.1) 
                    return True # Full success
                
                elif response.get("code") == 429:
                    wait_time = (attempt + 1) * 2
                    print(f"⚠️ Rate limit hit for {symbol}. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                
                elif response.get("code") in [401, -16, -17] or "authenticate" in str(response.get("message", "")).lower():
                    print(f"🔑 Authentication error for {symbol}. Attempting to refresh token...")
                    fyers = get_session(force_refresh=True)
                    if fyers:
                        continue # Retry this symbol with new session
                    else:
                        print("❌ Failed to refresh session. Aborting.")
                        return False

                
                else:
                    print(f"❓ Fyers Response for {symbol} ({fyers_symbol}): {response}")
                    # If it's an invalid symbol error, we break the retry loop and try next segment
                    msg = str(response.get("message", "")).lower()
                    if "invalid symbol" in msg or "symbol not found" in msg:
                        break 
                    
                    break # Generic error, stop retrying this segment
                    
            except Exception as e:
                print(f"Traceback Error for {symbol}: {e}")
                break

    print(f"Failed to update {symbol} after trying all segments.")
    return False

if __name__ == "__main__":
    my_stocks = ["RELIANCE", "TCS", "SBIN", "RELINFRA", "SCHNEIDER"]
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(update_stock_data, s): s for s in my_stocks}
        for future in as_completed(futures):
            s = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error for {s}: {e}")

import requests
from bs4 import BeautifulSoup
import json
import time
import os
import sys

# Add parent dir to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database import StockDatabase

# Standard headers to avoid blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}

def scrape_screener_sectors():
    base_url = "https://www.screener.in"
    market_url = f"{base_url}/market/"
    
    print(f"Fetching market sectors from {market_url}...")
    try:
        resp = requests.get(market_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"Failed to fetch market page: {resp.status_code}")
            return
    except Exception as e:
        print(f"Network error: {e}")
        return
        
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # Find all industry links (/market/IN...)
    industry_links = []
    for a in soup.find_all("a", href=True):
        href = a['href']
        if "/market/IN" in href:
            industry_links.append(f"{base_url}{href}")
    
    industry_links = sorted(list(set(industry_links)))
    print(f"Found {len(industry_links)} industry categories.")
    
    sector_map = {}
    
    # Create data directory if missing
    os.makedirs("data", exist_ok=True)

    count = 0
    total = len(industry_links)
    
    for url in industry_links:
        count += 1
        print(f"[{count}/{total}] Scraping industry...", end="\r")
        
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200: continue
            
            s = BeautifulSoup(res.text, "html.parser")
            
            # Extract industry name
            headline = s.find("h1")
            industry_name = headline.text.strip() if headline else "Others"
            
            # Find symbols
            for a in s.find_all("a", href=True):
                if "/company/" in a['href']:
                    parts = a['href'].split('/')
                    if len(parts) >= 3:
                        symbol = parts[2].strip().upper()
                        fyers_sym = f"NSE:{symbol}-EQ"
                        sector_map[fyers_sym] = industry_name
            
            time.sleep(0.3)
        except Exception as e:
            print(f"\nError on {url}: {e}")

    print(f"\nDone! Mapped {len(sector_map)} symbols to detailed industries.")
    
    # Save to Database
    db = StockDatabase()
    print(f"Saving to database {db.db_path}...")
    db.save_sectors(sector_map, source="Screener")
    
    print(f"Database updated successfully!")

if __name__ == "__main__":
    scrape_screener_sectors()

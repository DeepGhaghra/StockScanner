"""
core/database.py — SQLite manager for historical OHLCV data
"""
import sqlite3
import pandas as pd
import os
from datetime import datetime

class StockDatabase:
    def __init__(self, db_path="data/stock_scanner.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            # Table for OHLCV data
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT,
                    resolution TEXT,
                    datetime TIMESTAMP,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    PRIMARY KEY (symbol, resolution, datetime)
                )
            """)
            # Index for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol ON ohlcv (symbol, resolution)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_datetime ON ohlcv (datetime)")
            
            # Metadata for sync tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    def save_candles(self, symbol: str, resolution: str, df: pd.DataFrame):
        """Upsert candles into the database"""
        if df.empty:
            return
            
        # Ensure correct types and index
        df = df.copy()
        df['symbol'] = symbol
        df['resolution'] = resolution
        
        with self._get_connection() as conn:
            # Use 'REPLACE' to handle updates to existing timestamps
            df.to_sql('ohlcv', conn, if_exists='append', index=False, method=self._upsert_method)

    def _upsert_method(self, table, conn, keys, data_iter):
        """Custom multi-row insert with REPLACE for SQLite"""
        from sqlite3 import IntegrityError
        sql = f"REPLACE INTO {table.name} ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})"
        conn.executemany(sql, data_iter)

    def get_history(self, symbol: str, resolution: str, start_date: datetime = None, end_date: datetime = None) -> pd.DataFrame:
        """Fetch historical data from DB as DataFrame"""
        query = "SELECT datetime, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND resolution = ?"
        params = [symbol, resolution]
        
        if start_date:
            query += " AND datetime >= ?"
            params.append(start_date.strftime("%Y-%m-%d %H:%M:%S"))
        if end_date:
            query += " AND datetime <= ?"
            params.append(end_date.strftime("%Y-%m-%d %H:%M:%S"))
            
        query += " ORDER BY datetime ASC"
        
        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params, parse_dates=['datetime'])
            return df

    def get_last_date(self, symbol: str, resolution: str) -> datetime | None:
        """Get the timestamp of the latest candle stored"""
        query = "SELECT MAX(datetime) FROM ohlcv WHERE symbol = ? AND resolution = ?"
        with self._get_connection() as conn:
            res = conn.execute(query, (symbol, resolution)).fetchone()
            if res and res[0]:
                return pd.to_datetime(res[0])
            return None

    def get_db_stats(self):
        """Get summary of stored data"""
        with self._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv").fetchone()[0]
            return {"total_rows": count, "total_symbols": symbols}

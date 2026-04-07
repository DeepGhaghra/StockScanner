"""
app.py — Smart Stock Scanner — Streamlit Dashboard
"""
import json
import time
import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Smart Stock Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Imports ─────────────────────────────────────────────────────────────────
from auth import (
    generate_auth_url,
    exchange_code_for_token,
    extract_auth_code_from_url,
    load_token,
    get_token_info,
)
from core.data_fetcher import fetch_ohlcv, get_fyers_client, clear_cache
from core.indicators import add_all_indicators
from core.scanner import run_scan
from core.strategy_engine import STRATEGIES, STRATEGY_DESCRIPTIONS, STRATEGY_PARAMS
from core.symbol_manager import (
    get_available_indices,
    get_symbols,
    get_cache_status,
    refresh_all_indices,
    get_custom_watchlist_symbols,
)

# ─── Load Custom CSS ─────────────────────────────────────────────────────────
def load_css(file_name):
    with open(file_name) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css(os.path.join("assets", "style.css"))


# ─── Constants ────────────────────────────────────────────────────────────────
WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "data", "custom_watchlist.csv")
ALL_INDICES = get_available_indices()
CUSTOM_OPT = "📋 Custom Watchlist"


# ─── Session State ────────────────────────────────────────────────────────────
for key, default in [
    ("scan_results", None),
    ("scan_meta", {}),
    ("fyers_client", None),
    ("symbols_loaded", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def get_fyers():
    if st.session_state.fyers_client:
        return st.session_state.fyers_client
    token = load_token()
    if token:
        client = get_fyers_client(token)
        st.session_state.fyers_client = client
        return client
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def cached_get_symbols(index_name: str) -> list[str]:
    """Streamlit-level cache on top of file cache (1hr refresh in UI)"""
    if index_name == CUSTOM_OPT:
        return get_custom_watchlist_symbols()
    return get_symbols(index_name)


def render_metric_card(label: str, value, col):
    col.markdown(
        f"""<div class="metric-card">
            <div class="metric-value">{value}</div>
            <div class="metric-label">{label}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def make_candlestick(candles: list[dict], symbol: str) -> go.Figure:
    df = pd.DataFrame(candles)
    fig = go.Figure(data=[go.Candlestick(
        x=df["datetime"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color="#34d399", decreasing_line_color="#f87171",
        increasing_fillcolor="#34d399", decreasing_fillcolor="#f87171",
        name=symbol,
    )])
    fig.update_layout(
        title=f"{symbol} — Last 10 Candles", template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,15,35,0.6)",
        height=300, margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        font=dict(family="Inter", color="#94a3b8"),
    )
    return fig


def display_name(sym: str) -> str:
    import re
    m = re.search(r":([^-]+)", sym)
    return m.group(1) if m else sym


# ─── Header ──────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([7, 2])
with col_title:
    st.markdown(
        "<h1 style='margin:0;color:#0f172a;font-size:2rem;font-weight:700;letter-spacing:-0.02em;'>📈 Smart Stock Scanner</h1>"
        "<p style='margin:0;color:#64748b;font-size:0.85rem;font-weight:500;'>Multi-Strategy Analysis • Fyers API v3</p>",
        unsafe_allow_html=True,
    )
with col_status:
    info = get_token_info()
    if info["valid"]:
        expires = datetime.fromisoformat(info["expires_at"])
        remaining = expires - datetime.now()
        h, rem = divmod(int(remaining.total_seconds()), 3600)
        st.markdown(f'<p style="color:#166534;font-weight:600;margin:0;text-align:right;">● Token Active <span style="font-size:0.7rem;color:#64748b;font-weight:400;margin-left:5px;">Exp: {h}h {rem//60}m</span></p>', unsafe_allow_html=True)
    else:
        st.markdown('<p style="color:#ef4444;font-weight:600;margin:0;text-align:right;">● Token Required</p>', unsafe_allow_html=True)

st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab_scanner, tab_hunter, tab_details, tab_indices, tab_settings, tab_watchlist = st.tabs(
    ["🔍 Scanner", "🧬 Signal Hunter", "📊 Results Detail", "📑 Index Manager", "⚙️ Settings & Auth", "📂 Watchlist"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: SIGNAL HUNTER (REVERSE SCAN)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_hunter:
    st.markdown("### 🧬 Signal Hunter (Historical Backtest)")
    st.info("Select a stock and a strategy to find all historical trade dates.")

    h1, h2, h3, h4 = st.columns([2, 2, 2, 1])
    with h1:
        final_sym = st.text_input("🎯 Enter Stock Symbol", value="NSE:SBIN-EQ", placeholder="e.g. NSE:RELIANCE or BSE:532174").upper()
        st.caption("💡 NSE:SBIN-EQ, BSE:500112-B")

    with h2:
        h_strat = st.selectbox("🎯 Strategy", options=list(STRATEGIES.keys()), key="h_st")
        h_res = st.selectbox("⏳ Timeframe", options=["15", "60", "1D", "1W", "1M"], index=2, key="h_rs")
    
    with h3:
        h_lookback = st.selectbox("📅 Search Period", options=["3 Months", "6 Months", "1 Year", "2 Years", "Max"], index=2)
        
    with h4:
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        hunt_btn = st.button("🔥 HUNT", use_container_width=True, type="primary")

    if hunt_btn:
        fyers = get_fyers()
        if not fyers:
            st.error("Please login first!")
        else:
            with st.spinner(f"Hunting {h_strat} signals for {final_sym}..."):
                # Fetch more data for backtesting (Buffer for SMA)
                days_map = {"3 Months": 90, "6 Months": 180, "1 Year": 365, "2 Years": 730, "Max": 1500}
                lookback_days = days_map.get(h_lookback, 365)
                
                # Normalize symbol
                clean_sym = final_sym.strip().upper()
                
                # Full history is loaded from DB (since 1994)
                df = fetch_ohlcv(fyers, clean_sym, h_res, date.today(), lookback_days + 100)
                
                if df is not None and len(df) > 52:
                    df = add_all_indicators(df)
                    
                    # Optimization: Only run backtest on the user's requested period
                    # Filter data to start from (today - lookback_days)
                    start_date_limit = pd.Timestamp(date.today() - timedelta(days=lookback_days))
                    backtest_df = df[df["datetime"] >= start_date_limit].copy()
                    
                    # We still need the original df for ATH calculation within the strategy
                    # But the loop should only run on backtest_df indices
                    
                    strat_func = STRATEGIES[h_strat]
                    strat_params = STRATEGY_PARAMS.get(h_strat, {})

                    signals = []
                    # Find where backtest_df starts in the main df
                    if not backtest_df.empty:
                        start_offset = df.index[df['datetime'] == backtest_df.iloc[0]['datetime']][0]
                        # Start from offset OR 52 (indicator buffer), whichever is later
                        start_pos = max(start_offset, 52)
                        
                        for i in range(start_pos, len(df)):
                            sub_df = df.iloc[:i+1]
                            res = strat_func(sub_df, strat_params)
                            if res.matched:
                                row = df.iloc[i]
                                signals.append({
                                    "Date": row["datetime"].strftime("%d-%m-%Y %H:%M:%S"),
                                    "Price": round(row["close"], 2),
                                    "Details": ", ".join([f"{k}: {v}" for k, v in res.details.items() if k != "close" and k != "price"])
                                })
                    
                    if signals:
                        st.success(f"Found {len(signals)} signals!")
                        signals_df = pd.DataFrame(signals).iloc[::-1]
                        st.dataframe(signals_df, use_container_width=True)
                    else:
                        st.info("No signals found in the selected period.")
                        with st.expander("🛠️ Debug Information (Why 0 signals?)", expanded=False):
                            st.write(f"Symbol: `{final_sym}`")
                            st.write(f"Rows fetched after parsing: `{len(df)}`")
                            st.write(f"Strategy: `{h_strat}`")
                            st.write(f"Resolution Used: `{h_res}`")
                else:
                    st.error("No Data Found or Symbol Invalid!")
                    with st.expander("🚨 Detailed Error Log", expanded=True):
                        st.write(f"User Input: `{final_sym}`")
                        st.write(f"Timeframe: `{h_res}`")
                        st.write(f"Data Status: `{'API EMPTY (0 rows)' if df is not None else 'API ERROR (None response)'}`")
                        st.write(f"Lookback: `{lookback_days} days`")
                        if st.button("Force Clear Resolver Cache", help="Click if symbols are stuck"):
                            clear_cache()
                            st.rerun()
                            st.rerun()
with tab_scanner:

    # ─── Scanner Command Center (Main View) ──────────────────────────────────
    st.markdown("<div class='section-header'>Scanner Command Center</div>", unsafe_allow_html=True)
    
    # Grid for main controls + RUN Button
    c1, c2, c3, c4, c5 = st.columns([1.6, 1.4, 2.5, 2.5, 1.8])
    
    with c1:
        scan_date = st.date_input("📅 Date", value=date.today(), max_value=date.today())

    with c2:
        resolution = st.selectbox(
            "⏱️ TF",
            options=["M", "W", "D", "60", "30", "15", "5", "1"],
            format_func=lambda x: {"1":"1m","5":"5m","15":"15m","30":"30m","60":"1h","D":"Day","W":"Week","M":"Month"}.get(x, x),
            index=2 # Keep Daily as default
        )

    with c3:
        stock_group = st.selectbox("📂 Index", options=ALL_INDICES + [CUSTOM_OPT], index=0)
        symbols = cached_get_symbols(stock_group)

    with c4:
        selected_strategies = st.multiselect("🎯 Setup", options=list(STRATEGIES.keys()), default=["Higher High"])
        
        # Guide Preview (Small text below selection)
        if selected_strategies:
            last_sel = selected_strategies[-1]
            st.caption(f"ℹ️ {STRATEGY_DESCRIPTIONS.get(last_sel, '').split(':')[0]}")

        strategy_params = {}
        if selected_strategies:
            with st.popover("⚙️ Params & Guide"):
                st.markdown("### 📚 Setup Guide")
                for s in selected_strategies:
                    st.info(f"**{s}**: {STRATEGY_DESCRIPTIONS.get(s, '')}")
                
                st.divider()
                st.markdown("### 🔧 Fine-tune Logic")
                for s in selected_strategies:
                    st.markdown(f"**{s}**")
                    defaults = STRATEGY_PARAMS.get(s, {})
                    p = {}
                    pc = st.columns(2)
                    if "min_body_pct" in defaults: p["min_body_pct"] = pc[0].number_input("Body%", 20.0, 90.0, float(defaults["min_body_pct"]), 5.0, key=f"p_{s}_b")
                    if "proximity_pct" in defaults: p["proximity_pct"] = pc[1].number_input("SMA%", 0.5, 5.0, float(defaults["proximity_pct"]), 0.5, key=f"p_{s}_px")
                    if "rsi_threshold" in defaults: p["rsi_threshold"] = pc[0].number_input("RSI", 40.0, 85.0, float(defaults["rsi_threshold"]), 5.0, key=f"p_{s}_rs")
                    if "vol_multiplier" in defaults: p["vol_multiplier"] = pc[1].number_input("VolX", 1.0, 5.0, float(defaults["vol_multiplier"]), 0.5, key=f"p_{s}_vl")
                    if "abc_proximity_pct" in defaults: p["abc_proximity_pct"] = pc[0].number_input("Prox%", 0.1, 3.0, float(defaults["abc_proximity_pct"]), 0.1, key=f"p_{s}_abc")
                    if "ath_threshold_pct" in defaults: p["ath_threshold_pct"] = pc[1].number_input("ATH%", 0.1, 15.0, float(defaults["ath_threshold_pct"]), 0.5, key=f"p_{s}_ath")
                    strategy_params[s] = p
        
        strategy_logic = "OR" # Compact logic

    with c5:
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        run_btn = st.button("🚀 SCAN", use_container_width=True, type="primary")

    # ── Main Panel ────────────────────────────────────────────────────────────
    if run_btn:
        fyers = get_fyers()
        if fyers is None:
            st.error("⚠️ No valid Fyers token. Go to **Settings & Auth** tab to login first.")
        elif not selected_strategies:
            st.warning("Please select at least one strategy from the sidebar.")
        elif not symbols:
            st.warning("No symbols loaded. Go to **Index Manager** tab and click Refresh.")
        else:
            progress_bar = st.progress(0, text="Initializing scanner...")
            status_text = st.empty()
            t_start = time.time()

            def on_progress(current, total, sym):
                progress_bar.progress(current / total, text=f"Scanning {sym} ({current}/{total})...")
                status_text.caption(f"Processing: {sym}")

            results_df = run_scan(
                fyers=fyers,
                symbols=symbols,
                scan_date=scan_date,
                resolution=resolution,
                selected_strategies=selected_strategies,
                logic=strategy_logic,
                strategy_params=strategy_params,
                progress_callback=on_progress,
            )

            elapsed = time.time() - t_start
            progress_bar.empty()
            status_text.empty()

            st.session_state.scan_results = results_df
            st.session_state.scan_meta = {
                "scan_date": scan_date,
                "resolution": resolution,
                "stock_group": stock_group,
                "strategies": selected_strategies,
                "logic": strategy_logic,
                "total_scanned": len(symbols),
                "elapsed": elapsed,
            }
            clear_cache()

    # ── Results ───────────────────────────────────────────────────────────────
    results = st.session_state.scan_results
    meta = st.session_state.scan_meta

    if results is not None:
        matched_count = len(results)
        total_scanned = meta.get("total_scanned", 0)
        elapsed = meta.get("elapsed", 0)
        tf_label = {"1":"1Min","5":"5Min","15":"15Min","30":"30Min","60":"1Hr","D":"Daily","W":"Weekly"}.get(meta.get("resolution","D"),"Daily")

        c1, c2, c3, c4 = st.columns(4)
        render_metric_card("Stocks Scanned", total_scanned, c1)
        render_metric_card("Matches Found", matched_count, c2)
        render_metric_card("Time Taken", f"{elapsed:.1f}s", c3)
        render_metric_card("Scan Date", str(meta.get("scan_date", "")), c4)

        st.markdown(
            f"<div style='margin:1rem 0;color:#64748b;font-size:0.85rem;'>"
            f"📅 {meta.get('scan_date','')} &nbsp;|&nbsp; ⏱️ {tf_label} &nbsp;|&nbsp; "
            f"📂 <b style='color:#a5b4fc'>{meta.get('stock_group','')}</b> &nbsp;|&nbsp; "
            f"🎯 {' + '.join(meta.get('strategies',[]))} &nbsp;|&nbsp; Logic: <b>{meta.get('logic','OR')}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if matched_count == 0:
            st.info("📭 No matches found. Try different strategies, timeframe, or date.")
        else:
            st.markdown(f"<div class='section-header'>✅ {matched_count} Matches Found</div>", unsafe_allow_html=True)
            
            # Grid Layout (3 stocks per row)
            grid_cols = 3
            for i in range(0, matched_count, grid_cols):
                cols = st.columns(grid_cols)
                for j in range(grid_cols):
                    idx = i + j
                    if idx < matched_count:
                        row = results.iloc[idx]
                        tv_res = resolution if resolution not in ["D", "W", "M"] else ("1D" if resolution=="D" else ("1W" if resolution=="W" else "1M"))
                        tv_sym = row['Name'].split(':')[-1].replace('-EQ','')
                        tv_url = f"https://in.tradingview.com/chart/?symbol=NSE:{tv_sym}&interval={tv_res}"
                        tags = "".join([f'<span class="strategy-tag">{s.strip()}</span>' for s in row['Strategies Matched'].split(",")])
                        
                        with cols[j]:
                            st.markdown(f"""
                            <div class="result-card" style="min-height:230px; display:flex; flex-direction:column; justify-content:space-between;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                                        <div style="font-size:1.1rem; font-weight:800; color:#0f172a; font-family:'Outfit';">{tv_sym}</div>
                                        <span class="buy-badge">{row['Signal']}</span>
                                    </div>
                                    <div style="font-size:1.4rem; font-weight:700; color:#6366f1; margin:8px 0; font-family:'Outfit';">₹{row['Close']:.2f}</div>
                                    <div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom:10px;">{tags}</div>
                                </div>
                                <div style="border-top:1px solid #f1f5f9; padding-top:10px;">
                                    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:2px; text-align:center; margin-bottom:10px;">
                                        <div><div class="label-muted">RSI</div><div class="indicator-val" style="font-size:1rem;">{row['RSI']:.1f}</div></div>
                                        <div><div class="label-muted">SMA50</div><div class="indicator-val" style="font-size:1rem;">{row['SMA50']:.0f}</div></div>
                                        <div><div class="label-muted">VOL</div><div class="indicator-val" style="font-size:1rem;">{row['Vol Ratio']:.1f}x</div></div>
                                    </div>
                                    <a href="{tv_url}" target="_blank" style="text-decoration:none;">
                                        <div style="text-align:center; background:linear-gradient(135deg, #6366f1, #a855f7); color:white; border-radius:8px; font-size:0.8rem; font-weight:700; padding:8px 0;">📊 Chart</div>
                                    </a>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
    elif not run_btn:
        st.markdown("""
        <div style="text-align:center;padding:4rem 2rem;color:#475569;">
            <div style="font-size:4rem;margin-bottom:1rem;">🔍</div>
            <h3 style="color:#64748b;font-weight:500;">Ready to Scan</h3>
            <p style="max-width:420px;margin:0 auto;font-size:0.9rem;">
                Select an index, pick strategies and click <b>Run Scanner</b> to start.
            </p>
        </div>""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: RESULTS DETAIL
# ═══════════════════════════════════════════════════════════════════════════════
with tab_details:
    results = st.session_state.scan_results
    if results is None or len(results) == 0:
        st.info("Run a scan first to see detailed results here.")
    else:
        st.markdown("<div class='section-header'>📊 Detailed Analysis per Stock</div>", unsafe_allow_html=True)
        for _, row in results.iterrows():
            with st.expander(f"📈 {row['Name']}  —  ₹{row['Close']:.2f}  |  {row['Strategies Matched']}", expanded=False):
                col_a, col_b = st.columns([3, 2])
                
                # Resolution Mapper for TradingView
                tv_res = resolution if resolution not in ["D", "W"] else ("1D" if resolution == "D" else "1W")
                tv_symbol = row['Name'].split(':')[-1].replace('-EQ', '') 
                tv_url = f"https://in.tradingview.com/chart/?symbol=NSE:{tv_symbol}&interval={tv_res}"

                with col_a:
                    st.markdown(f"### {row['Name']} &nbsp; [🔗 TradingView]({tv_url})")
                    candles = row.get("_df", [])
                    if candles:
                        fig = make_candlestick(candles, row["Name"])
                        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                with col_b:
                    st.markdown("**📊 Indicator Snapshot**")
                    st.metric("Close", f"₹{row['Close']:.2f}")
                    if row.get("SMA50"):
                        diff_pct = (row["Close"] - row["SMA50"]) / row["SMA50"] * 100
                        st.metric("SMA 50", f"₹{row['SMA50']:.2f}", delta=f"{diff_pct:+.2f}%")
                    if row.get("RSI"):
                        st.metric("RSI (14)", f"{row['RSI']:.1f}")
                    if row.get("Vol Ratio"):
                        st.metric("Volume Ratio", f"{row['Vol Ratio']:.2f}×")

                details = row.get("_details", {})
                if details:
                    st.markdown("**🎯 Condition Breakdown**")
                    for strategy_name, d in details.items():
                        st.markdown(f"*{strategy_name}*")
                        detail_df = pd.DataFrame([{"Parameter": k, "Value": v} for k, v in d.items()])
                        st.dataframe(detail_df, use_container_width=True, hide_index=True, height=min(200, 50 + len(detail_df)*35))

                candles = row.get("_df", [])
                if candles:
                    st.markdown("**🕯️ Last Candles**")
                    cdf = pd.DataFrame(candles)[["datetime","open","high","low","close","volume"]].tail(5)
                    cdf.columns = ["DateTime","Open","High","Low","Close","Volume"]
                    st.dataframe(cdf, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: INDEX MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
with tab_indices:
    st.markdown("### 📑 Index Symbol Manager")
    st.caption("Symbols are auto-fetched from NSE India and cached for **7 days**. Changes in index composition are picked up automatically on next refresh.")

    col_l, col_r = st.columns([3, 2])

    with col_l:
        # Cache status table
        status_data = get_cache_status()
        status_df = pd.DataFrame(status_data)
        st.markdown("**📋 Index Cache Status**")
        st.dataframe(
            status_df,
            use_container_width=True,
            hide_index=True,
            height=min(700, 56 + len(status_df) * 38),
            column_config={
                "Index": st.column_config.TextColumn("Index"),
                "Stocks": st.column_config.NumberColumn("# Stocks", format="%d"),
                "Last Fetched": st.column_config.TextColumn("Last Fetched"),
                "Expires": st.column_config.TextColumn("Expires On"),
                "Status": st.column_config.TextColumn("Status"),
            },
        )

    with col_r:
        st.markdown("**🔄 Refresh Controls**")

        # Single index refresh
        selected_idx = st.selectbox("Select index to refresh", options=ALL_INDICES, key="idx_refresh_sel")
        if st.button(f"🔄 Refresh '{selected_idx}'", key="btn_refresh_one"):
            with st.spinner(f"Fetching {selected_idx} from NSE..."):
                syms = get_symbols(selected_idx, force_refresh=True)
                # Bust Streamlit cache too
                cached_get_symbols.clear()
            if syms:
                st.success(f"✅ {len(syms)} symbols fetched for {selected_idx}")
                st.rerun()
            else:
                st.error(f"❌ Failed to fetch {selected_idx}. Check network & retry.")

        st.divider()

        # Refresh all
        st.markdown("**⚠️ Refresh ALL Indices**")
        st.caption("This will download all 20 index lists from NSE. Takes ~30–60 seconds.")
        if st.button("🔄 Refresh All Indices Now", key="btn_refresh_all"):
            progress_all = st.progress(0, text="Refreshing all indices...")
            results_counts = {}

            def prog_all(current, total, name):
                progress_all.progress(current / total, text=f"Fetching {name}... ({current}/{total})")

            results_counts = refresh_all_indices(progress_callback=prog_all)
            progress_all.empty()
            cached_get_symbols.clear()

            ok = sum(1 for v in results_counts.values() if v > 0)
            fail = len(results_counts) - ok
            st.success(f"✅ Refreshed {ok} indices successfully. {fail} failed.")
            st.rerun()

        st.divider()
        st.markdown("**ℹ️ Data Source**")
        st.markdown("""
        Index constituents are downloaded from:
        - **NSE India archives** (`archives.nseindia.com`)
        - Public URLs, no authentication required
        - Cached locally in `data/cache/`
        - Refresh TTL: **7 days**
        """)

        # Preview a specific index
        st.divider()
        st.markdown("**🔍 Preview Index Symbols**")
        prev_idx = st.selectbox("Preview", options=ALL_INDICES + [CUSTOM_OPT], key="prev_idx_sel")
        if st.button("Show Symbols", key="btn_preview_idx"):
            syms = cached_get_symbols(prev_idx)
            if syms:
                preview_df = pd.DataFrame({
                    "Fyers Symbol": syms,
                    "Name": [display_name(s) for s in syms],
                })
                st.dataframe(preview_df, use_container_width=True, hide_index=True, height=300)
                st.caption(f"Total: {len(syms)} stocks")
            else:
                st.warning("No symbols found. Try refreshing this index.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4: SETTINGS & AUTH
# ═══════════════════════════════════════════════════════════════════════════════
with tab_settings:
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown("### 🔑 Fyers Authentication")
        info = get_token_info()
        if info["valid"]:
            st.success(f"✅ Token active until **{info['expires_at']}**")
        else:
            st.error("❌ No valid token. Authenticate below.")

        st.divider()
        st.markdown("#### Login Steps")
        st.markdown("""
        1. Click **Generate Auth URL** — browser opens Fyers login
        2. Log in to Fyers
        3. After login, the auth code is captured **automatically** via local server
        4. If auto-capture fails, paste the redirect URL manually
        """)

        if st.button("🔗 Generate Auth URL & Open Browser", key="btn_auth"):
            try:
                import webbrowser
                url = generate_auth_url()
                webbrowser.open(url)
                st.success("✅ Browser opened! Log in — token will be captured automatically.")
                st.code(url)
            except Exception as e:
                st.error(f"Error: {e}")

        redirect_url = st.text_input(
            "📋 Manual fallback — paste redirect URL",
            placeholder="http://127.0.0.1:5000/callback?auth_code=...",
            key="redir_input",
        )
        if st.button("✅ Submit Token Manually", key="btn_manual_token"):
            if redirect_url:
                try:
                    auth_code = extract_auth_code_from_url(redirect_url)
                    exchange_code_for_token(auth_code)
                    st.session_state.fyers_client = None
                    st.success("🎉 Token saved!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.warning("Paste the redirect URL first.")

    with col_r:
        st.markdown("### 📋 API Credentials")
        client_id = os.getenv("FYERS_CLIENT_ID", "Not set")
        secret_key = os.getenv("FYERS_SECRET_KEY", "Not set")
        redirect = os.getenv("FYERS_REDIRECT_URL", "Not set")
        st.markdown(f"**Client ID:** `{client_id}`")
        st.markdown(f"**Secret:** `{'*' * min(len(secret_key), 8)}`")
        st.markdown(f"**Redirect URI:** `{redirect}`")

        st.divider()
        st.markdown("### 🔄 Session")
        if st.button("🗑️ Clear All Cache", key="btn_cache_clr"):
            clear_cache()
            cached_get_symbols.clear()
            st.session_state.fyers_client = None
            st.session_state.scan_results = None
            st.success("Cache cleared.")

    st.divider()
    st.markdown("### 🗄️ Local Database Monitor")
    try:
        from core.database import StockDatabase
        db_inst = StockDatabase()
        stats = db_inst.get_db_stats()
        st.metric("Total Symbols Cached", stats["total_symbols"])
        st.metric("Total Rows (since 1994)", f"{stats['total_rows']:,}")
        st.info("💡 Data is stored in `data/stock_scanner.db`. Daily resolution is automatically synced from 1994 on first search.")
    except Exception as e:
        st.error(f"Database Error: {e}")

    st.divider()
    st.markdown("### 🚀 Historical Bulk Sync Hub")
    st.caption("One-time mass download to fill your local database since 1994.")
    
    sync_group_options = ALL_INDICES + [CUSTOM_OPT]
    sync_group = st.selectbox("🎯 Select Group to Sync", options=sync_group_options, key="sync_grp_sel")
    sync_symbols = cached_get_symbols(sync_group)
    
    if "sync_running" not in st.session_state: st.session_state.sync_running = False
    if "sync_stop" not in st.session_state: st.session_state.sync_stop = False

    col_s1, col_s2 = st.columns(2)
    
    if not st.session_state.sync_running:
        if col_s1.button("🔥 START BULK SYNC", use_container_width=True, type="primary"):
            st.session_state.sync_running = True
            st.session_state.sync_stop = False
            st.rerun()
    else:
        if col_s1.button("🛑 STOP SYNC", use_container_width=True):
            st.session_state.sync_stop = True
            st.warning("Stopping after current symbol...")

    if st.session_state.sync_running:
        fyers = get_fyers()
        if not fyers:
            st.error("Login required for sync.")
            st.session_state.sync_running = False
        else:
            progress_overall = st.progress(0, text="Starting Bulk Sync...")
            status_overall = st.empty()
            
            total = len(sync_symbols)
            for i, sym in enumerate(sync_symbols):
                if st.session_state.sync_stop:
                    st.session_state.sync_running = False
                    st.info("Sync stopped by user.")
                    break
                
                progress_overall.progress((i + 1) / total, text=f"Syncing {i+1}/{total}: {sym}")
                status_overall.caption(f"Current Target: {sym}")
                
                try:
                    # fetch_ohlcv handles the internal DB sync since 1994 for Daily res
                    fetch_ohlcv(fyers, sym, "D", date.today(), 100)
                except Exception as e:
                    st.warning(f"Failed to sync {sym}: {e}")
                    time.sleep(1)
            
            st.session_state.sync_running = False
            progress_overall.empty()
            status_overall.empty()
            st.success(f"🎉 Bulk Sync Complete for {sync_group}!")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5: CUSTOM WATCHLIST
# ═══════════════════════════════════════════════════════════════════════════════
with tab_watchlist:
    st.markdown("### 📂 Custom Watchlist Manager")
    st.caption(f"File: `{WATCHLIST_FILE}`")

    col_wl_l, col_wl_r = st.columns([3, 2])


    with col_wl_l:
        current = get_custom_watchlist_symbols()
        st.markdown(f"**Current Watchlist ({len(current)} stocks)**")
        if current:
            wl_df = pd.DataFrame({"Symbol": current, "Name": [display_name(s) for s in current]})
            st.dataframe(wl_df, use_container_width=True, hide_index=True)
        else:
            st.info("Watchlist is empty.")

        st.divider()
        new_sym = st.text_input("➕ Add Symbol (e.g. NSE:RELIANCE-EQ)", key="new_sym_inp")
        if st.button("Add to Watchlist", key="btn_add"):
            if new_sym:
                sym = new_sym.strip().upper()
                if sym not in current:
                    current.append(sym)
                    pd.DataFrame({"symbol": current}).to_csv(WATCHLIST_FILE, index=False)
                    cached_get_symbols.clear()
                    st.success(f"Added {sym}")
                    st.rerun()
                else:
                    st.warning("Already in watchlist.")

        if current:
            rem = st.selectbox("❌ Remove Symbol", options=[""] + current, key="rem_inp")
            if st.button("Remove", key="btn_rem") and rem:
                current.remove(rem)
                pd.DataFrame({"symbol": current}).to_csv(WATCHLIST_FILE, index=False)
                cached_get_symbols.clear()
                st.success(f"Removed {rem}")
                st.rerun()

    with col_wl_r:
        st.markdown("**📤 Upload CSV**")
        st.caption("CSV must have a `symbol` column")
        st.download_button(
            "⬇️ Download Template",
            "symbol\nNSE:RELIANCE-EQ\nNSE:TCS-EQ",
            "watchlist_template.csv", "text/csv",
        )
        uploaded = st.file_uploader("Upload CSV", type=["csv"], key="wl_up")
        if uploaded:
            try:
                df_up = pd.read_csv(uploaded)
                if "symbol" not in df_up.columns:
                    st.error("CSV must have a `symbol` column")
                else:
                    new_syms = df_up["symbol"].dropna().str.strip().str.upper().tolist()
                    merged = list(dict.fromkeys(current + new_syms))
                    pd.DataFrame({"symbol": merged}).to_csv(WATCHLIST_FILE, index=False)
                    cached_get_symbols.clear()
                    st.success(f"Uploaded {len(new_syms)} symbols. Total: {len(merged)}")
                    st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

        st.divider()
        if st.button("❌ Clear All", key="btn_clr_wl"):
            pd.DataFrame({"symbol": []}).to_csv(WATCHLIST_FILE, index=False)
            cached_get_symbols.clear()
            st.success("Cleared.")
            st.rerun()

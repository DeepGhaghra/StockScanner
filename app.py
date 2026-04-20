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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from core.data_fetcher import fetch_ohlcv, get_fyers_client, clear_cache, validate_session
from core.indicators import add_all_indicators
from core.scanner import run_scan
from core.momentum import run_momentum_scan
from plotly.subplots import make_subplots
from core.strategy_engine import STRATEGIES, STRATEGY_DESCRIPTIONS, STRATEGY_PARAMS, find_pivots
from core.symbol_manager import (
    get_available_indices,
    get_symbols,
    get_cache_status,
    refresh_all_indices,
    get_custom_watchlist_symbols,
)
from core.result_manager import clear_results_cache

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
    ("max_workers", 10),
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


def make_candlestick(candles: list[dict], symbol: str, matched_strats: str = "") -> go.Figure:
    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["datetime"])
    
    # Indicators based on selection
    show_rsi = "RSI Momentum" in matched_strats
    show_bb = any(x in matched_strats for x in ["ABC", "50 SMA Support"])
    show_dow = "Dow Trend" in matched_strats
    
    # Subplot configuration
    if show_rsi:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.08, row_heights=[0.7, 0.3])
    else:
        fig = go.Figure()

    # Styling colors
    bg_color = "#0f172a" 
    grid_color = "rgba(148, 163, 184, 0.08)"
    
    # ─── MAIN CHART (ROW 1) ──────────────────────────────────────────────────
    
    # 1. Bollinger Bands
    if show_bb and "bb_upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["datetime"], y=df["bb_upper"],
            line=dict(color="rgba(148, 163, 184, 0.4)", width=1),
            hoverinfo="skip", showlegend=False
        ), row=1 if show_rsi else None, col=1 if show_rsi else None)
        fig.add_trace(go.Scatter(
            x=df["datetime"], y=df["bb_lower"],
            line=dict(color="rgba(148, 163, 184, 0.4)", width=1),
            fill='tonexty', fillcolor="rgba(148, 163, 184, 0.03)",
            hoverinfo="skip", showlegend=False
        ), row=1 if show_rsi else None, col=1 if show_rsi else None)

    # 2. SMA 50
    if "sma_50" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["datetime"], y=df["sma_50"],
            line=dict(color="#f87171", width=1.2, dash="dot"),
            name="SMA 50", opacity=0.8
        ), row=1 if show_rsi else None, col=1 if show_rsi else None)

    # 3. Dow Theory Zig-Zag
    if show_dow:
        peaks, troughs = find_pivots(df, strength=3)
        if peaks:
            px = [p["date"] for p in peaks]
            py = [p["price"] for p in peaks]
            fig.add_trace(go.Scatter(
                x=px, y=py, mode="lines+markers",
                line=dict(color="#60a5fa", width=1.5),
                marker=dict(symbol="x-thin", size=7, line=dict(width=1)),
                name="Dow Highs"
            ), row=1 if show_rsi else None, col=1 if show_rsi else None)
        if troughs:
            tx = [t["date"] for t in troughs]
            ty = [t["price"] for t in troughs]
            fig.add_trace(go.Scatter(
                x=tx, y=ty, mode="lines+markers",
                line=dict(color="#fb7185", width=1.5),
                marker=dict(symbol="x-thin", size=7, line=dict(width=1)),
                name="Dow Lows"
            ), row=1 if show_rsi else None, col=1 if show_rsi else None)

    # 4. Candlesticks
    fig.add_trace(go.Candlestick(
        x=df["datetime"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color="#10b981", decreasing_line_color="#ef4444",
        increasing_fillcolor="#10b981", decreasing_fillcolor="#ef4444",
        name=symbol,
    ), row=1 if show_rsi else None, col=1 if show_rsi else None)

    # ─── RSI SUBPLOT (ROW 2) ─────────────────────────────────────────────────
    if show_rsi and "rsi_14" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["datetime"], y=df["rsi_14"],
            line=dict(color="#818cf8", width=2),
            name="RSI"
        ), row=2, col=1)
        # RSI 70/30/50 Lines
        for val, color in [(70, "#ef4444"), (30, "#10b981"), (50, "rgba(148, 163, 184, 0.3)")]:
            fig.add_hline(y=val, line=dict(color=color, width=1, dash="dash"), row=2, col=1)

    # Layout Tuning
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=bg_color,
        plot_bgcolor=bg_color,
        height=550 if show_rsi else 420,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        showlegend=False,
        font=dict(family="Inter", size=11, color="#94a3b8"),
        title=dict(text=f"<b>{symbol}</b> Analysis", x=0.01, font=dict(size=16, color="#f8fafc"))
    )
    fig.update_yaxes(gridcolor=grid_color, zeroline=False, tickfont=dict(size=10))
    fig.update_xaxes(gridcolor=grid_color, zeroline=False, tickfont=dict(size=10))
    
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
tab_scanner, tab_hunter, tab_details, tab_indices, tab_settings, tab_watchlist, tab_momentum = st.tabs(
    ["🔍 Scanner", "🧬 Signal Hunter", "📊 Results Detail", "📑 Index Manager", "⚙️ Settings & Auth", "📂 Watchlist", "🚀 Momentum"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: SIGNAL HUNTER (REVERSE SCAN)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_hunter:
    st.markdown("### 🧬 Signal Hunter (Historical Backtest)")
    st.info("Select a stock and a strategy to find all historical trade dates.")

    h1, h2, h3, h4 = st.columns([2, 2, 2, 1])
    with h1:
        final_sym = st.text_input("🎯 Enter Stock Symbol", value="NSE:SBIN", placeholder="e.g. NSE:RELIANCE or BSE:532174").upper()
        st.caption("💡 NSE:SBIN, BSE:ECORECO")

    with h2:
        h_strat = st.selectbox("🎯 Strategy", options=list(STRATEGIES.keys()), key="h_st")
        h_res = st.selectbox("⏳ Timeframe", options=["15", "60", "1D", "1W", "1M"], index=2, key="h_rs")
    
    with h3:
        h_lookback = st.selectbox("📅 Search Period", options=["3 Months", "6 Months", "1 Year", "2 Years", "Max"], index=2)
        
    with h4:
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        hunt_btn = st.button("🔥 HUNT", width="stretch", type="primary")

    if hunt_btn:
        fyers = get_fyers()
        if not fyers:
            st.error("Please login first!")
        elif not validate_session(fyers):
            st.error("🔑 **Fyers Token Expired!** Please re-login in the **Settings & Auth** tab.")
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
                        st.dataframe(signals_df, width="stretch")
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
        scan_date = st.date_input("📅 Date", value=date.today(), max_value=date.today(), format="DD/MM/YYYY")

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
        selected_strategies = st.multiselect("🎯 Setup", options=list(STRATEGIES.keys()), default=["Dow Trend (HH/HL)"])
        
        strategy_params = {}
        strategy_logic = "OR"
        if selected_strategies:
            cc1, cc2 = st.columns([1.5, 1])
            with cc1:
                last_sel = selected_strategies[-1]
                st.caption(f"ℹ️ {STRATEGY_DESCRIPTIONS.get(last_sel, '').split(':')[0]}")
            with cc2:
                with st.popover("⚙️ Params"):
                    st.markdown("### 🎯 Global Match Mode")
                    strategy_logic = st.radio("Logic", ["Any (OR)", "All (AND)"], index=0, horizontal=True, help="Any: Shows stocks matching at least one setup. All: Shows only stocks matching ALL selected setups.")
                    strategy_logic = "AND" if "All" in strategy_logic else "OR"
                    st.divider()
                    pg_col1, pg_col2 = st.columns([1.1, 1])
                    
                    with pg_col1:
                        st.markdown("### 📚 Setup Guide")
                        for s in selected_strategies:
                            st.info(f"**{s}**: {STRATEGY_DESCRIPTIONS.get(s, '')}")
                    
                    with pg_col2:
                        st.markdown("### 🔧 Fine-tune Logic")
                        for s in selected_strategies:
                            st.markdown(f"**{s}**")
                            defaults = STRATEGY_PARAMS.get(s, {})
                            p = {}
                            pc = st.columns(2)
                            # Display inputs based on what's available in defaults
                            if "min_body_pct" in defaults: p["min_body_pct"] = pc[0].number_input("Body%", 20.0, 90.0, float(defaults["min_body_pct"]), 5.0, key=f"p_{s}_b")
                            if "proximity_pct" in defaults: p["proximity_pct"] = pc[1].number_input("SMA%", 0.5, 5.0, float(defaults["proximity_pct"]), 0.5, key=f"p_{s}_px")
                            if "rsi_threshold" in defaults: p["rsi_threshold"] = pc[0].number_input("RSI", 40.0, 85.0, float(defaults["rsi_threshold"]), 5.0, key=f"p_{s}_rs")
                            if "vol_multiplier" in defaults: p["vol_multiplier"] = pc[1].number_input("VolX", 1.0, 5.0, float(defaults["vol_multiplier"]), 0.5, key=f"p_{s}_vl")
                            if "abc_proximity_pct" in defaults: p["abc_proximity_pct"] = pc[0].number_input("Prox%", 0.1, 3.0, float(defaults["abc_proximity_pct"]), 0.1, key=f"p_{s}_abc")
                            if "ath_threshold_pct" in defaults: p["ath_threshold_pct"] = pc[1].number_input("ATH%", 0.1, 15.0, float(defaults["ath_threshold_pct"]), 0.5, key=f"p_{s}_ath")
                            if "pivot_strength" in defaults: p["pivot_strength"] = pc[0].number_input("Pivot Strength", 2, 20, int(defaults["pivot_strength"]), 1, key=f"p_{s}_piv")
                            strategy_params[s] = p
        

    with c5:
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        run_btn = st.button("🚀 SCAN", width="stretch", type="primary")

    # ── Main Panel ────────────────────────────────────────────────────────────
    if run_btn:
        fyers = get_fyers()
        if fyers is None:
            st.error("⚠️ No valid Fyers token. Go to **Settings & Auth** tab to login first.")
        elif not validate_session(fyers):
            st.error("🔑 **Fyers Token Expired!** Please re-login in the **Settings & Auth** tab.")
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
                max_workers=st.session_state.max_workers,
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
        
        # Self-healing for stale session results
        if 'Sector' not in results.columns:
            results['Sector'] = 'Others'

        c1, c2, c3, c4 = st.columns(4)
        render_metric_card("Stocks Scanned", total_scanned, c1)
        render_metric_card("Matches Found", matched_count, c2)
        render_metric_card("Time Taken", f"{elapsed:.1f}s", c3)
        render_metric_card("Scan Date", str(meta.get("scan_date", "")), c4)

        # ── Sectoral Insights ──
        if not results.empty and 'Sector' in results.columns:
            sector_counts = results['Sector'].value_counts()
            blasts = sector_counts[(sector_counts > 1) & (sector_counts.index != "Others")]
            
            if not blasts.empty:
                blast_list = [f"{name} ({count})" for name, count in blasts.items()]
                st.markdown(f"""
                <div style="background:rgba(99,102,241,0.1); border-left:4px solid #6366f1; padding:12px 20px; border-radius:8px; margin:1rem 0;">
                    <div style="color:#6366f1; font-weight:700; font-size:1.1rem; margin-bottom:4px;">🚀 Sector Blast Detected!</div>
                    <div style="color:#475569; font-size:0.95rem;">Collective institutional move suspected in: <b>{", ".join(blast_list)}</b></div>
                </div>
                """, unsafe_allow_html=True)

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
                        sector_badge = f'<span style="font-size:0.7rem; background:#f1f5f9; color:#64748b; padding:2px 6px; border-radius:4px; font-weight:600;">{row["Sector"]}</span>'
                        
                        # Format indicators safely
                        rsi_val = f"{row['RSI']:.1f}" if pd.notna(row['RSI']) else "N/A"
                        sma_val = f"{row['SMA50']:.0f}" if pd.notna(row['SMA50']) else "N/A"
                        vol_val = f"{row['Vol Ratio']:.1f}x" if pd.notna(row['Vol Ratio']) else "N/A"
                        
                        with cols[j]:
                            st.markdown(f"""
                            <div class="result-card" style="min-height:230px; display:flex; flex-direction:column; justify-content:space-between;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                                        <div>
                                            <div style="font-size:1.1rem; font-weight:800; color:#0f172a; font-family:\'Outfit\';">{tv_sym}</div>
                                            {sector_badge}
                                        </div>
                                        <span class="buy-badge">{row['Signal']}</span>
                                    </div>
                                    <div style="font-size:1.4rem; font-weight:700; color:#6366f1; margin:8px 0; font-family:\'Outfit\';">₹{row['Close']:.2f}</div>
                                    <div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom:10px;">{tags}</div>
                                </div>
                                <div style="border-top:1px solid #f1f5f9; padding-top:10px;">
                                    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:2px; text-align:center; margin-bottom:10px;">
                                        <div><div class="label-muted">RSI</div><div class="indicator-val" style="font-size:1rem;">{rsi_val}</div></div>
                                        <div><div class="label-muted">SMA50</div><div class="indicator-val" style="font-size:1rem;">{sma_val}</div></div>
                                        <div><div class="label-muted">VOL</div><div class="indicator-val" style="font-size:1rem;">{vol_val}</div></div>
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
                        fig = make_candlestick(candles, row["Name"], row["Strategies Matched"])
                        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
                        # FIX: string conversion to prevent Arrow mixed-type errors
                        detail_df = pd.DataFrame([{"Parameter": str(k), "Value": str(v)} for k, v in d.items()])
                        st.dataframe(detail_df, width="stretch", hide_index=True, height=min(200, 50 + len(detail_df)*35))

                candles = row.get("_df", [])
                if candles:
                    st.markdown("**🕯️ Last Candles**")
                    cdf = pd.DataFrame(candles)[["datetime","open","high","low","close","volume"]].tail(5)
                    cdf.columns = ["DateTime","Open","High","Low","Close","Volume"]
                    st.dataframe(cdf, width="stretch", hide_index=True)


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
            width="stretch",
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
                st.dataframe(preview_df, width="stretch", hide_index=True, height=300)
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
        st.markdown("### ⚡ Performance Settings")
        st.session_state.max_workers = st.slider(
            "Parallel Threads (Concurrency)", 
            min_value=1, 
            max_value=30, 
            value=st.session_state.max_workers,
            help="Number of simultaneous stock scans. Higher is faster but may trigger Fyers rate limits."
        )

        st.divider()
        st.markdown("### 🔄 Session")
        if st.button("🗑️ Clear All Cache", key="btn_cache_clr"):
            clear_cache()
            clear_results_cache()
            cached_get_symbols.clear()
            st.session_state.fyers_client = None
            st.session_state.scan_results = None
            st.success("All caches cleared (Data + Results).")

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
        if col_s1.button("🔥 START BULK SYNC", width="stretch", type="primary"):
            st.session_state.sync_running = True
            st.session_state.sync_stop = False
            st.rerun()
    else:
        if col_s1.button("🛑 STOP SYNC", width="stretch"):
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
            processed = 0
            
            def _sync_worker(sym):
                try:
                    fetch_ohlcv(fyers, sym, "D", date.today(), 100)
                    return sym, True, None
                except Exception as e:
                    time.sleep(1)
                    return sym, False, str(e)
            
            with ThreadPoolExecutor(max_workers=st.session_state.max_workers) as executor:
                future_to_sym = {executor.submit(_sync_worker, sym): sym for sym in sync_symbols}
                
                for future in as_completed(future_to_sym):
                    if st.session_state.sync_stop:
                        for f in future_to_sym:
                            f.cancel()
                        st.session_state.sync_running = False
                        st.info("Sync stopped by user.")
                        break
                    
                    sym, success, err = future.result()
                    processed += 1
                    
                    try:
                        progress_overall.progress(processed / total, text=f"Syncing {processed}/{total}: {sym}")
                        status_overall.caption(f"Current Target: {sym}")
                    except Exception:
                        pass
                        
                    if not success:
                        st.warning(f"Failed to sync {sym}: {err}")
            
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

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7: MOMENTUM RANKING
# ═══════════════════════════════════════════════════════════════════════════════
with tab_momentum:
    st.markdown("### 🚀 Multi-Factor Momentum Ranking")
    st.caption(
        "Stocks ranked by a **5-factor composite score** with a hard **SMA-200 filter**. "
        "Pick any past date to backtest which stocks topped the momentum list on that day."
    )

    # ── Controls Row ──────────────────────────────────────────────────────────
    mc1, mc2, mc3, mc4 = st.columns([2, 2, 2, 1.5])

    with mc1:
        m_index = st.selectbox(
            "📂 Index Universe",
            options=ALL_INDICES + [CUSTOM_OPT],
            index=ALL_INDICES.index("Nifty Total Market") if "Nifty Total Market" in ALL_INDICES else 0,
            key="mom_index_sel",
        )

    with mc2:
        m_scan_date = st.date_input(
            "📅 As-of Date  (backtest any day)",
            value=date.today(),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="mom_scan_date",
            help="Choose today for a live scan, or any past date to see that day's top-100 momentum stocks.",
        )
        if m_scan_date < date.today():
            st.caption(f"🕰️ Backtest mode — results as of **{m_scan_date.strftime('%d %b %Y')}**")

    with mc3:
        with st.expander("⚖️ Adjust Factor Weights"):
            st.caption("Weights must sum to 100%")
            w_f1 = st.slider("1-Year Return %",    0, 100, 50, 5, key="w1")
            w_f3 = st.slider("100 EMA Distance",     0, 100, 35, 5, key="w3")
            w_f4 = st.slider("Relative Volume",    0, 100, 15, 5, key="w4")
            total_w = w_f1 + w_f3 + w_f4
            if total_w != 100:
                st.warning(f"⚠️ Weights sum to **{total_w}%** — must be 100%")
            else:
                st.success("✅ Weights sum to 100%")
        custom_weights = {
            "return_12m": w_f1 / 100,
            "ema100_dist": w_f3 / 100,
            "rel_volume": w_f4 / 100,
        }

    with mc4:
        st.markdown("<div style='margin-top:24px;'></div>", unsafe_allow_html=True)
        run_mom_btn = st.button(
            "🚀 RUN SCAN",
            type="primary",
            use_container_width=True,
            key="run_mom_btn",
            disabled=(w_f1 + w_f3 + w_f4 != 100),
        )

    st.divider()

    # ── Run Scan ─────────────────────────────────────────────────────────────
    if run_mom_btn:
        fyers = get_fyers()
        if not fyers:
            st.error("⚠️ No valid Fyers token. Go to **Settings & Auth** tab to login first.")
        elif not validate_session(fyers):
            st.error("🔑 **Fyers Token Expired!** Please re-login in the **Settings & Auth** tab.")
        else:
            m_symbols = cached_get_symbols(m_index)
            if not m_symbols:
                st.error(f"No symbols found for **{m_index}**. Go to Index Manager and refresh.")
            else:
                m_prog_bar = st.progress(0, text="Initialising momentum scan...")
                m_status   = st.empty()

                def on_mom_prog(current, total, sym):
                    m_prog_bar.progress(current / total, text=f"Analysing {sym} ({current}/{total})...")
                    m_status.caption(f"⚙️ Processing: `{sym}`")

                mt_df, mt_stats = run_momentum_scan(
                    fyers=fyers,
                    symbols=m_symbols,
                    scan_date=m_scan_date,
                    weights=custom_weights,
                    progress_callback=on_mom_prog,
                    max_workers=st.session_state.max_workers,
                )
                m_prog_bar.empty()
                m_status.empty()

                if not mt_df.empty:
                    st.session_state["momentum_df"]    = mt_df
                    st.session_state["momentum_stats"] = mt_stats
                    st.session_state["momentum_date"]  = m_scan_date
                    st.session_state["momentum_index"] = m_index
                    mode_label = "🕰️ Backtest (DB only)" if mt_stats.get("is_backtest") else f"🔄 Live (synced {mt_stats.get('synced', 0)} symbols)"
                    st.success(
                        f"✅ Ranked **{len(mt_df)} stocks**  |  {mode_label}  |  "
                        f"Passed SMA-200: {mt_stats.get('passed', 0)}  |  "
                        f"Filtered (below SMA-200): {mt_stats.get('filtered', 0)}  |  "
                        f"No data: {mt_stats.get('failed', 0)}"
                    )
                else:
                    st.error("No stocks passed the SMA-200 filter on this date. Try a broader index or an earlier date.")

    # ── Results Display ───────────────────────────────────────────────────────
    if (
        "momentum_df" in st.session_state
        and st.session_state["momentum_df"] is not None
        and not st.session_state["momentum_df"].empty
    ):
        m_df    = st.session_state["momentum_df"]
        m_stats = st.session_state.get("momentum_stats", {})
        m_date  = st.session_state.get("momentum_date", date.today())
        m_idx   = st.session_state.get("momentum_index", "")

        # ── Header KPIs ──────────────────────────────────────────────────────
        k1, k2, k3, k4 = st.columns(4)
        render_metric_card("As-of Date", m_date.strftime("%d %b %Y"), k1)
        render_metric_card("Universe", m_idx, k2)
        render_metric_card("Passed SMA-200", m_stats.get("passed", len(m_df)), k3)
        render_metric_card("SMA-200 Filtered", m_stats.get("sma_filtered", "–"), k4)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Tabs: Table | Chart ───────────────────────────────────────────────
        tab_tbl, tab_chart = st.tabs(["📋 Ranked List", "📊 Score Breakdown"])

        with tab_tbl:
            display_cols = [
                "Rank", "Name", "Composite Score", 
                "12M Return %", "EMA100 Dist %", "Rel Volume",
                "f1_12m", "f3_ema100", "f4_vol"
            ]
            disp_df = m_df[[c for c in display_cols if c in m_df.columns]]

            # Dynamic styling based on available columns
            common_format = {
                "Close":           "₹{:.2f}",
                "SMA200":          "₹{:.2f}",
                "EMA100":          "₹{:.2f}",
                "Composite Score": "{:.1f}",
                "12M Return %":    "{:+.1f}%",
                "EMA100 Dist %":   "{:+.1f}%",
                "Rel Volume":      "{:.2f}×",
                "f1_12m":          "{:.0f} pts",
                "f3_ema100":       "{:.0f} pts",
                "f4_vol":          "{:.0f} pts",
            }
            
            # Filter format to only existing columns
            fmt = {k: v for k, v in common_format.items() if k in disp_df.columns}
            styled = disp_df.style.format(fmt)
            
            # Apply individual styles ONLY if columns exist
            if "Composite Score" in disp_df.columns:
                styled = styled.background_gradient(subset=["Composite Score"], cmap="YlGn")
            if "12M Return %" in disp_df.columns:
                styled = styled.bar(subset=["12M Return %"], color="#10b981", vmin=0)
            if "EMA100 Dist %" in disp_df.columns:
                styled = styled.bar(subset=["EMA100 Dist %"], color="#34d399", vmin=0)

            st.dataframe(styled, width="stretch", height=600, hide_index=True)

            csv_data = disp_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇️ Download CSV",
                data=csv_data,
                file_name=f"momentum_top100_{m_date.strftime('%Y%m%d')}_{m_idx.replace(' ', '_')}.csv",
                mime="text/csv",
                key="mom_csv_dl",
            )

        with tab_chart:
            factor_cols = {
                "f1_12m": "1Y Return (50%)",
                "f3_ema100": "100 EMA Dist (35%)",
                "f4_vol": "Rel Volume (15%)",
            }
            avail = {k: v for k, v in factor_cols.items() if k in m_df.columns}
            if avail:
                top20 = m_df.head(20).copy()
                top20["Label"] = top20["Name"].apply(
                    lambda s: s.split(":")[-1].replace("-EQ", "").replace("-BE", "")
                )
                bar_colors = ["#6366f1", "#22d3ee", "#10b981", "#f59e0b", "#f87171"]
                fig_bd = go.Figure()
                for (fcol, flabel), color in zip(avail.items(), bar_colors):
                    fig_bd.add_trace(go.Bar(
                        name=flabel,
                        x=top20["Label"],
                        y=top20[fcol].round(1),
                        marker_color=color,
                        opacity=0.85,
                    ))
                fig_bd.update_layout(
                    barmode="stack",
                    template="plotly_dark",
                    paper_bgcolor="#0f172a",
                    plot_bgcolor="#0f172a",
                    height=480,
                    margin=dict(l=10, r=10, t=50, b=80),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    yaxis_title="Factor Score (percentile × weight)",
                    xaxis_tickangle=-40,
                    font=dict(family="Inter", size=11, color="#94a3b8"),
                    title=dict(
                        text=f"<b>Factor Breakdown — Top 20 Stocks</b>  |  As of {m_date.strftime('%d %b %Y')}",
                        x=0.01, font=dict(size=14, color="#f8fafc"),
                    ),
                )
                st.plotly_chart(fig_bd, use_container_width=True)
            else:
                st.info("Factor detail columns not available. Run a new scan.")
    else:
        st.markdown("""
        <div style="text-align:center;padding:4rem 2rem;color:#475569;">
            <div style="font-size:3rem;margin-bottom:1rem;">🚀</div>
            <h3 style="color:#64748b;font-weight:500;">Ready to Rank</h3>
            <p style="max-width:500px;margin:0 auto;font-size:0.9rem;">
                Select an index universe, pick a date (today or any past date for backtesting),
                adjust factor weights if needed, then click <b>RUN SCAN</b>.
            </p>
        </div>""", unsafe_allow_html=True)


import streamlit as st
import sqlite3
import pandas as pd
import time
import os
import requests
import plotly.express as px
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass

load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")

client = None
if API_KEY and API_SECRET:
    try:
        client = TradingClient(API_KEY, API_SECRET, paper=True)
    except Exception:
        pass

# Page Configuration
st.set_page_config(
    page_title="Institutional Quant Terminal",
    page_icon="⚡",
    layout="wide"
)

def get_db_connection():
    try:
        conn = sqlite3.connect("trading_bot.db", timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None

def safe_get_positions():
    try:
        if client:
            return client.get_all_positions()
    except Exception:
        pass
    
    try:
        url = "https://paper-api.alpaca.markets/v2/positions"
        headers = {
            "APCA-API-KEY-ID": API_KEY,
            "APCA-API-SECRET-KEY": API_SECRET
        }
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            raw_pos = res.json()
            class MockPosition:
                def __init__(self, data):
                    self.symbol = data.get("symbol")
                    self.qty = float(data.get("qty", 0))
                    self.available_qty = float(data.get("qty_available", data.get("qty", 0)))
                    self.avg_entry_price = float(data.get("avg_entry_price", 0))
                    self.current_price = float(data.get("current_price", self.avg_entry_price))
                    self.unrealized_pl = float(data.get("unrealized_pl", 0.0))
                    self.asset_class = data.get("asset_class")
            return [MockPosition(p) for p in raw_pos]
    except Exception:
        pass
    return []

def get_bot_status():
    conn = get_db_connection()
    if not conn:
        return "DISCONNECTED"
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_state WHERE key = 'status'")
        row = cur.fetchone()
        conn.close()
        return row["value"] if row else "ACTIVE 🟢"
    except Exception:
        return "ACTIVE 🟢"

def set_bot_pause(paused: bool):
    conn = get_db_connection()
    if conn:
        conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('paused', ?)", (str(paused),))
        conn.commit()
        conn.close()

def is_bot_paused() -> bool:
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_state WHERE key = 'paused'")
        row = cur.fetchone()
        conn.close()
        return row and row["value"] == "True"
    return False

# Measure API latency
api_latency_ms = 0
if client:
    t_start = time.time()
    try:
        client.get_account()
        api_latency_ms = int((time.time() - t_start) * 1000)
    except Exception:
        api_latency_ms = 999

# --- SIDEBAR CONTROLS ---
st.sidebar.title("⚡ Control Panel")
refresh_rate = st.sidebar.slider("Refresh Interval (s):", 2, 30, 3)
est_tax_rate = st.sidebar.slider("Est. Tax Rate (%):", 0, 50, 25) / 100.0

st.sidebar.markdown("---")
paused_state = st.sidebar.toggle("⏸️ Pause New Entries", value=is_bot_paused())
if paused_state != is_bot_paused():
    set_bot_pause(paused_state)

st.sidebar.markdown("---")
if st.sidebar.button("🚨 EMERGENCY FLATTEN ALL", type="primary"):
    if client:
        try:
            positions = safe_get_positions()
            for pos in positions:
                if getattr(pos, 'asset_class', None) == AssetClass.US_OPTION or getattr(pos, 'asset_class', None) == 'us_option':
                    continue
                qty_to_sell = float(getattr(pos, 'available_qty', 0) or 0)
                if qty_to_sell > 0:
                    client.submit_order(MarketOrderRequest(
                        symbol=pos.symbol,
                        qty=qty_to_sell,
                        side=OrderSide.SELL if float(pos.qty) > 0 else OrderSide.BUY,
                        time_in_force=TimeInForce.DAY
                    ))
            conn = get_db_connection()
            if conn:
                conn.execute("DELETE FROM positions")
                conn.commit()
                conn.close()
            st.sidebar.success("Emergency liquidation complete!")
        except Exception as e:
            st.sidebar.error(f"Error: {e}")

# --- DATA GATHERING ---
current_equity = 100386.02
if client:
    try:
        account = client.get_account()
        current_equity = float(account.equity)
    except Exception:
        pass

positions_df = pd.DataFrame()
top_performer = "None"
highest_pnl_pct = -999.0

raw_positions = safe_get_positions()
if raw_positions:
    pos_data = []
    for p in raw_positions:
        if getattr(p, 'asset_class', None) != AssetClass.US_OPTION and getattr(p, 'asset_class', None) != 'us_option':
            ticker = p.symbol
            qty = float(p.qty)
            avg_entry = float(p.avg_entry_price)
            current_price = float(getattr(p, 'current_price', 0.0) or avg_entry)
            unrealized_pl = float(getattr(p, 'unrealized_pl', 0.0) or 0.0)
            pct_change = ((current_price - avg_entry) / avg_entry) * 100 if avg_entry > 0 else 0.0
            
            pos_data.append({
                "Ticker": ticker,
                "Sector": "Equities",
                "Qty": qty,
                "Avg Entry ($)": round(avg_entry, 2),
                "Current Price ($)": round(current_price, 2),
                "Unrealized PnL ($)": round(unrealized_pl, 2),
                "Return (%)": round(pct_change, 2)
            })
            
            if pct_change > highest_pnl_pct:
                highest_pnl_pct = pct_change
                top_performer = f"{ticker} (+{pct_change:.2f}%)"
                
    positions_df = pd.DataFrame(pos_data)

conn = get_db_connection()
trades_df, equity_curve_df = pd.DataFrame(), pd.DataFrame()
if conn:
    try:
        trades_df = pd.read_sql("SELECT timestamp, action, ticker, qty, pnl FROM trades ORDER BY timestamp DESC LIMIT 100", conn)
        equity_curve_df = pd.read_sql("SELECT timestamp, equity FROM equity_curve ORDER BY timestamp ASC", conn)
    except Exception:
        pass
    finally:
        conn.close()

bot_status = get_bot_status()

# --- HEADER SECTION ---
st.title("⚡ Institutional Quant Terminal")

m1, m2, m3 = st.columns(3)
m1.metric("Engine Status", bot_status)
m2.metric("API Latency", f"{api_latency_ms} ms")
m3.metric("Account Equity", f"${current_equity:,.2f}")

st.markdown("---")

# --- TABS ---
tab1, tab2, tab3, tab4 = st.tabs([
    "⚡ Overview", 
    "📦 Active Holdings", 
    "📜 Execution History", 
    "📊 Performance Metrics"
])

# TAB 1: OVERVIEW
with tab1:
    gain_usd = current_equity - 500.0
    gain_pct = (gain_usd / 500.0) * 100.0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Net Gain/Loss", f"${gain_usd:+,.2f}", f"{gain_pct:+.2f}%")
    c2.metric("Active Holdings", f"{len(positions_df)} Positions")
    c3.metric("Top Performer", top_performer)
    
    st.markdown("### Equity Curve")
    if not equity_curve_df.empty and len(equity_curve_df) > 1:
        equity_curve_df['timestamp'] = pd.to_datetime(equity_curve_df['timestamp'])
        st.line_chart(equity_curve_df.set_index('timestamp')['equity'], use_container_width=True)
    else:
        # Fallback chart using Alpaca Portfolio History if local database has < 2 points
        try:
            p_url = "https://paper-api.alpaca.markets/v2/account/portfolio/history?period=1D&timeframe=5Min"
            headers = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
            p_res = requests.get(p_url, headers=headers)
            if p_res.status_code == 200:
                p_data = p_res.json()
                timestamps = p_data.get("timestamp", [])
                equity_vals = p_data.get("equity", [])
                if timestamps and equity_vals:
                    hist_df = pd.DataFrame({
                        "timestamp": pd.to_datetime(timestamps, unit='s'),
                        "equity": equity_vals
                    })
                    st.line_chart(hist_df.set_index('timestamp')['equity'], use_container_width=True)
                else:
                    st.info("Accumulating data points for chart...")
            else:
                st.info("Accumulating data points for chart...")
        except Exception:
            st.info("Accumulating data points for chart...")

# TAB 2: ACTIVE HOLDINGS
with tab2:
    st.markdown("### Portfolio Treemap")
    if not positions_df.empty:
        fig = px.treemap(
            positions_df, 
            path=['Sector', 'Ticker'], 
            values='Qty', 
            color='Return (%)',
            color_continuous_scale='RdYlGn',
            color_continuous_midpoint=0
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.markdown("### Holdings Table")
        st.dataframe(positions_df, use_container_width=True)
    else:
        st.info("No active holdings found in portfolio.")

# TAB 3: EXECUTION HISTORY
with tab3:
    st.markdown("### Order Executions")
    if not trades_df.empty:
        st.dataframe(trades_df, use_container_width=True)
    else:
        st.info("No trades executed yet.")

# TAB 4: PERFORMANCE METRICS
with tab4:
    st.markdown("### Performance Telemetry")
    if not trades_df.empty and 'pnl' in trades_df.columns:
        gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = abs(trades_df[trades_df['pnl'] < 0]['pnl'].sum())
        net_pnl = trades_df['pnl'].sum()
        
        q1, q2, q3 = st.columns(3)
        q1.metric("Realized PnL", f"${net_pnl:,.2f}")
        q2.metric("Gross Profit", f"${gross_profit:,.2f}")
        q3.metric("Gross Loss", f"${gross_loss:,.2f}")
    else:
        st.info("Performance stats will populate as trades complete.")

# Auto-refresh loop
time.sleep(refresh_rate)
st.rerun()

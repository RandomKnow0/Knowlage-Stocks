import os
import time
import random
import requests
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockSnapshotRequest

from database import (
    init_db,
    log_equity,
    log_trade,
    update_positions_table,
    get_pending_command,
    clear_command,
)

load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not API_SECRET:
    raise ValueError("API Keys not found! Check your .env file.")

client = TradingClient(API_KEY, API_SECRET, paper=True)
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

# --- DYNAMIC SCALPER PARAMETERS ---
PROFIT_TARGET_PCT = 0.01          # Take profit (+1.0%)
STOP_LOSS_PCT = 0.007             # Stop loss (-0.7%)
MAX_POSITIONS_CAP = 150           # High ceiling for organic scaling
POSITION_ALLOCATION_USD = 15.0    # Allocation per trade slice

daily_start_equity = None
MASTER_WATCHLIST = []

def configure_db_wal():
    """Enable WAL mode and set a 30-second busy timeout to prevent 'database is locked' errors."""
    try:
        conn = sqlite3.connect("trading_bot.db", timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"WAL setup error: {e}")

def update_status(status: str):
    try:
        conn = sqlite3.connect("trading_bot.db", timeout=30.0)
        conn.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('status', ?)", (status,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Status update error: {e}")

def safe_get_positions():
    """Bypasses Pydantic Enum validation errors while providing all required attributes."""
    try:
        return client.get_all_positions()
    except Exception:
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
        return []

def get_market_state_info():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return "CLOSED", False, False
        
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    eod_flatten_start = now_et.replace(hour=15, minute=55, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    
    is_open = market_open <= now_et <= market_close
    is_eod_window = eod_flatten_start <= now_et < market_close
    
    return ("OPEN" if is_open else "CLOSED"), is_eod_window, is_open

def check_emergency_commands():
    cmd = get_pending_command()
    if cmd == "FLATTEN_ALL":
        print("-> 🚨 EMERGENCY FLATTEN SIGNAL DETECTED! Liquidating positions...")
        flatten_all_positions(reason="EMERGENCY_FLATTEN")
        clear_command("FLATTEN_ALL")

def sync_account_state() -> float:
    global daily_start_equity
    try:
        account = client.get_account()
        equity = float(account.equity)
        cash = float(account.buying_power)
        
        if daily_start_equity is None:
            daily_start_equity = equity
            
        log_equity(equity, cash)
        positions = safe_get_positions()
        update_positions_table(positions)
        return equity
    except Exception as e:
        print(f"Error syncing account state: {e}")
        return 500.0

def flatten_all_positions(reason="EOD_FLAT"):
    try:
        positions = safe_get_positions()
        for pos in positions:
            if getattr(pos, 'asset_class', None) == AssetClass.US_OPTION or getattr(pos, 'asset_class', None) == 'us_option':
                continue
            qty_to_sell = float(getattr(pos, 'available_qty', 0) or 0)
            if qty_to_sell > 0:
                side = OrderSide.SELL if float(pos.qty) > 0 else OrderSide.BUY
                print(f"-> 🔔 Executing {reason} liquidation for {pos.symbol}...")
                client.submit_order(MarketOrderRequest(
                    symbol=pos.symbol,
                    qty=qty_to_sell,
                    side=side,
                    time_in_force=TimeInForce.DAY
                ))
                log_trade(reason, pos.symbol, qty_to_sell, 0.0)
        time.sleep(1)
        update_positions_table([])
    except Exception as e:
        print(f"Error flattening positions: {e}")

def load_master_watchlist():
    global MASTER_WATCHLIST
    if not MASTER_WATCHLIST:
        print("-> 📥 Loading fractionable equity universe from Alpaca...")
        url = "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
        headers = {
            "APCA-API-KEY-ID": API_KEY,
            "APCA-API-SECRET-KEY": API_SECRET
        }
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                assets = response.json()
                MASTER_WATCHLIST = [
                    a["symbol"] for a in assets 
                    if a.get("tradable") and a.get("fractionable") and "." not in a["symbol"] and "/" not in a["symbol"]
                ]
                print(f"-> 🟢 Loaded {len(MASTER_WATCHLIST)} tradable symbols.")
        except Exception as e:
            print(f"-> ⚠️ Asset load error: {e}")

def run_momentum_engine(is_eod_window):
    try:
        load_master_watchlist()
        positions = safe_get_positions()
        
        if is_eod_window:
            update_status("EOD FLATTENING 🔔")
            flatten_all_positions(reason="EOD_FLAT")
            return

        account = client.get_account()
        available_cash = float(account.buying_power)

        # 1. MONITOR POSITIONS (Take Profit / Stop Loss)
        if positions:
            print(f"-> ⚡ Monitoring {len(positions)} active position(s)...")
            for pos in positions:
                if getattr(pos, 'asset_class', None) == AssetClass.US_OPTION or getattr(pos, 'asset_class', None) == 'us_option':
                    continue
                    
                ticker = pos.symbol
                qty_to_sell = float(getattr(pos, 'available_qty', 0) or 0)
                if qty_to_sell <= 0:
                    continue
                    
                avg_entry = float(pos.avg_entry_price)
                
                try:
                    trade_req = StockLatestTradeRequest(symbol_or_symbols=ticker)
                    latest_trade = data_client.get_stock_latest_trade(trade_req)
                    if ticker not in latest_trade:
                        continue
                    current_price = float(latest_trade[ticker].price)
                except Exception:
                    continue
                    
                pnl_pct = (current_price - avg_entry) / avg_entry
                pnl_usd = (current_price - avg_entry) * qty_to_sell

                if pnl_pct >= PROFIT_TARGET_PCT or pnl_pct <= -STOP_LOSS_PCT:
                    exit_reason = "TAKE_PROFIT" if pnl_pct >= 0 else "STOP_LOSS"
                    print(f"-> 🚨 {exit_reason} hit for {ticker} ({pnl_pct*100:+.2f}% PnL)! Exiting...")
                    
                    client.submit_order(MarketOrderRequest(
                        symbol=ticker,
                        qty=qty_to_sell,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY
                    ))
                    log_trade(f"SELL_{exit_reason}", ticker, qty_to_sell, round(pnl_usd, 2))
                    
                    time.sleep(0.3)
                    update_positions_table(safe_get_positions())

        # 2. MOMENTUM SCANNING & ENTRY
        live_positions = safe_get_positions()
        active_tickers = [p.symbol for p in live_positions if getattr(p, 'asset_class', None) != AssetClass.US_OPTION and getattr(p, 'asset_class', None) != 'us_option']
        
        if len(active_tickers) >= MAX_POSITIONS_CAP:
            print(f"-> 🛑 Position ceiling reached ({len(active_tickers)}/{MAX_POSITIONS_CAP}).")
            return

        scan_pool = [t for t in MASTER_WATCHLIST if t not in active_tickers]
        batch_tickers = random.sample(scan_pool, min(60, len(scan_pool)))
        
        print(f"-> 🔍 Scanning momentum across {len(batch_tickers)} tickers...")
        
        snapshot_req = StockSnapshotRequest(symbol_or_symbols=batch_tickers)
        snapshots = data_client.get_stock_snapshot(snapshot_req)
        
        purchases_made = 0
        for ticker, snap in snapshots.items():
            if purchases_made >= 5 or available_cash < POSITION_ALLOCATION_USD:
                break
                
            if snap and snap.latest_trade and snap.daily_bar:
                price = float(snap.latest_trade.price)
                open_price = float(snap.daily_bar.open)
                
                if 5.0 <= price <= 300.0 and open_price > 0:
                    day_gain = (price - open_price) / open_price
                    
                    if day_gain > 0.005:
                        if available_cash >= POSITION_ALLOCATION_USD:
                            print(f"-> 🚀 MOMENTUM BUY: Investing ${POSITION_ALLOCATION_USD:.2f} into {ticker} (+{day_gain*100:.2f}%)...")
                            
                            client.submit_order(MarketOrderRequest(
                                symbol=ticker,
                                notional=POSITION_ALLOCATION_USD,
                                side=OrderSide.BUY,
                                time_in_force=TimeInForce.DAY
                            ))
                            log_trade("PORTFOLIO_BUY", ticker, 0.0, 0.0)
                            available_cash -= POSITION_ALLOCATION_USD
                            purchases_made += 1
                            
                            time.sleep(0.3)
                            update_positions_table(safe_get_positions())

    except Exception as e:
        print(f"Error in momentum engine: {e}")

def run_bot_loop():
    print("🤖 Starting Robust Dynamic Momentum Engine...")
    init_db()
    configure_db_wal()
    load_master_watchlist()
    
    while True:
        check_emergency_commands()
        current_equity = sync_account_state()
        
        market_state, is_eod_window, is_open = get_market_state_info()
        print(f"\n[{time.strftime('%X')}] 🕒 Market State: {market_state} | Equity: ${current_equity:,.2f}")
        
        if not is_open:
            update_status("MARKET CLOSED 🌙")
            time.sleep(30)
            continue
            
        update_status("MOMENTUM SCALPING ACTIVE ⚡")
        run_momentum_engine(is_eod_window)
        
        time.sleep(3)

if __name__ == "__main__":
    run_bot_loop()

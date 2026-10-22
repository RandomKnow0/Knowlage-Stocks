import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
import time

load_dotenv()
API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")

client = TradingClient(API_KEY, API_SECRET, paper=True)

def force_liquidate_everything():
    print("🚨 Starting emergency purge of all open positions...")
    positions = client.get_all_positions()
    print(f"Found {len(positions)} open positions to close.")
    
    for i, pos in enumerate(positions):
        if pos.asset_class == AssetClass.US_OPTION:
            continue
            
        ticker = pos.symbol
        qty = float(getattr(pos, 'available_qty', 0) or pos.qty)
        
        if qty > 0:
            side = OrderSide.SELL if float(pos.qty) > 0 else OrderSide.BUY
            try:
                print(f"[{i+1}/{len(positions)}] Liquidating {qty} shares of {ticker}...")
                client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY
                ))
                # Brief pause to respect Alpaca API rate limits
                time.sleep(0.3)
            except Exception as e:
                print(f"⚠️ Error closing {ticker}: {e}")
                
    print("✅ Liquidation orders sent! Check your Alpaca dashboard in a few seconds.")

if __name__ == "__main__":
    force_liquidate_everything()

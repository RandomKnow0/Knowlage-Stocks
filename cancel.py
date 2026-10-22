from alpaca.trading.client import TradingClient
import os
from dotenv import load_dotenv

load_dotenv()
client = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)

# Cancel every single open order on the account
cancel_statuses = client.cancel_orders()
print(f"Cancelled all open orders: {cancel_statuses}")

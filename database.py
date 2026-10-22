import sqlite3
from datetime import datetime

DB_NAME = "trading_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS equity_history (
            timestamp TEXT PRIMARY KEY,
            equity REAL,
            cash REAL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            asset_type TEXT,
            qty REAL,
            avg_entry_price REAL,
            current_price REAL,
            premium_collected REAL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            action TEXT,
            symbol TEXT,
            qty REAL,
            details REAL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_plans (
            ticker TEXT PRIMARY KEY,
            action_plan TEXT,
            target_gain_usd REAL,
            max_loss_usd REAL,
            reasoning TEXT,
            timestamp TEXT
        )
    ''')
    
    # Table for manual command queue from dashboard UI (e.g. Flatten All)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS command_queue (
            command TEXT PRIMARY KEY,
            status TEXT,
            timestamp TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def log_equity(equity, cash):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT OR REPLACE INTO equity_history (timestamp, equity, cash) VALUES (?, ?, ?)", (timestamp, equity, cash))
    conn.commit()
    conn.close()

def log_trade(action, symbol, qty, details):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO trade_logs (timestamp, action, symbol, qty, details) VALUES (?, ?, ?, ?, ?)", 
                   (timestamp, action, symbol, qty, details))
    conn.commit()
    conn.close()

def update_positions_table(client_positions):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    
    for p in client_positions:
        symbol = p.symbol
        asset_type = p.asset_class.value if hasattr(p.asset_class, 'value') else str(p.asset_class)
        qty = float(p.qty)
        avg_price = float(p.avg_entry_price)
        curr_price = float(p.current_price) if p.current_price else avg_price
        premium = abs(qty * avg_price) if qty < 0 else 0.0
        
        cursor.execute('''
            INSERT OR REPLACE INTO positions (symbol, asset_type, qty, avg_entry_price, current_price, premium_collected)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (symbol, asset_type, qty, avg_price, curr_price, premium))
        
    conn.commit()
    conn.close()

def save_game_plans(plans):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM game_plans")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for p in plans:
        cursor.execute('''
            INSERT OR REPLACE INTO game_plans (ticker, action_plan, target_gain_usd, max_loss_usd, reasoning, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (p['ticker'], p['action_plan'], p['target_gain_usd'], p['max_loss_usd'], p['reasoning'], timestamp))
    conn.commit()
    conn.close()

def get_pending_command():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT command FROM command_queue WHERE status='PENDING'")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def clear_command(command):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE command_queue SET status='EXECUTED' WHERE command=?", (command,))
    conn.commit()
    conn.close()

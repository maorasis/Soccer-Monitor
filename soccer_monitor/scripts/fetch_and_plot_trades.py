"""
Polymarket Trade History Fetcher & Plotter

Searches the Polymarket Gamma API for a specific event and market (e.g.
Over/Under 2.5 goals), downloads the last ~4 hours of CLOB price history
at 1-second fidelity, saves it to CSV, and generates a probability chart.

Usage:
    1. Set EVENT_SLUG (or EVENT_QUERY / TEAM_2_QUERY) and MARKET_QUERY.
    2. Run:  python fetch_and_plot_trades.py
    3. Outputs: CSV data file + PNG plot.
"""

import requests
import pandas as pd
# pyrefly: ignore [missing-import]
import matplotlib.pyplot as plt
import datetime

# 1. Configuration
EVENT_QUERY = "Chelsea"
TEAM_2_QUERY = "Tottenham"
MARKET_QUERY = "O/U 2.5"

# If the search fails, paste the slug from the Polymarket URL here:
# Example: "bologna-vs-inter-milano-may-23"
EVENT_SLUG = "epl-che-tot-2026-05-19-more-markets"

CSV_FILENAME = "che_tot_history.csv"
PLOT_FILENAME = "che_tot_plot.png"

def fetch_market_info():
    """Finds the event and market on Polymarket."""
    url = "https://gamma-api.polymarket.com/events"
    
    if EVENT_SLUG:
        print(f"Fetching event exactly by slug: {EVENT_SLUG}")
        r = requests.get(url, params={'slug': EVENT_SLUG})
        events = r.json()
    else:
        print("Searching Gamma API for the event...")
        events = []
        # Fetch up to 2000 events to find it
        for offset in [0, 500, 1000, 1500]:
            r = requests.get(url, params={'limit': 500, 'offset': offset})
            data = r.json()
            if not data: break
            events.extend(data)
            
    if not events:
        return None
        
    for e in events:
        title = e.get('title', '')
        if EVENT_SLUG or (EVENT_QUERY in title and (TEAM_2_QUERY in title or "Milano" in title)):
            print(f"Found Event: {title}")
            for m in e.get('markets', []):
                question = m.get('question', '')
                if MARKET_QUERY in question:
                    print(f"Found Market: {question}")
                    return m
    return None

def fetch_price_history(token_id):
    """Fetches the historical price data from the CLOB."""
    print(f"Fetching price history for token ID: {token_id}")
    url = "https://clob.polymarket.com/prices-history"
    
    # First, get the last available timestamp for this market
    params_all = {
        'market': token_id,
        'interval': 'all'
    }
    r_all = requests.get(url, params=params_all)
    if r_all.status_code != 200:
        print(f"Error fetching history: {r_all.status_code} - {r_all.text}")
        return []
        
    history_all = r_all.json().get('history', [])
    if not history_all:
        return []
        
    last_t = history_all[-1]['t']
    start_ts = last_t - 4 * 3600
    
    params = {
        'startTs': start_ts,
        'endTs': last_t,
        'fidelity': 1,
        'market': token_id
    }
    
    r = requests.get(url, params=params)
    if r.status_code != 200:
        print(f"Error fetching history: {r.status_code} - {r.text}")
        return []
        
    data = r.json()
    return data.get('history', [])

def process_and_plot(history_data, market_info):
    """Convert raw price history into a DataFrame, save to CSV, and render a plot.

    Args:
        history_data: List of dicts with keys ``t`` (unix timestamp) and
            ``p`` (price / probability for the "Yes" / "Over" outcome).
        market_info: Market dict from the Gamma API, used to pull the
            market question for the plot title.
    """
    if not history_data:
        print("No historical data found for this market.")
        return
        
    print(f"Processing {len(history_data)} data points...")
    
    # The history data is usually a list of dicts: {"t": timestamp_unix, "p": price}
    # For binary markets, the price returned is generally for the "Yes" or "Over" token.
    # The "Under" token price is 1 - price.
    
    df = pd.DataFrame(history_data)
    
    # Convert timestamp to datetime
    df['datetime'] = pd.to_datetime(df['t'], unit='s')
    
    # Calculate prices
    df['price_over'] = df['p']
    df['price_under'] = 1.0 - df['p']
    
    # Save to CSV
    df.to_csv(CSV_FILENAME, index=False)
    print(f"Saved data to {CSV_FILENAME}")
    
    # Plot
    plt.figure(figsize=(12, 6))
    plt.plot(df['datetime'], df['price_over'], label='Outcome 1 (Yes/Over)', color='blue', linewidth=2)
    plt.plot(df['datetime'], df['price_under'], label='Outcome 2 (No/Under)', color='red', linewidth=2)
    
    plt.title(f"Polymarket Price History\n{market_info.get('question')}")
    plt.xlabel('Date/Time (UTC)')
    plt.ylabel('Probability / Price ($)')
    plt.ylim(0, 1.0)
    plt.grid(True, alpha=0.3)
    plt.legend()
    
    # Rotate x-axis labels for better readability
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    # Save Plot
    plt.savefig(PLOT_FILENAME)
    print(f"Saved plot to {PLOT_FILENAME}")
    plt.close()

def main():
    """Entry point: find the market, fetch price history, and plot it."""
    market_info = fetch_market_info()
    if not market_info:
        print("Could not find the specified market. The event might have expired or the name might be slightly different.")
        return
        
    import json
    clob_tokens_str = market_info.get('clobTokenIds')
    if not clob_tokens_str:
        print("Market missing clobTokenIds.")
        return
        
    clob_tokens = json.loads(clob_tokens_str)
    if not clob_tokens:
        print("Empty clobTokenIds.")
        return
        
    token_id = clob_tokens[0]
    
    history = fetch_price_history(token_id)
    process_and_plot(history, market_info)

if __name__ == "__main__":
    main()

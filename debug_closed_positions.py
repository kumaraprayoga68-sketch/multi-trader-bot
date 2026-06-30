import requests

wallet = "0x56687bf447db6ffa42ffe2204a05edaa20f55839"  # Theo4, profit leaderboard $22jt

headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

urls = [
    f"https://data-api.polymarket.com/positions?user={wallet}&sortBy=CASHPNL&sortDirection=DESC",
    f"https://data-api.polymarket.com/positions?user={wallet}&closed=true",
    f"https://data-api.polymarket.com/positions?user={wallet}&redeemable=true",
]

for url in urls:
    print(f"\nTrying: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"Status: {r.status_code}")
        data = r.json()
        print(f"Jumlah hasil: {len(data) if isinstance(data, list) else 'N/A'}")
        if isinstance(data, list) and len(data) > 0:
            print(f"Contoh data pertama: {data[0]}")
    except Exception as e:
        print(f"Error: {e}")
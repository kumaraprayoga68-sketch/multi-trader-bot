import requests

wallet = "0x56687bf447db6ffa42ffe2204a05edaa20f55839"
headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

urls = [
    f"https://data-api.polymarket.com/trades?user={wallet}&limit=20",
    f"https://data-api.polymarket.com/activity?user={wallet}&limit=20",
    f"https://lb-api.polymarket.com/profile/{wallet}",
]

for url in urls:
    print(f"\nTrying: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                print(f"Jumlah hasil: {len(data)}")
                if len(data) > 0:
                    print(f"Contoh data: {data[0]}")
            else:
                print(f"Data: {str(data)[:500]}")
    except Exception as e:
        print(f"Error: {e}")
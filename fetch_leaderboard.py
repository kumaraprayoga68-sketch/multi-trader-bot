import requests

urls_to_try = [
    "https://lb-api.polymarket.com/profit?window=all&limit=20",
    "https://data-api.polymarket.com/leaderboard?window=all&limit=20",
    "https://gamma-api.polymarket.com/leaderboard?limit=20",
]

headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

for url in urls_to_try:
    print(f"\nTrying: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            print(f"Response: {r.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")
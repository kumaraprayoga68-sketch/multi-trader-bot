"""
Diagnostic: nyari value 'window' yang bener-bener valid buat lb-api.polymarket.com/profit.

Kenapa perlu ini? Udah 2x salah tebak (lowercase 'month' -> 400, uppercase 'MONTH' -> 400
juga). Daripada tebak-tebakan lagi buang waktu, mending coba banyak kandidat sekaligus
dan liat langsung mana yang dibales 200.
"""

import requests

url = "https://lb-api.polymarket.com/profit"
headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

kandidat_window = [
    "all", "ALL",
    "day", "DAY", "today", "TODAY",
    "week", "WEEK",
    "month", "MONTH",
    "1d", "7d", "30d",
    "1", "7", "30",
]

print(f"{'='*70}")
print(f"Testing berbagai value 'window' ke {url}")
print(f"{'='*70}\n")

for w in kandidat_window:
    try:
        r = requests.get(url, headers=headers, params={"window": w, "limit": 5}, timeout=10)
        status = r.status_code
        if status == 200:
            data = r.json()
            jumlah = len(data) if isinstance(data, list) else "N/A"
            print(f"✅ window='{w}': status {status}, jumlah hasil: {jumlah}")
        else:
            print(f"❌ window='{w}': status {status}")
    except Exception as e:
        print(f"❌ window='{w}': error {e}")

print(f"\n{'='*70}")
print("Selesai. Pake value yang keluar ✅ di atas.")
print(f"{'='*70}")
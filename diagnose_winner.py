"""
Diagnostic: cek raw JSON dari get_market() buat 1 market yang UDAH KETAUAN
resolve (dari hasil evaluasi_hasil.csv), biar bisa divalidasi apakah field
"closed" dan "winner" yang dipake di market_status.py itu BENERAN ada dan
bener logic-nya -- bukan asumsi/tebakan lagi.
"""

import csv
import json
from market_status import get_market_info

INPUT_FILE = "riwayat_trading.csv"

with open(INPUT_FILE, "r", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# ambil 1 market_id yang udah ada bet_amount-nya (berarti IKUT), buat dicek raw-nya
baris_ikut = [r for r in rows if r.get("keputusan") == "IKUT" and r.get("market_id")]

if not baris_ikut:
    print("Gak ada baris IKUT dengan market_id yang bisa dicek.")
else:
    contoh = baris_ikut[0]
    condition_id = contoh["market_id"]
    outcome_dipilih = contoh["outcome"]

    print(f"{'='*70}")
    print(f"Market   : {contoh['market']}")
    print(f"Outcome yang dipilih bot: {outcome_dipilih}")
    print(f"condition_id: {condition_id}")
    print(f"{'='*70}\n")

    market_info = get_market_info(condition_id)

    if not market_info:
        print("❌ Gagal fetch market_info sama sekali (return None).")
    else:
        print("RAW market_info (field-level, bukan full JSON biar gak kepanjangan):")
        print(f"  Top-level keys: {list(market_info.keys())}")
        print(f"  closed        : {market_info.get('closed')}")
        print(f"  active        : {market_info.get('active')}")

        tokens = market_info.get("tokens", [])
        print(f"\n  Jumlah tokens : {len(tokens)}")
        for t in tokens:
            print(f"  - outcome='{t.get('outcome')}', winner={t.get('winner')!r} "
                  f"(type: {type(t.get('winner')).__name__}), price={t.get('price')}")

        print(f"\n{'='*70}")
        print("FULL RAW JSON (buat dicek manual kalo perlu):")
        print(f"{'='*70}")
        print(json.dumps(market_info, indent=2, default=str)[:3000])
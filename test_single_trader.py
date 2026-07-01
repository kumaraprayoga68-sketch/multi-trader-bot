"""
Test manual: scan posisi AKTIF trader yang lo copy, tampilin listnya, terus lo pilih
salah satu buat disimulasiin sebagai "posisi baru" -- biar bisa liat LANGSUNG seluruh
alur (AI analysis -> keputusan -> bet size) tanpa nunggu trader aslinya beneran entry
posisi baru, dan tanpa perlu nebak-nebak conditionId manual.

INI GAK NUNGGU APAPUN -- begitu lo pilih, langsung trigger eksekusi_single_trader().
Order tetep [PAPER] kalo SIMULASI_MODE=True di multi_monitor.py (gak beneran kirim apapun).
"""

from multi_monitor import fetch_posisi, parse_posisi, eksekusi_single_trader, performa_pnl_trader_terpilih
from trader_pnl import hitung_net_pnl_final

# ── GANTI KE WALLET TRADER YANG LO COPY SEKARANG ────────────────────────────
WALLET = "0xe9a6ed2e4d4ee8ce47cd47cac834746dc4cf627b"


def scan_posisi_aktif(wallet):
    print(f"🔍 Fetching posisi aktif buat {wallet[:10]}...\n")
    data = fetch_posisi(wallet)

    if not data:
        print("⚠️  Gagal fetch / wallet ini gak punya posisi aktif sama sekali.")
        return {}

    posisi_dict = parse_posisi(data)

    if not posisi_dict:
        print("⚠️  Wallet ini lagi gak pegang posisi apapun saat ini.")
        return {}

    return posisi_dict


def tampilkan_dan_pilih(posisi_dict):
    daftar = list(posisi_dict.items())  # [(market_id, posisi), ...]

    print(f"{'='*70}")
    print(f"POSISI AKTIF ({len(daftar)} posisi)")
    print(f"{'='*70}")
    for i, (market_id, p) in enumerate(daftar):
        print(f"[{i}] {p['title'][:55]}")
        print(f"     outcome: {p['outcome']}  |  size: {p['size']:.2f}  |  market_id: {market_id[:20]}...")
    print(f"{'='*70}\n")

    idx = 0  # otomatis ambil yang pertama, gak nanya lagi
    print(f"🤖 Otomatis pilih posisi [{idx}] buat ditest.\n")

    market_id, posisi = daftar[idx]
    return {
        "market_id": market_id,
        "title": posisi["title"],
        "outcome": posisi["outcome"],
        "wallet": WALLET,
    }


if __name__ == "__main__":
    posisi_dict = scan_posisi_aktif(WALLET)

    if not posisi_dict:
        print("\n🛑 Gak ada posisi buat ditest. Coba wallet lain atau tunggu dia entry posisi baru.")
    else:
        # isi data net PnL dulu biar AI dapet konteks track record beneran
        # (tanpa ini, AI cuma bakal bilang "data performa gak tersedia")
        print("📊 Ngambil net PnL track record buat konteks AI...")
        hasil_pnl = hitung_net_pnl_final(WALLET)
        if hasil_pnl:
            performa_pnl_trader_terpilih[WALLET] = hasil_pnl
            print(f"   ✅ Net PnL ${hasil_pnl['net_pnl']:,.0f}, win rate {hasil_pnl['win_rate']:.1f}%, "
                  f"{hasil_pnl['total_closed']} closed positions\n")
        else:
            print("   ⚠️  Gagal ambil net PnL, AI bakal nilai tanpa konteks track record.\n")

        sinyal_test = tampilkan_dan_pilih(posisi_dict)

        print(f"\n{'='*60}")
        print(f"MENJALANKAN TEST — Simulasi trader baru entry")
        print(f"{'='*60}")
        print(f"Market : {sinyal_test['title']}")
        print(f"Outcome: {sinyal_test['outcome']}")
        print(f"Wallet : {sinyal_test['wallet']}")
        print(f"{'='*60}\n")

        eksekusi_single_trader(sinyal_test)
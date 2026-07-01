"""
Report/logging module -- nyatet SETIAP keputusan bot (IKUT maupun SKIP) ke file CSV
yang numpuk terus antar-run (gak ke-reset pas bot di-restart). Biar ada histori
yang bisa dibuka di Excel/Google Sheets buat direview belakangan.
"""

import csv
import os
from datetime import datetime

REPORT_FILE = "riwayat_trading.csv"

KOLOM = [
    "tanggal", "waktu", "mode", "market", "market_id", "outcome", "wallet",
    "jumlah_trader", "keputusan", "confidence", "bet_amount", "harga_entry",
    "status", "alasan_ai", "budget_terpakai",
]


def catat_ke_report(entry: dict):
    """
    Nambahin 1 baris ke riwayat_trading.csv. Bikin file + header dulu kalo belum ada.

    PENTING: kalo file udah ada TAPI header-nya beda dari KOLOM sekarang (misal
    abis nambah kolom baru di update terakhir), file lama itu di-backup (rename
    pake timestamp) dan mulai file baru yang bersih -- biar kolom gak "geser"
    dan kebaca salah pas dianalisis nanti.
    """
    if os.path.exists(REPORT_FILE):
        try:
            with open(REPORT_FILE, "r", newline="", encoding="utf-8") as f:
                header_sekarang = next(csv.reader(f), [])
            if header_sekarang != KOLOM:
                backup_name = f"riwayat_trading_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                os.rename(REPORT_FILE, backup_name)
                print(f"⚠️  Skema kolom report berubah -- file lama di-backup ke {backup_name}, "
                      f"mulai file baru yang bersih.")
        except Exception as e:
            print(f"⚠️  Gagal cek header report lama: {e}")

    file_baru = not os.path.exists(REPORT_FILE)

    try:
        with open(REPORT_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=KOLOM)
            if file_baru:
                writer.writeheader()

            sekarang = datetime.now()
            baris = {
                "tanggal": sekarang.strftime("%Y-%m-%d"),
                "waktu": sekarang.strftime("%H:%M:%S"),
            }
            for k in KOLOM:
                if k not in ("tanggal", "waktu"):
                    baris[k] = entry.get(k, "")

            writer.writerow(baris)
    except Exception as e:
        print(f"⚠️  Gagal nulis ke {REPORT_FILE}: {e}")


def ringkasan_report():
    """
    Baca ulang riwayat_trading.csv, kasih ringkasan cepet (total sinyal, IKUT/SKIP,
    total bet). Berguna buat cek histori tanpa buka Excel.
    """
    if not os.path.exists(REPORT_FILE):
        print(f"Belum ada file {REPORT_FILE} -- belum pernah ada sinyal tercatat.")
        return

    with open(REPORT_FILE, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("File report ada tapi kosong.")
        return

    total = len(rows)
    ikut = [r for r in rows if r["keputusan"] == "IKUT"]
    skip = [r for r in rows if r["keputusan"] == "SKIP"]
    total_bet = sum(float(r["bet_amount"]) for r in ikut if r.get("bet_amount"))

    print(f"\n{'='*60}")
    print(f"📊 RINGKASAN {REPORT_FILE} ({total} total sinyal tercatat)")
    print(f"{'='*60}")
    print(f"✅ IKUT : {len(ikut)}")
    print(f"❌ SKIP : {len(skip)}")
    print(f"💰 Total bet (semua waktu) : ${total_bet:,.2f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ringkasan_report()
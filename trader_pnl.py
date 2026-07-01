"""
Net PnL calculator FINAL — gabungan /positions + /activity.

RINGKASAN PERJALANAN NGEBANGUN INI (biar kebaca alasannya kalo lupa nanti):
1. Coba /positions doang -> BIAS. Posisi MENANG ilang begitu di-redeem (uang cair),
   posisi KALAH numpuk terus (gak ada insentif redeem token $0). Hasilnya: 172 posisi,
   0 menang 172 kalah -- itu bias endpoint, bukan performa asli trader.
2. Coba /activity doang -> lengkap tapi butuh effort matching BUY vs REDEEM per market,
   dan ternyata ada tipe activity (REWARD/YIELD/REBATE) yang gak terikat ke market
   spesifik (conditionId kosong) -- harus di-exclude dari itungan per-market.
3. SOLUSI: gabungin dua-duanya.
   - /positions (filter curPrice 0 atau 1) kasih kita SEMUA market yang UDAH RESOLVE,
     termasuk yang KALAH (curPrice=0, masih nongol karena gak pernah di-redeem) dan
     yang MENANG tapi BELUM diklaim (curPrice=1, redeemable=true, masih nongol).
   - /activity (type=REDEEM) kasih kita market yang MENANG dan SUDAH diklaim (makanya
     ilang dari /positions) -- proceeds-nya dari usdcSize, cost-nya dari total BUY
     usdcSize di conditionId yang sama.
   - Gabung by conditionId, REDEEM diprioritasin (itu final/aktual), sisanya pake
     cashPnl dari /positions.

CATATAN KETERBATASAN (tetep ada, jujur aja):
- Market yang MASIH OPEN (belum resolve) otomatis ke-exclude (curPrice bukan 0/1 di
  /positions, dan gak ada REDEEM karena emang belum bisa diklaim) -- itu benar,
  gak masuk itungan realized PnL.
- Kalo /activity dibatasi API cuma ambil N record terakhir (offset makin gede kena
  400), riwayat BUY yang lebih lama dari itu gak kehitung cost-nya. Ini best-effort,
  bukan 100% lengkap kalo history trader-nya udah sangat panjang.
"""

import requests
import time
from datetime import datetime

REBATE_TYPES = {"MAKER_REBATE", "TAKER_REBATE", "REWARD", "YIELD"}  # exclude dari per-market PnL


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def _get(url, params, max_retry=2):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    for percobaan in range(max_retry + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code == 429 and percobaan < max_retry:
                time.sleep(2 * (percobaan + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception:
            if percobaan < max_retry:
                time.sleep(1.5 * (percobaan + 1))
                continue
            return []
    return []


def fetch_resolved_positions(wallet, limit=500):
    """Posisi yang market-nya udah resolve (curPrice 0 atau 1), dari /positions."""
    url = "https://data-api.polymarket.com/positions"
    semua = _get(url, {"user": wallet, "limit": limit})
    resolved = []
    for p in semua:
        try:
            cur_price = float(p.get("curPrice", -1))
        except (TypeError, ValueError):
            continue
        if cur_price in (0.0, 1.0):
            resolved.append(p)
    return resolved


def fetch_activity_all(wallet, page_size=500, max_records=5000):
    """Semua activity 1 wallet, dengan pagination (offset)."""
    url = "https://data-api.polymarket.com/activity"
    semua = []
    offset = 0
    while len(semua) < max_records:
        halaman = _get(url, {"user": wallet, "limit": page_size, "offset": offset})
        if not halaman:
            break
        semua.extend(halaman)
        if len(halaman) < page_size:
            break
        offset += page_size
        time.sleep(0.3)
    return semua


def hitung_net_pnl_final(wallet, min_closed=5):
    """
    Net PnL gabungan /positions (losses + unclaimed wins) + /activity (claimed wins).
    Return dict ringkasan, atau None kalo gagal total.
    """
    resolved_positions = fetch_resolved_positions(wallet)
    activity = fetch_activity_all(wallet)

    if not resolved_positions and not activity:
        return None

    # --- kumpulin cost BUY per conditionId dari activity ---
    buy_cost_per_market = {}
    redeem_proceeds_per_market = {}

    for a in activity:
        cid = a.get("conditionId", "")
        tipe = a.get("type", "")
        side = a.get("side", "")

        if not cid or tipe in REBATE_TYPES:
            continue

        try:
            usdc = float(a.get("usdcSize", 0))
        except (TypeError, ValueError):
            usdc = 0.0

        if tipe == "TRADE" and side == "BUY":
            buy_cost_per_market[cid] = buy_cost_per_market.get(cid, 0.0) + usdc
        elif tipe == "TRADE" and side == "SELL":
            # SELL sebelum resolve = proceeds juga (posisi dijual duluan sebelum market kelar)
            redeem_proceeds_per_market[cid] = redeem_proceeds_per_market.get(cid, 0.0) + usdc
        elif tipe == "REDEEM":
            redeem_proceeds_per_market[cid] = redeem_proceeds_per_market.get(cid, 0.0) + usdc

    # --- gabungin: market yang ada REDEEM/SELL = menang & sudah diklaim ---
    hasil_per_market = {}

    for cid, proceeds in redeem_proceeds_per_market.items():
        cost = buy_cost_per_market.get(cid, 0.0)
        hasil_per_market[cid] = proceeds - cost

    # --- market yang resolve tapi GAK ada di redeem (kalah, atau menang belum klaim) ---
    for p in resolved_positions:
        cid = p.get("conditionId", "")
        if not cid or cid in hasil_per_market:
            continue  # udah kehitung dari REDEEM, skip biar gak dobel
        try:
            pnl = float(p.get("cashPnl", 0))
        except (TypeError, ValueError):
            pnl = 0.0
        hasil_per_market[cid] = pnl

    if not hasil_per_market:
        return None

    total_closed = len(hasil_per_market)
    net_pnl = sum(hasil_per_market.values())
    menang = sum(1 for v in hasil_per_market.values() if v > 0)
    kalah = sum(1 for v in hasil_per_market.values() if v < 0)
    breakeven = total_closed - menang - kalah
    win_rate = (menang / total_closed * 100) if total_closed > 0 else 0

    return {
        "wallet": wallet,
        "total_closed": total_closed,
        "menang": menang,
        "kalah": kalah,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "avg_pnl_per_posisi": net_pnl / total_closed if total_closed > 0 else 0,
        "dari_redeem": len(redeem_proceeds_per_market),
        "dari_positions": total_closed - len(redeem_proceeds_per_market),
    }


def screening_trader_by_pnl_final(daftar_wallet, min_closed=5, min_net_pnl=0, min_win_rate=50):
    """Screening trader pake net PnL gabungan. Ini yang harusnya dipake di multi_monitor.py."""
    log(f"🔍 Screening {len(daftar_wallet)} trader (net PnL gabungan positions+activity)...")
    lolos, gagal = [], []

    for wallet in daftar_wallet:
        hasil = hitung_net_pnl_final(wallet)
        if not hasil or hasil["total_closed"] < min_closed:
            continue

        if hasil["net_pnl"] >= min_net_pnl and hasil["win_rate"] >= min_win_rate:
            lolos.append(hasil)
            log(f"✅ {wallet[:10]}... — net PnL ${hasil['net_pnl']:,.2f}, "
                f"win rate {hasil['win_rate']:.1f}% ({hasil['menang']}W/{hasil['kalah']}L)")
        else:
            gagal.append(hasil)
            log(f"❌ {wallet[:10]}... — net PnL ${hasil['net_pnl']:,.2f}, "
                f"win rate {hasil['win_rate']:.1f}%")

    return lolos, gagal


# ── TEST ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # coba wallet dari hasil scan leaderboard lo, BUKAN wallet market-maker
    daftar_test = [
        "0xf0318c32136c2db7fec88b84869aee6a1106c80c",   # BreakTheBank (market maker, expect banyak BUY dikit REDEEM)
        "0x56687bf447db6ffa42ffe2204a05edaa20f55839",   # Theo4 (dulu /positions kosong total)
    ]

    for w in daftar_test:
        print(f"\n{'='*70}")
        print(f"WALLET: {w}")
        print(f"{'='*70}")
        hasil = hitung_net_pnl_final(w)
        if hasil:
            print(f"Total closed  : {hasil['total_closed']} "
                  f"(dari REDEEM: {hasil['dari_redeem']}, dari positions: {hasil['dari_positions']})")
            print(f"Menang/Kalah  : {hasil['menang']}W / {hasil['kalah']}L / {hasil['breakeven']} BE")
            print(f"Win rate      : {hasil['win_rate']:.1f}%")
            print(f"Net PnL       : ${hasil['net_pnl']:,.2f}")
            print(f"Avg per posisi: ${hasil['avg_pnl_per_posisi']:,.2f}")
        else:
            print("Gagal / gak ada data.")
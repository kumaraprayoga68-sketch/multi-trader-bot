import requests
from datetime import datetime


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def fetch_activity_history(wallet, limit=100):
    """
    Fetch history aktivitas trader (BUY, SELL, REDEEM, MERGE, dll).
    REDEEM = klaim kemenangan dari posisi yang resolve menang.
    """
    url = f"https://data-api.polymarket.com/activity"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    params = {"user": wallet, "limit": limit}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"❌ Gagal fetch activity {wallet[:10]}...: {e}")
        return []


def hitung_performa_dari_activity(wallet, limit=100):
    """
    Hitung performa trader berdasarkan history REDEEM (menang) vs total trade.
    """
    activity = fetch_activity_history(wallet, limit=limit)

    if not activity:
        return None

    total_buy   = sum(1 for a in activity if a.get("type") == "TRADE" and a.get("side") == "BUY")
    total_redeem = sum(1 for a in activity if a.get("type") == "REDEEM")
    nilai_redeem = sum(float(a.get("size", 0)) for a in activity if a.get("type") == "REDEEM")
    nilai_buy    = sum(float(a.get("size", 0)) * float(a.get("price", 0))
                       for a in activity if a.get("type") == "TRADE" and a.get("side") == "BUY")

    total_transaksi = total_buy + total_redeem
    win_rate_estimasi = (total_redeem / total_transaksi * 100) if total_transaksi > 0 else 0

    return {
        "wallet": wallet,
        "total_buy": total_buy,
        "total_redeem": total_redeem,
        "nilai_redeem": nilai_redeem,
        "nilai_buy": nilai_buy,
        "win_rate_estimasi": win_rate_estimasi,
        "total_aktivitas": len(activity),
    }


def screening_trader_v2(daftar_wallet, min_aktivitas=5, min_redeem=1):
    """
    Screening trader berdasarkan history activity (REDEEM/BUY ratio).
    """
    log(f"🔍 Screening {len(daftar_wallet)} trader (berdasarkan activity history)...")
    lolos = []
    gagal = []

    for wallet in daftar_wallet:
        performa = hitung_performa_dari_activity(wallet)

        if not performa:
            continue

        if performa["total_aktivitas"] < min_aktivitas:
            log(f"⚠️  {wallet[:10]}... aktivitas terlalu sedikit ({performa['total_aktivitas']}), skip")
            continue

        if performa["total_redeem"] >= min_redeem:
            lolos.append(performa)
            log(f"✅ {wallet[:10]}... — {performa['total_redeem']} redeem, "
                f"nilai ${performa['nilai_redeem']:,.2f}, "
                f"win rate estimasi {performa['win_rate_estimasi']:.1f}%")
        else:
            gagal.append(performa)
            log(f"❌ {wallet[:10]}... — {performa['total_redeem']} redeem (terlalu sedikit)")

    return lolos, gagal


# ── TEST ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    daftar_trader = [
        "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
    ]

    lolos, gagal = screening_trader_v2(daftar_trader, min_aktivitas=5, min_redeem=1)

    print(f"\n{'='*60}")
    print(f"HASIL SCREENING v2 (berdasarkan activity)")
    print(f"{'='*60}")
    print(f"Lolos: {len(lolos)} | Gagal: {len(gagal)}")

    for t in lolos:
        print(f"\n{t['wallet']}")
        print(f"  Total redeem    : {t['total_redeem']}")
        print(f"  Nilai redeem    : ${t['nilai_redeem']:,.2f}")
        print(f"  Win rate est.   : {t['win_rate_estimasi']:.1f}%")
import requests
from datetime import datetime
from trader_screener import fetch_performa_trader


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def fetch_top_traders(limit=20):
    """
    Fetch top trader dari leaderboard profit Polymarket.
    """
    url = "https://lb-api.polymarket.com/profit"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    params = {"window": "all", "limit": limit}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        hasil = []
        for t in data:
            hasil.append({
                "wallet": t.get("proxyWallet"),
                "nama": t.get("pseudonym") or t.get("name", "Unknown"),
                "profit_leaderboard": float(t.get("amount", 0)),
            })
        return hasil

    except Exception as e:
        log(f"❌ Gagal fetch leaderboard: {e}")
        return []


def auto_screening(limit_trader=15, min_win_rate=55, min_posisi=10):
    """
    Fetch top trader dari leaderboard, lalu screening otomatis
    berdasarkan win rate dan PnL aktual dari posisi mereka.
    """
    log(f"📋 Fetch top {limit_trader} trader dari leaderboard...")
    top_traders = fetch_top_traders(limit=limit_trader)

    if not top_traders:
        log("❌ Gagal mendapatkan data leaderboard")
        return []

    log(f"✅ Dapat {len(top_traders)} trader, mulai screening detail...\n")

    lolos = []
    for t in top_traders:
        wallet = t["wallet"]
        nama   = t["nama"]

        performa = fetch_performa_trader(wallet)
        if not performa:
            continue

        if performa["total"] < min_posisi:
            log(f"⚠️  {nama} ({wallet[:10]}...) — data terlalu sedikit ({performa['total']} posisi), skip")
            continue

        if performa["win_rate"] >= min_win_rate and performa["total_pnl"] > 0:
            performa["nama"] = nama
            lolos.append(performa)
            log(f"✅ {nama} — LOLOS! Win rate {performa['win_rate']:.1f}%, "
                f"PnL ${performa['total_pnl']:,.2f}")
        else:
            log(f"❌ {nama} — GAGAL. Win rate {performa['win_rate']:.1f}%, "
                f"PnL ${performa['total_pnl']:,.2f}")

    return lolos


# ── TEST ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    lolos = auto_screening(limit_trader=30, min_win_rate=50, min_posisi=3)

    print(f"\n{'='*60}")
    print(f"HASIL AUTO-SCREENING")
    print(f"{'='*60}")
    print(f"Trader yang LOLOS: {len(lolos)}\n")

    if lolos:
        print("Copy paste ke DAFTAR_TRADER di multi_monitor.py:\n")
        print("DAFTAR_TRADER = [")
        for t in lolos:
            print(f'    "{t["wallet"]}",  # {t["nama"]} — WR {t["win_rate"]:.0f}%, PnL ${t["total_pnl"]:,.0f}')
        print("]")
    else:
        print("Tidak ada trader yang lolos kriteria. Coba turunkan min_win_rate.")
import requests
from datetime import datetime


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def fetch_performa_trader(wallet):
    """Fetch performa trader: win rate, total posisi, estimasi PnL."""
    url = f"https://data-api.polymarket.com/positions?user={wallet}&sizeThreshold=.1"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

        total = len(data)
        menang = 0
        loss = 0
        total_pnl = 0.0

        for p in data:
            size  = float(p.get("size", 0))
            value = float(p.get("currentValue", 0))
            pnl   = value - size
            total_pnl += pnl
            if pnl > 0:
                menang += 1
            elif pnl < 0:
                loss += 1

        win_rate = (menang / total * 100) if total > 0 else 0

        return {
            "wallet": wallet,
            "total": total,
            "menang": menang,
            "loss": loss,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
        }
    except Exception as e:
        log(f"❌ Gagal fetch performa {wallet[:10]}...: {e}")
        return None


def screening_trader(daftar_wallet, min_win_rate=50, min_posisi=10):
    """
    Screening beberapa trader sekaligus.
    Return: list trader yang LOLOS kriteria (layak di-monitor)
    """
    log(f"🔍 Screening {len(daftar_wallet)} trader...")
    lolos = []
    gagal = []

    for wallet in daftar_wallet:
        performa = fetch_performa_trader(wallet)

        if not performa:
            continue

        if performa["total"] < min_posisi:
            log(f"⚠️  {wallet[:10]}... terlalu sedikit data ({performa['total']} posisi), skip")
            continue

        if performa["win_rate"] >= min_win_rate and performa["total_pnl"] > 0:
            lolos.append(performa)
            log(f"✅ {wallet[:10]}... LOLOS — win rate {performa['win_rate']:.1f}%, "
                f"PnL ${performa['total_pnl']:,.2f}")
        else:
            gagal.append(performa)
            log(f"❌ {wallet[:10]}... GAGAL — win rate {performa['win_rate']:.1f}%, "
                f"PnL ${performa['total_pnl']:,.2f}")

    return lolos, gagal


# ── TEST ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    daftar_trader = [
        "0xf0318c32136c2db7fec88b84869aee6a1106c80c",
        "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
        "0x72254fe1a79fc8fd37de0168be735e6af4bd659a",
        "0x65018f9fc473f6e920b8929a375d39c26a461220",
        "0xe549581668a5751c1972d3ad2d1991d900bd2d54",
        "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",
        # tambahin wallet trader lain di sini
    ]

    lolos, gagal = screening_trader(daftar_trader, min_win_rate=55, min_posisi=10)

    print(f"\n{'='*60}")
    print(f"HASIL SCREENING")
    print(f"{'='*60}")
    print(f"Lolos  : {len(lolos)} trader")
    print(f"Gagal  : {len(gagal)} trader")

    if lolos:
        print(f"\nTrader yang LAYAK di-monitor:")
        for t in lolos:
            print(f"  {t['wallet']} — win rate {t['win_rate']:.1f}%, PnL ${t['total_pnl']:,.2f}")
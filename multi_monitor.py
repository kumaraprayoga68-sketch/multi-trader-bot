import requests
import time
from datetime import datetime
from trader_screener_v2 import hitung_performa_dari_activity
from ai_consensus_analyzer import analisis_consensus, print_analisis_consensus

# ── CONFIG ─────────────────────────────────────────────────────────────────
AUTO_PILIH_TRADER  = True   # True = bot pilih trader sendiri, False = pakai DAFTAR_TRADER manual
JUMLAH_TRADER      = 10     # berapa trader yang mau dimonitor (hasil auto-screening)
LIMIT_LEADERBOARD  = 30     # berapa banyak trader top yang di-scan dari leaderboard
MIN_REDEEM         = 3      # minimal berapa kali redeem buat dianggap "aktif menang"

DAFTAR_TRADER_MANUAL = [
    # isi manual di sini kalau AUTO_PILIH_TRADER = False
]

MIN_CONSENSUS      = 2
MIN_AI_CONFIDENCE  = 6
CHECK_INTERVAL     = 60
SUMMARY_INTERVAL   = 5
RESCAN_INTERVAL    = 60      # menit, berapa lama sebelum re-scan leaderboard buat trader baru
BUDGET_SAYA        = 100
MAX_PER_TRADE      = 5
MAX_LOSS           = 30
SIMULASI_MODE      = True

# ── STATE ──────────────────────────────────────────────────────────────────
loss_sekarang      = 0
posisi_per_trader  = {}
performa_cache     = {}
is_first_run       = True
riwayat_consensus  = []
last_summary_time  = 0
last_rescan_time   = 0
daftar_trader_aktif = []


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


# ── AUTO SCREENING ─────────────────────────────────────────────────────────
def fetch_top_traders(limit=30):
    url = "https://lb-api.polymarket.com/profit"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    params = {"window": "all", "limit": limit}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return [{"wallet": t.get("proxyWallet"), "nama": t.get("pseudonym") or t.get("name", "Unknown")}
                for t in data]
    except Exception as e:
        log(f"❌ Gagal fetch leaderboard: {e}")
        return []


def auto_pilih_trader():
    """
    Scan leaderboard, screening pakai redeem history, ambil top N trader terbaik.
    """
    log(f"📋 Auto-scan leaderboard ({LIMIT_LEADERBOARD} trader)...")
    top_traders = fetch_top_traders(limit=LIMIT_LEADERBOARD)

    if not top_traders:
        log("❌ Gagal fetch leaderboard, pakai daftar manual sebagai fallback")
        return DAFTAR_TRADER_MANUAL

    kandidat = []
    for t in top_traders:
        wallet = t["wallet"]
        nama   = t["nama"]

        perf = hitung_performa_dari_activity(wallet)
        if not perf or perf["total_redeem"] < MIN_REDEEM:
            continue

        perf["nama"] = nama
        kandidat.append(perf)

    kandidat.sort(key=lambda x: x["nilai_redeem"], reverse=True)
    terpilih = kandidat[:JUMLAH_TRADER]

    log(f"✅ {len(terpilih)} trader terpilih dari {len(top_traders)} yang di-scan:")
    for t in terpilih:
        log(f"   {t['nama']} — {t['total_redeem']} redeem, ${t['nilai_redeem']:,.0f}")

    return [t["wallet"] for t in terpilih]


# ── MONITORING ─────────────────────────────────────────────────────────────
def fetch_posisi(wallet):
    url = f"https://data-api.polymarket.com/positions?user={wallet}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"❌ Gagal fetch posisi {wallet[:10]}...: {e}")
        return None


def parse_posisi(data):
    hasil = {}
    for p in data:
        market_id = p.get("conditionId") or p.get("market", "unknown")
        hasil[market_id] = {
            "title"  : p.get("title", p.get("question", "N/A")),
            "outcome": p.get("outcome", "N/A"),
            "size"   : float(p.get("size", p.get("cash", 0))),
        }
    return hasil


def cek_consensus():
    market_outcome_count = {}

    for wallet, posisi_dict in posisi_per_trader.items():
        for market_id, posisi in posisi_dict.items():
            outcome = posisi["outcome"]
            title   = posisi["title"]

            if market_id not in market_outcome_count:
                market_outcome_count[market_id] = {"title": title, "outcomes": {}}

            if outcome not in market_outcome_count[market_id]["outcomes"]:
                market_outcome_count[market_id]["outcomes"][outcome] = []

            market_outcome_count[market_id]["outcomes"][outcome].append(wallet)

    sinyal_consensus = []
    for market_id, data in market_outcome_count.items():
        for outcome, wallets in data["outcomes"].items():
            if len(wallets) >= MIN_CONSENSUS:
                sinyal_consensus.append({
                    "market_id": market_id,
                    "title": data["title"],
                    "outcome": outcome,
                    "jumlah_trader": len(wallets),
                    "wallets": wallets,
                })

    return sinyal_consensus


def cek_boleh_bet(jumlah):
    if loss_sekarang >= MAX_LOSS:
        return False, "Stop loss kena"
    if jumlah > MAX_PER_TRADE:
        return False, f"Melebihi max per trade (${MAX_PER_TRADE})"
    return True, "OK"


def get_performa_wallets(wallets):
    hasil = []
    for w in wallets:
        if w not in performa_cache:
            perf = hitung_performa_dari_activity(w)
            if perf:
                performa_cache[w] = perf
        if w in performa_cache:
            hasil.append(performa_cache[w])
    return hasil


def eksekusi_consensus(sinyal):
    global loss_sekarang

    log(f"🚨 Consensus terdeteksi: {sinyal['jumlah_trader']} trader sepakat")
    log(f"   Market: {sinyal['title'][:50]}")

    performa_list = get_performa_wallets(sinyal["wallets"])

    log("🤖 Minta analisis AI...")
    hasil_ai = analisis_consensus(sinyal, performa_list)
    print_analisis_consensus(sinyal, hasil_ai)

    keputusan  = hasil_ai.get("keputusan", "SKIP")
    confidence = hasil_ai.get("confidence", 0)

    riwayat_consensus.append({
        "waktu": datetime.now().strftime("%H:%M:%S"),
        "market": sinyal["title"],
        "outcome": sinyal["outcome"],
        "jumlah_trader": sinyal["jumlah_trader"],
        "keputusan": keputusan,
        "confidence": confidence,
    })

    if keputusan == "IKUT" and confidence >= MIN_AI_CONFIDENCE:
        bet_amount = MAX_PER_TRADE
        boleh, alasan = cek_boleh_bet(bet_amount)

        if not boleh:
            log(f"⛔ {alasan}")
            return

        loss_sekarang += bet_amount
        log(f"✅ BET ${bet_amount} pada '{sinyal['outcome']}'")
        log(f"   Budget terpakai: ${loss_sekarang:.2f} / ${MAX_LOSS}")

        if SIMULASI_MODE:
            log(f"   [PAPER] Order dicatat ✏️")
        else:
            log(f"   ⚠️  Live execution belum diimplementasi")
    else:
        log(f"❌ SKIP — AI confidence {confidence}/10 (butuh ≥{MIN_AI_CONFIDENCE})")


def print_summary():
    sisa = BUDGET_SAYA - loss_sekarang
    n_ikut = sum(1 for c in riwayat_consensus if c["keputusan"] == "IKUT")
    n_skip = sum(1 for c in riwayat_consensus if c["keputusan"] == "SKIP")

    print(f"\n{'='*60}")
    print(f"📊 AUTO MULTI-TRADER SUMMARY — {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    print(f"Trader dimonitor : {len(daftar_trader_aktif)} (auto-selected)")
    print(f"Min consensus    : {MIN_CONSENSUS} trader")
    print(f"Sisa budget      : ${sisa:.2f} / ${BUDGET_SAYA}")
    print(f"Total sinyal     : {len(riwayat_consensus)}")
    print(f"✅ IKUT          : {n_ikut}")
    print(f"❌ SKIP          : {n_skip}")
    if riwayat_consensus:
        print(f"\n5 Sinyal Terakhir:")
        for c in riwayat_consensus[-5:]:
            print(f"  [{c['waktu']}] {c['market'][:40]}")
            print(f"     → {c['outcome']} | {c['jumlah_trader']} trader | "
                  f"AI: {c['keputusan']} ({c['confidence']}/10)")
    print(f"{'='*60}\n")


def main():
    global posisi_per_trader, is_first_run, last_summary_time
    global last_rescan_time, daftar_trader_aktif

    print(f"\n{'='*60}")
    print(f"  🤖 AUTO MULTI-TRADER + AI CONSENSUS BOT")
    print(f"{'='*60}")
    log(f"Mode trader       : {'AUTO (dari leaderboard)' if AUTO_PILIH_TRADER else 'MANUAL'}")
    log(f"Min consensus     : {MIN_CONSENSUS} trader harus searah")
    log(f"Min AI confidence : {MIN_AI_CONFIDENCE}/10")
    log(f"Modal: ${BUDGET_SAYA} | Max/trade: ${MAX_PER_TRADE} | Stop loss: ${MAX_LOSS}")
    log(f"Mode: {'SIMULASI 🟡' if SIMULASI_MODE else 'LIVE 🔴'}")
    print(f"{'='*60}\n")

    if AUTO_PILIH_TRADER:
        daftar_trader_aktif = auto_pilih_trader()
    else:
        daftar_trader_aktif = DAFTAR_TRADER_MANUAL

    if not daftar_trader_aktif:
        log("❌ Tidak ada trader yang bisa dimonitor. Bot berhenti.")
        return

    last_summary_time = time.time()
    last_rescan_time = time.time()

    while True:
        if loss_sekarang >= MAX_LOSS:
            log("🛑 STOP LOSS GLOBAL — Bot berhenti.")
            print_summary()
            break

        if AUTO_PILIH_TRADER:
            sekarang_rescan = time.time()
            if sekarang_rescan - last_rescan_time >= RESCAN_INTERVAL * 60:
                log("🔄 Waktunya re-scan leaderboard untuk update daftar trader...")
                daftar_trader_aktif = auto_pilih_trader()
                last_rescan_time = sekarang_rescan

        log(f"🔍 Mengecek posisi {len(daftar_trader_aktif)} trader...")
        for wallet in daftar_trader_aktif:
            data = fetch_posisi(wallet)
            if data:
                posisi_per_trader[wallet] = parse_posisi(data)

        if is_first_run:
            log("📸 Snapshot awal selesai, mulai cek consensus mulai sekarang...")
            is_first_run = False
        else:
            sinyal_list = cek_consensus()
            if sinyal_list:
                log(f"🚨 {len(sinyal_list)} consensus signal ditemukan!")
                for sinyal in sinyal_list:
                    eksekusi_consensus(sinyal)
            else:
                log("✅ Tidak ada consensus signal saat ini")

        sekarang = time.time()
        if sekarang - last_summary_time >= SUMMARY_INTERVAL * 60:
            print_summary()
            last_summary_time = sekarang
    
        log(f"💤 Tunggu {CHECK_INTERVAL} detik...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
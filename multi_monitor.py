import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from trader_screener_v2 import hitung_performa_dari_activity
from trader_pnl import hitung_net_pnl_final
from ai_consensus_analyzer import analisis_consensus, print_analisis_consensus
from order_executor import place_market_buy

# ── CONFIG ─────────────────────────────────────────────────────────────────
AUTO_PILIH_TRADER  = True   # True = bot pilih trader sendiri, False = pakai DAFTAR_TRADER manual
JUMLAH_TRADER      = 10     # berapa trader yang mau dimonitor (hasil auto-screening)
LIMIT_LEADERBOARD  = 100    # berapa banyak trader top yang di-scan dari leaderboard
MIN_REDEEM         = 3      # minimal berapa kali redeem buat dianggap "aktif menang"
SCAN_WORKERS        = 8     # jumlah thread paralel buat scan performa (jangan kegedean, rawan rate-limit)
SCAN_RETRY          = 2     # berapa kali retry kalo API kena rate-limit (429) pas scan

# Tahap 2: validasi net PnL beneran (lebih mahal — pagination /activity), makanya
# cuma dijalanin ke SEBAGIAN kandidat teratas dari tahap 1, bukan semua 100.
PNL_CHECK_TOP_N     = 25    # berapa kandidat teratas (by redeem) yang divalidasi net PnL-nya
PNL_SCAN_WORKERS    = 4     # lebih kecil dari SCAN_WORKERS karena tiap call lebih berat (pagination)
MIN_CLOSED_POSISI   = 5     # minimal sample size (closed positions) biar statistiknya gak ecek-ecek
MIN_NET_PNL         = 0     # net PnL minimal buat dianggap layak (0 = harus profit, bukan rugi)
MIN_WIN_RATE_PNL    = 50    # win rate minimal dari closed positions (%)
CACHE_TTL_MENIT      = 15   # performa_cache expired setelah sekian menit, biar data gak basi

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
        hasil = [{"wallet": t.get("proxyWallet"), "nama": t.get("pseudonym") or t.get("name", "Unknown")}
                  for t in data]
        if len(hasil) < limit:
            # API mungkin punya cap sendiri (mis. max 50/100 per request), bukan berarti error
            log(f"⚠️  Diminta {limit} trader, API cuma balikin {len(hasil)}. "
                f"Kemungkinan itu limit maksimum dari endpoint-nya.")
        return hasil
    except Exception as e:
        log(f"❌ Gagal fetch leaderboard: {e}")
        return []


def _scan_satu_trader(t):
    """
    Fetch performa 1 trader dengan retry kalo kena rate-limit/error.
    Dipanggil paralel lewat ThreadPoolExecutor.
    """
    wallet = t["wallet"]
    nama   = t["nama"]

    for percobaan in range(SCAN_RETRY + 1):
        try:
            perf = hitung_performa_dari_activity(wallet)
            return perf, nama
        except Exception as e:
            if percobaan < SCAN_RETRY:
                time.sleep(1.5 * (percobaan + 1))  # backoff sebelum retry
                continue
            log(f"❌ Gagal scan {nama} ({wallet[:10]}...) setelah retry: {e}")
            return None, nama


def _cek_pnl_satu_trader(t):
    """
    Hitung net PnL final (gabungan /positions + /activity REDEEM) buat 1 trader.
    Dipanggil paralel, dengan retry kalo error/rate-limit.
    """
    wallet = t["wallet"]
    nama   = t.get("nama", wallet[:10])

    for percobaan in range(SCAN_RETRY + 1):
        try:
            hasil = hitung_net_pnl_final(wallet)
            return hasil, t
        except Exception as e:
            if percobaan < SCAN_RETRY:
                time.sleep(1.5 * (percobaan + 1))
                continue
            log(f"❌ Gagal cek PnL {nama} ({wallet[:10]}...) setelah retry: {e}")
            return None, t


def auto_pilih_trader():
    """
    Scan leaderboard, screening 2 tahap:
    TAHAP 1 (murah, redeem count) -> narrow down dari LIMIT_LEADERBOARD ke top PNL_CHECK_TOP_N
    TAHAP 2 (mahal, net PnL real dari /positions + /activity) -> validasi & final selection
    """
    log(f"📋 Auto-scan leaderboard ({LIMIT_LEADERBOARD} trader)...")
    top_traders = fetch_top_traders(limit=LIMIT_LEADERBOARD)

    if not top_traders:
        log("❌ Gagal fetch leaderboard, pakai daftar manual sebagai fallback")
        return DAFTAR_TRADER_MANUAL

    log(f"🔎 TAHAP 1: Scanning performa {len(top_traders)} trader paralel ({SCAN_WORKERS} worker)...")

    kandidat = []
    gagal_scan = 0

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        futures = {executor.submit(_scan_satu_trader, t): t for t in top_traders}
        for future in as_completed(futures):
            perf, nama = future.result()
            if not perf:
                gagal_scan += 1
                continue
            if perf["total_redeem"] < MIN_REDEEM:
                continue
            perf["nama"] = nama
            kandidat.append(perf)

    if gagal_scan:
        log(f"⚠️  {gagal_scan} trader gagal di-scan (error/rate-limit), dilewatin.")

    kandidat.sort(key=lambda x: x["nilai_redeem"], reverse=True)
    kandidat_untuk_pnl = kandidat[:PNL_CHECK_TOP_N]

    log(f"✅ TAHAP 1 selesai: {len(kandidat)} lolos MIN_REDEEM, "
        f"validasi net PnL buat top {len(kandidat_untuk_pnl)}...")

    if not kandidat_untuk_pnl:
        log("❌ Gak ada kandidat yang lolos tahap 1. Bot berhenti pilih trader.")
        return DAFTAR_TRADER_MANUAL

    log(f"🔎 TAHAP 2: Validasi net PnL real ({PNL_SCAN_WORKERS} worker, ini lebih lambat)...")

    terpilih_pnl = []
    gagal_pnl = 0

    with ThreadPoolExecutor(max_workers=PNL_SCAN_WORKERS) as executor:
        futures = {executor.submit(_cek_pnl_satu_trader, t): t for t in kandidat_untuk_pnl}
        for future in as_completed(futures):
            hasil_pnl, t = future.result()
            nama = t.get("nama", t["wallet"][:10])

            if not hasil_pnl:
                gagal_pnl += 1
                continue

            if hasil_pnl["total_closed"] < MIN_CLOSED_POSISI:
                log(f"⚠️  {nama} — closed position terlalu sedikit ({hasil_pnl['total_closed']}), skip")
                continue

            if hasil_pnl["net_pnl"] >= MIN_NET_PNL and hasil_pnl["win_rate"] >= MIN_WIN_RATE_PNL:
                hasil_pnl["wallet"] = t["wallet"]
                hasil_pnl["nama"] = nama
                terpilih_pnl.append(hasil_pnl)
                log(f"✅ {nama} — net PnL ${hasil_pnl['net_pnl']:,.0f}, "
                    f"win rate {hasil_pnl['win_rate']:.1f}% ({hasil_pnl['menang']}W/{hasil_pnl['kalah']}L)")
            else:
                log(f"❌ {nama} — net PnL ${hasil_pnl['net_pnl']:,.0f}, "
                    f"win rate {hasil_pnl['win_rate']:.1f}% (gak lolos threshold)")

    if gagal_pnl:
        log(f"⚠️  {gagal_pnl} trader gagal di-cek PnL (error/rate-limit), dilewatin.")

    terpilih_pnl.sort(key=lambda x: x["net_pnl"], reverse=True)
    terpilih = terpilih_pnl[:JUMLAH_TRADER]

    if not terpilih:
        log("⚠️  Gak ada trader yang lolos validasi net PnL. "
            "Fallback ke hasil tahap 1 (redeem-based) biar bot tetep jalan.")
        terpilih_fallback = kandidat_untuk_pnl[:JUMLAH_TRADER]
        for t in terpilih_fallback:
            log(f"   [fallback] {t['nama']} — {t['total_redeem']} redeem, ${t['nilai_redeem']:,.0f}")
        return [t["wallet"] for t in terpilih_fallback]

    log(f"✅ {len(terpilih)} trader FINAL terpilih (net PnL validated):")
    for t in terpilih:
        log(f"   {t['nama']} — net PnL ${t['net_pnl']:,.0f}, win rate {t['win_rate']:.1f}%, "
            f"{t['total_closed']} closed positions")

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
    """
    Ambil performa trader dari cache kalo masih fresh (< CACHE_TTL_MENIT),
    kalo expired atau belum ada, fetch ulang dari API.
    """
    hasil = []
    sekarang = time.time()

    for w in wallets:
        cached = performa_cache.get(w)
        masih_fresh = cached and (sekarang - cached["waktu"]) < CACHE_TTL_MENIT * 60

        if not masih_fresh:
            perf = hitung_performa_dari_activity(w)
            if perf:
                performa_cache[w] = {"data": perf, "waktu": sekarang}
            elif cached:
                # fetch gagal tapi ada data lama -> mending pakai yang lama daripada kosong
                log(f"⚠️  Gagal refresh performa {w[:10]}..., pakai cache lama")
            else:
                continue

        if w in performa_cache:
            hasil.append(performa_cache[w]["data"])

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
            # dry_run=SIMULASI_MODE (False di sini) -- tapi order_executor PUNYA gate-nya
            # sendiri juga (LIVE_TRADING_ENABLED di order_executor.py). Order beneran
            # cuma jalan kalo DUA-DUANYA sepakat: SIMULASI_MODE=False DI SINI, dan
            # LIVE_TRADING_ENABLED=True DI order_executor.py. Sengaja double-gate.
            hasil_order = place_market_buy(
                condition_id=sinyal["market_id"],
                outcome_text=sinyal["outcome"],
                usd_amount=bet_amount,
                dry_run=SIMULASI_MODE,
            )
            status = hasil_order.get("status", "unknown")
            if status == "success":
                log(f"   ✅ Order LIVE berhasil dikirim.")
            elif status == "dry_run":
                log(f"   🟡 Order gak beneran dikirim (LIVE_TRADING_ENABLED masih False "
                    f"di order_executor.py). Nyalain manual kalo emang siap live.")
            else:
                log(f"   ❌ Order gagal: {hasil_order.get('alasan', 'unknown error')}")
                loss_sekarang -= bet_amount  # rollback, order gak beneran kejadian
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
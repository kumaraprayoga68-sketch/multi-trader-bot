import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from trader_screener_v2 import hitung_performa_dari_activity
from trader_pnl import hitung_net_pnl_final
from ai_consensus_analyzer import analisis_consensus, print_analisis_consensus, analisis_single_trader, print_analisis_single_trader
from order_executor import place_market_buy
from report import catat_ke_report
from market_status import get_market_info, cari_token_id, get_midpoint_price, cek_status_resolusi

# ── CONFIG ─────────────────────────────────────────────────────────────────
AUTO_PILIH_TRADER  = True   # True = bot pilih trader sendiri, False = pakai DAFTAR_TRADER manual
JUMLAH_TRADER      = 10     # berapa trader yang mau dimonitor (hasil auto-screening)
LIMIT_LEADERBOARD  = 30     # berapa banyak trader top yang di-scan dari leaderboard
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
LEADERBOARD_WINDOW  = "30d"  # ✅ udah divalidasi ke API: value yang valid cuma "all"/"ALL"/"1d"/"7d"/"30d"
                              # (BUKAN "day"/"week"/"month" -- itu 400 semua). "30d" ~ 1 bulan.
CACHE_TTL_MENIT      = 15   # performa_cache expired setelah sekian menit, biar data gak basi

DAFTAR_TRADER_MANUAL = [
    # isi manual di sini kalau AUTO_PILIH_TRADER = False
]

MIN_CONSENSUS      = 2
# Market tipe ini punya variance tinggi (susah ditebak walau tau siapa yang lebih kuat) --
# hasil evaluasi awal (0/5 win) kebetulan semua market spread. Di-exclude SEBELUM sampe
# ke AI (hemat API call juga), bukan diserahin ke AI buat mutusin.
KEYWORD_HIGH_VARIANCE = [
    "spread:", "spread (", "o/u ", "over/under", "exact score", "handicap",
]
MAX_HARI_KE_RESOLVE = 90  # skip market yang resolve-nya lebih dari 90 hari lagi (susah diprediksi jauh2)
MIN_AI_CONFIDENCE  = 6
SINGLE_TRADER_MODE  = False  # True = copy 1 trader terbaik aja (skip logic consensus antar-wallet),
                              # trigger begitu dia buka posisi BARU. Bet size proporsional ke confidence AI.
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
posisi_sebelumnya  = {}  # snapshot cycle sebelumnya, buat deteksi posisi baru di SINGLE_TRADER_MODE
performa_cache     = {}
performa_pnl_cache = {}  # cache terpisah buat data net PnL (hitung_net_pnl_final) -- lebih mahal, TTL sama
is_first_run       = True
riwayat_consensus  = []
last_summary_time  = 0
last_rescan_time   = 0
daftar_trader_aktif = []
sinyal_sudah_dievaluasi = set()  # {(market_id, outcome), ...} -- biar consensus signal yang
                                  # SAMA gak ditanya ulang ke AI tiap cycle selama posisinya masih sama
jumlah_sinyal_difilter = 0  # counter sinyal yang ke-skip SEBELUM sampe AI (resolve/kejauhan/high-variance)
performa_pnl_trader_terpilih = {}  # {wallet: hasil_pnl_dict} -- disi pas SINGLE_TRADER_MODE, dari tahap 2 auto_pilih_trader()


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


# ── AUTO SCREENING ─────────────────────────────────────────────────────────
def fetch_top_traders(limit=30):
    url = "https://lb-api.polymarket.com/profit"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    params = {"window": LEADERBOARD_WINDOW, "limit": limit}

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
    log(f"📋 Auto-scan leaderboard ({LIMIT_LEADERBOARD} trader, window={LEADERBOARD_WINDOW})...")
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

    if SINGLE_TRADER_MODE:
        # buat single-trader, konsistensi (win_rate) lebih penting dari total $ (net_pnl bisa
        # didominasi 1-2 win jumbo doang) -- net_pnl jadi tiebreaker aja
        terpilih_pnl.sort(key=lambda x: (x["win_rate"], x["net_pnl"]), reverse=True)
    else:
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
        performa_pnl_trader_terpilih[t["wallet"]] = t  # simpen buat dipake AI single-trader nanti

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


def capture_harga_entry(condition_id, outcome_text):
    """
    Ambil harga midpoint saat ini (pas keputusan IKUT diambil), buat disimpen ke
    report -- biar nanti win/loss checker bisa itung PnL beneran (bukan cuma
    binary menang/kalah doang). Return None kalo gagal (gak nge-block eksekusi).
    """
    try:
        market_info = get_market_info(condition_id)
        token = cari_token_id(market_info, outcome_text)
        if not token:
            return None
        return get_midpoint_price(token.get("token_id"))
    except Exception:
        return None


def is_market_terlalu_jauh(condition_id):
    """
    True kalo market ini resolve-nya lebih dari MAX_HARI_KE_RESOLVE hari lagi.
    Market jangka panjang (kayak election 2+ tahun lagi) susah diprediksi & bikin
    feedback loop evaluasi jadi lambat banget. Fail-safe: gagal cek -> False
    (tetep dievaluasi, jangan ke-block gara2 error network).
    """
    try:
        market_info = get_market_info(condition_id)
        if not market_info:
            return False
        end_date_str = market_info.get("end_date_iso", "")
        if not end_date_str:
            return False
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        sisa_hari = (end_date - datetime.now(end_date.tzinfo)).days
        return sisa_hari > MAX_HARI_KE_RESOLVE
    except Exception:
        return False


def is_market_sudah_resolve(condition_id, outcome_text):
    """
    True kalo market ini udah resolve (hasilnya UDAH KETAUAN). Nanya AI soal
    market yang udah resolve itu percuma -- gak ada gunanya "worth diikuti gak"
    buat sesuatu yang udah kejadian. Juga jaga-jaga biar gak ada percobaan order
    ke market yang udah mati.
    """
    try:
        status = cek_status_resolusi(condition_id, outcome_text)
        return status.get("resolved", False)
    except Exception:
        return False  # gagal cek -> anggap belum resolve, biar tetep dievaluasi (fail-safe)


def is_market_high_variance(title):
    """
    True kalo market ini tipe high-variance (spread, exact score, O/U, dll) --
    susah ditebak arahnya walau tau siapa yang lebih kuat. Di-skip SEBELUM
    sampe ke AI, bukan diserahin ke AI buat mutusin.
    """
    title_lower = title.lower()
    return any(kw in title_lower for kw in KEYWORD_HIGH_VARIANCE)


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


def get_performa_pnl_wallets(wallets):
    """
    Versi net PnL (data yang udah divalidasi -- gabungan /positions + /activity REDEEM,
    lebih akurat dari redeem-count doang). Dipake buat AI consensus analysis yang
    lebih kaya konteks. Cache terpisah dari get_performa_wallets(), TTL sama.
    """
    hasil = []
    sekarang = time.time()

    for w in wallets:
        # kalo udah pernah dihitung pas auto_pilih_trader() (tahap 2), pake itu dulu
        # sebelum cache lokal -- data itu udah paling akurat & gak perlu refetch
        if w in performa_pnl_trader_terpilih:
            hasil.append(performa_pnl_trader_terpilih[w])
            continue

        cached = performa_pnl_cache.get(w)
        masih_fresh = cached and (sekarang - cached["waktu"]) < CACHE_TTL_MENIT * 60

        if not masih_fresh:
            perf = hitung_net_pnl_final(w)
            if perf:
                performa_pnl_cache[w] = {"data": perf, "waktu": sekarang}
            elif cached:
                log(f"⚠️  Gagal refresh net PnL {w[:10]}..., pakai cache lama")
            else:
                continue

        if w in performa_pnl_cache:
            hasil.append(performa_pnl_cache[w]["data"])

    return hasil


def cek_posisi_baru(wallet):
    """
    Buat SINGLE_TRADER_MODE. Bandingin snapshot posisi wallet sekarang vs
    snapshot cycle sebelumnya -- posisi yang ADA sekarang tapi GAK ADA
    sebelumnya berarti trader ini baru aja entry.
    """
    lama = posisi_sebelumnya.get(wallet, {})
    baru = posisi_per_trader.get(wallet, {})

    sinyal_list = []
    for market_id, posisi in baru.items():
        if market_id not in lama:
            sinyal_list.append({
                "market_id": market_id,
                "title": posisi["title"],
                "outcome": posisi["outcome"],
                "wallet": wallet,
            })
    return sinyal_list


def eksekusi_single_trader(sinyal):
    """
    Versi SINGLE_TRADER_MODE dari eksekusi_consensus() -- 1 trader, bukan consensus
    antar-wallet. Bet size PROPORSIONAL ke confidence AI (beda dari eksekusi_consensus
    yang flat), sesuai request: confidence 10/10 = full MAX_PER_TRADE, 6/10 = 60%, dst.
    """
    global loss_sekarang

    log(f"🚨 Posisi baru dari trader yang di-copy ({sinyal['wallet'][:10]}...)")
    log(f"   Market: {sinyal['title'][:50]} → '{sinyal['outcome']}'")

    # pake data net PnL yang udah divalidasi tahap 2 (bukan redeem-count doang),
    # kalo gak ada (misal MANUAL mode / belum sempet di-scan), fallback ke None
    performa_pnl = performa_pnl_trader_terpilih.get(sinyal["wallet"])

    log("🤖 Minta analisis AI (single-trader, bukan consensus)...")
    hasil_ai = analisis_single_trader(sinyal, performa_pnl)
    print_analisis_single_trader(sinyal, hasil_ai)

    keputusan  = hasil_ai.get("keputusan", "SKIP")
    confidence = hasil_ai.get("confidence", 0)

    riwayat_consensus.append({
        "waktu": datetime.now().strftime("%H:%M:%S"),
        "market": sinyal["title"],
        "outcome": sinyal["outcome"],
        "jumlah_trader": 1,
        "keputusan": keputusan,
        "confidence": confidence,
    })

    if keputusan == "IKUT" and confidence >= MIN_AI_CONFIDENCE:
        # ── bet size PROPORSIONAL ke confidence AI (bukan flat kayak eksekusi_consensus) ──
        bet_amount = round(MAX_PER_TRADE * (confidence / 10), 2)
        boleh, alasan = cek_boleh_bet(bet_amount)

        if not boleh:
            log(f"⛔ {alasan}")
            catat_ke_report({
                "mode": "SINGLE_TRADER", "market": sinyal["title"], "market_id": sinyal["market_id"],
                "outcome": sinyal["outcome"], "wallet": sinyal["wallet"], "jumlah_trader": 1,
                "keputusan": "IKUT", "confidence": confidence, "bet_amount": 0,
                "harga_entry": "", "status": f"blocked: {alasan}",
                "alasan_ai": hasil_ai.get("alasan", ""), "budget_terpakai": loss_sekarang,
            })
            return

        loss_sekarang += bet_amount
        log(f"✅ BET ${bet_amount} (confidence {confidence}/10 → {confidence*10}% dari max "
            f"${MAX_PER_TRADE}) pada '{sinyal['outcome']}'")
        log(f"   Budget terpakai: ${loss_sekarang:.2f} / ${MAX_LOSS}")

        if SIMULASI_MODE:
            log(f"   [PAPER] Order dicatat ✏️")
            status_final = "paper"
        else:
            hasil_order = place_market_buy(
                condition_id=sinyal["market_id"],
                outcome_text=sinyal["outcome"],
                usd_amount=bet_amount,
                dry_run=SIMULASI_MODE,
            )
            status = hasil_order.get("status", "unknown")
            if status == "success":
                log(f"   ✅ Order LIVE berhasil dikirim.")
                status_final = "live_success"
            elif status == "dry_run":
                log(f"   🟡 Order gak beneran dikirim (LIVE_TRADING_ENABLED masih False "
                    f"di order_executor.py).")
                status_final = "dry_run"
            else:
                log(f"   ❌ Order gagal: {hasil_order.get('alasan', 'unknown error')}")
                loss_sekarang -= bet_amount
                status_final = f"gagal: {hasil_order.get('alasan', 'unknown error')}"

        harga_entry = capture_harga_entry(sinyal["market_id"], sinyal["outcome"])

        catat_ke_report({
            "mode": "SINGLE_TRADER", "market": sinyal["title"], "market_id": sinyal["market_id"],
            "outcome": sinyal["outcome"], "wallet": sinyal["wallet"], "jumlah_trader": 1,
            "keputusan": "IKUT", "confidence": confidence, "bet_amount": bet_amount,
            "harga_entry": harga_entry if harga_entry is not None else "",
            "status": status_final, "alasan_ai": hasil_ai.get("alasan", ""),
            "budget_terpakai": loss_sekarang,
        })
    else:
        log(f"❌ SKIP — AI confidence {confidence}/10 (butuh ≥{MIN_AI_CONFIDENCE})")
        catat_ke_report({
            "mode": "SINGLE_TRADER", "market": sinyal["title"], "market_id": sinyal["market_id"],
            "outcome": sinyal["outcome"], "wallet": sinyal["wallet"], "jumlah_trader": 1,
            "keputusan": keputusan, "confidence": confidence, "bet_amount": 0,
            "harga_entry": "", "status": "skip", "alasan_ai": hasil_ai.get("alasan", ""),
            "budget_terpakai": loss_sekarang,
        })


def eksekusi_consensus(sinyal):
    global loss_sekarang

    log(f"🚨 Consensus terdeteksi: {sinyal['jumlah_trader']} trader sepakat")
    log(f"   Market: {sinyal['title'][:50]}")

    performa_list = get_performa_pnl_wallets(sinyal["wallets"])

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
            catat_ke_report({
                "mode": "CONSENSUS", "market": sinyal["title"], "market_id": sinyal["market_id"],
                "outcome": sinyal["outcome"], "wallet": ", ".join(sinyal.get("wallets", [])),
                "jumlah_trader": sinyal["jumlah_trader"], "keputusan": "IKUT", "confidence": confidence,
                "bet_amount": 0, "harga_entry": "", "status": f"blocked: {alasan}",
                "alasan_ai": hasil_ai.get("alasan", ""), "budget_terpakai": loss_sekarang,
            })
            return

        loss_sekarang += bet_amount
        log(f"✅ BET ${bet_amount} pada '{sinyal['outcome']}'")
        log(f"   Budget terpakai: ${loss_sekarang:.2f} / ${MAX_LOSS}")

        if SIMULASI_MODE:
            log(f"   [PAPER] Order dicatat ✏️")
            status_final = "paper"
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
                status_final = "live_success"
            elif status == "dry_run":
                log(f"   🟡 Order gak beneran dikirim (LIVE_TRADING_ENABLED masih False "
                    f"di order_executor.py). Nyalain manual kalo emang siap live.")
                status_final = "dry_run"
            else:
                log(f"   ❌ Order gagal: {hasil_order.get('alasan', 'unknown error')}")
                loss_sekarang -= bet_amount  # rollback, order gak beneran kejadian
                status_final = f"gagal: {hasil_order.get('alasan', 'unknown error')}"

        harga_entry = capture_harga_entry(sinyal["market_id"], sinyal["outcome"])

        catat_ke_report({
            "mode": "CONSENSUS", "market": sinyal["title"], "market_id": sinyal["market_id"],
            "outcome": sinyal["outcome"], "wallet": ", ".join(sinyal.get("wallets", [])),
            "jumlah_trader": sinyal["jumlah_trader"], "keputusan": "IKUT", "confidence": confidence,
            "bet_amount": bet_amount, "harga_entry": harga_entry if harga_entry is not None else "",
            "status": status_final, "alasan_ai": hasil_ai.get("alasan", ""),
            "budget_terpakai": loss_sekarang,
        })
    else:
        log(f"❌ SKIP — AI confidence {confidence}/10 (butuh ≥{MIN_AI_CONFIDENCE})")
        catat_ke_report({
            "mode": "CONSENSUS", "market": sinyal["title"], "market_id": sinyal["market_id"],
            "outcome": sinyal["outcome"], "wallet": ", ".join(sinyal.get("wallets", [])),
            "jumlah_trader": sinyal["jumlah_trader"], "keputusan": keputusan, "confidence": confidence,
            "bet_amount": 0, "harga_entry": "", "status": "skip",
            "alasan_ai": hasil_ai.get("alasan", ""), "budget_terpakai": loss_sekarang,
        })


def print_summary():
    sisa = BUDGET_SAYA - loss_sekarang
    n_ikut = sum(1 for c in riwayat_consensus if c["keputusan"] == "IKUT")
    n_skip = sum(1 for c in riwayat_consensus if c["keputusan"] == "SKIP")

    print(f"\n{'='*60}")
    print(f"📊 AUTO MULTI-TRADER SUMMARY — {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    print(f"Trader dimonitor : {len(daftar_trader_aktif)} (auto-selected)")
    if SINGLE_TRADER_MODE:
        print(f"Mode sinyal      : SINGLE-TRADER (bukan consensus, MIN_CONSENSUS gak relevan)")
    else:
        print(f"Min consensus    : {MIN_CONSENSUS} trader")
    print(f"Sisa budget      : ${sisa:.2f} / ${BUDGET_SAYA}")
    print(f"Sinyal difilter  : {jumlah_sinyal_difilter} (resolve/kejauhan/high-variance, gak sampe AI)")
    print(f"Total sinyal     : {len(riwayat_consensus)} (yang sampe ke AI)")
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
    global posisi_per_trader, posisi_sebelumnya, is_first_run, last_summary_time
    global jumlah_sinyal_difilter
    global last_rescan_time, daftar_trader_aktif

    print(f"\n{'='*60}")
    print(f"  🤖 AUTO MULTI-TRADER + AI CONSENSUS BOT")
    print(f"{'='*60}")
    log(f"Mode trader       : {'AUTO (dari leaderboard)' if AUTO_PILIH_TRADER else 'MANUAL'}")
    if SINGLE_TRADER_MODE:
        log(f"Mode sinyal       : SINGLE-TRADER (copy 1 trader terbaik, bukan consensus)")
    else:
        log(f"Min consensus     : {MIN_CONSENSUS} trader harus searah")
    log(f"Min AI confidence : {MIN_AI_CONFIDENCE}/10")
    log(f"Modal: ${BUDGET_SAYA} | Max/trade: ${MAX_PER_TRADE} | Stop loss: ${MAX_LOSS}")
    log(f"Mode: {'SIMULASI 🟡' if SIMULASI_MODE else 'LIVE 🔴'}")
    print(f"{'='*60}\n")

    if AUTO_PILIH_TRADER:
        daftar_trader_aktif = auto_pilih_trader()
    else:
        daftar_trader_aktif = DAFTAR_TRADER_MANUAL

    if SINGLE_TRADER_MODE and len(daftar_trader_aktif) > 1:
        log(f"ℹ️  SINGLE_TRADER_MODE aktif — ambil cuma trader teratas dari "
            f"{len(daftar_trader_aktif)} kandidat.")
        daftar_trader_aktif = daftar_trader_aktif[:1]

    if not daftar_trader_aktif:
        log("❌ Tidak ada trader yang bisa dimonitor. Bot berhenti.")
        return

    if SINGLE_TRADER_MODE:
        log(f"👤 Trader yang di-copy: {daftar_trader_aktif[0]}")

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
                if SINGLE_TRADER_MODE and len(daftar_trader_aktif) > 1:
                    daftar_trader_aktif = daftar_trader_aktif[:1]
                last_rescan_time = sekarang_rescan

        log(f"🔍 Mengecek posisi {len(daftar_trader_aktif)} trader...")
        for wallet in daftar_trader_aktif:
            data = fetch_posisi(wallet)
            if data:
                if wallet in posisi_per_trader:
                    posisi_sebelumnya[wallet] = posisi_per_trader[wallet]
                posisi_per_trader[wallet] = parse_posisi(data)

        if is_first_run:
            log("📸 Snapshot awal selesai, mulai cek sinyal mulai sekarang...")
            is_first_run = False
        elif SINGLE_TRADER_MODE:
            wallet = daftar_trader_aktif[0]
            sinyal_list = cek_posisi_baru(wallet)
            if sinyal_list:
                log(f"🚨 {len(sinyal_list)} posisi baru dari trader yang di-copy!")
                for sinyal in sinyal_list:
                    if is_market_high_variance(sinyal["title"]):
                        log(f"⏭️  SKIP otomatis (high-variance market): {sinyal['title'][:50]}")
                        jumlah_sinyal_difilter += 1
                        continue
                    if is_market_sudah_resolve(sinyal["market_id"], sinyal["outcome"]):
                        log(f"⏭️  SKIP otomatis (market udah resolve, gak relevan lagi): "
                            f"{sinyal['title'][:50]}")
                        jumlah_sinyal_difilter += 1
                        continue
                    if is_market_terlalu_jauh(sinyal["market_id"]):
                        log(f"⏭️  SKIP otomatis (resolve >{MAX_HARI_KE_RESOLVE} hari lagi): "
                            f"{sinyal['title'][:50]}")
                        jumlah_sinyal_difilter += 1
                        continue
                    eksekusi_single_trader(sinyal)
            else:
                log("✅ Gak ada posisi baru dari trader ini saat ini")
        else:
            sinyal_list = cek_consensus()
            if sinyal_list:
                log(f"🚨 {len(sinyal_list)} consensus signal ditemukan!")
                for sinyal in sinyal_list:
                    kunci = (sinyal["market_id"], sinyal["outcome"])
                    if kunci in sinyal_sudah_dievaluasi:
                        continue  # udah pernah dievaluasi, gak usah tanya AI lagi
                    if is_market_high_variance(sinyal["title"]):
                        log(f"⏭️  SKIP otomatis (high-variance market): {sinyal['title'][:50]}")
                        sinyal_sudah_dievaluasi.add(kunci)
                        jumlah_sinyal_difilter += 1
                        continue
                    if is_market_sudah_resolve(sinyal["market_id"], sinyal["outcome"]):
                        log(f"⏭️  SKIP otomatis (market udah resolve, gak relevan lagi): "
                            f"{sinyal['title'][:50]}")
                        sinyal_sudah_dievaluasi.add(kunci)
                        jumlah_sinyal_difilter += 1
                        continue
                    if is_market_terlalu_jauh(sinyal["market_id"]):
                        log(f"⏭️  SKIP otomatis (resolve >{MAX_HARI_KE_RESOLVE} hari lagi): "
                            f"{sinyal['title'][:50]}")
                        sinyal_sudah_dievaluasi.add(kunci)
                        jumlah_sinyal_difilter += 1
                        continue
                    eksekusi_consensus(sinyal)
                    sinyal_sudah_dievaluasi.add(kunci)
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
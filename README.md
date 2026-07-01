# Multi-Trader Consensus Bot (Polymarket)

Bot yang scan leaderboard Polymarket, screening trader berdasarkan performa **net PnL real** (bukan cuma redeem count), dan **keputusan trading deterministik** (formula matematis, bukan AI yang mutusin) sebelum ikut posisi mereka. Default-nya **paper trading** (simulasi), bukan trading beneran.

⚠️ **Ini eksperimen pribadi, bukan financial advice. Trading prediction market ada resiko rugi total.**

## Cara Kerja (Pipeline)

```
1. Scan leaderboard Polymarket (window 30 hari)
2. TAHAP 1: Filter cepat berdasarkan jumlah redeem (klaim kemenangan)
3. TAHAP 2: Validasi net PnL REAL dari histori closed positions
            (gabungan data /positions + /activity REDEEM -- ada bias kalau
            cuma pake salah satu, lihat komentar di trader_pnl.py)
4. Monitor posisi trader yang lolos, tiap ada sinyal (consensus 2+ trader ATAU
   1 trader di SINGLE_TRADER_MODE), filter dulu SEBELUM dievaluasi:
   - Skip kalau market high-variance (spread, exact score, O/U)
   - Skip kalau market udah resolve (gak relevan lagi)
   - Skip kalau resolve-nya >90 hari lagi (terlalu jangka panjang)
   - Skip kalau sinyal ini udah pernah dievaluasi (dedup, hemat API call)
5. Sinyal yang lolos filter -> FORMULA (scoring.py) mutusin IKUT/SKIP.
   Deterministik: net PnL <=0 -> otomatis 0, win rate & sample size dihitung
   matematis. AI (Groq) CUMA nulis narasi "alasan" -- gak bisa ngubah keputusan.
6. Kalau IKUT -> Kelly Criterion (kelly.py) itung ukuran bet berdasarkan EDGE
   (win rate vs harga market SAAT INI). Kalau harga udah kemahalan / gak ada
   edge, Kelly bisa override jadi SKIP walau skor formula tinggi.
7. Dynamic sizing (streak menang/kalah dari evaluasi_hasil.csv) nge-adjust
   ukuran bet lebih jauh.
8. Kalau IKUT & lolos semua tahap -> paper trade (atau live kalau
   LIVE_TRADING_ENABLED=True). Semua keputusan tercatat ke riwayat_trading.csv.
9. cek_hasil.py -> evaluasi menang/kalah beneran setelah market resolve.
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# isi .env: GROQ_API_KEY, PRIVATE_KEY, FUNDER_ADDRESS
```

`PRIVATE_KEY` & `FUNDER_ADDRESS` cuma dibutuhin kalau mau live trading. Untuk paper trading (default), boleh dikosongin.

## Menjalankan Bot

```bash
python multi_monitor.py
```

Berhenti kapan aja pake `Ctrl+C` -- aman, gak ada order yang "nyangkut" karena masih simulasi.

## Cek Hasil

```bash
python cek_hasil.py
```

Ngecek semua sinyal yang udah di-log, apakah market-nya udah resolve, dan kalau iya menang atau kalah beneran (bukan cuma "budget terpakai" yang keliatan kayak kerugian tapi sebenarnya belum tentu). Hasil detail disimpan ke `evaluasi_hasil.csv` -- file ini juga dipakai `multi_monitor.py` buat dynamic sizing (streak menang/kalah).

## Konfigurasi Penting (di `multi_monitor.py`)

| Variabel | Fungsi |
|---|---|
| `SIMULASI_MODE` | `True` = paper trading (default, aman). `False` = coba kirim order beneran |
| `SINGLE_TRADER_MODE` | `True` = copy 1 trader terbaik (win rate diprioritaskan). `False` = consensus mode (butuh 2+ trader sepakat) |
| `AUTO_PILIH_TRADER` | `True` = scan leaderboard otomatis. `False` = pakai `DAFTAR_TRADER_MANUAL` |
| `MODE_KEPUTUSAN` | `"HYBRID"` = formula mutusin + AI cuma narasi (default, deterministik). `"AI"` = balik ke AI yang mutusin |
| `BUDGET_SAYA` / `MAX_PER_TRADE` / `MAX_LOSS` | Modal simulasi, size max per bet, cap modal terkomit |
| `PAUSE_THRESHOLD_PCT` / `PAUSE_DURASI_MENIT` | Soft pause bertingkat -- istirahat sementara sebelum stop total |
| `DYNAMIC_SIZING_ENABLED` | Adjust bet size berdasarkan streak menang/kalah terakhir |
| `MIN_WIN_RATE_PNL` / `MIN_NET_PNL` | Threshold seleksi trader di tahap 2 |
| `MAX_HARI_KE_RESOLVE` | Skip market yang resolve-nya lebih dari sekian hari lagi |
| `NEWS_CONTEXT_ENABLED` | AI dapet konteks berita (scraping, best-effort) buat narasi alasan |
| `TEST_MODE_SKIP_FILTER` | Testing doang. Matiin filter pre-evaluasi biar sinyal lolos ke formula. Balikin ke `False` setelah selesai testing |

## Sistem Keputusan: Formula, Bukan AI

Ini prinsip inti yang penting dipahami: **AI (Groq) TIDAK mutusin IKUT/SKIP**. Keputusan 100% dari formula matematis di `scoring.py` -- deterministik, bisa di-backtest, gak ada "mode collapse" atau variasi random yang biasa terjadi kalau LLM dipakai buat keputusan berulang. AI cuma dipanggil buat nulis narasi penjelasan (`buat_alasan()`), dan boleh dikasih konteks berita tambahan, tapi gak bisa mengubah angka atau keputusan yang udah final.

## Live Trading

Ada double-gate yang sengaja dibikin ribet biar gak ke-trigger gak sengaja:

1. `SIMULASI_MODE = False` di `multi_monitor.py`
2. `LIVE_TRADING_ENABLED = True` di `order_executor.py`

Dua-duanya harus di-set bener sebelum order beneran kekirim. Plus ada hard cap `MAX_ORDER_SIZE_ABSOLUTE` di `order_executor.py` yang independen dari config lain.

Test koneksi dulu tanpa kirim order apapun:
```bash
python order_executor.py
```

## Struktur File

```
multi_monitor.py         -- orchestrator utama, loop bot
scoring.py                -- formula deterministik buat keputusan IKUT/SKIP
kelly.py                  -- Kelly Criterion buat position sizing (edge-based)
news_context.py            -- scraping berita ringan buat konteks narasi (opsional)
trader_pnl.py              -- net PnL calculator (gabungan /positions + /activity)
trader_screener_v2.py      -- screening cepat tahap 1 (redeem count)
ai_consensus_analyzer.py   -- narasi "alasan" dari Groq (bukan pengambil keputusan)
market_status.py           -- cek harga & status resolusi market (endpoint publik)
order_executor.py          -- eksekusi order live (py-clob-client)
report.py                  -- logging tiap keputusan ke CSV
cek_hasil.py                -- evaluasi win/loss setelah market resolve
test_single_trader.py       -- test manual alur tanpa nunggu sinyal asli
```

## Known Limitations

- Net PnL dihitung dari data yang kebaca API saat itu -- kalau trader punya histori sangat panjang, sebagian data lama mungkin gak ke-capture (pagination `/activity` ada batasnya).
- `MAX_LOSS` itu total modal terkomit ke bet, BUKAN kerugian realized -- belum ada mekanisme auto-release modal begitu posisi resolve menang. Soft pause (`PAUSE_THRESHOLD_PCT`) sedikit mengurangi dampaknya tapi belum solusi penuh.
- Kelly Criterion di sini pakai win rate historis sebagai proxy probabilitas -- itu simplifikasi, bukan model probabilitas independen yang sebenarnya.
- `news_context.py` scraping DuckDuckGo HTML tanpa API resmi -- rawan break kalau struktur halaman mereka berubah. Fail-safe (gak nge-block pipeline), tapi belum tentu selalu dapet hasil.
- Dynamic sizing & backtest baru bisa jalan efektif setelah ada cukup data resolved dari `cek_hasil.py` -- di awal pemakaian, hampir semua fitur berbasis histori masih netral/kosong.
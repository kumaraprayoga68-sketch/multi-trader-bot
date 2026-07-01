# Multi-Trader Consensus Bot (Polymarket)

Bot yang scan leaderboard Polymarket, screening trader berdasarkan performa **net PnL real** (bukan cuma redeem count), dan otomatis analisis pake AI (Groq/Llama) sebelum ikut posisi mereka. Default-nya **paper trading** (simulasi), bukan trading beneran.

⚠️ **Ini eksperimen pribadi, bukan financial advice. Trading prediction market ada resiko rugi total.**

## Cara Kerja (Pipeline)

```
1. Scan leaderboard Polymarket (window 30 hari)
2. TAHAP 1: Filter cepat berdasarkan jumlah redeem (klaim kemenangan)
3. TAHAP 2: Validasi net PnL REAL dari histori closed positions
            (gabungan data /positions + /activity REDEEM -- lihat trader_pnl.py
            buat penjelasan kenapa perlu digabung, ada bias kalau cuma pake salah satu)
4. Monitor posisi trader yang lolos, tiap ada sinyal (consensus 2+ trader ATAU
   1 trader di SINGLE_TRADER_MODE), filter dulu:
   - Skip kalau market high-variance (spread, exact score, O/U)
   - Skip kalau market udah resolve (gak relevan lagi)
   - Skip kalau resolve-nya >90 hari lagi (terlalu jangka panjang)
5. Sinyal yang lolos filter -> AI (Groq) analisis: IKUT atau SKIP + confidence 1-10
6. Kalau IKUT & confidence cukup -> paper trade (atau live kalau LIVE_TRADING_ENABLED=True)
7. Semua keputusan tercatat ke riwayat_trading.csv
8. cek_hasil.py -> evaluasi menang/kalah beneran setelah market resolve
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

Ngecek semua sinyal yang udah di-log, apakah market-nya udah resolve, dan kalau iya menang atau kalah beneran. Hasil detail disimpan ke `evaluasi_hasil.csv`.

## Konfigurasi Penting (di `multi_monitor.py`)

| Variabel | Fungsi |
|---|---|
| `SIMULASI_MODE` | `True` = paper trading (default, aman). `False` = coba kirim order beneran |
| `SINGLE_TRADER_MODE` | `True` = copy 1 trader terbaik. `False` = consensus mode (butuh 2+ trader sepakat) |
| `AUTO_PILIH_TRADER` | `True` = scan leaderboard otomatis. `False` = pakai `DAFTAR_TRADER_MANUAL` |
| `BUDGET_SAYA` / `MAX_PER_TRADE` / `MAX_LOSS` | Modal simulasi, size per bet, cap modal terkomit sebelum bot stop |
| `MIN_WIN_RATE_PNL` / `MIN_NET_PNL` | Threshold seleksi trader di tahap 2 |

## ⚠️ Live Trading

Ada **double-gate** yang sengaja dibikin ribet biar gak ke-trigger gak sengaja:

1. `SIMULASI_MODE = False` di `multi_monitor.py`
2. `LIVE_TRADING_ENABLED = True` di `order_executor.py`

**Dua-duanya** harus di-set bener sebelum order beneran kekirim. Plus ada hard cap `MAX_ORDER_SIZE_ABSOLUTE` di `order_executor.py` yang independen dari config lain.

Test koneksi dulu tanpa kirim order apapun:
```bash
python order_executor.py
```

## Struktur File

```
multi_monitor.py         -- orchestrator utama, loop bot
trader_pnl.py             -- net PnL calculator (gabungan /positions + /activity)
trader_screener_v2.py     -- screening cepat tahap 1 (redeem count)
ai_consensus_analyzer.py  -- prompt & call ke Groq API
market_status.py          -- cek harga & status resolusi market (endpoint publik)
order_executor.py         -- eksekusi order live (py-clob-client)
report.py                 -- logging tiap keputusan ke CSV
cek_hasil.py               -- evaluasi win/loss setelah market resolve
test_single_trader.py      -- test manual alur AI tanpa nunggu sinyal asli
```

## Known Limitations

- Net PnL dihitung dari data yang kebaca API saat itu -- kalau trader punya histori sangat panjang, sebagian data lama mungkin gak ke-capture (pagination `/activity` ada batasnya).
- Belum ada tracking budget yang "muter" -- `loss_sekarang` di `MAX_LOSS` itu total modal terkomit, bukan kerugian realized (belum ada mekanisme auto-release modal begitu posisi resolve).
- AI confidence itu estimasi kualitatif dari LLM, bukan model statistik -- treat sebagai salah satu input, bukan kebenaran mutlak.
"""
Scoring deterministik -- gantiin AI buat KEPUTUSAN (IKUT/SKIP), AI cuma dipake
buat narasi "alasan" doang (lihat ai_consensus_analyzer.py: buat_alasan()).

Kenapa? LLM inherently gak konsisten buat tugas penilaian berulang -- input yang
sama bisa dapet skor beda di run yang beda (udah kebukti dari histori chat ini:
awalnya AI selalu 7-8/10, abis dikalibrasi selalu 4/10 -- dua-duanya nunjukkin
LLM gampang "nyumbat" di 1 angka, bukan beneran evaluasi tiap kasus).

Formula matematis: deterministik (input sama = output sama SELALU), bisa
di-backtest, gampang di-tuning berdasarkan evaluasi_hasil.csv, gak ada API
cost/latency/rate-limit buat KEPUTUSANNYA (AI cuma dipake buat narasi, gagal
pun gak nge-block keputusan).
"""

# ── TUNING PARAMETER ─────────────────────────────────────────────────────────
SAMPLE_SIZE_PENUH = 30    # closed positions >= ini dianggap "sample size penuh" (confidence 100%)
WIN_RATE_LANTAI    = 50    # win rate di titik ini = skor 0 (baseline "gak lebih baik dari coin flip")
WIN_RATE_ATAP       = 100   # win rate di titik ini = skor 10 (sempurna, teoretis)
BONUS_PER_TRADER_CONSENSUS = 0.3  # bonus skor tiap trader EKSTRA di atas 2 yang sepakat
BONUS_CONSENSUS_MAX         = 1.0  # cap bonus consensus


def skor_trader_individu(win_rate, net_pnl, total_closed):
    """
    Skor 0-10 buat 1 trader, berdasarkan win rate + net PnL + sample size.
    net_pnl <= 0 -> skor 0 langsung (gak peduli win rate-nya berapa, kalo net
    PnL rugi/breakeven, gak layak diikuti).
    """
    if net_pnl is None or net_pnl <= 0 or not total_closed:
        return 0.0

    # komponen win rate: <=50% -> 0, 100% -> 10, linear di antaranya
    wr_score = max(0.0, (win_rate - WIN_RATE_LANTAI) / (WIN_RATE_ATAP - WIN_RATE_LANTAI)) * 10

    # komponen confidence sample size: makin dikit closed positions, makin didiskon
    # (sqrt biar diskon-nya gak terlalu curam -- 10 posisi masih dapet ~58% confidence,
    # bukan 33% kalo linear)
    sample_conf = min(1.0, (total_closed / SAMPLE_SIZE_PENUH) ** 0.5)

    return round(wr_score * sample_conf, 2)


def skor_single_trader(performa_pnl):
    """Wrapper buat SINGLE_TRADER_MODE -- performa_pnl itu 1 dict hasil hitung_net_pnl_final()."""
    if not performa_pnl:
        return 0.0
    return skor_trader_individu(
        performa_pnl.get("win_rate", 0),
        performa_pnl.get("net_pnl", 0),
        performa_pnl.get("total_closed", 0),
    )


def skor_consensus(performa_list, jumlah_trader):
    """
    Skor 0-10 buat consensus signal (2+ trader). Kombinasi:
    - rata-rata skor individu (kualitas keseluruhan)
    - skor individu PALING RENDAH (weakest link -- 1 trader lemah gak boleh
      ketutupan sama 1 trader kuat, itu bahaya soalnya consensus-nya jadi gak
      solid semua)
    - bonus kecil buat jumlah trader yang sepakat (LEBIH BANYAK trader solid
      yang sepakat itu lebih meyakinkan, tapi bonus ini gak bisa nutupin
      kualitas individu yang jelek -- makanya ditambahin di akhir, bukan
      dikali)
    """
    skor_individu = [
        skor_trader_individu(t.get("win_rate", 0), t.get("net_pnl", 0), t.get("total_closed", 0))
        for t in performa_list if "net_pnl" in t
    ]

    if not skor_individu:
        return 0.0

    skor_rata = sum(skor_individu) / len(skor_individu)
    skor_min = min(skor_individu)

    kombinasi = (skor_rata * 0.6) + (skor_min * 0.4)

    bonus = min(max(0, jumlah_trader - 2) * BONUS_PER_TRADER_CONSENSUS, BONUS_CONSENSUS_MAX)

    return round(min(kombinasi + bonus, 10), 2)


def keputusan_dari_skor(skor, threshold):
    """Skor >= threshold -> IKUT, selain itu SKIP. Deterministik, gak ada abu-abu."""
    return "IKUT" if skor >= threshold else "SKIP"
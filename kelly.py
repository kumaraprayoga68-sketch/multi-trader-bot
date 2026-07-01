"""
Kelly Criterion -- position sizing yang mempertimbangkan EDGE (selisih probabilitas
menang menurut kita vs harga pasar saat ini), bukan cuma confidence/win rate doang.

Konsep: kalo harga beli udah deket $1 (market udah "yakin" outcome itu bakal
menang), edge-nya kecil walau win rate historis trader tinggi -- gak worth bet
gede. Kalo harga masih murah tapi win rate historis tinggi, itu edge yang bagus.

Formula Kelly standar:
    f* = p - (1-p) * P / (1-P)
    dimana p = estimasi probabilitas menang (dari win rate historis trader,
    dipake sebagai proxy -- BUKAN model probabilitas independen beneran, ini
    simplifikasi, catet di README),
    P = harga beli token saat ini (0-1)

f* negatif = Kelly bilang JANGAN BET SAMA SEKALI (harga udah gak ngasih edge),
walau skor/win rate historisnya kelihatan bagus.

Dipake FRACTIONAL Kelly (setengah dari f* penuh) -- full Kelly itu agresif
banget & rawan drawdown gede, praktik umum di quant trading pake 1/4 - 1/2 Kelly.
"""

KELLY_FRACTION_MULTIPLIER = 0.5  # half-Kelly, lebih konservatif dari full Kelly
KELLY_FRACTION_MAX = 1.0          # cap fraksi maksimal (gak lebih dari 100% MAX_PER_TRADE)


def kelly_fraction(win_rate_pct, harga_beli):
    """
    Hitung fraksi optimal dari MAX_PER_TRADE yang worth di-bet, berdasarkan
    Kelly Criterion. Return 0.0 kalo Kelly bilang jangan bet (edge negatif/nol),
    atau harga_beli invalid (<=0 atau >=1, gak ada ruang buat profit/gak valid).
    """
    if harga_beli is None or harga_beli <= 0 or harga_beli >= 1:
        return 0.0

    p = max(0.0, min(1.0, win_rate_pct / 100))  # clamp ke [0,1], jaga2 data aneh
    q = 1 - p

    f_full = p - (q * harga_beli / (1 - harga_beli))

    if f_full <= 0:
        return 0.0  # Kelly bilang: harga udah gak ngasih edge, jangan bet

    f_fractional = f_full * KELLY_FRACTION_MULTIPLIER
    return round(min(f_fractional, KELLY_FRACTION_MAX), 3)
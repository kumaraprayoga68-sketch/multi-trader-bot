"""
Cek harga & status resolusi market via endpoint PUBLIK Polymarket CLOB (gak butuh
auth/PRIVATE_KEY -- ini cuma baca data, beda dari order_executor.py yang emang
butuh auth buat kirim order).

Dipake buat 2 hal:
1. Capture harga entry pas bot IKUT (biar nanti bisa itung PnL beneran)
2. Cek market udah resolve apa belum, dan menang/kalah, buat win/loss checker
"""

import requests
import time

HOST = "https://clob.polymarket.com"


def get_market_info(condition_id, max_retry=2):
    """Fetch info market (termasuk daftar token per outcome) -- endpoint publik."""
    url = f"{HOST}/markets/{condition_id}"
    for percobaan in range(max_retry + 1):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            if percobaan < max_retry:
                time.sleep(1.5 * (percobaan + 1))
                continue
            return None
    return None


def cari_token_id(market_info, outcome_text):
    """Dari hasil get_market_info(), cari token_id buat outcome tertentu."""
    if not market_info or "tokens" not in market_info:
        return None
    for token in market_info["tokens"]:
        if token.get("outcome", "").strip().lower() == outcome_text.strip().lower():
            return token
    return None


def get_midpoint_price(token_id, max_retry=2):
    """Harga midpoint saat ini buat 1 token -- endpoint publik."""
    url = f"{HOST}/midpoint"
    for percobaan in range(max_retry + 1):
        try:
            r = requests.get(url, params={"token_id": token_id}, timeout=10)
            r.raise_for_status()
            data = r.json()
            return float(data.get("mid", 0))
        except Exception:
            if percobaan < max_retry:
                time.sleep(1.5 * (percobaan + 1))
                continue
            return None
    return None


def cek_status_resolusi(condition_id, outcome_text):
    """
    Cek apakah market ini udah resolve, dan kalo iya, apakah outcome yang kita
    pilih itu MENANG atau KALAH.

    Return dict: {"resolved": bool, "menang": bool|None, "harga_sekarang": float|None}
    - resolved=False: market masih berjalan, belum bisa dievaluasi
    - resolved=True, menang=True/False: udah final
    """
    market_info = get_market_info(condition_id)
    if not market_info:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "gagal fetch market"}

    token = cari_token_id(market_info, outcome_text)
    if not token:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "outcome gak ketemu"}

    closed = market_info.get("closed", False)
    winner = token.get("winner", False)  # biasanya True/False per token kalo market resolved

    if not closed:
        return {"resolved": False, "menang": None, "harga_sekarang": token.get("price")}

    return {"resolved": True, "menang": bool(winner), "harga_sekarang": token.get("price")}
"""
News context -- versi ringan dari konsep RAG di Polymarket/agents (resmi).
BEDA PENTING: di sini berita CUMA dipake buat ngasih konteks ke narasi "alasan"
AI, BUKAN buat mutusin IKUT/SKIP. Keputusan tetep 100% dari formula (scoring.py)
-- prinsip yang udah kita pegang dari awal (deterministik, gak boleh AI yang
mutusin). Berita di sini murni "pemanis" penjelasan, opsional, best-effort.

Gak pake API berita berbayar (NewsAPI, dll) biar gak nambah setup/biaya --
pake DuckDuckGo HTML search yang gak butuh API key. KONSEKUENSINYA: ini scraping
HTML kasar, RAWAN BREAK kalo DuckDuckGo ubah struktur halaman mereka. Makanya
selalu wrapped try/except, gagal = return list kosong, GAK PERNAH nge-block
pipeline utama.
"""

import requests
import re

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"


def cari_berita_terkait(query, max_hasil=3, timeout=10):
    """
    Cari 1-3 headline + snippet terkait query (biasanya judul market).
    Best-effort -- return list kosong kalo gagal apapun alasannya, GAK raise
    exception (biar bot utama gak keganggu kalo scraping ini bermasalah).
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.post(DUCKDUCKGO_URL, data={"q": query}, headers=headers, timeout=timeout)
        r.raise_for_status()
    except Exception:
        return []

    try:
        judul_list = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        snippet_list = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)

        hasil = []
        for judul, snippet in zip(judul_list[:max_hasil], snippet_list[:max_hasil]):
            judul_bersih = re.sub(r'<[^<]+?>', '', judul).strip()
            snippet_bersih = re.sub(r'<[^<]+?>', '', snippet).strip()
            if judul_bersih:
                hasil.append(f"{judul_bersih}: {snippet_bersih}" if snippet_bersih else judul_bersih)
        return hasil
    except Exception:
        return []  # parsing gagal (kemungkinan struktur HTML DDG berubah) -- gak fatal
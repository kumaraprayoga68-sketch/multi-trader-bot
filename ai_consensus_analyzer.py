import requests
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def analisis_consensus(sinyal: dict, performa_trader: list) -> dict:
    if not GROQ_API_KEY:
        return {
            "keputusan": "SKIP",
            "confidence": 0,
            "alasan": "GROQ_API_KEY tidak ditemukan. Cek file .env kamu.",
        }

    info_trader = ""
    for t in performa_trader:
        if "net_pnl" in t:
            # data net PnL yang udah divalidasi (gabungan /positions + /activity REDEEM)
            info_trader += (
                f"- Wallet {t['wallet'][:10]}...: net PnL ${t.get('net_pnl', 0):,.2f}, "
                f"win rate {t.get('win_rate', 0):.1f}% "
                f"({t.get('menang', 0)}W/{t.get('kalah', 0)}L), "
                f"sample size {t.get('total_closed', 0)} closed positions\n"
            )
        else:
            # fallback data lama (redeem-count) kalo net PnL gagal difetch
            info_trader += (
                f"- Wallet {t['wallet'][:10]}...: {t.get('total_redeem', 0)} kali klaim kemenangan, "
                f"total nilai redeem ${t.get('nilai_redeem', 0):,.2f} (data terbatas, gak ada win rate)\n"
            )

    prompt = f"""Kamu adalah analis trading Polymarket yang tajam dan objektif.

CONSENSUS SIGNAL TERDETEKSI:
Market         : {sinyal['title']}
Outcome dipilih: {sinyal['outcome']}
Jumlah trader  : {sinyal['jumlah_trader']} trader independen sepakat pada outcome ini

TRACK RECORD TIAP TRADER YANG TERLIBAT (dari closed positions, net PnL & win rate REAL):
{info_trader}

Tugasmu: nilai kelayakan sinyal ini berdasarkan KOMBINASI 3 FAKTOR (bukan cuma
salah satu doang):

1. KUALITAS INDIVIDU tiap trader -- net PnL positif konsisten + win rate solid
   (>60%) + sample size besar (>30 closed positions) itu kuat. Win rate tinggi
   tapi sample kecil (<10), atau net PnL positif tapi tipis/didominasi 1-2 win
   jumbo, itu lemah.
2. KEKUATAN KESEPAKATAN -- consensus dari 2 trader yang SAMA-SAMA kuat track
   record-nya lebih meyakinkan daripada 2 trader yang salah satunya lemah.
   Consensus dari LEBIH banyak trader (3+) yang semuanya solid itu lebih kuat
   lagi. Tapi consensus dari trader-trader yang track record-nya biasa aja
   TETEP lemah walau jumlahnya banyak -- kuantitas gak nutupin kualitas rendah.
3. MASUK AKAL GAK-nya consensus ini -- apakah wajar beberapa trader independen
   sepakat di market ini, atau kemungkinan cuma herd behavior/ngikutin sinyal
   luar yang sama (bukan analisis independen beneran).

PENTING -- kalibrasi confidence kamu:
- DEFAULT-nya SKEPTIS. Kebanyakan consensus signal itu SEHARUSNYA di-SKIP --
  cuma sebagian kecil yang beneran layak diikuti. Jangan asal kasih confidence
  7-8 karena "kelihatannya oke", itu bias yang harus dihindari.
- confidence 1-3: track record individu lemah, ATAU consensus-nya cuma dari
  trader biasa-biasa aja walau jumlahnya banyak. SKIP.
- confidence 4-6: ada 1-2 trader kuat tapi gak semua, atau sample size masih
  meragukan. Kemungkinan SKIP kecuali ada faktor kuat lain yang nutupin.
- confidence 7-8: MAYORITAS/SEMUA trader yang terlibat punya net PnL positif
  konsisten + win rate solid + sample besar. Baru pantas IKUT di sini.
- confidence 9-10: semua trader kuat DAN jumlah yang sepakat banyak (3+). Jarang.
- JANGAN nyumbat di angka 7-8 terus-terusan -- variasikan sesuai kekuatan data
  yang beneran ada, bukan default optimis.

Jawab HANYA dalam format JSON berikut (tanpa markdown, tanpa backtick):
{{
  "keputusan": "IKUT" atau "SKIP",
  "confidence": angka 1-10,
  "alasan": "penjelasan singkat 2-3 kalimat"
}}"""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "max_tokens": 300,
                "temperature": 0.5,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        response.raise_for_status()
        teks = response.json()["choices"][0]["message"]["content"].strip()
        teks = teks.replace("```json", "").replace("```", "").strip()
        return json.loads(teks)

    except Exception as e:
        return {
            "keputusan": "SKIP",
            "confidence": 0,
            "alasan": f"Error: {e}",
        }


def analisis_single_trader(sinyal: dict, performa_pnl: dict) -> dict:
    """
    Versi khusus SINGLE_TRADER_MODE. BEDA dari analisis_consensus() -- prompt ini
    GAK nanya "seberapa reliable konsensus ini", karena emang cuma ada 1 trader,
    itu bukan kelemahan (analisis_consensus salah asumsi soal ini kalo dipaksa
    dipake buat 1 trader -- dia bakal selalu nganggep "cuma 1 trader" itu lemah).

    Di sini yang dinilai murni: track record trader ITU SENDIRI (net PnL, win rate,
    sample size dari closed positions), bukan soal berapa banyak yang sepakat.
    """
    if not GROQ_API_KEY:
        return {
            "keputusan": "SKIP",
            "confidence": 0,
            "alasan": "GROQ_API_KEY tidak ditemukan. Cek file .env kamu.",
        }

    if performa_pnl:
        info_performa = (
            f"- Net PnL (realized, dari closed positions): ${performa_pnl.get('net_pnl', 0):,.2f}\n"
            f"- Win rate: {performa_pnl.get('win_rate', 0):.1f}% "
            f"({performa_pnl.get('menang', 0)}W / {performa_pnl.get('kalah', 0)}L / "
            f"{performa_pnl.get('breakeven', 0)}BE)\n"
            f"- Sample size: {performa_pnl.get('total_closed', 0)} closed positions\n"
            f"- Avg PnL per posisi: ${performa_pnl.get('avg_pnl_per_posisi', 0):,.2f}\n"
        )
    else:
        info_performa = "- Data performa gak tersedia (fetch gagal atau belum ada history).\n"

    prompt = f"""Kamu adalah analis trading Polymarket yang tajam dan objektif.

TRADER YANG DI-COPY BARU AJA BUKA POSISI BARU:
Market  : {sinyal['title']}
Outcome : {sinyal['outcome']}

TRACK RECORD TRADER INI (dari histori closed positions, BUKAN estimasi):
{info_performa}

Tugasmu: ini strategi COPY-TRADING 1 trader (bukan consensus antar banyak trader),
jadi JANGAN nilai berdasarkan "berapa banyak trader lain yang sepakat" -- itu gak
relevan di sini. Fokus HANYA ke: apakah track record trader ini (net PnL, win rate,
sample size) cukup solid buat dipercaya diikuti dengan modal kecil ($1-5) di posisi
barunya ini?

Pertimbangkan: win rate tinggi tapi sample size kecil (<10 closed positions) itu
kurang meyakinkan dibanding win rate solid dengan sample size besar. Net PnL positif
konsisten lebih penting daripada 1-2 win jumbo yang mendominasi angka.

PENTING -- kalibrasi confidence kamu:
- DEFAULT-nya SKEPTIS. Cuma trader dengan track record BENERAN kuat yang pantas
  dapet confidence tinggi -- jangan asal kasih 7-8 karena "kelihatannya oke".
- confidence 1-3: win rate rendah, sample size kecil, atau net PnL negatif/marginal. SKIP.
- confidence 4-6: track record oke tapi ada catatan (sample size <20, win rate
  50-60%, atau net PnL positif tapi tipis). Kemungkinan SKIP kecuali datanya kuat.
- confidence 7-8: win rate solid (>60%) DAN sample size besar (>30 closed
  positions) DAN net PnL positif konsisten. Baru pantas IKUT di sini.
- confidence 9-10: sangat meyakinkan di semua aspek (win rate tinggi, sample
  besar, net PnL besar). Jarang terjadi.
- JANGAN nyumbat di angka 7-8 terus-terusan -- variasikan sesuai kekuatan data
  yang beneran ada.

Jawab HANYA dalam format JSON berikut (tanpa markdown, tanpa backtick):
{{
  "keputusan": "IKUT" atau "SKIP",
  "confidence": angka 1-10,
  "alasan": "penjelasan singkat 2-3 kalimat"
}}"""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "max_tokens": 300,
                "temperature": 0.5,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        response.raise_for_status()
        teks = response.json()["choices"][0]["message"]["content"].strip()
        teks = teks.replace("```json", "").replace("```", "").strip()
        return json.loads(teks)

    except Exception as e:
        return {
            "keputusan": "SKIP",
            "confidence": 0,
            "alasan": f"Error: {e}",
        }


def print_analisis_consensus(sinyal: dict, hasil: dict):
    keputusan  = hasil.get("keputusan", "?")
    confidence = hasil.get("confidence", 0)
    alasan     = hasil.get("alasan", "-")

    emoji = "✅" if keputusan == "IKUT" else "❌"

    print(f"\n{'='*60}")
    print(f"🤖 AI ANALYSIS — CONSENSUS SIGNAL")
    print(f"{'='*60}")
    print(f"Market        : {sinyal['title'][:50]}")
    print(f"Outcome       : {sinyal['outcome']}")
    print(f"Trader sepakat: {sinyal['jumlah_trader']}")
    print(f"{'─'*60}")
    print(f"Keputusan AI  : {emoji} {keputusan}")
    print(f"Confidence    : {'⭐'*confidence} ({confidence}/10)")
    print(f"Alasan        : {alasan}")
    print(f"{'='*60}\n")


def print_analisis_single_trader(sinyal: dict, hasil: dict):
    keputusan  = hasil.get("keputusan", "?")
    confidence = hasil.get("confidence", 0)
    alasan     = hasil.get("alasan", "-")

    emoji = "✅" if keputusan == "IKUT" else "❌"

    print(f"\n{'='*60}")
    print(f"🤖 AI ANALYSIS — SINGLE-TRADER COPY")
    print(f"{'='*60}")
    print(f"Market        : {sinyal['title'][:50]}")
    print(f"Outcome       : {sinyal['outcome']}")
    print(f"{'─'*60}")
    print(f"Keputusan AI  : {emoji} {keputusan}")
    print(f"Confidence    : {'⭐'*confidence} ({confidence}/10)")
    print(f"Alasan        : {alasan}")
    print(f"{'='*60}\n")


# ── TEST ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_sinyal = {
        "title": "Will the Fed cut rates in July 2025?",
        "outcome": "Yes",
        "jumlah_trader": 3,
    }
    test_performa = [
        {"wallet": "0xabc123def456", "total_redeem": 12, "nilai_redeem": 41281117.79},
        {"wallet": "0xdef456ghi789", "total_redeem": 8, "nilai_redeem": 8200000.0},
        {"wallet": "0xghi789jkl012", "total_redeem": 15, "nilai_redeem": 21000000.0},
    ]
    hasil = analisis_consensus(test_sinyal, test_performa)
    print_analisis_consensus(test_sinyal, hasil)
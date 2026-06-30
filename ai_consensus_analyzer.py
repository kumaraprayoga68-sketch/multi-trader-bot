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
        info_trader += (
            f"- Wallet {t['wallet'][:10]}...: "
            f"{t['total_redeem']} kali berhasil klaim kemenangan, "
            f"total nilai redeem ${t['nilai_redeem']:,.2f}\n"
        )

    prompt = f"""Kamu adalah analis trading Polymarket yang tajam dan objektif.

CONSENSUS SIGNAL TERDETEKSI:
Market         : {sinyal['title']}
Outcome dipilih: {sinyal['outcome']}
Jumlah trader  : {sinyal['jumlah_trader']} trader independen sepakat pada outcome ini

PERFORMA TRADER YANG TERLIBAT (berdasarkan history klaim kemenangan):
{info_trader}

Tugasmu: Beberapa trader dengan track record berbeda-beda secara independen masuk
ke posisi yang SAMA. Analisis apakah consensus ini layak dipercaya dan worth
untuk diikuti dengan modal kecil ($1-5), atau ini hanya kebetulan/herd behavior
yang tidak reliable.

Pertimbangkan: apakah trader yang terlibat punya history klaim kemenangan yang
solid? Apakah masuk akal banyak trader sepakat di market ini?

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
                "temperature": 0.3,
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
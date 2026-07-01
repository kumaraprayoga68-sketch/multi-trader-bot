"""
Order execution module — INI YANG NGIRIM DUIT BENERAN KE POLYMARKET.

⚠️⚠️⚠️ BACA INI SEBELUM PAKE ⚠️⚠️⚠️
File ini pake py-clob-client (SDK resmi Polymarket) buat sign & kirim order.
Begitu LIVE_TRADING_ENABLED=True dan bot jalan, DUIT BENERAN BISA KEPAKE.

Struktur ini udah divalidasi ke source code SDK resmi (py-clob-client v0.34.6)
+ dokumentasi resmi Polymarket, BUKAN tebakan. Tapi tetep:
1. TEST DULU pake wallet/amount super kecil ($1-2) sebelum lo percaya penuh
2. Jangan pernah naikin MAX_ORDER_SIZE_ABSOLUTE tanpa ngerti resikonya
3. .env JANGAN PERNAH di-share/commit ke git manapun

SETUP .env YANG DIBUTUHIN (udah ada di .env.example lo):
    PRIVATE_KEY=<private key wallet BOT, bukan wallet utama>
    FUNDER_ADDRESS=<address yang nampung dana trading>

CARA DAPETIN CREDENTIAL INI DENGAN AMAN:
- JANGAN pake private key wallet utama lo yang isinya banyak duit
- Bikin wallet KHUSUS buat bot ini, isi cuma sejumlah budget yang lo relain ($100 sesuai BUDGET_SAYA)
- Private key ini punya akses PENUH ke semua dana di wallet itu -- perlakukan kayak password bank
"""

import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ─────────────────────────────────────────────────────────────────
HOST                    = "https://clob.polymarket.com"
CHAIN_ID                = 137  # Polygon mainnet
PRIVATE_KEY             = os.getenv("PRIVATE_KEY", "")
FUNDER_ADDRESS           = os.getenv("FUNDER_ADDRESS", "")
SIGNATURE_TYPE           = 1    # 1 = proxy wallet (funder address terpisah dari signing key)

LIVE_TRADING_ENABLED     = False   # ⚠️ HARUS lo ubah manual jadi True buat live trading beneran
MAX_ORDER_SIZE_ABSOLUTE  = 5.0     # $ -- HARD CAP, independen dari config manapun di multi_monitor.py

_client = None  # singleton, di-init sekali aja pas dibutuhin


def log(msg):
    waktu = datetime.now().strftime("%H:%M:%S")
    print(f"[{waktu}] {msg}")


def _get_client():
    """Init ClobClient sekali (lazy), reuse buat request selanjutnya."""
    global _client
    if _client is not None:
        return _client

    if not PRIVATE_KEY or not FUNDER_ADDRESS:
        raise RuntimeError(
            "PRIVATE_KEY / FUNDER_ADDRESS kosong di .env. "
            "Isi dulu sesuai .env.example sebelum pake fitur ini."
        )

    from py_clob_client.client import ClobClient

    client = ClobClient(
        HOST,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER_ADDRESS,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    _client = client
    log("🔑 ClobClient berhasil diinisialisasi & authenticated.")
    return _client


def get_token_id_untuk_outcome(condition_id, outcome_text):
    """
    Cari token_id (buat dipake pas order) dari conditionId + nama outcome ('Yes'/'No').
    Return None kalo gak ketemu.
    """
    client = _get_client()
    market = client.get_market(condition_id)

    if not market or "tokens" not in market:
        log(f"❌ Market {condition_id[:12]}... gak ketemu / gak ada field 'tokens'")
        return None

    for token in market["tokens"]:
        if token.get("outcome", "").strip().lower() == outcome_text.strip().lower():
            return token.get("token_id")

    log(f"❌ Outcome '{outcome_text}' gak ketemu di market {condition_id[:12]}... "
        f"(outcome yang ada: {[t.get('outcome') for t in market['tokens']]})")
    return None


def place_market_buy(condition_id, outcome_text, usd_amount, dry_run=True):
    """
    Pasang MARKET BUY order (FOK -- fill or kill, gak nyangkut partial).

    dry_run=True (default): cuma nunjukkin apa yang BAKAL dikirim, GAK ngirim beneran.
    dry_run=False: kirim order beneran ke Polymarket -- CUMA jalan kalo
                   LIVE_TRADING_ENABLED juga True (double gate, sengaja).

    Return dict hasil (status, response/alasan), gak pernah raise exception ke caller
    (biar bot utama gak crash gara-gara 1 order gagal).
    """
    # ── hard safety cap, gak peduli parameter usd_amount yang dikirim ──
    if usd_amount > MAX_ORDER_SIZE_ABSOLUTE:
        log(f"⛔ Order ${usd_amount} melebihi MAX_ORDER_SIZE_ABSOLUTE (${MAX_ORDER_SIZE_ABSOLUTE}). DIBATALIN.")
        return {"status": "blocked", "alasan": "melebihi hard cap"}

    log(f"📝 Menyiapkan order: BUY '{outcome_text}' senilai ${usd_amount} "
        f"di market {condition_id[:12]}...")

    if dry_run or not LIVE_TRADING_ENABLED:
        log(f"🟡 [DRY-RUN] Order TIDAK dikirim (dry_run={dry_run}, "
            f"LIVE_TRADING_ENABLED={LIVE_TRADING_ENABLED}). Ini cuma simulasi.")
        return {
            "status": "dry_run",
            "condition_id": condition_id,
            "outcome": outcome_text,
            "usd_amount": usd_amount,
        }

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        token_id = get_token_id_untuk_outcome(condition_id, outcome_text)
        if not token_id:
            return {"status": "error", "alasan": "token_id gak ketemu"}

        client = _get_client()

        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=usd_amount,      # dalam USDC
            side=BUY,
            order_type=OrderType.FOK,
        )

        log(f"🚀 MENGIRIM ORDER LIVE — ${usd_amount} BUY '{outcome_text}' "
            f"(token_id={token_id[:16]}...)")

        signed_order = client.create_market_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK)

        log(f"✅ Order terkirim. Response: {resp}")
        return {"status": "success", "response": resp}

    except Exception as e:
        log(f"❌ GAGAL kirim order: {e}")
        return {"status": "error", "alasan": str(e)}


def cek_koneksi():
    """Test koneksi & auth ke Polymarket CLOB, tanpa kirim order apapun."""
    try:
        client = _get_client()
        ok = client.get_ok()
        server_time = client.get_server_time()
        log(f"✅ Koneksi OK. Server time: {server_time}")
        return True
    except Exception as e:
        log(f"❌ Koneksi/auth gagal: {e}")
        return False


# ── TEST ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("STEP 1: Cek koneksi & auth ke Polymarket CLOB (belum kirim order apapun)\n")
    ok = cek_koneksi()

    if ok:
        print("\nSTEP 2: Contoh dry-run order (GAK beneran dikirim, cuma simulasi)")
        print("Ganti condition_id di bawah ini pake market beneran buat ngetest\n")
        hasil = place_market_buy(
            condition_id="0xGANTI_DENGAN_CONDITION_ID_BENERAN",
            outcome_text="Yes",
            usd_amount=1.0,
            dry_run=True,
        )
        print(f"\nHasil dry-run: {hasil}")
    else:
        print("\n🛑 Koneksi gagal. Cek PRIVATE_KEY & FUNDER_ADDRESS di .env lo.")
"""
EL MUNDO OMNI-NEURAL - 3-IN-1 BOT (Render Free Tier, 1 web service, 1 worker)
"""
from __future__ import annotations

import os
import hmac
import json
import time
import base64
import sqlite3
import hashlib
import logging
import threading
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("elmundo_3bots")

app = Flask(__name__)

SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")

CJ_EMAIL = os.environ.get("CJ_EMAIL", "")
CJ_API_KEY = os.environ.get("CJ_API_KEY", "")
CJ_API_BASE = os.environ.get("CJ_API_BASE", "https://developers.cjdropshipping.com/api2.0/v1")
CJ_SEARCH_KEYWORD = os.environ.get("CJ_SEARCH_KEYWORD", "gadget")
CJ_MAX_IMPORT_PER_CYCLE = int(os.environ.get("CJ_MAX_IMPORT_PER_CYCLE", "5"))
CJ_PRICE_MARKUP_PCT = float(os.environ.get("CJ_PRICE_MARKUP_PCT", "60"))

ALIEXPRESS_APP_KEY = os.environ.get("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_APP_SECRET = os.environ.get("ALIEXPRESS_APP_SECRET", "")
ALIEXPRESS_ENABLED = bool(ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")

HUNTER_INTERVAL_SEC = int(os.environ.get("HUNTER_INTERVAL_SEC", str(60 * 60)))
FULFILLMENT_POLL_SEC = int(os.environ.get("FULFILLMENT_POLL_SEC", "120"))
ENABLE_BACKGROUND_THREADS = os.environ.get("ENABLE_BACKGROUND_THREADS", "true").lower() == "true"

DB_PATH = os.environ.get("DB_PATH", "coordinator.db")

_db_lock = threading.Lock()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def db_init() -> None:
    with _db_lock, db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier TEXT NOT NULL,
                supplier_id TEXT NOT NULL,
                image_hash TEXT,
                shopify_product_id TEXT,
                shopify_variant_sku TEXT,
                title TEXT,
                supplier_price REAL,
                shopify_price REAL,
                imported_at TEXT NOT NULL,
                UNIQUE(supplier, supplier_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_image_hash ON products(image_hash)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shopify_order_id TEXT NOT NULL UNIQUE,
                shopify_order_name TEXT,
                supplier TEXT,
                supplier_order_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_duplicate_product(supplier: str, supplier_id: str, image_hash: str | None) -> bool:
    with _db_lock, db_connect() as conn:
        row = conn.execute(
            "SELECT id FROM products WHERE (supplier = ? AND supplier_id = ?) "
            "OR (image_hash IS NOT NULL AND image_hash = ?) LIMIT 1",
            (supplier, supplier_id, image_hash),
        ).fetchone()
        return row is not None


def save_imported_product(supplier, supplier_id, image_hash, shopify_product_id,
                           shopify_variant_sku, title, supplier_price, shopify_price) -> None:
    with _db_lock, db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO products "
            "(supplier, supplier_id, image_hash, shopify_product_id, shopify_variant_sku, "
            " title, supplier_price, shopify_price, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (supplier, supplier_id, image_hash, shopify_product_id, shopify_variant_sku,
             title, supplier_price, shopify_price, now_iso()),
        )
        conn.commit()


def find_product_by_sku(sku: str) -> sqlite3.Row | None:
    with _db_lock, db_connect() as conn:
        return conn.execute("SELECT * FROM products WHERE shopify_variant_sku = ?", (sku,)).fetchone()


def order_already_fulfilled(shopify_order_id: str) -> bool:
    with _db_lock, db_connect() as conn:
        row = conn.execute(
            "SELECT id FROM orders WHERE shopify_order_id = ? AND status IN ('sent','confirmed')",
            (shopify_order_id,),
        ).fetchone()
        return row is not None


def upsert_order(shopify_order_id, shopify_order_name, supplier=None, supplier_order_id=None,
                  status="pending", last_error=None) -> None:
    with _db_lock, db_connect() as conn:
        existing = conn.execute(
            "SELECT id FROM orders WHERE shopify_order_id = ?", (shopify_order_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE orders SET supplier=?, supplier_order_id=?, status=?, last_error=?, updated_at=? "
                "WHERE shopify_order_id=?",
                (supplier, supplier_order_id, status, last_error, now_iso(), shopify_order_id),
            )
        else:
            conn.execute(
                "INSERT INTO orders (shopify_order_id, shopify_order_name, supplier, supplier_order_id, "
                "status, last_error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (shopify_order_id, shopify_order_name, supplier, supplier_order_id, status,
                 last_error, now_iso(), now_iso()),
            )
        conn.commit()


def get_order(shopify_order_id_or_name: str) -> sqlite3.Row | None:
    with _db_lock, db_connect() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE shopify_order_id = ? OR shopify_order_name = ?",
            (shopify_order_id_or_name, shopify_order_id_or_name),
        ).fetchone()


def _http(url, method="GET", headers=None, payload=None, timeout=30) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {e.read().decode('utf-8', 'ignore')}")


def hash_image_url(image_url: str) -> str | None:
    if not image_url:
        return None
    try:
        with urllib.request.urlopen(image_url, timeout=20) as resp:
            data = resp.read()
        return hashlib.md5(data).hexdigest()
    except Exception as e:
        log.warning("Pa t ka hash imaj %s: %s", image_url, e)
        return None


def shopify_base() -> str:
    return f"https://{SHOPIFY_SHOP}/admin/api/{SHOPIFY_API_VERSION}"


def shopify_headers() -> dict:
    return {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}


def shopify_create_product(title: str, description: str, price: float, sku: str,
                            image_url: str | None, vendor: str = "El Mundo") -> dict:
    payload = {
        "product": {
            "title": title,
            "body_html": description,
            "vendor": vendor,
            "variants": [{"price": f"{price:.2f}", "sku": sku, "inventory_management": None}],
        }
    }
    if image_url:
        payload["product"]["images"] = [{"src": image_url}]
    return _http(f"{shopify_base()}/products.json", "POST", shopify_headers(), payload)


def shopify_get_order(order_id: str) -> dict:
    return _http(f"{shopify_base()}/orders/{order_id}.json", "GET", shopify_headers())


_cj_token_cache = {"token": None, "expires_at": 0}


def cj_configured() -> bool:
    return bool(CJ_EMAIL and CJ_API_KEY)


def cj_get_access_token() -> str:
    if _cj_token_cache["token"] and time.time() < _cj_token_cache["expires_at"]:
        return _cj_token_cache["token"]
    if not cj_configured():
        raise RuntimeError("CJ pa configure (CJ_EMAIL / CJ_API_KEY manke)")
    resp = _http(
        f"{CJ_API_BASE}/authentication/getAccessToken", "POST",
        {"Content-Type": "application/json"},
        {"email": CJ_EMAIL, "password": CJ_API_KEY},
    )
    token = (resp.get("data") or {}).get("accessToken")
    if not token:
        raise RuntimeError(f"Pa t ka jwenn accessToken CJ: {resp}")
    _cj_token_cache["token"] = token
    _cj_token_cache["expires_at"] = time.time() + 60 * 60 * 12
    return token


def cj_search_products(keyword: str, page_size: int = 10) -> list[dict]:
    token = cj_get_access_token()
    resp = _http(
        f"{CJ_API_BASE}/product/list?keyword={urllib.parse.quote(keyword)}&pageSize={page_size}",
        "GET", {"CJ-Access-Token": token},
    )
    return (resp.get("data") or {}).get("list", []) or []


def cj_create_order(supplier_sku: str, qty: int, shipping: dict) -> dict:
    token = cj_get_access_token()
    payload = {
        "orderNumber": f"EL-MUNDO-{int(time.time())}",
        "shippingAddress": shipping,
        "products": [{"vid": supplier_sku, "quantity": qty}],
    }
    return _http(f"{CJ_API_BASE}/shopping/order/createOrder", "POST",
                 {"CJ-Access-Token": token, "Content-Type": "application/json"}, payload)


def aliexpress_search_products(keyword: str) -> list[dict]:
    if not ALIEXPRESS_ENABLED:
        log.info("AliExpress pa configure (ALIEXPRESS_APP_KEY/SECRET manke) - sote.")
        return []
    log.warning("aliexpress_search_products(): enplemantasyon poko fèt (mande apwobasyon API).")
    return []


def hunter_cycle() -> None:
    log.info("[HUNTER] Sik chase pwodui kòmanse (keyword=%s)", CJ_SEARCH_KEYWORD)
    imported = 0
    try:
        candidates = cj_search_products(CJ_SEARCH_KEYWORD) + aliexpress_search_products(CJ_SEARCH_KEYWORD)
    except Exception as e:
        log.error("[HUNTER] Erè pandan rechèch founisè: %s", e)
        return

    for item in candidates:
        if imported >= CJ_MAX_IMPORT_PER_CYCLE:
            break
        supplier = item.get("_supplier", "cj")
        supplier_id = str(item.get("pid") or item.get("productId") or item.get("id") or "")
        title = item.get("productNameEn") or item.get("title") or "Pwodui San Non"
        image_url = item.get("productImage") or item.get("image") or ""
        supplier_price = float(item.get("sellPrice") or item.get("price") or 0)
        if not supplier_id or supplier_price <= 0:
            continue

        image_hash = hash_image_url(image_url)

        if is_duplicate_product(supplier, supplier_id, image_hash):
            log.info("[HUNTER] Sote doub: %s / %s", supplier, supplier_id)
            continue

        shopify_price = round(supplier_price * (1 + CJ_PRICE_MARKUP_PCT / 100), 2)
        sku = f"{supplier.upper()}-{supplier_id}"
        try:
            result = shopify_create_product(
                title=title,
                description=item.get("description", "") or f"{title} - enpòte otomatikman.",
                price=shopify_price,
                sku=sku,
                image_url=image_url,
            )
            shopify_product_id = str((result.get("product") or {}).get("id", ""))
            save_imported_product(
                supplier, supplier_id, image_hash, shopify_product_id, sku,
                title, supplier_price, shopify_price,
            )
            imported += 1
            log.info("[HUNTER] Enpòte: %s (SKU=%s, %.2f -> %.2f)", title, sku, supplier_price, shopify_price)
        except Exception as e:
            log.error("[HUNTER] Erè enpòte '%s': %s", title, e)

    log.info("[HUNTER] Sik fini - %d pwodui enpòte", imported)


def hunter_loop() -> None:
    while True:
        try:
            hunter_cycle()
        except Exception as e:
            log.exception("[HUNTER] Erè fatal nan sik la: %s", e)
        time.sleep(HUNTER_INTERVAL_SEC)


def fulfill_shopify_order(order: dict) -> None:
    shopify_order_id = str(order.get("id"))
    shopify_order_name = order.get("name", shopify_order_id)

    if order_already_fulfilled(shopify_order_id):
        log.info("[FULFILL] Lòd %s deja voye kote founisè - sote.", shopify_order_name)
        return

    if not cj_configured():
        upsert_order(shopify_order_id, shopify_order_name, supplier=None,
                     status="awaiting_manual",
                     last_error="CJ_API_KEY manke - fulfillment an atant")
        log.warning("[FULFILL] Lòd %s pare pou voye bay founisè, men CJ_API_KEY "
                    "manke. Mete l nan Render Environment pou otomatize sa.", shopify_order_name)
        telegram_alert_admin(
            f"⚠️ LÒD {shopify_order_name} PARE POU FOUNISÈ - CJ_API_KEY MANKE\n"
            f"Ajoute CJ_API_KEY + CJ_EMAIL nan Render pou l otomatize, "
            f"oswa kòmande l manyèlman kounye a."
        )
        return

    upsert_order(shopify_order_id, shopify_order_name, status="processing")

    shipping_addr = order.get("shipping_address") or {}
    shipping = {
        "name": shipping_addr.get("name", ""),
        "address1": shipping_addr.get("address1", ""),
        "city": shipping_addr.get("city", ""),
        "province": shipping_addr.get("province", ""),
        "zip": shipping_addr.get("zip", ""),
        "country": shipping_addr.get("country_code", ""),
        "phone": shipping_addr.get("phone", "") or order.get("phone", ""),
    }

    any_error = None
    for line in order.get("line_items", []):
        sku = line.get("sku")
        qty = int(line.get("quantity", 1))
        mapped = find_product_by_sku(sku) if sku else None
        if not mapped:
            any_error = f"SKU '{sku}' pa gen founisè asosye - fè fulfillment manyèl."
            log.warning("[FULFILL] %s", any_error)
            continue
        try:
            cj_create_order(mapped["supplier_id"], qty, shipping)
        except Exception as e:
            any_error = f"Erè kòmande kote founisè pou SKU {sku}: {e}"
            log.error("[FULFILL] %s", any_error)

    status = "sent" if any_error is None else "error"
    upsert_order(shopify_order_id, shopify_order_name, supplier="cj", status=status, last_error=any_error)
    log.info("[FULFILL] Lòd %s trete - status=%s", shopify_order_name, status)
    if status == "sent":
        telegram_alert_admin(f"✅ Lòd {shopify_order_name} voye bay CJ otomatikman.")
    else:
        telegram_alert_admin(f"❌ Lòd {shopify_order_name} gen erè: {any_error}")


def verify_shopify_hmac(raw_body: bytes, header_hmac: str) -> bool:
    if not SHOPIFY_WEBHOOK_SECRET:
        log.error("[SEKIRITE] SHOPIFY_WEBHOOK_SECRET pa configure - webhook refize.")
        return False
    if not header_hmac:
        return False
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, header_hmac)


@app.route("/shopify-webhook/orders-create", methods=["POST"])
def shopify_orders_create_webhook():
    raw_body = request.get_data()
    header_hmac = request.headers.get("X-Shopify-Hmac-Sha256", "")

    if not verify_shopify_hmac(raw_body, header_hmac):
        log.warning("[SEKIRITE] Webhook rejte - siyati HMAC envalid oswa manke.")
        return jsonify({"ok": False, "error": "siyati envalid"}), 401

    order = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    if not order.get("id"):
        return jsonify({"ok": False, "error": "pa gen lòd nan body"}), 400
    threading.Thread(target=fulfill_shopify_order, args=(order,), daemon=True).start()
    return jsonify({"ok": True})


def fulfillment_backup_poll_loop() -> None:
    while True:
        try:
            if cj_configured():
                with _db_lock, db_connect() as conn:
                    pending = conn.execute(
                        "SELECT shopify_order_id FROM orders WHERE status='awaiting_manual' LIMIT 10"
                    ).fetchall()
                for row in pending:
                    try:
                        full_order = shopify_get_order(row["shopify_order_id"]).get("order")
                        if full_order:
                            log.info("[FULFILL-POLL] CJ configure - reyesèy lòd %s", row["shopify_order_id"])
                            fulfill_shopify_order(full_order)
                    except Exception as e:
                        log.error("[FULFILL-POLL] Erè reyesèy lòd %s: %s", row["shopify_order_id"], e)

            with _db_lock, db_connect() as conn:
                errored = conn.execute(
                    "SELECT * FROM orders WHERE status='error' ORDER BY updated_at DESC LIMIT 5"
                ).fetchall()
            if errored:
                log.info("[FULFILL-POLL] %d lòd an erè ap tann aksyon manyèl.", len(errored))
        except Exception as e:
            log.exception("[FULFILL-POLL] Erè: %s", e)
        time.sleep(FULFILLMENT_POLL_SEC)


def telegram_send(chat_id: str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.warning("[TELEGRAM] TELEGRAM_BOT_TOKEN manke - pa ka voye mesaj.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        _http(url, "POST", {"Content-Type": "application/json"}, {"chat_id": chat_id, "text": text})
    except Exception as e:
        log.error("[TELEGRAM] Erè voye mesaj: %s", e)


def telegram_alert_admin(text: str) -> None:
    if not TELEGRAM_ALLOWED_CHAT_ID:
        log.warning("[TELEGRAM] TELEGRAM_ALLOWED_CHAT_ID manke - pa ka voye alèt admin.")
        return
    telegram_send(TELEGRAM_ALLOWED_CHAT_ID, text)


def telegram_handle(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return "Itilize /status, /order ID, /find SKU, oswa /help."
    cmd = parts[0].lower()

    if cmd == "/help":
        return (
            "/status - eta 3 bot yo\n"
            "/order ID_OSWA_NON - verifye yon lòd (egzanp: /order #1001)\n"
            "/find SKU - verifye si yon SKU gen founisè asosye"
        )

    if cmd == "/status":
        with _db_lock, db_connect() as conn:
            n_products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
            n_orders = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
            n_errors = conn.execute("SELECT COUNT(*) c FROM orders WHERE status='error'").fetchone()["c"]
        return (
            f"EL MUNDO - 3 BOT STATUS\n"
            f"Pwodui enpòte: {n_products}\n"
            f"Lòd trete: {n_orders} (erè: {n_errors})\n"
            f"Hunter: {'ON' if ENABLE_BACKGROUND_THREADS else 'OFF'} "
            f"(chak {HUNTER_INTERVAL_SEC}s)\n"
            f"AliExpress: {'konfigire' if ALIEXPRESS_ENABLED else 'pa konfigire'}\n"
            f"CJ: {'KONFIGIRE (otomatik)' if cj_configured() else 'PA KONFIGIRE (mòd manyèl + alèt)'}"
        )

    if cmd == "/order":
        if len(parts) != 2:
            return "Fòma: /order ID_OSWA_NON"
        o = get_order(parts[1])
        if not o:
            return f"Pa jwenn okenn lòd '{parts[1]}' nan baz done a."
        return (
            f"Lòd {o['shopify_order_name']}\n"
            f"Status: {o['status']}\n"
            f"Founisè: {o['supplier'] or '-'}\n"
            f"ID founisè: {o['supplier_order_id'] or '-'}\n"
            f"Erè: {o['last_error'] or 'okenn'}"
        )

    if cmd == "/find":
        if len(parts) != 2:
            return "Fòma: /find SKU"
        p = find_product_by_sku(parts[1])
        if not p:
            return f"SKU '{parts[1]}' pa gen founisè asosye nan baz done a."
        return f"{p['title']}\nSKU: {p['shopify_variant_sku']}\nFounisè: {p['supplier']} ({p['supplier_id']})"

    return "Kòmand pa rekonèt. Tape /help."


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")

    if not chat_id or not text:
        return jsonify({"ok": True})
    if TELEGRAM_ALLOWED_CHAT_ID and chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        log.warning("[TELEGRAM] Chat ID pa otorize: %s", chat_id)
        return jsonify({"ok": True})

    try:
        reply = telegram_handle(text)
    except Exception as e:
        log.exception("[TELEGRAM] Erè pandan n ap trete kòmand")
        reply = f"[ERE] {type(e).__name__}: {e}"

    telegram_send(chat_id, reply)
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "bots": ["product_hunter", "auto_fulfillment", "telegram_support"],
        "background_threads": ENABLE_BACKGROUND_THREADS,
    })


_threads_started = False
_threads_lock = threading.Lock()


def start_background_threads() -> None:
    global _threads_started
    with _threads_lock:
        if _threads_started or not ENABLE_BACKGROUND_THREADS:
            return
        db_init()
        threading.Thread(target=hunter_loop, daemon=True, name="hunter").start()
        threading.Thread(target=fulfillment_backup_poll_loop, daemon=True, name="fulfill-poll").start()
        _threads_started = True
        log.info("Threads background yo lanse (hunter + fulfillment-poll).")


start_background_threads()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

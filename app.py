"""
EL MUNDO OMNI-NEURAL - BOT TELEGRAM 24/7 (Shopify Casa Mia Store Chile)

Kòmand Telegram ki sipòte:
  /list                     - montre 10 pwodui yo ak pri aktyèl yo
  /price SKU NOUVO_PRIX     - chanje pri yon pwodui (CLP)
  /photo SKU URL_IMAJ       - ajoute yon foto sou yon pwodui
  /anonse SKU               - jenere + pibliye yon anons pwomosyon
                              (Telegram kanal toujou; Facebook/Instagram
                              otomatikman tou lè varyab Meta configure)

Deplwaye sou Render.com kòm "Web Service". Varyab anviwònman ki OBLIGATWA:
  SHOPIFY_SHOP            = elmondocomprafacil.myshopify.com
  SHOPIFY_ACCESS_TOKEN     = shpat_...  (NAN RENDER SELMAN, PA JANM NAN KOD LA)
  TELEGRAM_BOT_TOKEN       = (token Telegram ou jwenn nan @BotFather)
  TELEGRAM_ALLOWED_CHAT_ID = (ID chat Telegram OU sèlman - anpeche lòt moun kontwole boutik ou)
"""
from __future__ import annotations

import os
import logging
import urllib.request
import urllib.error
import json

from flask import Flask, request, jsonify

import shopify_client
import ad_generator
import facebook_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("elmundo_bot")

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")  # kanal piblik pou anonse yo
TELEGRAM_API_OVERRIDE = os.environ.get("TELEGRAM_API_BASE_OVERRIDE")  # pou tès lokal

TRACKED_SKUS = [
    "EL-MUNDO-CL-025", "EL-MUNDO-CL-024", "EL-MUNDO-CL-023", "EL-MUNDO-CL-022",
    "EL-MUNDO-CL-021", "EL-MUNDO-CL-020", "EL-MUNDO-CL-019", "EL-MUNDO-CL-018",
    "EL-MUNDO-CL-017", "EL-MUNDO-CL-016",
]


def _telegram_api_base() -> str:
    return TELEGRAM_API_OVERRIDE or f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id: str, text: str) -> None:
    url = f"{_telegram_api_base()}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        log.error("Erè voye mesaj Telegram: %s", e.read().decode("utf-8", "ignore"))


def handle_command(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return "Kòmand vid. Itilize /list, /price SKU PRIX, oswa /photo SKU URL."

    cmd = parts[0].lower()

    if cmd == "/list":
        rows = shopify_client.list_all_tracked_skus(TRACKED_SKUS)
        lines = [f"{r['sku']}: {r['title']} - {r['price']} CLP" for r in rows]
        return "PWODUI AKTYÈL YO:\n" + "\n".join(lines)

    if cmd == "/price":
        if len(parts) != 3:
            return "Fòma: /price SKU NOUVO_PRIX  (egzanp: /price EL-MUNDO-CL-016 9500)"
        sku, raw_price = parts[1], parts[2]
        try:
            new_price = float(raw_price)
        except ValueError:
            return f"Pri envalid: {raw_price}"
        found = shopify_client.find_product_and_variant_by_sku(sku)
        if not found:
            return f"Pa jwenn pwodui ak SKU {sku}"
        product, variant = found
        shopify_client.update_price(variant["id"], new_price)
        return f"[OK] Pri {product['title']} ({sku}) chanje pou {new_price:.0f} CLP"

    if cmd == "/photo":
        if len(parts) != 3:
            return "Fòma: /photo SKU URL_IMAJ"
        sku, image_url = parts[1], parts[2]
        found = shopify_client.find_product_and_variant_by_sku(sku)
        if not found:
            return f"Pa jwenn pwodui ak SKU {sku}"
        product, _variant = found
        shopify_client.attach_image_url(product["id"], image_url)
        return f"[OK] Foto ajoute sou {product['title']} ({sku})"

    if cmd == "/anonse":
        if len(parts) != 2:
            return "Fòma: /anonse SKU  (egzanp: /anonse EL-MUNDO-CL-016)"
        sku = parts[1]
        found = shopify_client.find_product_and_variant_by_sku(sku)
        if not found:
            return f"Pa jwenn pwodui ak SKU {sku}"
        product, variant = found
        caption = ad_generator.generate_ad_caption(product["title"], variant["price"], sku)
        image_url = (product.get("images") or [{}])[0].get("src")

        results = []
        if TELEGRAM_CHANNEL_ID:
            send_message(TELEGRAM_CHANNEL_ID, caption)
            results.append("Telegram: [OK]")
        else:
            results.append("Telegram: [PA CONFIGURE - TELEGRAM_CHANNEL_ID manke]")

        if facebook_client.is_facebook_configured():
            try:
                facebook_client.post_to_facebook_page(caption, image_url)
                results.append("Facebook: [OK]")
            except Exception as e:
                results.append(f"Facebook: [ERE] {e}")
        else:
            results.append("Facebook: [PA KONEKTE ANKÒ - tann Meta Business]")

        if facebook_client.is_instagram_configured():
            try:
                if image_url:
                    facebook_client.post_to_instagram(caption, image_url)
                    results.append("Instagram: [OK]")
                else:
                    results.append("Instagram: [SOTE - pa gen foto sou pwodui a]")
            except Exception as e:
                results.append(f"Instagram: [ERE] {e}")
        else:
            results.append("Instagram: [PA KONEKTE ANKÒ - tann Meta Business]")

        return f"ANONS PWOMOSYON '{product['title']}':\n\n{caption}\n\n" + "\n".join(results)

    return "Kòmand pa rekonèt. Itilize /list, /price SKU PRIX, /photo SKU URL, oswa /anonse SKU."


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")

    if not chat_id or not text:
        return jsonify({"ok": True})

    if TELEGRAM_ALLOWED_CHAT_ID and chat_id != TELEGRAM_ALLOWED_CHAT_ID:
        log.warning("Mesaj ki soti nan chat ID ki pa otorize: %s", chat_id)
        return jsonify({"ok": True})

    try:
        reply = handle_command(text)
    except Exception as e:
        log.exception("Erè pandan n ap trete kòmand")
        reply = f"[ERE] {type(e).__name__}: {e}"

    send_message(chat_id, reply)
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot": "el-mundo-telegram", "tracked_skus": len(TRACKED_SKUS)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

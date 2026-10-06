from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SHOPIFY_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "elmundo_secret")

# 3 bots A/B logic
BOTS = {
    "bot1": "Support - A",
    "bot2": "Sales - B", 
    "bot3": "CJ Dropshipping - Fulfillment"
}

@app.route("/")
def home():
    return "El Mundo STORE LIVE - Bot Actif"

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "store": "El Mundo STORE LIVE",
        "bots": list(BOTS.keys()),
        "bots_detail": BOTS,
        "mode": "A/B",
        "bot_configured": bool(BOT_TOKEN),
        "chat_configured": bool(CHAT_ID)
    })

@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    
    # Info commande
    order_id = data.get("id")
    customer = data.get("customer", {})
    total = data.get("total_price")
    email = customer.get("email", "N/A")
    
    msg = f"🔥 NOUVO LOD El Mundo!\n\nID: {order_id}\nKliyan: {email}\nTotal: ${total}\n\nBot CJ ap trete..."
    
    # Voye Telegram si configure
    if BOT_TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": msg})
        except Exception as e:
            print(f"Telegram error: {e}")
    
    return jsonify({"received": True, "bots": "3-bot A/B active"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

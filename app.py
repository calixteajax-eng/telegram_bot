import os, hmac, hashlib, base64, sqlite3, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET","elmundo_secret")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN","")
TG_CHAT = os.getenv("TELEGRAM_ALLOWED_CHAT_ID","")
CJ_KEY = os.getenv("CJ_API_KEY","")

conn = sqlite3.connect("/tmp/elmundo.db", check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY)")

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id":TG_CHAT,"text":msg}, timeout=5)
    except: pass

@app.route("/")
def home():
    return "El Mundo STORE LIVE - Bot Actif"

@app.route("/webhook/shopify", methods=["POST"])
def shopify_hook():
    h = request.headers.get("X-Shopify-Hmac-Sha256","")
    if WEBHOOK_SECRET and h:
        d = hmac.new(WEBHOOK_SECRET.encode(), request.data, hashlib.sha256).digest()
        if base64.b64encode(d).decode() != h:
            return "Unauthorized", 401
    data = request.get_json(silent=True) or {}
    oid = str(data.get("id",""))
    if conn.execute("SELECT 1 FROM orders WHERE id=?",(oid,)).fetchone():
        return jsonify({"status":"duplicate"}),200
    conn.execute("INSERT INTO orders VALUES (?)",(oid,)); conn.commit()
    if not CJ_KEY:
        send_tg(f"🛒 NOUVO KÒMAND #{oid} - {data.get('total_price','')} - CJ vid, mete kle a")
        return jsonify({"status":"telegram_sent"}),200
    send_tg(f"✅ Kòmand #{oid} voye CJ")
    return jsonify({"status":"cj"}),200

@app.route("/webhook/telegram", methods=["POST"])
def tg():
    return "ok",200

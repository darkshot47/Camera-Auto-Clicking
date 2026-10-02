"""Flask web app plus a supervised Telegram polling thread for Render."""

import asyncio
import base64
import logging
import os
import threading
from io import BytesIO

import requests
from flask import Flask, jsonify, render_template, request

from database import add_visitor, get_link

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Browser sends a base64 data URL. The decoded JPEG is limited separately below.
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
MAX_PHOTO_BYTES = 8 * 1024 * 1024
JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"

_bot_lock = threading.Lock()
_bot_thread = None
_bot_loop = None
_bot_application = None
_bot_state = {"status": "not_started", "error": None}


def _set_bot_state(status, error=None):
    with _bot_lock:
        _bot_state["status"] = status
        _bot_state["error"] = error


def get_bot_status():
    with _bot_lock:
        return dict(_bot_state)


def _run_bot():
    """Run python-telegram-bot on a dedicated asyncio event loop."""
    global _bot_loop, _bot_application

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with _bot_lock:
        _bot_loop = loop

    application = None
    initialized = False
    started = False
    failure = None
    try:
        from bot import get_bot_app

        application = get_bot_app()
        loop.run_until_complete(application.initialize())
        initialized = True

        if application.updater is None:
            raise RuntimeError("Telegram polling is unavailable in this bot app")
        # Follow PTB's lifecycle order: initialize, start polling, then start
        # the update-processing application.
        loop.run_until_complete(
            application.updater.start_polling(drop_pending_updates=False)
        )
        loop.run_until_complete(application.start())
        started = True

        with _bot_lock:
            _bot_application = application
            _bot_state["status"] = "running"
            _bot_state["error"] = None
        logger.info("Telegram bot polling started")
        loop.run_forever()
    except Exception as exc:
        failure = exc
        _set_bot_state("failed", type(exc).__name__)
        # Telegram API URLs contain the bot token; log only the exception type.
        logger.error("Telegram bot failed to start or stopped (%s)", type(exc).__name__)
    finally:
        if application is not None:
            async def shutdown():
                if application.updater and application.updater.running:
                    await application.updater.stop()
                if application.running:
                    await application.stop()
                if initialized:
                    await application.shutdown()

            try:
                loop.run_until_complete(shutdown())
            except Exception as exc:
                logger.error("Error while shutting down Telegram bot (%s)", type(exc).__name__)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            logger.exception("Error while closing the Telegram event loop")
        loop.close()
        asyncio.set_event_loop(None)

        with _bot_lock:
            if _bot_loop is loop:
                _bot_loop = None
            if _bot_application is application:
                _bot_application = None
            if failure is None and _bot_state["status"] != "failed":
                _bot_state["status"] = "stopped"
                _bot_state["error"] = None


def start_bot_background():
    """Start exactly one Telegram polling thread in this web worker."""
    global _bot_thread

    if not os.environ.get("BOT_TOKEN", "").strip():
        _set_bot_state("not_configured", "BOT_TOKEN is missing")
        logger.error("BOT_TOKEN is missing; the website will run without the bot")
        return False

    with _bot_lock:
        if _bot_thread is not None and _bot_thread.is_alive():
            return True
        _bot_state["status"] = "starting"
        _bot_state["error"] = None
        _bot_thread = threading.Thread(
            target=_run_bot,
            name="telegram-polling",
            daemon=True,
        )
        thread = _bot_thread

    try:
        thread.start()
        return True
    except RuntimeError as exc:
        _set_bot_state("failed", type(exc).__name__)
        logger.exception("Could not start Telegram bot thread")
        return False


def stop_bot_background():
    """Ask the bot loop to shut down cleanly (useful for local runs/tests)."""
    with _bot_lock:
        loop = _bot_loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)


def send_photo_to_telegram(chat_id, photo_bytes):
    """Send a visitor-approved JPEG to the Telegram chat that made the link."""
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        logger.error("Cannot send a photo: BOT_TOKEN is not configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    caption = (
        "📸 Someone chose to take and send this photo from your shared link."
    )
    try:
        response = requests.post(
            url,
            data={"chat_id": str(chat_id), "caption": caption},
            files={
                "photo": ("shared-camera-photo.jpg", BytesIO(photo_bytes), "image/jpeg")
            },
            timeout=(5, 30),
        )
        try:
            result = response.json()
        except ValueError:
            result = {}

        if response.ok and result.get("ok") is True:
            return True

        logger.warning(
            "Telegram rejected sendPhoto (HTTP %s): %s",
            response.status_code,
            str(result.get("description", "no API description"))[:300],
        )
        return False
    except requests.RequestException as exc:
        # Do not log the request URL: Telegram bot URLs contain the bot token.
        logger.error("Telegram sendPhoto request failed (%s)", type(exc).__name__)
        return False


@app.after_request
def add_privacy_and_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
    )
    # Shared images and camera results should not be stored by intermediary caches.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Photo is too large. Please try a smaller image."}), 413


@app.route("/")
def home():
    bot_username = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")
    return render_template("home.html", bot_username=bot_username)


@app.route("/view/<link_id>")
def view_photo(link_id):
    """Show the submitted image and the optional, explicit-consent camera UI."""
    link_data = get_link(link_id)
    if not link_data:
        return render_template(
            "error.html",
            title="Link not found",
            message="This link is invalid or may have expired.",
        ), 404
    if not link_data.get("is_active", True):
        return render_template(
            "error.html",
            title="Link unavailable",
            message="The person who created this link has deactivated it.",
        ), 410

    return render_template(
        "viewer.html",
        link_id=link_id,
        image_data=link_data.get("image_url", ""),
        custom_message=link_data.get("custom_message") or "A photo was shared with you.",
    )


@app.route("/api/capture", methods=["POST"])
def capture_photo():
    """Accept one JPEG only after the visitor explicitly chooses Send."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A valid JSON request is required."}), 400

    link_id = data.get("link_id")
    photo_data = data.get("photo")
    if not isinstance(link_id, str) or not link_id or len(link_id) > 80:
        return jsonify({"error": "Invalid link."}), 400
    if not isinstance(photo_data, str) or not photo_data.startswith(
        JPEG_DATA_URL_PREFIX
    ):
        return jsonify({"error": "A JPEG photo is required."}), 400

    link_data = get_link(link_id)
    if not link_data:
        return jsonify({"error": "Link not found."}), 404
    if not link_data.get("is_active", True):
        return jsonify({"error": "This link is no longer active."}), 410

    encoded_photo = photo_data[len(JPEG_DATA_URL_PREFIX) :]
    if not encoded_photo or len(encoded_photo) > ((MAX_PHOTO_BYTES + 2) // 3) * 4 + 8:
        return jsonify({"error": "Photo is too large."}), 413
    try:
        photo_bytes = base64.b64decode(encoded_photo, validate=True)
    except (ValueError, base64.binascii.Error):
        return jsonify({"error": "The photo data is invalid."}), 400

    if not photo_bytes.startswith(b"\xff\xd8\xff"):
        return jsonify({"error": "The uploaded data is not a JPEG photo."}), 400
    if len(photo_bytes) > MAX_PHOTO_BYTES:
        return jsonify({"error": "Photo is too large."}), 413
    if not link_data.get("chat_id"):
        logger.error("Link %s has no Telegram destination", link_id)
        return jsonify({"error": "This link cannot receive photos right now."}), 500

    if not send_photo_to_telegram(link_data["chat_id"], photo_bytes):
        return jsonify(
            {"error": "Telegram could not deliver the photo. Please try again later."}
        ), 502

    try:
        # Store only an aggregate count; no visitor metadata is kept.
        add_visitor(link_id)
    except Exception:
        # Delivery already succeeded; don't encourage a repeat send just because
        # updating the local statistics failed.
        logger.exception("Photo was sent but its link statistics could not be saved")

    return jsonify({"status": "sent"}), 200


@app.route("/health")
def health():
    """Render health check. The web service stays healthy if polling is missing."""
    status = get_bot_status()
    return jsonify(
        {
            "status": "healthy",
            "telegram_bot": status["status"],
        }
    ), 200


if __name__ == "__main__":
    # Local development entry point. Render uses Gunicorn and gunicorn.conf.py.
    start_bot_background()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

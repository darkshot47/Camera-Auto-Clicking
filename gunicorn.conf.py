"""Render/Gunicorn configuration: one worker owns Telegram long polling."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
worker_class = "gthread"
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
accesslog = None
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()


def post_worker_init(worker):
    # Start after Gunicorn forks the single web worker. Starting polling in the
    # master or in every web worker causes duplicate Telegram getUpdates calls.
    from server import start_bot_background

    start_bot_background()
    worker.log.info("Web worker ready; Telegram bot startup requested")

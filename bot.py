"""Telegram bot handlers for creating camera-demo links.

The linked web page always explains where a voluntarily captured photo will go.
It never opens a camera or captures/sends anything without the visitor's action.
"""

import base64
import logging
import os
from urllib.parse import urlparse

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import (
    create_link,
    deactivate_link,
    get_link,
    get_user_links,
)

logger = logging.getLogger(__name__)

WAITING_IMAGE = 1
CAMERA_MODE_BUTTON = "📸 Camera Mode"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PHOTO_FILTER = filters.PHOTO | filters.Document.IMAGE


def get_website_url():
    """Use the configured public HTTPS URL (Render's URL is a fallback)."""
    value = (
        os.environ.get("WEBSITE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or ""
    ).strip().rstrip("/")
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        return None
    return value


def _main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(CAMERA_MODE_BUTTON)],
            [KeyboardButton("📋 My Links"), KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the bot's camera-mode option and explain the consent-based flow."""
    welcome_text = (
        "Welcome to Photo Link Bot!\n\n"
        "📸 Camera Mode creates a link that displays your photo. The person who "
        "opens it will see a clear notice before camera access is requested. "
        "They must choose to enable the camera, take a photo, and press Send.\n\n"
        "Nothing is captured or uploaded automatically. If they choose to send "
        "a photo, it is delivered to this Telegram chat.\n\n"
        "Choose an option below:"
    )
    await update.effective_message.reply_text(
        welcome_text, reply_markup=_main_keyboard()
    )


async def create_photo_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Ask the Telegram user to upload the photo shown on the share page."""
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "Please open a private chat with me to create a link. Any approved "
            "camera photos are delivered only to the account that created it."
        )
        return ConversationHandler.END

    if not get_website_url():
        await update.effective_message.reply_text(
            "Setup issue: set WEBSITE_URL in Render to your public service URL "
            "(for example, https://your-service.onrender.com)."
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "📸 Camera Mode\n\n"
        "Send the photo you want to show on the link. As soon as I receive it, "
        "I will create your link.\n\n"
        "The page tells visitors that any camera photo they choose to send "
        "will be delivered to the Telegram account that created the link. "
        "They stay in control: camera access, taking a photo, and sending it "
        "are all separate, optional actions.\n\n"
        "Send a photo now, or use /cancel to stop."
    )
    return WAITING_IMAGE


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store an uploaded Telegram photo and immediately return its unique link."""
    message = update.effective_message
    attachment = None

    if message.photo:
        attachment = message.photo[-1]
    elif message.document and (message.document.mime_type or "").startswith(
        "image/"
    ):
        attachment = message.document

    if attachment is None:
        await message.reply_text("Please send an image as a photo or image file.")
        return WAITING_IMAGE
    if attachment.file_size and attachment.file_size > MAX_IMAGE_BYTES:
        await message.reply_text(
            "That image is too large. Please send an image smaller than 8 MB."
        )
        return WAITING_IMAGE

    try:
        telegram_file = await context.bot.get_file(attachment.file_id)
        photo_bytes = bytes(await telegram_file.download_as_bytearray())
        if not photo_bytes:
            raise ValueError("The uploaded image was empty")
        if len(photo_bytes) > MAX_IMAGE_BYTES:
            await message.reply_text(
                "That image is too large. Please send an image smaller than 8 MB."
            )
            return WAITING_IMAGE

        # Telegram photos are re-encoded by Telegram. For document uploads keep
        # the original bytes as received; the page displays this data URL as JPEG
        # because standard Telegram photo uploads are JPEGs. Normalize other
        # document image types to JPEG is not available without an image library,
        # so reject non-JPEG documents rather than serving a broken preview.
        is_jpeg = photo_bytes.startswith(b"\xff\xd8\xff")
        if not is_jpeg:
            await message.reply_text(
                "Please resend this as a Telegram photo (not as a file), so the "
                "preview can be displayed correctly."
            )
            return WAITING_IMAGE

        image_base64 = base64.b64encode(photo_bytes).decode("ascii")
        custom_message = (message.caption or "").strip() or "A photo was shared with you."
        link_id = create_link(
            chat_id=update.effective_chat.id,
            image_url=image_base64,
            custom_message=custom_message,
        )
        full_url = f"{get_website_url()}/view/{link_id}"

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔗 Open link", url=full_url)],
                [
                    InlineKeyboardButton(
                        "📊 Link stats", callback_data=f"stats_{link_id}"
                    ),
                    InlineKeyboardButton(
                        "🗑️ Delete link", callback_data=f"delete_{link_id}"
                    ),
                ],
            ]
        )
        await message.reply_text(
            "✅ Photo received — your link is ready.\n\n"
            f"🔗 {full_url}\n\n"
            "The page clearly asks the visitor before camera access. They must "
            "manually take and send a photo; nothing happens automatically.",
            reply_markup=keyboard,
        )
        return ConversationHandler.END
    except Exception as exc:
        # Do not log the exception text: Telegram file/API errors can include
        # sensitive request details.
        logger.error("Could not create a photo link (%s)", type(exc).__name__)
        await message.reply_text(
            "Sorry, I couldn't create the link just now. Please try again with "
            "another photo, or use /cancel."
        )
        return WAITING_IMAGE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel link creation."""
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Cancelled. Send /start when you want to try again."
    )
    return ConversationHandler.END


async def my_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the current Telegram user's links."""
    chat_id = update.effective_chat.id
    links = get_user_links(chat_id)
    if not links:
        await update.effective_message.reply_text(
            "You don't have any links yet. Tap 📸 Camera Mode to create one."
        )
        return

    lines = ["📋 Your links:"]
    base_url = get_website_url()
    if not base_url:
        await update.effective_message.reply_text(
            "WEBSITE_URL is not configured, so links cannot be displayed."
        )
        return

    for link_id, data in links.items():
        status = "Active" if data["is_active"] else "Inactive"
        lines.extend(
            [
                f"\n🔗 {base_url}/view/{link_id}",
                f"Status: {status}",
                f"Photos voluntarily sent: {data['photos_received']}",
                f"Created: {data['created_at'][:10]}",
            ]
        )
    await update.effective_message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain bot commands and the explicit camera-sharing workflow."""
    await update.effective_message.reply_text(
        "ℹ️ Help\n\n"
        "/start — show the menu\n"
        "/create — create a photo link\n"
        "/mylinks — list your links\n"
        "/help — show this help\n"
        "/cancel — cancel photo upload\n\n"
        "How it works: choose Camera Mode, send a photo, and receive a link. "
        "The link displays your photo and explains to visitors that camera use "
        "is optional. A visitor must enable the camera, take a picture, review "
        "it, and press Send before anything is delivered to your Telegram chat. "
        "There is no automatic capture or upload."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle link stats/delete buttons, restricted to the link owner."""
    query = update.callback_query
    data = query.data or ""

    if data.startswith(("stats_", "delete_")):
        prefix = "stats_" if data.startswith("stats_") else "delete_"
        link_id = data.removeprefix(prefix)
        link_data = get_link(link_id)
        if not link_data or str(query.from_user.id) != str(link_data["chat_id"]):
            await query.answer("This link does not belong to you.", show_alert=True)
            return

        await query.answer()
        if prefix == "stats_":
            await query.edit_message_text(
                f"📊 Link stats\n\n"
                f"Photos voluntarily sent: {link_data['photos_received']}\n"
                f"Created: {link_data['created_at'][:10]}\n"
                f"Active: {'Yes' if link_data['is_active'] else 'No'}"
            )
        elif deactivate_link(link_id):
            await query.edit_message_text("🗑️ This link has been deactivated.")
    else:
        await query.answer()


async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the non-conversation reply-keyboard buttons."""
    text = update.effective_message.text
    if text == "📋 My Links":
        await my_links(update, context)
    elif text == "ℹ️ Help":
        await help_command(update, context)


def setup_bot():
    """Build and return the python-telegram-bot application."""
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")

    application = Application.builder().token(token).build()
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("create", create_photo_link),
            MessageHandler(
                filters.Regex(r"^📸 Camera Mode$"), create_photo_link
            ),
        ],
        states={
            WAITING_IMAGE: [
                MessageHandler(PHOTO_FILTER, receive_image),
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_image),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("mylinks", my_links))
    application.add_handler(conversation)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^(📋 My Links|ℹ️ Help)$"), handle_text_buttons
        )
    )
    return application


def get_bot_app():
    """Compatibility entry point used by the Render server process."""
    return setup_bot()

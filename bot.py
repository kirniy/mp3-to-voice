import logging
import os
import sys  # Added for exit
import tempfile
import io # Added
from functools import wraps
import asyncpg # Added
import google.generativeai as genai # Added
import pytz # Added
from datetime import datetime # Added
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # Added
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.constants import ChatAction, ParseMode # Added ParseMode
from telegram.helpers import escape_markdown as telegram_escape_markdown # Added for V2, renamed to avoid confusion

from pydub import AudioSegment
from locales import get_dual_string, LANGUAGES, get_string
from db_utils import create_tables, save_summary, get_summary_context_for_callback, update_summary_mode_and_text, get_user_history, get_chat_default_mode, set_chat_default_mode, get_user_language, set_user_language, get_chat_language, set_chat_language, get_chat_paused_status, delete_chat_history, get_all_chat_history # Added get_user_history
from gemini_utils import process_audio_with_gemini, DEFAULT_MODE, SUPPORTED_MODES, get_mode_name # Added get_mode_name
# Import diagram utils
from diagram_utils import generate_diagram_data, create_mermaid_syntax, render_mermaid_to_png

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Constants ---
HISTORY_PAGE_SIZE = 1 # Show one history item at a time

# --- Helper Functions ---

def send_typing_action(func):
    """Sends typing action while processing."""
    @wraps(func)
    async def command_func(update: Update, context: CallbackContext, *args, **kwargs):
        if update.effective_message:
            # Changed to TYPING as a general processing indicator
            await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        return await func(update, context, *args, **kwargs)
    return command_func

def protect_markdown(text):
    """Replace Markdown syntax with placeholders to protect during processing."""
    if not text:
        return ""
    
    logger.debug(f"Protecting markdown for text of length {len(text)}")
    
    # Code blocks
    code_blocks_count = len(re.findall(r'```(\w+)?\n(.*?)\n```', text, flags=re.DOTALL))
    text = re.sub(r'```(\w+)?\n(.*?)\n```', lambda m: f'§CODE§{m.group(1) or ""}\n{m.group(2)}§CODE§', text, flags=re.DOTALL)
    logger.debug(f"Protected {code_blocks_count} code blocks")
    
    # Inline code
    inline_code_count = len(re.findall(r'`([^`]+)`', text))
    text = re.sub(r'`([^`]+)`', r'§INLINE_CODE§\1§INLINE_CODE§', text)
    logger.debug(f"Protected {inline_code_count} inline code segments")
    
    # Bold text
    bold_count = len(re.findall(r'\*\*([^*]+)\*\*', text))
    text = re.sub(r'\*\*([^*]+)\*\*', r'§BOLD§\1§BOLD§', text)
    logger.debug(f"Protected {bold_count} bold segments")
    
    # Italic text
    italic_count = len(re.findall(r'\_([^_]+)\_', text))
    text = re.sub(r'\_([^_]+)\_', r'§ITALIC§\1§ITALIC§', text)
    logger.debug(f"Protected {italic_count} italic segments")
    
    # Links
    links_count = len(re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', text))
    text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'§LINK§\1§URL§\2§LINK§', text)
    logger.debug(f"Protected {links_count} links")
    
    return text

def unprotect_markdown(text):
    """Restore markdown placeholders to actual syntax."""
    if not text:
        return ""
    
    logger.debug("Restoring markdown placeholders")
    
    # Code blocks
    code_blocks_count = len(re.findall(r'§CODE§(\w*)\n(.*?)§CODE§', text, flags=re.DOTALL))
    text = re.sub(r'§CODE§(\w*)\n(.*?)§CODE§',
                 lambda m: f"```{m.group(1)}\n{m.group(2)}\n```",
                 text, flags=re.DOTALL)
    logger.debug(f"Restored {code_blocks_count} code blocks")
    
    # Inline code
    inline_code_count = text.count('§INLINE_CODE§') // 2
    text = text.replace('§INLINE_CODE§', '`')
    logger.debug(f"Restored {inline_code_count} inline code segments")
    
    # Bold text
    bold_count = text.count('§BOLD§') // 2
    text = text.replace('§BOLD§', '*')
    logger.debug(f"Restored {bold_count} bold segments")
    
    # Italic text
    italic_count = text.count('§ITALIC§') // 2
    text = text.replace('§ITALIC§', '_')
    logger.debug(f"Restored {italic_count} italic segments")
    
    # Links
    links_count = len(re.findall(r'§LINK§([^§]+)§URL§([^§]+)§LINK§', text))
    text = re.sub(r'§LINK§([^§]+)§URL§([^§]+)§LINK§', r'[\1](\2)', text)
    logger.debug(f"Restored {links_count} links")
    
    return text

def format_for_telegram(text):
    """Format text for Telegram ensuring proper Markdown support."""
    if not text:
        logger.warning("Empty text received for formatting")
        return ""
    
    logger.debug(f"Formatting for Telegram: text of length {len(text)}")
    
    # Convert bullet points for consistency
    bullet_count = len(re.findall(r'^\s*[\-\*]\s+', text, flags=re.MULTILINE))
    text = re.sub(r'^\s*[\-\*]\s+', '• ', text, flags=re.MULTILINE)
    logger.debug(f"Converted {bullet_count} bullet points")
    
    # First protect all markdown formatting
    text = protect_markdown(text)
    
    # Escape special characters
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    lines = text.split('\n')
    processed_lines = []
    total_escapes = 0
    
    for line in lines:
        if not line.startswith('```') and not line.endswith('```'):
            for char in special_chars:
                char_count = line.count(char)
                line = line.replace(char, fr'\{char}')
                total_escapes += char_count
        processed_lines.append(line)
    
    text = '\n'.join(processed_lines)
    logger.debug(f"Escaped {total_escapes} special characters")
    
    # Restore markdown formatting
    text = unprotect_markdown(text)
    
    # Normalize line breaks
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Clean up paragraphs
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    text = '\n\n'.join(paragraphs)
    
    # Fix any double escapes that might have occurred
    double_escape_count = len(re.findall(r'\\\\([_*\[\]()~`>#+=|{}.!])', text))
    text = re.sub(r'\\\\([_*\[\]()~`>#+=|{}.!])', r'\\\1', text)
    logger.debug(f"Fixed {double_escape_count} double escapes")
    
    return text

def escape_markdown(text, version=2, entity_type=None):
    """
    Enhanced function to escape telegram markup symbols while preserving formatting.
    This replaces the standard escape_markdown to handle complex formatting properly.
    """
    if not text:
        return text
    
    # For simple escaping without any formatting preservation, use telegram's function
    if version == 1 or (entity_type in ['pre', 'code', 'text_link']):
        return telegram_escape_markdown(text, version, entity_type)
    
    # For everything else, use our sophisticated formatter
    return format_for_telegram(text)

def escape_markdown_preserve_formatting(text):
    """
    Legacy function for backward compatibility.
    Now using the more advanced format_for_telegram function internally.
    """
    return format_for_telegram(text)

def create_action_buttons(original_msg_id: int, language: str = 'ru') -> InlineKeyboardMarkup:
    """Creates the action buttons for voice message responses."""
    # Localize button labels
    mode_label = "👤 Режим"
    redo_label = "🔁 Заново" 
    settings_label = "⚙️ Настройки" # Use the gear icon
    done_label = "❎ Готово"
    
    if language == 'en':
        mode_label = "👤 Mode"
        redo_label = "🔁 Redo"
        settings_label = "⚙️ Settings" # Use the gear icon
        done_label = "❎ Done"
    elif language == 'kk':
        mode_label = "👤 Режим"
        redo_label = "🔁 Қайта"
        settings_label = "⚙️ Параметрлер" # Use the gear icon
        done_label = "❎ Дайын"
        
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(mode_label, callback_data=f"mode_select:{original_msg_id}"),
            InlineKeyboardButton(redo_label, callback_data=f"redo:{original_msg_id}"),
        ],
        [
            InlineKeyboardButton(settings_label, callback_data=f"settings:{original_msg_id}"), # Include original_msg_id
            InlineKeyboardButton(done_label, callback_data=f"confirm:{original_msg_id}"),
        ]
    ])

# --- History Formatting Helpers ---

def create_voice_settings_buttons(original_msg_id: int, language: str = 'ru') -> InlineKeyboardMarkup:
    """Creates the settings buttons for voice message responses."""
    # Localize button labels
    language_label = "🌐 Язык"
    history_label = "📚 История"
    mode_label = "⚙️ Режим"
    subscription_label = "💰 Подписка"
    back_label = "⬅️ Назад"
    
    if language == 'en':
        language_label = "🌐 Language"
        history_label = "📚 History"
        mode_label = "⚙️ Mode"
        subscription_label = "💰 Subscription"
        back_label = "⬅️ Back"
    elif language == 'kk':
        language_label = "🌐 Тіл"
        history_label = "📚 Тарих"
        mode_label = "⚙️ Режим"
        subscription_label = "💰 Жазылым"
        back_label = "⬅️ Артқа"
        
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(language_label, callback_data=f"voice_language_menu:{original_msg_id}"),
            InlineKeyboardButton(mode_label, callback_data=f"mode_select:{original_msg_id}"),
        ],
        [
            InlineKeyboardButton(history_label, callback_data=f"history:{original_msg_id}:0"),
            InlineKeyboardButton(subscription_label, callback_data=f"voice_subscription_info:{original_msg_id}"),
        ],
        [
            InlineKeyboardButton(back_label, callback_data=f"back_to_main:{original_msg_id}")
        ]
    ])

def format_history_message(record: asyncpg.Record, current_index: int, total_count: int, language: str = 'ru', author_name: str = "Unknown User") -> str:
    """Formats a single history record for display using MarkdownV2."""
    # Safely get text, defaulting to an empty string if both are None
    summary = record.get('summary_text', None)
    transcript = record.get('transcript_text', None)
    text_to_display = summary if summary is not None else transcript if transcript is not None else "" 

    # Safely get mode, defaulting to a placeholder string if None or invalid
    mode_key = record.get('mode', 'unknown')
    # Ensure mode_key is a string before using it in .get()
    if not isinstance(mode_key, str):
        mode_key = 'unknown'
    # Get localized mode name using the utility function
    localized_mode_name = get_mode_name(mode_key, language) 
    # Ensure mode_display is a string before escaping
    if not isinstance(localized_mode_name, str):
        localized_mode_name = str(localized_mode_name) 

    created_at_utc = record['created_at']
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    created_at_moscow = created_at_utc.astimezone(moscow_tz) if created_at_utc else None # Handle None created_at
    # Ensure time_str is generated safely even if created_at is somehow None
    time_str = escape_markdown(created_at_moscow.strftime('%d.%m.%Y %H:%M МСК'), version=2) if created_at_moscow else "(no date)"
    escaped_mode = escape_markdown(localized_mode_name, version=2)
    escaped_author = escape_markdown(author_name, version=2)
    
    # Localized header
    header_text = f"История \({current_index}/{total_count}\)"
    if language == 'en':
        header_text = f"History \({current_index}/{total_count}\)"
    elif language == 'kk':
        header_text = f"Тарих \({current_index}/{total_count}\)"
    
    # Use MarkdownV2 formatting - Bold for heading, italic for mode and author
    header = f"*{header_text}* \| _{escaped_mode}_ \| _{escaped_author}_ \| {time_str}"
    
    # Content with proper formatting preservation
    empty_text = "(пусто)"
    if language == 'en':
        empty_text = "(empty)"
    elif language == 'kk':
        empty_text = "(бос)"
    
    # Use empty_text if text_to_display ended up being empty after the initial check
    escaped_text = escape_markdown_preserve_formatting(text_to_display if text_to_display else empty_text)
    
    # Don't use code block to allow formatting to be visible
    return f"{header}\n\n{escaped_text}"

def create_history_pagination_buttons(original_msg_id: int, current_offset: int, total_count: int, page_size: int, language: str = 'ru') -> InlineKeyboardMarkup | None:
    """Creates buttons for history pagination, including delete and export."""
    if total_count <= 0:
        # Still provide delete/export options even if history is empty
        pass # Continue to create buttons

    # Since page_size is 1, current_offset is the index
    current_index = current_offset 
    
    buttons = []
    nav_row = []
    
    # Previous page button (if not the first item)
    if current_index > 0:
        prev_offset = current_index - 1
        prev_label = "⬅️"
        nav_row.append(InlineKeyboardButton(prev_label, callback_data=f"history_nav:{original_msg_id}:{prev_offset}"))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    # Center button to return to settings menu (localize label later if needed)
    back_to_settings_label = "⚙️ Настройки"
    if language == 'en':
        back_to_settings_label = "⚙️ Settings"
    elif language == 'kk':
        back_to_settings_label = "⚙️ Параметрлер"
    # Ensure callback_data is just "settings", not dependent on msg_id
    nav_row.append(InlineKeyboardButton(back_to_settings_label, callback_data="settings")) 
    
    # Next page button (if not the last item)
    if total_count > 0 and current_index < total_count - 1:
        next_offset = current_index + 1
        next_label = "➡️"
        nav_row.append(InlineKeyboardButton(next_label, callback_data=f"history_nav:{original_msg_id}:{next_offset}"))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    buttons.append(nav_row)

    # Add Delete and Export buttons in a new row
    action_row = []
    delete_label = get_string('history_delete', language)
    export_label = get_string('history_export', language)
    
    # Note: Using original_msg_id in callback data for context, though not strictly necessary for the action itself
    action_row.append(InlineKeyboardButton(delete_label, callback_data=f"delete_history_confirm:{original_msg_id}:{current_offset}")) # Include offset for cancel
    action_row.append(InlineKeyboardButton(export_label, callback_data=f"export_history:{original_msg_id}"))
    buttons.append(action_row)
    
    return InlineKeyboardMarkup(buttons)

# --- Mode Selection and Handling ---

async def show_mode_selection(update: Update, context: CallbackContext, original_msg_id: int):
    """Shows a keyboard with mode selection options."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        await query.answer("Database error", show_alert=True)
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Create mode selection keyboard
    keyboard = []
    
    # Add appropriate emojis for each mode
    mode_emojis = {
        "brief": "📝",
        "detailed": "📋",
        "bullet": "🔍",
        "combined": "📊",
        "as_is": "📄",
        "pasha": "💊",
        "diagram": "📈" # Added diagram emoji
    }
    
    # Define the order of modes
    mode_order = ["as_is", "brief", "detailed", "bullet", "combined", "diagram", "pasha"]
    
    # Get current default mode
    current_default_mode = DEFAULT_MODE
    try:
        current_default_mode = await get_chat_default_mode(pool, chat_id, DEFAULT_MODE)
    except Exception as e:
        logger.error(f"Error getting default mode: {e}")
    
    # Localized button texts
    pin_label = "📌 Закрепить"
    cancel_label = "❌ Отмена"
    
    if chat_lang == 'en':
        pin_label = "📌 Pin"
        cancel_label = "❌ Cancel"
    elif chat_lang == 'kk':
        pin_label = "📌 Бекіту"
        cancel_label = "❌ Болдырмау"
    
    # Add each mode selection button
    for mode_key in mode_order:
        if mode_key in SUPPORTED_MODES:
            emoji = mode_emojis.get(mode_key, "")
            # Get localized mode name
            mode_name = get_mode_name(mode_key, chat_lang)
            
            # Add indicator if this is the default mode
            if mode_key == current_default_mode:
                mode_name = f"{mode_name} ★"
            
            # Mode selection button
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {mode_name}", 
                    callback_data=f"mode_set:{original_msg_id}:{mode_key}"
                )
            ])
    
    # Add pin and cancel buttons
    bottom_row = []
    bottom_row.append(InlineKeyboardButton(
        pin_label, 
        callback_data=f"show_pin_menu:{original_msg_id}"
    ))
    bottom_row.append(InlineKeyboardButton(
        cancel_label, 
        callback_data=f"cancel_mode_select:{original_msg_id}"
    ))
    keyboard.append(bottom_row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        logger.info(f"Showed mode selection for message {original_msg_id} in language {chat_lang}")
    except Exception as e:
        logger.error(f"Error showing mode selection: {e}", exc_info=True)
        
        # Error message based on chat language
        error_message = "Error showing mode options"
        if chat_lang == 'ru':
            error_message = "Ошибка при отображении опций режима"
        elif chat_lang == 'kk':
            error_message = "Режим опцияларын көрсету кезінде қате"
            
        await query.answer(error_message, show_alert=True)

async def mode_set(update: Update, context: CallbackContext, data_parts: list, original_msg_id: int):
    """Handles mode selection and updates the summary/diagram."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool: 
        logger.error("Database pool not found...")
        await query.answer("Database error...", show_alert=True)
        return
    if len(data_parts) < 3:
        logger.error(f"Invalid mode_set data...")
        await query.answer("Invalid mode data...", show_alert=True)
        return
    new_mode = data_parts[2]
    if new_mode not in SUPPORTED_MODES:
        logger.error(f"Unsupported mode: {new_mode}")
        await query.answer("Unsupported mode...", show_alert=True)
        return
    chat_lang = await get_chat_language(pool, chat_id)
    
    localized_mode_name = get_mode_name(new_mode, chat_lang)
    switching_msg = f"⏳ Switching to '{localized_mode_name}'..." # Simplified example
    await query.edit_message_text(switching_msg, reply_markup=None)

    try:
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        
        if not db_record:
            logger.error(f"Record not found...")
            error_buttons = create_action_buttons(original_msg_id, chat_lang)
            await query.edit_message_text(get_string('error_record_not_found', chat_lang), reply_markup=error_buttons)
            return
        
        record_id = db_record['id']
        audio_file_id = db_record['telegram_audio_file_id']
        user_id = db_record['user_id']
        transcript_text = db_record['transcript_text']
        
        original_user = await context.bot.get_chat(user_id)
        original_message_date = query.message.date
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        reply_markup = create_action_buttons(original_msg_id, chat_lang)

        # --- Handle Diagram Mode --- 
        if new_mode == 'diagram':
            logger.info(f"Switching to diagram mode...")
            if not transcript_text:
                 await query.edit_message_text(get_string('error', chat_lang), reply_markup=reply_markup)
                 return
            diagram_data = await generate_diagram_data(transcript_text, chat_lang)
            if not diagram_data:
                 await query.edit_message_text(f"{header}\n\n{escape_markdown_preserve_formatting(get_string('diagram_error_data', chat_lang))}", reply_markup=reply_markup, parse_mode='MarkdownV2')
                 return
            mermaid_syntax = create_mermaid_syntax(diagram_data)
            if not mermaid_syntax:
                 await query.edit_message_text(f"{header}\n\n{escape_markdown_preserve_formatting(get_string('diagram_error_syntax', chat_lang))}", reply_markup=reply_markup, parse_mode='MarkdownV2')
                 return
            png_bytes = render_mermaid_to_png(mermaid_syntax)
            if not png_bytes:
                 await query.edit_message_text(f"{header}\n\n{escape_markdown_preserve_formatting(get_string('diagram_error_render', chat_lang))}", reply_markup=reply_markup, parse_mode='MarkdownV2')
                 return
            try:
                await query.message.delete()
                sent_message = await context.bot.send_photo(chat_id=chat_id, photo=png_bytes, caption=header, parse_mode='MarkdownV2', reply_markup=reply_markup, reply_to_message_id=original_msg_id)
                logger.info(f"Sent new diagram message...")
                await update_summary_mode_and_text(pool=pool, record_id=record_id, new_mode=new_mode, new_summary_text=mermaid_syntax, new_transcript_text=transcript_text, new_summary_message_id=sent_message.message_id)
            except Exception as send_photo_e:
                logger.error(f"Failed to send diagram photo...: {send_photo_e}", exc_info=True)
                await context.bot.send_message(chat_id, get_string('error', chat_lang), reply_to_message_id=original_msg_id)
            # No return here, falls through to the end of the main try block

        # --- Handle Text Modes --- 
        else: 
            summary_text = None
            with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
                 file = await context.bot.get_file(audio_file_id)
                 await file.download_to_drive(custom_path=temp_audio_file.name)
                 logger.info(f"Re-downloaded audio for mode change to {new_mode}.")
                 summary_text, new_transcript = await process_audio_with_gemini(temp_audio_file.name, new_mode, chat_lang)
                 if new_transcript:
                     transcript_text = new_transcript
            if new_mode == 'as_is' or new_mode == 'transcript':
                 display_text = transcript_text
            else:
                 if not summary_text:
                     await query.edit_message_text(get_string('error_generating_summary', chat_lang), reply_markup=reply_markup)
                     return 
                 display_text = summary_text
            escaped_display_text = escape_markdown_preserve_formatting(display_text)
            final_text = f"{header}\n\n{escaped_display_text}"
            try:
                 await query.edit_message_text(final_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
                 await update_summary_mode_and_text(pool=pool, record_id=record_id, new_mode=new_mode, new_summary_text=summary_text, new_transcript_text=transcript_text, new_summary_message_id=query.message.message_id)
                 logger.info(f"Successfully updated summary to mode {new_mode}...")
            except Exception as edit_text_e:
                  logger.error(f"Failed to edit text message...: {edit_text_e}", exc_info=True)
                  try:
                       await query.edit_message_text(get_string('error', chat_lang), reply_markup=reply_markup)
                  except Exception: 
                       pass
            # No return here, falls through to the end of the main try block

    except Exception as e:
        logger.error(f"Error in mode_set (mode: {new_mode}): {e}", exc_info=True)
        try:
            # General fallback error message edit
            error_buttons = create_action_buttons(original_msg_id, chat_lang)
            await query.edit_message_text(get_string('error', chat_lang), reply_markup=error_buttons)
        except Exception as final_edit_e:
            logger.error(f"Failed to edit message after main error in mode_set: {final_edit_e}")

async def post_init(application: Application) -> None:
    """Create DB pool and tables after initialization."""
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        logger.error("DATABASE_URL environment variable not set.")
        sys.exit(1)
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        application.bot_data['db_pool'] = pool
        logger.info("Database pool created successfully.")
        # Ensure tables exist
        await create_tables(pool)
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)


async def pre_shutdown(application: Application) -> None:
    """Close DB pool before shutdown."""
    pool = application.bot_data.get('db_pool')
    if pool:
        await pool.close()
        logger.info("Database pool closed.")


def main() -> None:
    """Start the bot."""
    # Load environment variables
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY environment variable not set.")
        sys.exit(1)
    # DATABASE_URL checked in post_init

    # Configure Gemini
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Optional: Check if model exists
        # model_check = genai.get_model('models/gemini-1.5-flash') # Example check
        logger.info("Gemini API configured successfully.")
    except Exception as e:
        logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
        sys.exit(1)


    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init) # Create DB pool after loop starts
        .post_shutdown(pre_shutdown) # Corrected: Close DB pool using post_shutdown
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("resume", resume_command))

    # Handler for MP3/WAV conversion (Document or Audio)
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))

    # Handler for Voice messages (Summarization)
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message)) # Added

    # Handler for Callback Queries
    application.add_handler(CallbackQueryHandler(button_callback)) # Added

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main() 
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
from db_utils import create_tables, save_summary, get_summary_context_for_callback, update_summary_mode_and_text, get_user_history, get_chat_default_mode, set_chat_default_mode, clear_chat_default_mode, get_user_language, set_user_language, get_chat_language, set_chat_language, get_chat_paused_status # Added get_user_history
from gemini_utils import process_audio_with_gemini, DEFAULT_MODE, SUPPORTED_MODES, get_mode_name # Added get_mode_name

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
            InlineKeyboardButton(settings_label, callback_data="settings"), # Use the correct callback_data
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

def format_history_message(record: asyncpg.Record, current_index: int, total_count: int, language: str = 'ru') -> str:
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
    mode_display = SUPPORTED_MODES.get(mode_key, mode_key) # Get display name or use the key itself
    # Ensure mode_display is a string before escaping
    if not isinstance(mode_display, str):
        mode_display = str(mode_display) 

    created_at_utc = record['created_at']
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    created_at_moscow = created_at_utc.astimezone(moscow_tz) if created_at_utc else None # Handle None created_at
    # Ensure time_str is generated safely even if created_at is somehow None
    time_str = escape_markdown(created_at_moscow.strftime('%d.%m.%Y %H:%M МСК'), version=2) if created_at_moscow else "(no date)"
    escaped_mode = escape_markdown(mode_display, version=2)
    
    # Localized header
    header_text = f"История \\({current_index}/{total_count}\\)"
    if language == 'en':
        header_text = f"History \\({current_index}/{total_count}\\)"
    elif language == 'kk':
        header_text = f"Тарих \\({current_index}/{total_count}\\)"
    
    # Use MarkdownV2 formatting - Bold for heading, italic for mode
    header = f"*{header_text}* \\| _{escaped_mode}_ \\| {time_str}"
    
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
    """Creates buttons for history pagination."""
    if total_count <= 0:
        return None
    
    current_page = (current_offset // page_size) + 1
    total_pages = (total_count + page_size - 1) // page_size
    
    buttons = []
    row = []
    
    # Previous page button
    if current_offset > 0:
        prev_offset = max(0, current_offset - page_size)
        prev_label = "⬅️ Назад"
        if language == 'en':
            prev_label = "⬅️ Back"
        elif language == 'kk':
            prev_label = "⬅️ Артқа"
        row.append(InlineKeyboardButton(prev_label, callback_data=f"history_nav:{original_msg_id}:{prev_offset}"))
    else:
        row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    # Center button to return to main menu
    back_to_menu_label = "🔍 Меню"
    if language == 'en':
        back_to_menu_label = "🔍 Menu"
    elif language == 'kk':
        back_to_menu_label = "🔍 Мәзір"
    row.append(InlineKeyboardButton(back_to_menu_label, callback_data=f"back_to_main:{original_msg_id}"))
    
    # Next page button
    if current_page < total_pages:
        next_offset = current_offset + page_size
        next_label = "➡️ Вперёд"
        if language == 'en':
            next_label = "➡️ Next"
        elif language == 'kk':
            next_label = "➡️ Алға"
        row.append(InlineKeyboardButton(next_label, callback_data=f"history_nav:{original_msg_id}:{next_offset}"))
    else:
        row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    buttons.append(row)
    
    if not buttons:
        return None
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
        "pasha": "💊"
    }
    
    # Define the order of modes
    mode_order = ["as_is", "brief", "detailed", "bullet", "combined", "pasha"]
    
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
    """Handles mode selection and updates the summary."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not found for mode_set callback")
        await query.answer("Database error / Ошибка базы данных", show_alert=True)
        return
    
    if len(data_parts) < 3:
        logger.error(f"Invalid mode_set data: {data_parts}")
        await query.answer("Invalid mode data / Недопустимые данные режима", show_alert=True)
        return
    
    new_mode = data_parts[2]
    
    if new_mode not in SUPPORTED_MODES:
        logger.error(f"Unsupported mode: {new_mode}")
        await query.answer("Unsupported mode / Неподдерживаемый режим", show_alert=True)
        return
    
    # Get chat's language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Show processing indicator with localized mode name
    localized_mode_name = get_mode_name(new_mode, chat_lang)
    
    # Localized "Switching mode" message
    switching_msg = f"⏳ Переключаю режим на '{localized_mode_name}'..."
    if chat_lang == 'en':
        switching_msg = f"⏳ Switching mode to '{localized_mode_name}'..."
    elif chat_lang == 'kk':
        switching_msg = f"⏳ '{localized_mode_name}' режиміне ауысу..."
    
    await query.edit_message_text(
        switching_msg,
        reply_markup=None
    )
    
    try:
        # Get the record from the database
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        
        if not db_record:
            logger.error(f"Record not found for message {original_msg_id}")
            await query.edit_message_text(
                "🇬🇧 Error: Could not find the record.\n\n"
                "🇷🇺 Ошибка: Не удалось найти запись.",
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
            return
        
        record_id = db_record['id']
        audio_file_id = db_record['telegram_audio_file_id']
        user_id = db_record['user_id']
        
        # Get user info for the header
        original_user = await context.bot.get_chat(user_id)
        original_message_date = query.message.date
        
        # Get existing summary for this mode if available
        # If mode is 'brief', check if summary_brief exists in db_record
        mode_field_name = f"summary_{new_mode}"
        has_existing_summary = mode_field_name in db_record and db_record[mode_field_name]
        
        summary_text = None
        transcript_text = db_record['transcript_text']
        
        # If we don't have a summary for this mode yet, process the audio again
        if not has_existing_summary:
            # Re-download the audio file
            with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
                file = await context.bot.get_file(audio_file_id)
                await file.download_to_drive(custom_path=temp_audio_file.name)
                logger.info(f"Re-downloaded audio {audio_file_id} for mode change.")
                
                # Process audio with new mode
                summary_text, new_transcript = await process_audio_with_gemini(temp_audio_file.name, new_mode, chat_lang)
                
                # Use the new transcript if we got one, otherwise keep the existing one
                if new_transcript:
                    transcript_text = new_transcript
        else:
            # Use existing summary for this mode
            summary_text = db_record[mode_field_name]
            logger.info(f"Using existing {new_mode} summary from database")
        
        if new_mode == 'as_is':
            display_text = transcript_text
        elif new_mode == 'transcript':
            display_text = transcript_text
        else:
            if not summary_text:
                logger.error(f"Failed to generate summary for mode {new_mode}")
                await query.edit_message_text(
                    "🇬🇧 Error generating summary. Please try again.\n\n"
                    "🇷🇺 Ошибка при создании сводки. Пожалуйста, попробуйте снова.",
                    reply_markup=create_action_buttons(original_msg_id, chat_lang)
                )
                return
            display_text = summary_text
        
        # Format message with normal formatting (not code block)
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        
        # Now format the display text with proper markdown
        escaped_display_text = escape_markdown_preserve_formatting(display_text)
        final_text = f"{header}\n\n{escaped_display_text}"
        
        # Update message with new summary and buttons
        await query.edit_message_text(
            final_text,
            reply_markup=create_action_buttons(original_msg_id, chat_lang),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Update database with new mode and summary
        await update_summary_mode_and_text(
            pool=pool,
            record_id=record_id,
            new_mode=new_mode,
            new_summary_text=summary_text,
            new_transcript_text=transcript_text
        )
        
        logger.info(f"Successfully updated summary to mode {new_mode} for message {original_msg_id}")
        
    except Exception as e:
        logger.error(f"Error in mode_set: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                "🇬🇧 An error occurred. Please try again.\n\n"
                "🇷🇺 Произошла ошибка. Пожалуйста, попробуйте снова.",
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
        except Exception as edit_e:
            logger.error(f"Failed to edit message after error: {edit_e}")

async def redo(update: Update, context: CallbackContext, original_msg_id: int):
    """Re-processes the voice message with the current mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not found for redo callback")
        await query.answer("Database error / Ошибка базы данных", show_alert=True)
        return
    
    # Show processing indicator
    await query.edit_message_text("⏳ Переделываю сводку...", reply_markup=None)
    
    try:
        # Get the record from the database
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        
        if not db_record:
            logger.error(f"Record not found for message {original_msg_id}")
            await query.edit_message_text(
                "🇬🇧 Error: Could not find the record.\n\n"
                "🇷🇺 Ошибка: Не удалось найти запись.",
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
            return
        
        record_id = db_record['id']
        audio_file_id = db_record['telegram_audio_file_id']
        current_mode = db_record['mode']
        user_id = db_record['user_id']
        
        # Get user info for the header
        original_user = await context.bot.get_chat(user_id)
        original_message_date = query.message.date
        
        # Re-download the audio file
        with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
            file = await context.bot.get_file(audio_file_id)
            await file.download_to_drive(custom_path=temp_audio_file.name)
            logger.info(f"Re-downloaded audio {audio_file_id} for redo.")
            
            # Process audio with current mode
            summary_text, transcript_text = await process_audio_with_gemini(temp_audio_file.name, current_mode, chat_lang)
        
        if current_mode == 'transcript':
            display_text = transcript_text
        else:
            display_text = summary_text
        
        if not display_text:
            logger.error(f"Failed to generate content for mode {current_mode}")
            await query.edit_message_text(
                "🇬🇧 Error generating content. Please try again.\n\n"
                "🇷🇺 Ошибка при создании контента. Пожалуйста, попробуйте снова.",
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
            return
        
        # Format message with normal formatting (not code block)
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        
        # Now format the display text with proper markdown
        escaped_display_text = escape_markdown_preserve_formatting(display_text)
        final_text = f"{header}\n\n{escaped_display_text}"
        
        # Update message with new summary and buttons
        await query.edit_message_text(
            final_text,
            reply_markup=create_action_buttons(original_msg_id, chat_lang),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Update database with new summary
        await update_summary_mode_and_text(
            pool=pool,
            record_id=record_id,
            new_mode=current_mode,
            new_summary_text=summary_text,
            new_transcript_text=transcript_text
        )
        
        logger.info(f"Successfully redid summary for message {original_msg_id}")
        
    except Exception as e:
        logger.error(f"Error in redo: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                "🇬🇧 An error occurred. Please try again.\n\n"
                "🇷🇺 Произошла ошибка. Пожалуйста, попробуйте снова.",
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
        except Exception as edit_e:
            logger.error(f"Failed to edit message after error: {edit_e}")

async def handle_history_navigation(update: Update, context: CallbackContext, data_parts: list):
    """Handles history navigation actions."""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not found for history callback")
        await query.answer("Database error / Ошибка базы данных", show_alert=True)
        return
    
    if len(data_parts) < 3:
        logger.error(f"Invalid history data: {data_parts}")
        await query.answer("Invalid history data / Неверные данные истории", show_alert=True)
        return
    
    original_msg_id = int(data_parts[1])
    offset = int(data_parts[2])
    
    try:
        # Get chat's language
        chat_lang = await get_chat_language(pool, chat_id)
        
        # Get history records
        history_records, total_count = await get_user_history(
            pool, user_id, chat_id, limit=HISTORY_PAGE_SIZE, offset=offset
        )
        
        if not history_records:
            await query.answer("No history found / История не найдена", show_alert=True)
            return
        
        # Format the history message
        history_message = ""
        
        for i, record in enumerate(history_records, 1):
            current_index = offset + i
            formatted_entry = format_history_message(
                record, current_index, total_count, chat_lang
            )
            history_message += formatted_entry
            
            # Add separator between entries
            if i < len(history_records):
                history_message += "\n\n" + "—" * 20 + "\n\n"
        
        # Create pagination buttons
        reply_markup = create_history_pagination_buttons(original_msg_id, offset, total_count, HISTORY_PAGE_SIZE, chat_lang)
        
        # Update the message
        await query.edit_message_text(
            history_message,
            reply_markup=reply_markup,
        )
        
        logger.info(f"Updated history view for user {user_id} to offset {offset}")
        
    except Exception as e:
        logger.error(f"Error in handle_history_navigation: {e}", exc_info=True)
        await query.answer("Error navigating history / Ошибка при навигации по истории", show_alert=True)

async def show_pin_menu(update: Update, context: CallbackContext, original_msg_id: int):
    """Shows a menu for pinning a default mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        await query.answer("Database error", show_alert=True)
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Create pin menu keyboard
    keyboard = []
    
    # Add appropriate emojis for each mode
    mode_emojis = {
        "brief": "📝",
        "detailed": "📋",
        "bullet": "🔍",
        "combined": "📊",
        "as_is": "📄",
        "pasha": "💊"
    }
    
    # Define the order of modes
    mode_order = ["as_is", "brief", "detailed", "bullet", "combined", "pasha"]
    
    # Get current default mode
    current_default_mode = DEFAULT_MODE
    try:
        current_default_mode = await get_chat_default_mode(pool, chat_id, DEFAULT_MODE)
    except Exception as e:
        logger.error(f"Error getting default mode: {e}")
    
    # Localized button texts
    back_label = "⬅️ Назад"
    
    if chat_lang == 'en':
        back_label = "⬅️ Back"
    elif chat_lang == 'kk':
        back_label = "⬅️ Артқа"
    
    # Add each mode with selection indicator
    for mode_key in mode_order:
        if mode_key in SUPPORTED_MODES:
            emoji = mode_emojis.get(mode_key, "")
            # Get localized mode name
            mode_name = get_mode_name(mode_key, chat_lang)
            
            # Add selection indicator
            if mode_key == current_default_mode:
                mode_name = f"{mode_name} ✓"
                
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {mode_name}", 
                    callback_data=f"set_default_mode:{original_msg_id}:{mode_key}"
                )
            ])
    
    # Add back button
    keyboard.append([
        InlineKeyboardButton(
            back_label, 
            callback_data=f"mode_select:{original_msg_id}"
        )
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        logger.info(f"Showed pin menu for message {original_msg_id} in language {chat_lang}")
    except Exception as e:
        logger.error(f"Error showing pin menu: {e}", exc_info=True)
        
        # Error message based on chat language
        error_message = "Error showing pin menu"
        if chat_lang == 'ru':
            error_message = "Ошибка при отображении меню закрепления"
        elif chat_lang == 'kk':
            error_message = "Бекіту мәзірін көрсету кезінде қате"
            
        await query.answer(error_message, show_alert=True)

async def show_settings_mode_menu(update: Update, context: CallbackContext):
    """Shows a menu for setting the default mode from settings."""
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
        "pasha": "💊"
    }
    
    # Define the order of modes
    mode_order = ["as_is", "brief", "detailed", "bullet", "combined", "pasha"]
    
    # Get current default mode
    current_default_mode = DEFAULT_MODE
    try:
        current_default_mode = await get_chat_default_mode(pool, chat_id, DEFAULT_MODE)
    except Exception as e:
        logger.error(f"Error getting default mode: {e}")
    
    # Localized button texts
    back_label = "⬅️ Назад"
    
    if chat_lang == 'en':
        back_label = "⬅️ Back"
    elif chat_lang == 'kk':
        back_label = "⬅️ Артқа"
    
    # Localized heading text
    heading_text = "Select default mode for voice messages:"
    if chat_lang == 'ru':
        heading_text = "Выберите режим по умолчанию для голосовых сообщений:"
    elif chat_lang == 'kk':
        heading_text = "Дауыстық хабарламалар үшін әдепкі режимді таңдаңыз:"
    
    # Add each mode with selection indicator
    for mode_key in mode_order:
        if mode_key in SUPPORTED_MODES:
            emoji = mode_emojis.get(mode_key, "")
            # Get localized mode name
            mode_name = get_mode_name(mode_key, chat_lang)
            
            # Add selection indicator
            if mode_key == current_default_mode:
                mode_name = f"{mode_name} ✓"
                
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {mode_name}", 
                    callback_data=f"settings_set_default_mode:{mode_key}"
                )
            ])
    
    # Add back button
    keyboard.append([
        InlineKeyboardButton(
            back_label, 
            callback_data="settings"
        )
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            heading_text,
            reply_markup=reply_markup
        )
        logger.info(f"Showed settings mode menu for chat {chat_id} in language {chat_lang}")
    except Exception as e:
        logger.error(f"Error showing settings mode menu: {e}", exc_info=True)
        
        # Error message based on chat language
        error_message = "Error showing mode settings"
        if chat_lang == 'ru':
            error_message = "Ошибка при отображении настроек режима"
        elif chat_lang == 'kk':
            error_message = "Режим параметрлерін көрсету кезінде қате"
            
        await query.answer(error_message, show_alert=True)

# --- Command Handlers ---

async def start(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message and language selection options."""
    user = update.effective_user
    chat = update.effective_chat
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not available in start command")
        await update.message.reply_text("Error connecting to database. Please try again later.")
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat.id)
    
    # Create keyboard with language options
    keyboard = []
    row = []
    for code, lang_info in LANGUAGES.items():
        button = InlineKeyboardButton(
            f"{lang_info['emoji']} {lang_info['name']}", 
            callback_data=f"set_language:{code}"
        )
        row.append(button)
        if len(row) == 2:  # 2 buttons per row
            keyboard.append(row)
            row = []
    
    if row:  # Add any remaining buttons
        keyboard.append(row)
    
    # Add settings button
    keyboard.append([
        InlineKeyboardButton("⚙️ Settings/Настройки", callback_data="settings")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send the welcome message with language selection buttons
    await update.message.reply_text(
        get_string('start', chat_lang),
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def settings_command(update: Update, context: CallbackContext) -> None:
    """Show settings menu."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    logger.info(f"Settings command from user {user_id} in chat {chat_id}")
    
    # Get database pool
    pool = context.bot_data.get('db_pool')
    if not pool:
        await update.message.reply_text("Database error. Please try again later.")
        return
    
    # Get chat language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Localize menu options
    lang_btn_text = "🌐 Change Language"
    history_btn_text = "📚 History"
    sub_btn_text = "💰 Subscription Info"
    mode_btn_text = "⚙️ Default Mode"
    close_btn_text = "❌ Close"
    
    if chat_lang == 'ru':
        lang_btn_text = "🌐 Изменить язык"
        history_btn_text = "📚 История"
        sub_btn_text = "💰 Информация о подписке"
        mode_btn_text = "⚙️ Режимы"
        close_btn_text = "❌ Закрыть"
    elif chat_lang == 'kk':
        lang_btn_text = "🌐 Тілді өзгерту"
        history_btn_text = "📚 Тарих"
        sub_btn_text = "💰 Жазылым туралы ақпарат"
        mode_btn_text = "⚙️ Режим"
        close_btn_text = "❌ Жабу"
    
    # Create keyboard
    keyboard = [
        [InlineKeyboardButton(lang_btn_text, callback_data="language_menu")],
        [InlineKeyboardButton(mode_btn_text, callback_data="settings_mode_menu")],
        [InlineKeyboardButton(history_btn_text, callback_data="show_command_history:0")],
        [InlineKeyboardButton(sub_btn_text, callback_data="subscription_info")],
        [InlineKeyboardButton(close_btn_text, callback_data="close_settings")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get appropriate welcome message based on language
    welcome_text = "Please select an option:"
    if chat_lang == 'ru':
        welcome_text = "Пожалуйста, выберите опцию:"
    elif chat_lang == 'kk':
        welcome_text = "Опцияны таңдаңыз:"
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def help_command(update: Update, context: CallbackContext) -> None:
    """Sends detailed help information about the bot."""
    user = update.effective_user
    chat = update.effective_chat
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not available in help command")
        await update.message.reply_text("Error connecting to database. Please try again later.")
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat.id)
    
    settings_label = "⚙️ Настройки"
    
    if chat_lang == 'en':
        settings_label = "⚙️ Settings"
    elif chat_lang == 'kk':
        settings_label = "⚙️ Параметрлер"
    
    # Create settings button
    help_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(settings_label, callback_data="settings")]
    ])
    
    await update.message.reply_text(
        get_string('help', chat_lang),
        reply_markup=help_buttons,
        parse_mode=ParseMode.MARKDOWN
    )

async def pause_command(update: Update, context: CallbackContext) -> None:
    """Pauses the bot for the current chat."""
    user = update.effective_user
    chat = update.effective_chat
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not available in pause command")
        await update.message.reply_text("Error connecting to database. Please try again later.")
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat.id)
    
    # Set a flag in the database that this chat is paused
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                INSERT INTO chat_preferences (chat_id, is_paused, updated_at)
                VALUES ($1, TRUE, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET is_paused = TRUE, updated_at = NOW();
            """, chat.id)
            
            await update.message.reply_text(
                get_string('pause_success', chat_lang),
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Bot paused for chat {chat.id}")
        except Exception as e:
            logger.error(f"Error pausing bot for chat {chat.id}: {e}", exc_info=True)
            await update.message.reply_text(get_dual_string('error'))

async def resume_command(update: Update, context: CallbackContext) -> None:
    """Resumes the bot for the current chat."""
    user = update.effective_user
    chat = update.effective_chat
    pool = context.bot_data.get('db_pool')
    
    if not pool:
        logger.error("Database pool not available in resume command")
        await update.message.reply_text("Error connecting to database. Please try again later.")
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat.id)
    
    # Set a flag in the database that this chat is no longer paused
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                INSERT INTO chat_preferences (chat_id, is_paused, updated_at)
                VALUES ($1, FALSE, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET is_paused = FALSE, updated_at = NOW();
            """, chat.id)
            
            await update.message.reply_text(
                get_string('resume_success', chat_lang),
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Bot resumed for chat {chat.id}")
        except Exception as e:
            logger.error(f"Error resuming bot for chat {chat.id}: {e}", exc_info=True)
            await update.message.reply_text(get_dual_string('error'))

# --- Message Handlers ---

@send_typing_action
async def handle_audio(update: Update, context: CallbackContext) -> None:
    """Handles audio file uploads (MP3 and WAV) sent as Document or Audio for conversion."""
    message = update.message

    # Ensure we only handle Document or Audio explicitly, not VOICE
    if message.voice:
        return # Let the voice handler take care of this

    audio_obj = message.document or message.audio

    if not audio_obj:
        logger.warning(f"handle_audio called but no document or audio found in message {message.message_id}")
        await message.reply_text(get_dual_string('error'))
        return

    file_id = audio_obj.file_id
    mime_type = audio_obj.mime_type
    file_name = audio_obj.file_name or f"audio.{mime_type.split('/')[-1] if mime_type else 'file'}"
    file_name_lower = file_name.lower()
    file_size = audio_obj.file_size

    is_mp3 = ('audio/mpeg' in mime_type) or file_name_lower.endswith(".mp3") if mime_type else file_name_lower.endswith(".mp3")
    is_wav = ('audio/wav' in mime_type or 'audio/x-wav' in mime_type) or file_name_lower.endswith(".wav") if mime_type else file_name_lower.endswith(".wav")

    if not (is_mp3 or is_wav):
        logger.info(f"Invalid file type received: {file_name} (MIME: {mime_type})")
        await message.reply_text(get_dual_string('invalid_file'))
        return

    if file_size > 20 * 1024 * 1024: # 20 MB limit
         await message.reply_text(get_dual_string('file_too_large'))
         return

    await message.reply_text(get_dual_string('processing'))

    try:
        audio_file = await context.bot.get_file(file_id)
        
        input_suffix = ".mp3" if is_mp3 else ".wav"
        
        with tempfile.NamedTemporaryFile(suffix=input_suffix) as temp_input_file, \
             tempfile.NamedTemporaryFile(suffix=".ogg") as temp_output_file:
            
            await audio_file.download_to_drive(custom_path=temp_input_file.name)
            logger.info(f"Downloaded file ({mime_type}) to: {temp_input_file.name}")

            audio_format = "mp3" if is_mp3 else "wav"
            audio = AudioSegment.from_file(temp_input_file.name, format=audio_format) 
            
            audio.export(
                temp_output_file.name, 
                format="ogg", 
                codec="libopus", 
                bitrate="64k",
                parameters=["-application", "voip"]
            )
            logger.info(f"Converted file to: {temp_output_file.name}")

            await message.reply_voice(voice=open(temp_output_file.name, 'rb'))
            logger.info(f"Sent voice message for user {update.effective_user.id}")

    except Exception as e:
        logger.error(f"Error processing file for user {update.effective_user.id}: {e}", exc_info=True)
        await message.reply_text(get_dual_string('error'))


@send_typing_action
async def handle_voice_message(update: Update, context: CallbackContext) -> None:
    """Handles voice messages for transcription and summarization."""
    message = update.message
    user = update.effective_user
    voice = message.voice
    pool = context.bot_data.get('db_pool')

    if not voice:
        logger.warning(f"handle_voice_message called but no voice found in message {message.message_id}")
        return

    if not pool:
        logger.error("Database pool not found in bot_data")
        await message.reply_text(get_dual_string('error'), quote=True)
        return

    # Get chat's language preference
    chat_lang = await get_chat_language(pool, message.chat_id)
    
    # Check if the bot is paused for this chat
    is_paused = await get_chat_paused_status(pool, message.chat_id)
    if is_paused:
        logger.info(f"Ignoring voice message {message.message_id} because bot is paused for chat {message.chat_id}")
        # Don't respond at all when paused
        return
    
    logger.info(f"Received voice message {message.message_id} from user {user.id} (duration: {voice.duration}s, chat language: {chat_lang})")

    # Acknowledge receipt with chat's preferred language
    status_message = await message.reply_text(
        "⏳ " + get_string('processing', chat_lang).split('\n')[0].replace('🇬🇧 ', '').replace('🇷🇺 ', '').replace('🇰🇿 ', ''), 
        reply_to_message_id=message.message_id
    )

    try:
        # 1. Download voice file
        with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
            file = await voice.get_file()
            await file.download_to_drive(custom_path=temp_audio_file.name)
            logger.info(f"Downloaded voice file {file.file_id} to {temp_audio_file.name}")

            # 2. Get chat's default mode or use system default
            mode = await get_chat_default_mode(pool, message.chat_id, DEFAULT_MODE)
            
            # 3. Pass chat language to Gemini for processing in the correct language
            summary_text, transcript_text = await process_audio_with_gemini(temp_audio_file.name, mode, chat_lang)

        # 3. Handle Gemini Response
        if transcript_text is None: # Indicates a processing error in Gemini
            logger.error(f"Gemini processing failed for message {message.message_id}")
            await status_message.edit_text(get_dual_string('error')) # Update status message
            return

        # Determine primary text to display based on the mode
        if mode == 'as_is' or mode == 'transcript':
            display_text = transcript_text
        else:
            display_text = summary_text if summary_text is not None else transcript_text
        
        # 4. Format response header with emoji
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = message.date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        # Escape the entire timestamp for MarkdownV2 (especially the '.' characters)
        moscow_time_str = escape_markdown(moscow_time, version=2)
        # Escape username for MarkdownV2
        user_name = escape_markdown(message.from_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        
        # 5. Properly escape content for MarkdownV2 while preserving Gemini's formatting
        escaped_display_text = escape_markdown_preserve_formatting(display_text)
        final_text = f"{header}\n\n{escaped_display_text}"

        # 6. Create Inline Keyboard Buttons with localized labels
        # Localize button labels
        mode_label = "👤 Режим"
        redo_label = "🔁 Заново" 
        settings_label = "⚙️ Настройки" # Use the gear icon and correct label
        done_label = "❎ Готово"
        
        if chat_lang == 'en':
            mode_label = "👤 Mode"
            redo_label = "🔁 Redo"
            settings_label = "⚙️ Settings" # Use the gear icon and correct label
            done_label = "❎ Done"
        elif chat_lang == 'kk':
            mode_label = "👤 Режим"
            redo_label = "🔁 Қайта"
            settings_label = "⚙️ Параметрлер" # Use the gear icon and correct label
            done_label = "❎ Дайын"
        
        keyboard = [
            [
                InlineKeyboardButton(mode_label, callback_data=f"mode_select:{message.message_id}"),
                InlineKeyboardButton(redo_label, callback_data=f"redo:{message.message_id}"),
            ],
            [
                InlineKeyboardButton(settings_label, callback_data="settings"), # Use the correct callback_data
                InlineKeyboardButton(done_label, callback_data=f"confirm:{message.message_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # 7. Send response message (edit the status message)
        sent_message = await status_message.edit_text(
            final_text,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
        logger.info(f"Sent summary message {sent_message.message_id} for original message {message.message_id}")

        # 8. Save summary details to DB
        record_id = await save_summary(
            pool=pool,
            user_id=user.id,
            chat_id=message.chat_id,
            original_message_id=message.message_id,
            summary_message_id=sent_message.message_id,
            audio_file_id=voice.file_id, # Store Telegram's file ID
            mode=mode,
            summary_text=summary_text,
            transcript_text=transcript_text # Store the raw transcript
        )

        if record_id is None:
            logger.error(f"Failed to save summary to DB for message {message.message_id}")
            # Bot already replied, maybe send a follow-up error?
            # await sent_message.reply_text("Warning: Could not save to history.")

    except Exception as e:
        logger.error(f"Error processing voice message {message.message_id}: {e}", exc_info=True)
        # Try to edit the status message to show the error
        try:
            await status_message.edit_text(get_dual_string('error'))
        except Exception as edit_e:
            logger.error(f"Failed to edit status message to show error: {edit_e}")
            # Fallback to replying if editing failed (Corrected: use reply_to_message_id)
            await message.reply_text(
                get_dual_string('error'), 
                reply_to_message_id=message.message_id
            )

# --- Callback Query Handler ---

async def button_callback(update: Update, context: CallbackContext):
    """Handle button callbacks from inline keyboards."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    # Parse the callback data
    data_parts = query.data.split(":")
    action = data_parts[0]
    
    logger.debug(f"Button callback: {action} with data {data_parts}")
    
    # Get database pool from context
    pool = context.bot_data.get('db_pool')
    if not pool:
        await query.answer("Database error", show_alert=True)
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Handle noop action specifically
    if action == "noop":
        return
    
    # Handle show command history
    elif action == "show_command_history":
        if len(data_parts) < 2:
            await query.answer("Invalid callback data", show_alert=True)
            return
        
        # Parse offset parameter
        try:
            offset = int(data_parts[1])
        except ValueError:
            await query.answer("Invalid offset value", show_alert=True)
            return
        
        # Get user history
        user_id = update.effective_user.id
        limit = 5  # Number of history items per page
        
        try:
            # Fetch user history from database
            history_records, total_count = await get_user_history(
                pool, user_id, chat_id, limit, offset
            )
            
            if not history_records:
                # No history found
                no_history_message = "You don't have any voice message history yet."
                if chat_lang == 'ru':
                    no_history_message = "У вас пока нет истории голосовых сообщений."
                elif chat_lang == 'kk':
                    no_history_message = "Сізде әлі дауыстық хабарлама тарихы жоқ."
                
                await query.edit_message_text(
                    no_history_message,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="settings")]
                    ])
                )
                await query.answer()
                return
            
            # Format history message
            history_message = ""
            
            for i, record in enumerate(history_records, 1):
                current_index = offset + i
                formatted_entry = format_history_message(
                    record, current_index, total_count, chat_lang
                )
                history_message += formatted_entry
                
                # Add separator between entries
                if i < len(history_records):
                    history_message += "\n\n" + "—" * 20 + "\n\n"
            
            # Create pagination buttons
            buttons = []
            
            # Previous page button
            if offset > 0:
                prev_offset = max(0, offset - limit)
                prev_text = "⬅️ Previous"
                if chat_lang == 'ru':
                    prev_text = "⬅️ Предыдущие"
                elif chat_lang == 'kk':
                    prev_text = "⬅️ Алдыңғы"
                buttons.append(
                    InlineKeyboardButton(
                        prev_text, 
                        callback_data=f"show_command_history:{prev_offset}"
                    )
                )
            
            # Next page button
            if offset + limit < total_count:
                next_offset = offset + limit
                next_text = "Next ➡️"
                if chat_lang == 'ru':
                    next_text = "Следующие ➡️"
                elif chat_lang == 'kk':
                    next_text = "Келесі ➡️"
                buttons.append(
                    InlineKeyboardButton(
                        next_text, 
                        callback_data=f"show_command_history:{next_offset}"
                    )
                )
            
            # If we have both previous and next buttons, put them on the same row
            pagination_row = []
            if buttons:
                pagination_row = [buttons]
            
            # Add back button
            back_text = "⬅️ Back to Settings"
            if chat_lang == 'ru':
                back_text = "⬅️ Назад к настройкам"
            elif chat_lang == 'kk':
                back_text = "⬅️ Параметрлерге оралу"
            
            keyboard = pagination_row + [
                [InlineKeyboardButton(back_text, callback_data="settings")]
            ]
            
            # Update the message with history and pagination buttons
            await query.edit_message_text(
                history_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            
        except Exception as e:
            logger.error(f"Error displaying history: {str(e)}")
            error_message = "Error retrieving history. Please try again later."
            if chat_lang == 'ru':
                error_message = "Ошибка при получении истории. Пожалуйста, попробуйте позже."
            elif chat_lang == 'kk':
                error_message = "Тарихты алу қатесі. Кейінірек қайталап көріңіз."
            
            await query.answer(error_message, show_alert=True)
        
        await query.answer()
        return
    
    # Handle language settings
    if action == "set_language":
        if len(data_parts) < 2:
            await query.answer("Missing language parameter", show_alert=True)
            return
            
        language = data_parts[1]
        if language not in LANGUAGES:
            await query.answer("Unsupported language", show_alert=True)
            return
            
        # Set language for this chat
        success = await set_chat_language(pool, chat_id, language)
        if success:
            # Show confirmation
            await query.answer(get_string('language_set', language), show_alert=True)
            
            # Create settings and help buttons
            settings_label = "⚙️ Настройки"
            help_label = "❓ Помощь"
            
            if language == 'en':
                settings_label = "⚙️ Settings"
                help_label = "❓ Help"
            elif language == 'kk':
                settings_label = "⚙️ Параметрлер"
                help_label = "❓ Көмек"
            
            start_buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(settings_label, callback_data="settings"),
                    InlineKeyboardButton(help_label, callback_data="help")
                ]
            ])
            
            # Get the welcome text without the language selection prompt
            start_text = get_string('start', language)
            # Replace the language selection prompt at the end with the voice check request
            start_text = start_text.replace("**Please choose your language to start:**", "")
            start_text = start_text.replace("**Пожалуйста, выберите ваш язык для начала:**", "")
            start_text = start_text.replace("**Бастау үшін тілді таңдаңыз:**", "")
            # Add the voice check request
            start_text = start_text.strip() + "\n\n" + get_string('send_voice_check', language)
            
            # Update the message with the new language and buttons
            await query.edit_message_text(
                start_text,
                reply_markup=start_buttons,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer("Failed to set language", show_alert=True)
        return
    
    # Handle language setting from voice menu
    elif action == "set_language_and_back":
        if len(data_parts) < 3:
            await query.answer("Missing parameters", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        language = data_parts[2]
        
        if language not in LANGUAGES:
            await query.answer("Unsupported language", show_alert=True)
            return
        
        # Set language for this chat
        success = await set_chat_language(pool, chat_id, language)
        if success:
            # Show confirmation
            lang_info = LANGUAGES[language]
            confirm_message = f"Language set to {lang_info['name']}"
            if language == 'ru':
                confirm_message = f"Язык изменен на {lang_info['name']}"
            elif language == 'kk':
                confirm_message = f"Тіл {lang_info['name']} тіліне өзгертілді"
                
            await query.answer(confirm_message, show_alert=True)
            
            # Return to voice settings with updated language
            await query.edit_message_reply_markup(
                reply_markup=create_voice_settings_buttons(original_msg_id, language)
            )
        else:
            await query.answer("Failed to set language", show_alert=True)
        return
    
    elif action == "settings":
        # Localize menu options
        lang_btn_text = "🌐 Change Language"
        history_btn_text = "📚 History"
        sub_btn_text = "💰 Subscription Info"
        mode_btn_text = "⚙️ Default Mode"
        close_btn_text = "❌ Close"
        
        if chat_lang == 'ru':
            lang_btn_text = "🌐 Изменить язык"
            history_btn_text = "📚 История"
            sub_btn_text = "💰 Информация о подписке"
            mode_btn_text = "⚙️ Выбор режима"
            close_btn_text = "❌ Закрыть"
        elif chat_lang == 'kk':
            lang_btn_text = "🌐 Тілді өзгерту"
            history_btn_text = "📚 Тарих"
            sub_btn_text = "💰 Жазылым туралы ақпарат"
            mode_btn_text = "⚙️ Әдепкі режим"
            close_btn_text = "❌ Жабу"
        
        keyboard = [
            [InlineKeyboardButton(lang_btn_text, callback_data="language_menu")],
            [InlineKeyboardButton(mode_btn_text, callback_data="settings_mode_menu")],
            [InlineKeyboardButton(history_btn_text, callback_data="show_command_history:0")],
            [InlineKeyboardButton(sub_btn_text, callback_data="subscription_info")],
            [InlineKeyboardButton(close_btn_text, callback_data="close_settings")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Get appropriate welcome message based on language
        welcome_text = "Please select an option:"
        if chat_lang == 'ru':
            welcome_text = "Пожалуйста, выберите опцию:"
        elif chat_lang == 'kk':
            welcome_text = "Опцияны таңдаңыз:"
            
        await query.edit_message_text(welcome_text, reply_markup=reply_markup)
        await query.answer()
        return
    
    # Handle voice settings menu
    elif action == "voice_settings":
        if len(data_parts) < 2:
            await query.answer("Missing message ID", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        await query.edit_message_reply_markup(
            reply_markup=create_voice_settings_buttons(original_msg_id, chat_lang)
        )
        return
        
    # Handle subscription info from voice menu
    elif action == "voice_subscription_info":
        if len(data_parts) < 2:
            await query.answer("Missing message ID", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        # Create back button to voice settings
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"voice_settings:{original_msg_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send subscription info
        await query.edit_message_text(
            get_string('subscription_info', chat_lang),
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle help button
    elif action == "help":
        settings_label = "⚙️ Настройки"
        
        if chat_lang == 'en':
            settings_label = "⚙️ Settings"
        elif chat_lang == 'kk':
            settings_label = "⚙️ Параметрлер"
        
        # Create settings button
        help_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(settings_label, callback_data="settings")]
        ])
        
        await query.edit_message_text(
            get_string('help', chat_lang),
            reply_markup=help_buttons,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle voice language menu
    elif action == "voice_language_menu":
        if len(data_parts) < 2:
            await query.answer("Missing message ID", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        # Create keyboard with language options
        keyboard = []
        row = []
        for code, lang_info in LANGUAGES.items():
            button = InlineKeyboardButton(
                f"{lang_info['emoji']} {lang_info['name']}", 
                callback_data=f"set_language_and_back:{original_msg_id}:{code}"
            )
            row.append(button)
            if len(row) == 2:  # 2 buttons per row
                keyboard.append(row)
                row = []
        
        if row:  # Add any remaining buttons
            keyboard.append(row)
        
        # Add back button
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data=f"voice_settings:{original_msg_id}")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send language selection message
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        return
    
    # Handle back to main menu from any submenu
    elif action == "back_to_main":
        if len(data_parts) < 2:
            await query.answer("Missing message ID", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        await query.edit_message_reply_markup(
            reply_markup=create_action_buttons(original_msg_id, chat_lang)
        )
        return
        
    elif action == "language_menu":
        # Create keyboard with language options
        keyboard = []
        row = []
        for code, lang_info in LANGUAGES.items():
            button = InlineKeyboardButton(
                f"{lang_info['emoji']} {lang_info['name']}", 
                callback_data=f"set_language:{code}"
            )
            row.append(button)
            if len(row) == 2:  # 2 buttons per row
                keyboard.append(row)
                row = []
        
        if row:  # Add any remaining buttons
            keyboard.append(row)
        
        # Add back button
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="settings")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send language selection message
        await query.edit_message_text(
            get_string('choose_language', chat_lang),
            reply_markup=reply_markup
        )
        return
    
    elif action == "subscription_info":
        # Create back button
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send subscription info
        await query.edit_message_text(
            get_string('subscription_info', chat_lang),
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    elif action == "close_settings":
        # Just remove the keyboard and change text
        await query.edit_message_text(get_string('settings', chat_lang) + " ✅")
        return
    
    # Handle settings mode menu
    elif action == "settings_mode_menu":
        await show_settings_mode_menu(update, context)
        return
    
    # Handle setting default mode from settings
    elif action == "settings_set_default_mode":
        if len(data_parts) < 2:
            await query.answer("Missing mode parameter", show_alert=True)
            return
            
        mode = data_parts[1]
        if mode not in SUPPORTED_MODES:
            await query.answer("Unsupported mode", show_alert=True)
            return
            
        # Set this mode as default for the chat
        success = await set_chat_default_mode(pool, chat_id, mode)
        if success:
            # Get mode name in current chat language
            mode_name = get_mode_name(mode, chat_lang)
            confirm_message = f"Mode '{mode_name}' set as default"
            if chat_lang == 'ru':
                confirm_message = f"Режим '{mode_name}' установлен по умолчанию"
            elif chat_lang == 'kk':
                confirm_message = f"Режим '{mode_name}' әдепкі бойынша орнатылды"
            
            await query.answer(confirm_message, show_alert=True)
            
            # Show updated settings mode menu
            await show_settings_mode_menu(update, context)
        else:
            # Error message in current chat language
            error_message = "Failed to set default mode"
            if chat_lang == 'ru':
                error_message = "Не удалось установить режим по умолчанию"
            elif chat_lang == 'kk':
                error_message = "Әдепкі режимді орнату сәтсіз аяқталды"
            
            await query.answer(error_message, show_alert=True)
        return
    
    # For original message callbacks, ensure we have the original_msg_id
    if action in ["confirm", "mode_select", "mode_set", "redo", "history", "history_nav", 
                  "set_default_mode", "cancel_mode_select", "show_pin_menu"]:
        if len(data_parts) < 2:
            await query.answer("Missing message ID", show_alert=True)
            return
            
        original_msg_id = int(data_parts[1])
        
        # Handle different button actions for original messages
        if action == "confirm":
            # Just acknowledge and do nothing - user is satisfied
            await query.edit_message_reply_markup(reply_markup=None)
            return
            
        elif action == "mode_select":
            await show_mode_selection(update, context, original_msg_id)
            return
            
        elif action == "mode_set":
            await mode_set(update, context, data_parts, original_msg_id)
            return
            
        elif action == "redo":
            await redo(update, context, original_msg_id)
            return
            
        elif action == "history":
            await handle_history_navigation(update, context, data_parts)
            return
            
        elif action == "history_nav":
            await handle_history_navigation(update, context, data_parts)
            return
            
        elif action == "show_pin_menu":
            await show_pin_menu(update, context, original_msg_id)
            return
            
        elif action == "set_default_mode":
            if len(data_parts) < 3:
                await query.answer("Missing mode parameter", show_alert=True)
                return
                
            mode = data_parts[2]
            if mode not in SUPPORTED_MODES:
                await query.answer("Unsupported mode", show_alert=True)
                return
                
            # Set this mode as default for the chat
            success = await set_chat_default_mode(pool, chat_id, mode)
            if success:
                # Get mode name in current chat language
                mode_name = get_mode_name(mode, chat_lang)
                confirm_message = f"Mode '{mode_name}' set as default"
                if chat_lang == 'ru':
                    confirm_message = f"Режим '{mode_name}' установлен по умолчанию"
                elif chat_lang == 'kk':
                    confirm_message = f"Режим '{mode_name}' әдепкі бойынша орнатылды"
                
                await query.answer(confirm_message, show_alert=True)
                
                # Return to mode selection with updated default
                await show_mode_selection(update, context, original_msg_id)
            else:
                # Error message in current chat language
                error_message = "Failed to set default mode"
                if chat_lang == 'ru':
                    error_message = "Не удалось установить режим по умолчанию"
                elif chat_lang == 'kk':
                    error_message = "Әдепкі режимді орнату сәтсіз аяқталды"
                
                await query.answer(error_message, show_alert=True)
            return
            
        elif action == "cancel_mode_select":
            # Return to normal action buttons
            await query.edit_message_reply_markup(reply_markup=create_action_buttons(original_msg_id, chat_lang))
            return
    
    # If we get here, we didn't handle the action
    logger.warning(f"Unhandled button callback action: {action}")
    await query.answer("Unhandled action / Необработанное действие")

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
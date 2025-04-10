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
    
    # Check if data_parts has the expected structure (action, original_msg_id, offset)
    if len(data_parts) < 3:
        logger.error(f"Invalid history data structure for navigation: {data_parts}")
        await query.answer("Invalid history data / Неверные данные истории", show_alert=True)
        return
    
    try:
        # original_msg_id is needed for button context but not for fetching history
        original_msg_id = int(data_parts[1]) 
        offset = int(data_parts[2])
    except ValueError:
        logger.error(f"Invalid integer in history navigation data: {data_parts}")
        await query.answer("Invalid history data format / Неверный формат данных истории", show_alert=True)
        return
    
    try:
        # Get chat's language
        chat_lang = await get_chat_language(pool, chat_id)
        
        # Get history records (limit=1, offset is the index)
        history_records, total_count = await get_user_history(
            pool, user_id, chat_id, limit=HISTORY_PAGE_SIZE, offset=offset
        )
        
        if not history_records:
            await query.answer("No history found at this position / История не найдена", show_alert=True)
            return
        
        # Since limit=1, we only care about the first record
        record = history_records[0]
        current_index = offset + 1 # Display index is offset + 1

        # Fetch author name
        author_name = "Unknown User"
        record_user_id = record.get('user_id')
        if record_user_id:
            try:
                author_chat = await context.bot.get_chat(record_user_id)
                author_name = author_chat.full_name or author_name
            except Exception as name_e:
                logger.warning(f"Could not fetch author name for user_id {record_user_id}: {name_e}")
        
        # Format the single history message, passing the author name
        history_message = format_history_message(
            record, current_index, total_count, chat_lang, author_name=author_name
        )
        
        # Create pagination buttons (original_msg_id is needed for context in buttons)
        reply_markup = create_history_pagination_buttons(original_msg_id, offset, total_count, HISTORY_PAGE_SIZE, chat_lang)
        
        # Update the message with MarkdownV2
        await query.edit_message_text(
            history_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2 # Re-enable MarkdownV2
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
                INSERT INTO chat_preferences (chat_id, default_mode, is_paused, updated_at)
                VALUES ($1, $2, TRUE, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET is_paused = TRUE, updated_at = NOW();
            """, chat.id, DEFAULT_MODE) # Add DEFAULT_MODE here
            
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
                INSERT INTO chat_preferences (chat_id, default_mode, is_paused, updated_at)
                VALUES ($1, $2, FALSE, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET is_paused = FALSE, updated_at = NOW();
            """, chat.id, DEFAULT_MODE) # Add DEFAULT_MODE here
            
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
                InlineKeyboardButton(settings_label, callback_data=f"settings:{message.message_id}"), # Include original_msg_id
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
    user_id = update.effective_user.id
    
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
        
        # Parse offset parameter (default to 0 if missing or invalid)
        try:
            offset = int(data_parts[1]) if len(data_parts) > 1 else 0
        except ValueError:
            offset = 0
            logger.warning(f"Invalid offset value in show_command_history data: {data_parts}, defaulting to 0")
        
        # Get user history
        user_id = update.effective_user.id
        # limit = 5  # Number of history items per page -> Now uses HISTORY_PAGE_SIZE
        limit = HISTORY_PAGE_SIZE 
        
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
                
                # Create a simple back button if no history
                no_history_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад к настройкам" if chat_lang == 'ru' else "⬅️ Back to Settings", callback_data="settings")]
                ])
                await query.edit_message_text(
                    no_history_message,
                    reply_markup=no_history_keyboard
                )
                await query.answer()
                return
            
            # Format history message (only the first record since limit=1)
            record = history_records[0]
            current_index = offset + 1

            # Fetch author name
            author_name = "Unknown User"
            record_user_id = record.get('user_id')
            if record_user_id:
                try:
                    author_chat = await context.bot.get_chat(record_user_id)
                    author_name = author_chat.full_name or author_name
                except Exception as name_e:
                    logger.warning(f"Could not fetch author name for user_id {record_user_id}: {name_e}")
            
            history_message = format_history_message(
                record, current_index, total_count, chat_lang, author_name=author_name
            )
            
            # Create pagination buttons (pass original message ID if available, else maybe 0 or handle differently?)
            # We don't have original_msg_id here, as this is from settings menu.
            # Let's pass the current message_id being edited.
            current_message_id = query.message.message_id if query.message else 0
            reply_markup = create_history_pagination_buttons(current_message_id, offset, total_count, limit, chat_lang)
            
            # Update the message with history and pagination buttons, using MarkdownV2
            await query.edit_message_text(
                history_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
        except Exception as e:
            logger.error(f"Error displaying history: {str(e)}", exc_info=True)
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
        # Check if the settings menu was opened from a message
        original_msg_id = None
        if len(data_parts) > 1:
            try:
                original_msg_id = int(data_parts[1])
            except (ValueError, TypeError):
                original_msg_id = None
        
        # Localize menu options
        lang_btn_text = "🌐 Change Language"
        history_btn_text = "📚 History"
        sub_btn_text = "💰 Subscription Info"
        mode_btn_text = "⚙️ Default Mode"
        help_btn_text = get_string('settings_help', chat_lang) # New Help button
        close_btn_text = "❌ Close"
        back_to_msg_text = "⬅️ Back to Message"
        
        if chat_lang == 'ru':
            lang_btn_text = "🌐 Изменить язык"
            history_btn_text = "📚 История"
            sub_btn_text = "💰 Информация о подписке"
            mode_btn_text = "⚙️ Выбор режима"
            # help_btn_text is already localized via get_string
            close_btn_text = "❌ Закрыть"
            back_to_msg_text = "⬅️ Назад к сообщению"
        elif chat_lang == 'kk':
            lang_btn_text = "🌐 Тілді өзгерту"
            history_btn_text = "📚 Тарих"
            sub_btn_text = "💰 Жазылым туралы ақпарат"
            mode_btn_text = "⚙️ Әдепкі режим"
            # help_btn_text is already localized via get_string
            close_btn_text = "❌ Жабу"
            back_to_msg_text = "⬅️ Хабарламаға оралу"
        
        keyboard = [
            [InlineKeyboardButton(lang_btn_text, callback_data="language_menu")],
            [InlineKeyboardButton(mode_btn_text, callback_data="settings_mode_menu")],
            [InlineKeyboardButton(history_btn_text, callback_data="show_command_history:0")],
            [InlineKeyboardButton(sub_btn_text, callback_data="subscription_info")],
            [InlineKeyboardButton(help_btn_text, callback_data="help")], # Added Help button
        ]
        
        # Add "Back to Message" button if opened from a message
        if original_msg_id is not None:
            keyboard.append([InlineKeyboardButton(back_to_msg_text, callback_data=f"back_to_message:{original_msg_id}")])
        
        # Add close button
        keyboard.append([InlineKeyboardButton(close_btn_text, callback_data="close_settings")])
        
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
    
    # --- History Action Handlers ---
    elif action == "delete_history_confirm":
        if len(data_parts) < 3:
            await query.answer("Missing data for delete confirmation", show_alert=True)
            return
        try:
            original_msg_id = int(data_parts[1])
            current_offset = int(data_parts[2]) # Needed for cancel
        except ValueError:
            await query.answer("Invalid data format for delete confirmation", show_alert=True)
            return
        
        confirm_text = get_string('history_delete_confirm', chat_lang)
        yes_button = InlineKeyboardButton(get_string('history_delete_yes', chat_lang), callback_data=f"delete_history_execute:{original_msg_id}")
        cancel_button = InlineKeyboardButton(get_string('history_delete_cancel', chat_lang), callback_data=f"history_nav:{original_msg_id}:{current_offset}")
        
        keyboard = [[yes_button, cancel_button]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(confirm_text, reply_markup=reply_markup)
        await query.answer()
        return
        
    elif action == "delete_history_execute":
        if len(data_parts) < 2:
            await query.answer("Missing data for delete execution", show_alert=True)
            return
        try:
            original_msg_id = int(data_parts[1]) # Keep for potential future use
        except ValueError:
            await query.answer("Invalid data format for delete execution", show_alert=True)
            return
            
        success = await delete_chat_history(pool, chat_id)
        
        if success:
            await query.edit_message_text(get_string('history_deleted', chat_lang))
            # Optional: Automatically navigate back to settings after deletion
            # await settings_command(update, context) # Requires settings_command to handle callback query context
        else:
            await query.edit_message_text(get_string('history_delete_error', chat_lang))
        await query.answer()
        return

    elif action == "export_history":
        if len(data_parts) < 2:
            await query.answer("Missing data for export", show_alert=True)
            return
        try:
            original_msg_id = int(data_parts[1]) # Keep for potential future use
        except ValueError:
            await query.answer("Invalid data format for export", show_alert=True)
            return
            
        await query.answer() # Acknowledge button press immediately
        status_msg = await context.bot.send_message(chat_id, get_string('history_exporting', chat_lang))
        
        try:
            history_records = await get_all_chat_history(pool, chat_id)
            
            if not history_records:
                await status_msg.edit_text(get_string('history_export_empty', chat_lang))
                return
                
            export_lines = []
            moscow_tz = pytz.timezone('Europe/Moscow') # Ensure pytz is imported
            
            for record in history_records:
                author_name = "Unknown User"
                record_user_id = record.get('user_id')
                if record_user_id:
                    try:
                        author_chat = await context.bot.get_chat(record_user_id)
                        author_name = author_chat.full_name or author_name
                    except Exception as name_e:
                        logger.warning(f"Could not fetch author name for user_id {record_user_id} during export: {name_e}")
                        author_name = f"User ID {record_user_id}"
                        
                created_at_utc = record['created_at']
                created_at_moscow = created_at_utc.astimezone(moscow_tz) if created_at_utc else None
                time_str = created_at_moscow.strftime('%Y-%m-%d %H:%M:%S МСК') if created_at_moscow else "(no date)"
                
                mode_key = record.get('mode', 'unknown')
                localized_mode_name = get_mode_name(mode_key, chat_lang)
                
                summary = record.get('summary_text', None)
                transcript = record.get('transcript_text', None)
                text_to_display = summary if summary is not None else transcript if transcript is not None else "(empty)"
                
                # Basic formatting for the TXT file
                export_lines.append(f"--- Entry ---")
                export_lines.append(f"Time: {time_str}")
                export_lines.append(f"Author: {author_name}")
                export_lines.append(f"Mode: {localized_mode_name}")
                export_lines.append(f"Content:\n{text_to_display}")
                export_lines.append("\n") # Add a blank line between entries

            history_str = "\n".join(export_lines)
            # Corrected datetime usage
            file_name = f"history_{chat_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            
            f = io.BytesIO(history_str.encode('utf-8'))
            f.name = file_name
            
            await context.bot.send_document(chat_id=chat_id, document=f)
            await status_msg.edit_text(get_string('history_export_complete', chat_lang))
            
        except Exception as e:
            logger.error(f"Error exporting history for chat {chat_id}: {e}", exc_info=True)
            try:
                await status_msg.edit_text(get_string('history_export_error', chat_lang))
            except Exception as edit_e:
                logger.error(f"Failed to edit export status message after error: {edit_e}")
        return

    # --- Original Message Callbacks (Confirm, Mode, Redo, History Nav, Pin, etc.) ---
    if action in ["confirm", "mode_select", "mode_set", "redo", "history", "history_nav", 
                  "set_default_mode", "cancel_mode_select", "show_pin_menu", "back_to_message"]:
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
            
        elif action == "back_to_message":
            if len(data_parts) < 2:
                await query.answer("Missing message ID", show_alert=True)
                return
                
            try:
                original_msg_id = int(data_parts[1])
            except (ValueError, TypeError):
                await query.answer("Invalid message ID", show_alert=True)
                return
            
            # Get the record from the database to retrieve mode and transcript
            db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
            if not db_record:
                logger.error(f"Record not found for message {original_msg_id}")
                await query.answer("Could not find the original message", show_alert=True)
                return
                
            # Re-create the original message with action buttons
            try:
                # Similar to what we do in mode_set - format the message
                user_id = db_record['user_id']
                mode = db_record['mode']
                display_text = db_record['summary_text'] if db_record['summary_text'] else db_record['transcript_text']
                
                # Get user info for the header
                original_user = await context.bot.get_chat(user_id)
                original_date = query.message.date  # Use current date as fallback
                
                # Format message header
                moscow_tz = pytz.timezone('Europe/Moscow')
                moscow_time = original_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
                moscow_time_str = escape_markdown(moscow_time, version=2)
                user_name = escape_markdown(original_user.full_name, version=2)
                header = f"*{user_name}* \\| {moscow_time_str}"
                
                # Format the display text with markdown
                escaped_display_text = escape_markdown_preserve_formatting(display_text)
                final_text = f"{header}\n\n{escaped_display_text}"
                
                # Edit message with original action buttons
                await query.edit_message_text(
                    final_text,
                    reply_markup=create_action_buttons(original_msg_id, chat_lang),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                logger.error(f"Error returning to message {original_msg_id}: {e}", exc_info=True)
                await query.answer("Error returning to message", show_alert=True)
            
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
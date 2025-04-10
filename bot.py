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
from db_utils import create_tables, save_summary, get_summary_context_for_callback, update_summary_mode_and_text, get_user_history, get_chat_default_mode, set_chat_default_mode, clear_chat_default_mode, get_user_language, set_user_language, get_chat_language, set_chat_language # Added get_user_history
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
    history_label = "📚 История"
    done_label = "❎ Готово"
    
    if language == 'en':
        mode_label = "👤 Mode"
        redo_label = "🔁 Redo"
        history_label = "📚 History"
        done_label = "❎ Done"
    elif language == 'kk':
        mode_label = "👤 Режим"
        redo_label = "🔁 Қайта"
        history_label = "📚 Тарих"
        done_label = "❎ Дайын"
        
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(mode_label, callback_data=f"mode_select:{original_msg_id}"),
            InlineKeyboardButton(redo_label, callback_data=f"redo:{original_msg_id}"),
        ],
        [
            InlineKeyboardButton(history_label, callback_data=f"history:{original_msg_id}:0"),
            InlineKeyboardButton(done_label, callback_data=f"confirm:{original_msg_id}"),
        ]
    ])

# --- History Formatting Helpers ---

def format_history_message(record: asyncpg.Record, current_index: int, total_count: int) -> str:
    """Formats a single history record for display using MarkdownV2."""
    mode_display = SUPPORTED_MODES.get(record['mode'], record['mode'])
    text_to_display = record['summary_text'] if record['summary_text'] else record['transcript_text']
    created_at_utc = record['created_at']
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    created_at_moscow = created_at_utc.astimezone(moscow_tz)
    time_str = escape_markdown(created_at_moscow.strftime('%d.%m.%Y %H:%M МСК'), version=2)
    escaped_mode = escape_markdown(mode_display, version=2)
    
    # Use MarkdownV2 formatting - Bold for heading, italic for mode
    # Note: Telegram doesn't support # headings, so we use bold instead
    header = f"*История \\({current_index}/{total_count}\\)* \\| _{escaped_mode}_ \\| {time_str}"
    
    # Content with proper formatting preservation
    escaped_text = escape_markdown_preserve_formatting(text_to_display or "(пусто)")
    
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
    
    # Current page indicator
    page_indicator = f"📄 {current_page}/{total_pages}"
    row.append(InlineKeyboardButton(page_indicator, callback_data="noop")) # Just display
    
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
    
    # Optional close button - could be added if needed
    # buttons.append([InlineKeyboardButton("Закрыть историю", callback_data=f"close_history:{original_msg_id}")])
    
    # For now, let user press "Готово" on original message or send new voice msg

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
    mode_order = ["brief", "detailed", "bullet", "combined", "pasha", "as_is"]
    
    # Get current default mode
    current_default_mode = DEFAULT_MODE
    try:
        current_default_mode = await get_chat_default_mode(pool, chat_id, DEFAULT_MODE)
    except Exception as e:
        logger.error(f"Error getting default mode: {e}")
    
    # Localized button texts
    default_label = "📌 По умолчанию" # Russian default
    set_default_label = "📌 Сделать по умолчанию"
    reset_defaults_label = "🔄 Сбросить умолчания"
    cancel_label = "❌ Отмена"
    
    if chat_lang == 'en':
        default_label = "📌 Default"
        set_default_label = "📌 Set as Default"
        reset_defaults_label = "🔄 Reset Defaults"
        cancel_label = "❌ Cancel"
    elif chat_lang == 'kk':
        default_label = "📌 Әдепкі"
        set_default_label = "📌 Әдепкі ретінде орнату"
        reset_defaults_label = "🔄 Әдепкілерді қалпына келтіру"
        cancel_label = "❌ Болдырмау"
    
    # Add each mode with a separate "Set as Default" button
    for mode_key in mode_order:
        if mode_key in SUPPORTED_MODES:
            emoji = mode_emojis.get(mode_key, "")
            # Get localized mode name
            mode_name = get_mode_name(mode_key, chat_lang)
            
            # Add a row for each mode
            mode_row = []
            
            # Mode selection button
            mode_row.append(InlineKeyboardButton(
                f"{emoji} {mode_name}", 
                callback_data=f"mode_set:{original_msg_id}:{mode_key}"
            ))
            
            # Default indicator or Set Default button
            if mode_key == current_default_mode:
                # This is already the default mode
                mode_row.append(InlineKeyboardButton(
                    default_label, 
                    callback_data="noop"
                ))
            else:
                # Option to set as default
                mode_row.append(InlineKeyboardButton(
                    set_default_label, 
                    callback_data=f"set_default_mode:{original_msg_id}:{mode_key}"
                ))
            
            keyboard.append(mode_row)
    
    # Add reset default and cancel buttons
    bottom_row = []
    bottom_row.append(InlineKeyboardButton(
        reset_defaults_label, 
        callback_data=f"reset_default_mode:{original_msg_id}"
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
        # Get history records
        history_records, total_count = await get_user_history(
            pool, user_id, chat_id, limit=HISTORY_PAGE_SIZE, offset=offset
        )
        
        if not history_records:
            await query.answer("No history found / История не найдена", show_alert=True)
            return
        
        # Format the history message
        record = history_records[0]
        current_index = offset + 1
        message_text = format_history_message(record, current_index, total_count)
        
        # Create pagination buttons
        reply_markup = create_history_pagination_buttons(original_msg_id, offset, total_count, HISTORY_PAGE_SIZE, chat_lang)
        
        # Update the message
        await query.edit_message_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        logger.info(f"Updated history view for user {user_id} to offset {offset}")
        
    except Exception as e:
        logger.error(f"Error in handle_history_navigation: {e}", exc_info=True)
        await query.answer("Error navigating history / Ошибка при навигации по истории", show_alert=True)

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
    
    # Send a brief welcome message
    await update.message.reply_text(get_string('start_brief', chat_lang))
    
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
    
    # Send language selection message in current chat language
    await update.message.reply_text(
        get_string('choose_language', chat_lang),
        reply_markup=reply_markup
    )

async def history_command(update: Update, context: CallbackContext) -> None:
    """Handles the /history command."""
    user = update.effective_user
    chat = update.effective_chat
    pool = context.bot_data.get('db_pool')

    if not user or not chat:
        logger.warning("Could not get user/chat info for /history command")
        return
    if not pool:
        logger.error("Database pool not available for /history")
        await update.message.reply_text(get_dual_string('error'))
        return
    
    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    
    offset = 0
    history_records, total_count = await get_user_history(
        pool, user.id, chat.id, limit=HISTORY_PAGE_SIZE, offset=offset
    )

    if not history_records:
        await update.message.reply_text("Ваша история сводок пуста.") # TODO: Add to locales
        return

    record = history_records[0]
    current_index = offset + 1
    message_text = format_history_message(record, current_index, total_count)
    reply_markup = create_history_pagination_buttons(update.message.message_id, offset, total_count, HISTORY_PAGE_SIZE)

    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2 # Changed to V2
    )

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
        history_label = "📚 История"
        done_label = "❎ Готово"
        
        if chat_lang == 'en':
            mode_label = "👤 Mode"
            redo_label = "🔁 Redo"
            history_label = "📚 History"
            done_label = "❎ Done"
        elif chat_lang == 'kk':
            mode_label = "👤 Режим"
            redo_label = "🔁 Қайта"
            history_label = "📚 Тарих"
            done_label = "❎ Дайын"
        
        keyboard = [
            [
                InlineKeyboardButton(mode_label, callback_data=f"mode_select:{message.message_id}"),
                InlineKeyboardButton(redo_label, callback_data=f"redo:{message.message_id}"),
            ],
            [
                InlineKeyboardButton(history_label, callback_data=f"history:{message.message_id}:0"),
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

async def button_callback_handler(update: Update, context: CallbackContext):
    """Handle clicks on inline keyboard buttons."""
    
    query = update.callback_query
    await query.answer()  # Required to acknowledge the callback
    
    # Parse callback data
    data_parts = query.data.split(":")
    action = data_parts[0]
    chat_id = update.effective_chat.id
    
    # Get pool for database operations
    pool = context.bot_data.get('db_pool')
    if not pool:
        await query.answer("Database error", show_alert=True)
        return
    
    # Get chat's current language
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Handle noop action specifically
    if action == "noop":
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
            # Get the language confirmation message
            lang_info = LANGUAGES[language]
            await query.edit_message_text(
                get_string('language_set', language),
                reply_markup=None
            )
            
            # Send the full welcome message in the selected language
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_string('start', language),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer("Failed to set language", show_alert=True)
        return
    
    elif action == "settings":
        # Create settings keyboard
        keyboard = [
            [InlineKeyboardButton("🌐 Change Language", callback_data="language_menu")],
            [InlineKeyboardButton("💰 Subscription Info", callback_data="subscription_info")],
            [InlineKeyboardButton("❌ Close", callback_data="close_settings")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send settings message
        await query.edit_message_text(
            get_string('settings', chat_lang),
            reply_markup=reply_markup
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
    
    # For original message callbacks, ensure we have the original_msg_id
    if action in ["confirm", "mode_select", "mode_set", "redo", "history", "history_nav", 
                  "set_default_mode", "cancel_mode_select", "reset_default_mode"]:
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
                
                # Add the default mode button to the current mode keyboard
                if action == "mode_set":
                    # Return to mode selection with updated default
                    await show_mode_selection(update, context, original_msg_id)
                else:
                    # Return to the original buttons
                    await query.edit_message_reply_markup(reply_markup=create_action_buttons(original_msg_id, chat_lang))
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
            
        elif action == "reset_default_mode":
            # Clear default mode for this chat
            success = await clear_chat_default_mode(pool, chat_id)
            if success:
                # Get default mode name in current chat language
                default_mode_name = get_mode_name(DEFAULT_MODE, chat_lang)
                confirm_message = f"Default mode reset to '{default_mode_name}'"
                if chat_lang == 'ru':
                    confirm_message = f"Режим по умолчанию сброшен на '{default_mode_name}'"
                elif chat_lang == 'kk':
                    confirm_message = f"Әдепкі режим '{default_mode_name}' қалпына келтірілді"
                
                await query.answer(confirm_message, show_alert=True)
                
                # Return to the original buttons
                await query.edit_message_reply_markup(reply_markup=create_action_buttons(original_msg_id, chat_lang))
            else:
                # Error message in current chat language
                error_message = "Failed to reset default mode"
                if chat_lang == 'ru':
                    error_message = "Не удалось сбросить режим по умолчанию"
                elif chat_lang == 'kk':
                    error_message = "Әдепкі режимді қалпына келтіру сәтсіз аяқталды"
                
                await query.answer(error_message, show_alert=True)
            return
    
    # Handle unknown actions
    logger.warning(f"Unknown button callback action: {action}")
    
    # Error message in current chat language
    error_message = "Oops! Something went wrong with that button."
    if chat_lang == 'ru':
        error_message = "Упс! Что-то пошло не так с этой кнопкой."
    elif chat_lang == 'kk':
        error_message = "Упс! Осы түймемен бірдеңе дұрыс болмады."
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=error_message
    )

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
    application.add_handler(CommandHandler("history", history_command)) # Added

    # Handler for MP3/WAV conversion (Document or Audio)
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))

    # Handler for Voice messages (Summarization)
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message)) # Added

    # Handler for Callback Queries
    application.add_handler(CallbackQueryHandler(button_callback_handler)) # Added

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main() 
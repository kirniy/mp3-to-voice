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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message # Added
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ContextTypes
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

async def create_voice_settings_buttons(original_msg_id: int, language: str = 'ru') -> InlineKeyboardMarkup:
    """Creates the settings buttons for voice message responses."""
    # Localize button labels
    lang_btn_text = get_string('settings_language', language)
    history_btn_text = get_string('settings_history', language)
    mode_btn_text = get_string('settings_mode', language)
    sub_btn_text = get_string('settings_subscription', language)
    back_btn_text = get_string('button_back', language)
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(lang_btn_text, callback_data=f"voice_language_menu:{original_msg_id}"),
            InlineKeyboardButton(mode_btn_text, callback_data=f"mode_select:{original_msg_id}"), # Mode selection for this message
        ],
        [
            InlineKeyboardButton(history_btn_text, callback_data=f"history:{original_msg_id}:0"),
            InlineKeyboardButton(sub_btn_text, callback_data=f"voice_subscription_info:{original_msg_id}"),
        ],
        [
            InlineKeyboardButton(back_btn_text, callback_data=f"back_to_main:{original_msg_id}") # Back to main action buttons
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

def create_history_pagination_buttons(context_message_id: int, current_offset: int, total_count: int, page_size: int, language: str = 'ru', from_settings_menu: bool = False) -> InlineKeyboardMarkup | None:
    """Creates buttons for history pagination, including delete and export.
    
    Args:
        context_message_id: The ID of the message context (original voice msg or settings msg).
        current_offset: The current offset in the history.
        total_count: Total number of history records.
        page_size: Number of items per page (currently 1).
        language: The chat language.
        from_settings_menu: True if called from the main settings menu, False otherwise.
    """
    if total_count <= 0 and not from_settings_menu:
        # Only show delete/export if NOT from settings and history is empty?
        # Let's adjust: Always show nav row if possible, show actions only if not from_settings
        pass # Continue below

    # Since page_size is 1, current_offset is the index
    current_index = current_offset 
    
    buttons = []
    nav_row = []
    
    # Previous page button (if not the first item)
    if current_index > 0:
        prev_offset = current_index - 1
        prev_label = "⬅️"
        nav_row.append(InlineKeyboardButton(prev_label, callback_data=f"history_nav:{context_message_id}:{prev_offset}"))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    # Center button to return to settings menu (localize label later if needed)
    # Distinguish between back_to_main (voice context) and settings (main context)
    if from_settings_menu:
        back_label = get_string('settings_title', language)
        back_callback = "settings" # Go back to main settings
    else:
        back_label = get_string('button_back', language)
        back_callback = f"back_to_main:{context_message_id}" # Go back to voice msg buttons

    nav_row.append(InlineKeyboardButton(back_label, callback_data=back_callback)) 
    
    # Next page button (if not the last item)
    if total_count > 0 and current_index < total_count - 1:
        next_offset = current_index + 1
        next_label = "➡️"
        nav_row.append(InlineKeyboardButton(next_label, callback_data=f"history_nav:{context_message_id}:{next_offset}"))
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="noop")) # Placeholder
    
    buttons.append(nav_row)

    # Conditionally add Delete and Export buttons
    if not from_settings_menu:
        action_row = []
        delete_label = get_string('history_delete', language)
        export_label = get_string('history_export', language)
        
        # Use context_message_id here
        action_row.append(InlineKeyboardButton(delete_label, callback_data=f"delete_history_confirm:{context_message_id}:{current_offset}")) # Include offset for cancel
        action_row.append(InlineKeyboardButton(export_label, callback_data=f"export_history:{context_message_id}"))
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
    # Use query.answer for quick feedback, edit message later
    await query.answer(f"⏳ Switching to '{localized_mode_name}'...")

    try:
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        
        if not db_record:
            logger.error(f"Record not found for original message {original_msg_id} during mode set")
            await query.edit_message_text(
                get_string('error_record_not_found', chat_lang),
                reply_markup=create_action_buttons(original_msg_id, chat_lang) # Show buttons again
            )
            return
        
        record_id = db_record['id']
        audio_file_id = db_record['telegram_audio_file_id']
        user_id = db_record['user_id']
        transcript_text = db_record['transcript_text'] # Keep original transcript
        current_summary_msg_id = db_record['summary_telegram_message_id'] # Get current summary msg id

        # Fetch user/time info for header (consistent across modes)
        original_user = await context.bot.get_chat(user_id)
        # Use DB timestamp if available, else query message date
        original_message_date = db_record.get('created_at') or query.message.date
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        # Create standard action buttons
        reply_markup = create_action_buttons(original_msg_id, chat_lang)
        
        # Show processing indicator by editing the *current* summary message
        processing_text = f"⏳ Generating {localized_mode_name}..."
        if chat_lang == 'ru': processing_text = f"⏳ Генерирую {localized_mode_name}..."
        elif chat_lang == 'kk': processing_text = f"⏳ {localized_mode_name} жасалуда..."
        try:
            # Edit the existing message (text or photo caption)
            await context.bot.edit_message_caption(
                 chat_id=chat_id, message_id=current_summary_msg_id, caption=processing_text
            )
        except Exception: # Try editing as text if caption edit fails
             try:
                  await context.bot.edit_message_text(
                       processing_text, chat_id=chat_id, message_id=current_summary_msg_id
                  )
             except Exception as edit_err:
                  logger.warning(f"Could not edit message {current_summary_msg_id} for mode set status: {edit_err}")
                  # Proceed anyway

        # --- Re-download and process audio ---
        summary_text = None
        new_transcript_text = transcript_text # Start with existing transcript
        mermaid_code_body = None # For diagram mode saving
        diagram_png = None # For diagram mode sending

        with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
            try:
                file = await context.bot.get_file(audio_file_id)
                await file.download_to_drive(custom_path=temp_audio_file.name)
                logger.info(f"Re-downloaded audio {audio_file_id} for mode change to {new_mode}.")
                
                # Process with Gemini (only gets summary/transcript here)
                # Diagram-specific generation happens later if needed
                summary_text, updated_transcript = await process_audio_with_gemini(temp_audio_file.name, new_mode, chat_lang)
                if updated_transcript: # Update transcript if Gemini provided a new one
                    new_transcript_text = updated_transcript
                    
            except Exception as audio_err:
                 logger.error(f"Failed to download/process audio for mode set: {audio_err}", exc_info=True)
                 await context.bot.edit_message_text(
                      get_string('error', chat_lang), chat_id=chat_id, message_id=current_summary_msg_id,
                      reply_markup=reply_markup # Add buttons back on error
                 )
                 return

        # --- Handle Diagram Mode --- 
        if new_mode == 'diagram':
            logger.info(f"Switching to diagram mode for original message {original_msg_id}...")
            
            if not new_transcript_text: # Need transcript for diagrams
                 logger.error("Transcript missing, cannot generate diagram.")
                 await context.bot.edit_message_text(
                      get_string('error', chat_lang), chat_id=chat_id, message_id=current_summary_msg_id,
                      reply_markup=reply_markup
                 )
                 return

            # Generate diagram data and render PNG
            author_name = original_user.full_name # Use already fetched name
            try:
                diagram_data = await generate_diagram_data(new_transcript_text, chat_lang, author_name)
                if not diagram_data:
                    raise ValueError(get_string('diagram_error_data', chat_lang))
                    
                mermaid_code_body = create_mermaid_syntax(diagram_data, chat_lang)
                if mermaid_code_body is None:
                    raise ValueError(get_string('diagram_error_syntax', chat_lang))
                    
                diagram_png = render_mermaid_to_png(mermaid_code_body, diagram_data, chat_lang)
                if not diagram_png:
                    raise ValueError(get_string('diagram_error_render', chat_lang))

                # Successfully generated diagram
                logger.info(f"Diagram generated successfully for original message {original_msg_id}.")

            except Exception as diagram_err:
                logger.error(f"Error generating diagram: {diagram_err}", exc_info=True)
                error_message = str(diagram_err) if isinstance(diagram_err, ValueError) else get_string('error', chat_lang)
                # Edit the *original* message back to show the error
                try:
                    await context.bot.edit_message_text(
                        f"{header}\n\n{escape_markdown_preserve_formatting(error_message)}",
                        chat_id=chat_id, message_id=current_summary_msg_id,
                        reply_markup=reply_markup, parse_mode='MarkdownV2'
                    )
                except Exception: # If editing as text fails (was photo), try editing caption
                     try:
                          await context.bot.edit_message_caption(
                               chat_id=chat_id, message_id=current_summary_msg_id,
                               caption=f"{header}\n\n{escape_markdown_preserve_formatting(error_message)}",
                               reply_markup=reply_markup, parse_mode='MarkdownV2'
                          )
                     except Exception as final_edit_err:
                          logger.error(f"Failed to edit message/caption to show diagram error: {final_edit_err}")
                return

            # --- Send Diagram (Delete old message, send new photo) ---
            new_summary_message = None
            try:
                # Delete the previous message (text or photo)
                await context.bot.delete_message(chat_id=chat_id, message_id=current_summary_msg_id)
                logger.info(f"Deleted previous message {current_summary_msg_id}")

                # Send the new photo message, replying to the *original voice message*
                new_summary_message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=diagram_png,
                    caption=header,
                    parse_mode='MarkdownV2',
                    reply_markup=reply_markup,
                    reply_to_message_id=original_msg_id # Reply to the voice message
                )
                logger.info(f"Sent new diagram message {new_summary_message.message_id} replying to {original_msg_id}")

                # Update DB with the NEW message ID and mermaid code
                await update_summary_mode_and_text(
                    pool=pool,
                    record_id=record_id,
                    new_mode=new_mode,
                    new_summary_text=mermaid_code_body, # Save mermaid code
                    new_transcript_text=new_transcript_text,
                    new_summary_message_id=new_summary_message.message_id # IMPORTANT: Update message ID
                )

            except Exception as send_err:
                logger.error(f"Error deleting old message or sending new diagram: {send_err}", exc_info=True)
                # Try to send a text error message as a fallback
                await context.bot.send_message(
                     chat_id=chat_id,
                     text=f"{header}\n\n{escape_markdown_preserve_formatting(get_string('error', chat_lang))}",
                     reply_to_message_id=original_msg_id, # Reply to original voice message
                     parse_mode='MarkdownV2'
                 )
                # We might have a dangling record with the old message ID here. Difficult to recover fully.
            return # Finished diagram mode switch

        # --- Handle Text Modes ---
        else:
            logger.info(f"Switching to text mode '{new_mode}' for original message {original_msg_id}...")
            
            # Determine display text for text modes
            if new_mode == 'as_is' or new_mode == 'transcript':
                display_text = new_transcript_text
            else: # brief, detailed, bullet, combined, pasha
                display_text = summary_text

            if display_text is None: # Should have transcript at least
                logger.error(f"Error: No content generated for text mode {new_mode}")
                await context.bot.edit_message_text(
                    get_string('error', chat_lang), chat_id=chat_id, message_id=current_summary_msg_id,
                    reply_markup=reply_markup # Add buttons back
                )
                return

            escaped_display_text = escape_markdown_preserve_formatting(display_text)
            final_text = f"{header}\n\n{escaped_display_text}"

            # --- Edit Message (Text or Photo Caption) ---
            try:
                # Try editing as a text message first
                await context.bot.edit_message_text(
                    final_text,
                    chat_id=chat_id,
                    message_id=current_summary_msg_id,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.info(f"Edited text message {current_summary_msg_id} for mode {new_mode}")

            except Exception: # If that fails, assume it was a photo and try editing caption
                try:
                    await context.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=current_summary_msg_id,
                        caption=final_text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    logger.info(f"Edited photo caption {current_summary_msg_id} for mode {new_mode}")
                except Exception as edit_err:
                    logger.error(f"Failed to edit message text or caption for mode {new_mode}: {edit_err}", exc_info=True)
                    # If editing caption fails, the message might have been deleted or is not a photo.
                    # Try sending a new text message as a robust fallback.
                    try:
                        new_summary_message = await context.bot.send_message(
                            chat_id=chat_id,
                            text=final_text,
                            reply_to_message_id=original_msg_id,
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        logger.info(f"Sent new text message {new_summary_message.message_id} as fallback for mode {new_mode}")
                        # Update DB with the NEW message ID
                        await update_summary_mode_and_text(
                             pool=pool, record_id=record_id, new_mode=new_mode,
                             new_summary_text=summary_text, new_transcript_text=new_transcript_text,
                             new_summary_message_id=new_summary_message.message_id
                        )
                        return # Successfully sent fallback message
                    except Exception as send_fallback_err:
                         logger.error(f"Failed to send fallback text message for mode {new_mode}: {send_fallback_err}")
                         # At this point, recovery is difficult. Log and return.
                         return

            # If editing text or caption succeeded, update DB keeping the *existing* message ID
            await update_summary_mode_and_text(
                pool=pool,
                record_id=record_id,
                new_mode=new_mode,
                new_summary_text=summary_text, # Save the specific summary for the mode
                new_transcript_text=new_transcript_text,
                new_summary_message_id=current_summary_msg_id # IMPORTANT: Keep existing message ID
            )
            logger.info(f"Successfully updated summary record {record_id} to text mode {new_mode}")

    except Exception as e:
        logger.error(f"General error in mode_set (mode: {new_mode}, original_msg: {original_msg_id}): {e}", exc_info=True)
        try:
            # General fallback: Try to edit the message to show an error
            error_buttons = create_action_buttons(original_msg_id, chat_lang)
            # Use current_summary_msg_id if available, else query message id
            msg_id_to_edit = current_summary_msg_id if 'current_summary_msg_id' in locals() else query.message.message_id
            await context.bot.edit_message_text(
                get_string('error', chat_lang),
                chat_id=chat_id,
                message_id=msg_id_to_edit,
                reply_markup=error_buttons
            )
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
    help_btn_text = get_string('settings_help', chat_lang) # New Help button
    close_btn_text = "❌ Close"
    
    if chat_lang == 'ru':
        lang_btn_text = "🌐 Изменить язык"
        history_btn_text = "📚 История"
        sub_btn_text = "💰 Информация о подписке"
        mode_btn_text = "⚙️ Выбор режима"
        # help_btn_text is already localized via get_string
        close_btn_text = "❌ Закрыть"
    elif chat_lang == 'kk':
        lang_btn_text = "🌐 Тілді өзгерту"
        history_btn_text = "📚 Тарих"
        sub_btn_text = "💰 Жазылым туралы ақпарат"
        mode_btn_text = "⚙️ Әдепкі режим"
        # help_btn_text is already localized via get_string
        close_btn_text = "❌ Жабу"
    
    # Create keyboard
    keyboard = [
        [InlineKeyboardButton(lang_btn_text, callback_data="language_menu")],
        [InlineKeyboardButton(mode_btn_text, callback_data="settings_mode_menu")],
        [InlineKeyboardButton(history_btn_text, callback_data="show_command_history:0")],
        [InlineKeyboardButton(sub_btn_text, callback_data="subscription_info")],
        [InlineKeyboardButton(help_btn_text, callback_data="help")], # Added Help button
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

        # 4. Format response header
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = message.date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(message.from_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"

        # 5. Create action buttons
        reply_markup = create_action_buttons(message.message_id, chat_lang)

        # 6. Process based on mode (Diagram vs Text)
        sent_message = None
        mermaid_syntax_to_save = None # Variable to hold mermaid syntax for DB
        
        if mode == "diagram":
            logger.info(f"Processing voice message {message.message_id} in diagram mode.")
            
            # Get author information
            author_name = "Unknown User"
            try:
                user_id = message.from_user.id
                author_chat = await context.bot.get_chat(user_id)
                author_name = author_chat.full_name or author_name
            except Exception as name_e:
                logger.warning(f"Could not fetch author name: {name_e}")
                
            # Generate diagram with author info and language
            diagram_data = await generate_diagram_data(transcript_text, chat_lang, author_name)
            if not diagram_data:
                error_msg = get_string('diagram_error_data', chat_lang)
                await status_message.edit_text(error_msg)
                return
                
            mermaid_code_body = create_mermaid_syntax(diagram_data, chat_lang) # Changed variable name
            if mermaid_code_body is None: # Check for None explicitly
                error_msg = get_string('diagram_error_syntax', chat_lang)
                await status_message.edit_text(error_msg)
                return
                
            # Save for history recording
            mermaid_syntax_to_save = mermaid_code_body
            
            # Render the diagram including metadata and language
            diagram_png = render_mermaid_to_png(mermaid_code_body, diagram_data, chat_lang) # Pass mermaid_code_body
            if not diagram_png:
                error_msg = get_string('diagram_error_render', chat_lang)
                await status_message.edit_text(error_msg)
                return
            
            # d. Send Photo
            try:
                # Send the photo with caption and buttons
                sent_message = await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=diagram_png,
                    caption=header, 
                    parse_mode='MarkdownV2',
                    reply_markup=reply_markup,
                    reply_to_message_id=message.message_id
                )
                # Delete the original status message
                await status_message.delete()
                logger.info(f"Sent diagram message {sent_message.message_id} for original message {message.message_id}")
            except Exception as send_photo_e:
                logger.error(f"Failed to send diagram photo: {send_photo_e}", exc_info=True)
                error_msg = get_string('error', chat_lang) # Generic error
                # Try editing the status message as fallback
                await status_message.edit_text(error_msg)
                return
        
        else: # Handle text modes
            # Determine primary text to display based on the mode
            if mode == 'as_is' or mode == 'transcript':
                display_text = transcript_text
            else:
                display_text = summary_text if summary_text is not None else transcript_text

            # Properly escape content for MarkdownV2
            escaped_display_text = escape_markdown_preserve_formatting(display_text)
            final_text = f"{header}\n\n{escaped_display_text}"

            # Send response message (edit the status message)
            sent_message = await status_message.edit_text(
                final_text,
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )
            logger.info(f"Sent summary message {sent_message.message_id} for original message {message.message_id}")
            
        # Ensure sent_message is not None before saving to DB
        if sent_message is None:
             logger.error(f"Sent message is None for original message {message.message_id}, cannot save to DB.")
             # No need to edit status_message here, as it was likely already edited or deleted
             return

        # 8. Save details to DB
        # Use mermaid_syntax_to_save if mode is diagram, otherwise use summary_text
        text_to_save_in_summary = mermaid_syntax_to_save if mode == "diagram" else summary_text
        
        record_id = await save_summary(
            pool=pool,
            user_id=user.id,
            chat_id=message.chat_id,
            original_message_id=message.message_id,
            summary_message_id=sent_message.message_id,
            audio_file_id=voice.file_id, # Store Telegram's file ID
            mode=mode,
            summary_text=text_to_save_in_summary, # Store Mermaid syntax or summary text
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
    pool = context.bot_data.get('db_pool')
    
    # Ensure pool is available
    if not pool:
        await query.answer(get_string('error_db', 'ru'), show_alert=True) # Default to RU for initial error
        return
        
    # Get chat language AFTER confirming pool exists
    chat_lang = await get_chat_language(pool, chat_id)

    data = query.data
    data_parts = data.split(":")
    action = data_parts[0]
    logger.info(f"Button callback received: action='{action}', data='{data}', user={user_id}, chat={chat_id}")

    # === No-Context Actions (Called from main settings or language select) ===
    if action == "noop":
        await query.answer() # Acknowledge silently
        return

    elif action == "settings":
        # This handles the /settings command or the top-level settings button
        await show_main_settings_menu(update, context)
        await query.answer()
        return

    elif action == "language_menu": # Show language options from main settings
        await show_language_selection(update, context, from_settings=True)
        await query.answer()
        return
        
    elif action == "set_language": # Set language from main language menu
        if len(data_parts) > 1:
            new_lang = data_parts[1]
            if new_lang in LANGUAGES:
                 await set_chat_language(pool, chat_id, new_lang)
                 await query.answer(f"{LANGUAGES[new_lang]['emoji']} Language set to {LANGUAGES[new_lang]['name']}")
                 # Go back to main settings menu
                 await show_main_settings_menu(update, context)
            else:
                 await query.answer("Invalid language code.", show_alert=True)
        else:
             await query.answer("Invalid language data.", show_alert=True)
        return # Answer handled within

    elif action == "settings_mode_menu": # Show default mode options from main settings
        await show_settings_mode_menu(update, context)
        await query.answer()
        return
        
    elif action == "set_default_mode_main": # Set default mode from main settings menu
        if len(data_parts) > 1:
             new_mode = data_parts[1]
             if new_mode in SUPPORTED_MODES:
                  success = await set_chat_default_mode(pool, chat_id, new_mode)
                  if success:
                       localized_mode_name = get_mode_name(new_mode, chat_lang)
                       await query.answer(f"Default mode set to '{localized_mode_name}'")
                       await show_main_settings_menu(update, context) # Go back to main settings
                  else:
                       await query.answer(get_string('error_db', chat_lang), show_alert=True)
             else:
                  await query.answer("Invalid mode.", show_alert=True)
        else:
             await query.answer("Invalid mode data.", show_alert=True)
        return # Answer handled within

    elif action == "show_command_history": # Show history from main settings
        if len(data_parts) > 1:
             try:
                  offset = int(data_parts[1])
                  await show_command_history(update, context, offset)
             except (ValueError, IndexError):
                  await query.answer("Invalid history data.", show_alert=True)
        else:
             await query.answer("Invalid history data.", show_alert=True)
        await query.answer() # Acknowledge button press
        return

    elif action == "subscription_info": # Show subscription info from main settings
        await show_subscription_info(update, context)
        await query.answer()
        return
        
    elif action == "help": # Show help info from main settings
        # Check if we need context (e.g., back button from message-specific help)
        original_msg_id_str = data_parts[1] if len(data_parts) > 1 else None
        await show_help_menu(update, context, original_msg_id_str)
        await query.answer()
        return

    elif action == "close_settings": # Close the main settings message
        try:
            await query.message.delete()
            await query.answer("Settings closed.")
        except Exception as e:
            logger.warning(f"Could not delete settings message: {e}")
            await query.answer() # Acknowledge anyway
        return

    # === Actions Requiring Original Message Context ===
    # All actions below this point require original_msg_id as the second part (data_parts[1])
    original_msg_id = None
    if len(data_parts) > 1:
        try:
            original_msg_id = int(data_parts[1])
        except (ValueError, TypeError, IndexError):
             # Allow certain actions like history nav/delete to potentially parse context differently if needed,
             # but most message-specific actions require original_msg_id here.
            if action not in ["history_nav", "delete_history_confirm", "delete_history_execute", "delete_history_execute_all", "export_history"]:
                 logger.warning(f"Callback action '{action}' missing or invalid original_msg_id in data: '{data}'")
                 await query.answer(get_string('error_invalid_context', chat_lang), show_alert=True)
                 return
            else:
                  pass # Let specific handlers parse their own context if necessary
    else:
        # If no second part, it's not a message-context action (should have been handled above)
        logger.warning(f"Callback action '{action}' seems to be missing original_msg_id context: '{data}'")
        await query.answer(get_string('error_invalid_context', chat_lang), show_alert=True)
        return

    # --- Dispatch Message-Context Actions ---
    if action == "confirm":
        try:
            # Remove buttons by editing reply markup to None
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer(get_string('action_confirmed', chat_lang)) # Localized confirmation
        except Exception as e:
            logger.warning(f"Could not remove buttons on confirm: {e}")
            await query.answer() # Acknowledge anyway
        return

    elif action == "mode_select": # Show mode options for the specific message
        await show_mode_selection(update, context, original_msg_id)
        await query.answer() # Acknowledge button press
        return

    elif action == "mode_set": # Set mode for the specific message
        await mode_set(update, context, data_parts, original_msg_id)
        # mode_set handles its own query.answer and message edits
        return

    elif action == "redo": # Redo processing for the specific message
        await redo(update, context, original_msg_id)
        # redo handles its own query.answer and message edits
        return

    elif action == "settings": # Show settings specific to this voice message
        # This action name is overloaded, context (original_msg_id) differentiates it
        await show_voice_message_settings(update, context, original_msg_id)
        await query.answer()
        return
        
    elif action == "back_to_main": # Back from voice settings to main action buttons
        await back_to_main_buttons(update, context, original_msg_id)
        await query.answer()
        return

    elif action == "show_pin_menu": # Show confirmation to pin mode from message context
        await show_pin_menu(update, context, original_msg_id)
        await query.answer() # Acknowledge button press
        return

    elif action == "set_default_mode": # Pin the mode from the specific message as chat default
        if len(data_parts) > 2:
            mode_to_pin = data_parts[2]
            await set_chat_default_mode_from_pin(update, context, original_msg_id, mode_to_pin)
        else:
            logger.error("Invalid data for set_default_mode")
            await query.answer(get_string('error', chat_lang), show_alert=True)
        # set_chat_default_mode_from_pin handles its own answer/edits
        return

    elif action == "cancel_mode_select": # Cancel mode selection, go back to main buttons
        await back_to_main_buttons(update, context, original_msg_id)
        await query.answer()
        return
        
    elif action == "voice_language_menu": # Show language options within voice message settings
        await show_language_selection(update, context, from_settings=False, original_msg_id=original_msg_id)
        await query.answer()
        return
        
    elif action == "set_language_and_back": # Set language from voice message settings
        if len(data_parts) > 2:
             new_lang = data_parts[1]
             original_msg_id_for_back = int(data_parts[2])
             if new_lang in LANGUAGES:
                  await set_chat_language(pool, chat_id, new_lang)
                  await query.answer(f"{LANGUAGES[new_lang]['emoji']} Language set to {LANGUAGES[new_lang]['name']}")
                  # Go back to voice message settings menu
                  await show_voice_message_settings(update, context, original_msg_id_for_back)
             else:
                  await query.answer("Invalid language code.", show_alert=True)
        else:
             await query.answer("Invalid language data.", show_alert=True)
        return # Answer handled within

    elif action == "voice_subscription_info": # Show subscription info from voice message settings
        await show_subscription_info(update, context, original_msg_id=original_msg_id)
        await query.answer()
        return

    elif action == "history": # Show history starting from a specific voice message context
        # Called from voice message settings
        if len(data_parts) > 2:
             try:
                  offset = int(data_parts[2])
                  # Use the original_msg_id as the context_message_id for history nav
                  await handle_history_navigation(update, context, [action, str(original_msg_id), str(offset)], from_settings_menu=False)
             except (ValueError, IndexError):
                  await query.answer("Invalid history data.", show_alert=True)
        else:
             await query.answer("Invalid history data.", show_alert=True)
        # handle_history_navigation handles its own query.answer
        return
        
    elif action == "history_nav": # Navigate history pages
        # Context ID (data_parts[1]) could be original_msg_id or settings msg id
        await handle_history_navigation(update, context, data_parts) # Pass all parts
        # handle_history_navigation handles its own query.answer
        return

    elif action == "delete_history_confirm":
        await show_delete_history_confirmation(update, context, data_parts)
        # show_delete_history_confirmation handles query.answer
        return
        
    elif action == "delete_history_execute":
        await execute_delete_history(update, context, data_parts)
        # execute_delete_history handles query.answer
        return
        
    elif action == "delete_history_execute_all":
        await execute_delete_all_history(update, context, data_parts)
        # execute_delete_all_history handles query.answer
        return

    elif action == "export_history":
        await export_user_history(update, context, data_parts)
        # export_user_history handles query.answer
        return

    # Fallback for unhandled actions
    logger.warning(f"Unhandled button callback action: '{action}' with data '{data}'")
    await query.answer(get_string('error_unhandled_action', chat_lang)) # Localized message

async def handle_history_navigation(update: Update, context: CallbackContext, data_parts: list, from_settings_menu: bool | None = None):
    """Handles navigation through user history. Can be called from main settings or voice settings."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    pool = context.bot_data.get('db_pool')
    chat_lang = await get_chat_language(pool, chat_id)

    if not pool:
        await query.answer("Database error", show_alert=True)
        return
    
    if len(data_parts) < 3:
        await query.answer("Invalid navigation data", show_alert=True)
        return

    try:
        # Note: This ID might be the original voice message OR the settings message ID
        context_message_id = int(data_parts[1]) 
        offset = int(data_parts[2])
    except ValueError:
        await query.answer("Invalid data format", show_alert=True)
        return
        
    limit = HISTORY_PAGE_SIZE # Use constant
    
    # Determine context if not explicitly passed (e.g., direct call from history_nav)
    if from_settings_menu is None:
         try:
              # Check if context_message_id corresponds to an existing summary record
              voice_record_check = await get_summary_context_for_callback(pool, context_message_id, chat_id)
              from_settings_menu = not bool(voice_record_check) # True if NO record found
              logger.debug(f"History nav context determined: from_settings={from_settings_menu} (context_msg_id: {context_message_id})")
         except Exception as e:
              logger.warning(f"Error auto-detecting history context, assuming settings: {e}")
              from_settings_menu = True
    
    try:
        # Fetch the history record for the new offset
        history_records, total_count = await get_user_history(pool, user_id, chat_id, limit, offset)

        if not history_records:
            # Should not happen if total_count > 0, but handle defensively
            await query.answer("History record not found for this offset.", show_alert=True)
            return
            
        record = history_records[0]
        current_index = offset + 1

        # Fetch author name (similar to show_command_history)
        author_name = "Unknown User"
        record_user_id = record.get('user_id')
        if record_user_id:
            try:
                author_chat = await context.bot.get_chat(record_user_id)
                author_name = author_chat.full_name or author_name
            except Exception as name_e:
                logger.warning(f"Could not fetch author name for user_id {record_user_id}: {name_e}")

        history_message = format_history_message(record, current_index, total_count, chat_lang, author_name)
        
        # Create pagination buttons, passing the from_settings flag
        reply_markup = create_history_pagination_buttons(
            context_message_id, 
            offset, 
            total_count, 
            limit, 
            chat_lang, 
            from_settings_menu=from_settings_menu # Pass the determined flag
        )
        
        await query.edit_message_text(
            history_message, 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Error during history navigation: {e}", exc_info=True)
        await query.answer("Error navigating history.", show_alert=True)
        
    await query.answer()

async def show_main_settings_menu(update: Update, context: CallbackContext):
    """Shows the main settings menu (not tied to a specific message)."""
    query = update.callback_query
    message = query.message if query else update.message # Handle command or callback
    chat_id = message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return # Error handled elsewhere or logged
    
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Localize menu options from locales.py
    lang_btn_text = get_string('settings_language', chat_lang)
    history_btn_text = get_string('settings_history', chat_lang)
    sub_btn_text = get_string('settings_subscription', chat_lang)
    mode_btn_text = get_string('settings_default_mode', chat_lang)
    help_btn_text = get_string('settings_help', chat_lang)
    close_btn_text = get_string('button_close', chat_lang)
    
    keyboard = [
        [InlineKeyboardButton(lang_btn_text, callback_data="language_menu")],
        [InlineKeyboardButton(mode_btn_text, callback_data="settings_mode_menu")],
        [InlineKeyboardButton(history_btn_text, callback_data="show_command_history:0")], # Show history from chat context
        [InlineKeyboardButton(sub_btn_text, callback_data="subscription_info")],
        [InlineKeyboardButton(help_btn_text, callback_data="help")],
        [InlineKeyboardButton(close_btn_text, callback_data="close_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = get_string('settings_welcome', chat_lang)
    
    try:
        if query:
             await query.edit_message_text(welcome_text, reply_markup=reply_markup)
        else:
             await message.reply_text(welcome_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error showing main settings menu: {e}")
        if query: await query.answer(get_string('error', chat_lang), show_alert=True)

async def show_language_selection(update: Update, context: CallbackContext, from_settings: bool, original_msg_id: int | None = None):
    """Shows language selection options, adapting callback for context."""
    query = update.callback_query
    message = query.message
    chat_id = message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    
    chat_lang = await get_chat_language(pool, chat_id)
    
    keyboard = []
    row = []
    for code, lang_info in LANGUAGES.items():
        # Callback depends on whether we came from main settings or voice settings
        callback_action = "set_language" if from_settings else f"set_language_and_back:{code}:{original_msg_id}"
        button = InlineKeyboardButton(
            f"{lang_info['emoji']} {lang_info['name']}", 
            callback_data=callback_action
        )
        row.append(button)
        if len(row) == 2:  # 2 buttons per row
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    # Add Back button
    back_callback = "settings" if from_settings else f"settings:{original_msg_id}" # Go back to main settings or voice settings
    back_label = get_string('button_back', chat_lang)
    keyboard.append([InlineKeyboardButton(back_label, callback_data=back_callback)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = get_string('language_select', chat_lang)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    
async def show_settings_mode_menu(update: Update, context: CallbackContext):
    """Shows the menu to select the default mode for the chat (from main settings)."""
    query = update.callback_query
    message = query.message
    chat_id = message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    
    chat_lang = await get_chat_language(pool, chat_id)
    current_default_mode = await get_chat_default_mode(pool, chat_id, DEFAULT_MODE)
    
    keyboard = []
    mode_emojis = {"brief": "📝", "detailed": "📋", "bullet": "🔍", "combined": "📊", "as_is": "📄", "pasha": "💊", "diagram": "📈"}
    mode_order = ["as_is", "brief", "detailed", "bullet", "combined", "diagram", "pasha"]

    for mode_key in mode_order:
        if mode_key in SUPPORTED_MODES:
            emoji = mode_emojis.get(mode_key, "")
            mode_name = get_mode_name(mode_key, chat_lang)
            display_name = f"{emoji} {mode_name}"
            if mode_key == current_default_mode:
                display_name += " ★" # Indicator for current default
            
            keyboard.append([
                InlineKeyboardButton(display_name, callback_data=f"set_default_mode_main:{mode_key}")
            ])
    
    # Add Back button to main settings
    back_label = get_string('button_back', chat_lang)
    keyboard.append([InlineKeyboardButton(back_label, callback_data="settings")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = get_string('default_mode_select', chat_lang)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    
async def show_command_history(update: Update, context: CallbackContext, offset: int):
    """Displays user history, called from the main settings menu."""
    query = update.callback_query
    message = query.message
    chat_id = message.chat_id
    user_id = query.from_user.id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    
    chat_lang = await get_chat_language(pool, chat_id)
    limit = HISTORY_PAGE_SIZE
    
    try:
        history_records, total_count = await get_user_history(pool, user_id, chat_id, limit, offset)
        
        if not history_records:
            await query.edit_message_text(get_string('history_empty', chat_lang), reply_markup=create_history_pagination_buttons(message.message_id, offset, total_count, limit, chat_lang, from_settings_menu=True))
            return
            
        record = history_records[0]
        current_index = offset + 1
        
        # Fetch author name safely
        author_name = "Unknown User"
        record_user_id = record.get('user_id')
        if record_user_id:
             try:
                  author_chat = await context.bot.get_chat(record_user_id)
                  author_name = author_chat.full_name or author_name
             except Exception as name_e:
                  logger.warning(f"Could not fetch author name for history: {name_e}")

        history_message = format_history_message(record, current_index, total_count, chat_lang, author_name)
        # Use the settings message ID as context for pagination
        reply_markup = create_history_pagination_buttons(message.message_id, offset, total_count, limit, chat_lang, from_settings_menu=True)
        
        await query.edit_message_text(history_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Error fetching/displaying command history: {e}", exc_info=True)
        await query.edit_message_text(get_string('error', chat_lang))

async def show_subscription_info(update: Update, context: CallbackContext, original_msg_id: int | None = None):
    """Displays subscription information."""
    query = update.callback_query
    message = query.message
    chat_id = message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Placeholder text - replace with actual subscription logic
    sub_info_text = get_string('subscription_info_placeholder', chat_lang)
    
    # Determine back button context
    if original_msg_id:
         back_callback = f"settings:{original_msg_id}" # Back to voice settings
    else:
         back_callback = "settings" # Back to main settings
         
    back_label = get_string('button_back', chat_lang)
    keyboard = [[InlineKeyboardButton(back_label, callback_data=back_callback)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(sub_info_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def show_help_menu(update: Update, context: CallbackContext, original_msg_id_str: str | None = None):
    """Sends detailed help information, potentially with context-aware back button."""
    query = update.callback_query
    message = query.message if query else update.message
    chat_id = message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    
    chat_lang = await get_chat_language(pool, chat_id)
    help_text = get_string('help', chat_lang)
    
    # Determine back button context
    if original_msg_id_str:
        try:
            original_msg_id = int(original_msg_id_str)
            back_callback = f"settings:{original_msg_id}" # Back to voice settings
        except (ValueError, TypeError):
            logger.warning(f"Invalid original_msg_id '{original_msg_id_str}' in help callback, defaulting to main settings back button.")
            back_callback = "settings" # Back to main settings
    else:
        back_callback = "settings" # Back to main settings
        
    back_label = get_string('button_back', chat_lang)
    keyboard = [[InlineKeyboardButton(back_label, callback_data=back_callback)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if query:
            await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error showing help menu: {e}")
        if query: await query.answer(get_string('error', chat_lang), show_alert=True)

async def show_voice_message_settings(update: Update, context: CallbackContext, original_msg_id: int):
    """Shows the settings menu specific to a voice message reply."""
    query = update.callback_query
    chat_id = query.message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)
    
    reply_markup = create_voice_settings_buttons(original_msg_id, chat_lang)
    text = get_string('voice_settings_title', chat_lang)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    
async def back_to_main_buttons(update: Update, context: CallbackContext, original_msg_id: int):
    """Returns the message to display the main action buttons."""
    query = update.callback_query
    chat_id = query.message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)

    try:
        # Get the original content to display
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        if not db_record:
            await query.edit_message_text(get_string('error_record_not_found', chat_lang), reply_markup=None)
            return

        record_id = db_record['id']
        mode = db_record['mode']
        summary_text = db_record['summary_text']
        transcript_text = db_record['transcript_text']
        user_id = db_record['user_id']
        current_summary_msg_id = db_record['summary_telegram_message_id']
        
        # Regenerate header
        original_user = await context.bot.get_chat(user_id)
        original_message_date = db_record.get('created_at') or query.message.date
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        reply_markup = create_action_buttons(original_msg_id, chat_lang)

        if mode == 'diagram':
             # For diagram, we just need to edit the caption back
             # The summary_text field holds the Mermaid code, not needed here
             # We assume the message is still a photo
             try:
                  await context.bot.edit_message_caption(
                       chat_id=chat_id, message_id=current_summary_msg_id,
                       caption=header, reply_markup=reply_markup, parse_mode='MarkdownV2'
                  )
             except Exception as e:
                  logger.error(f"Failed to edit caption back for diagram {current_summary_msg_id}: {e}")
                  # Fallback? Maybe send error text
                  await query.edit_message_text(get_string('error', chat_lang))
        else:
             # For text modes, regenerate the text content
             if mode == 'as_is' or mode == 'transcript':
                  display_text = transcript_text
             else:
                  display_text = summary_text
             
             escaped_display_text = escape_markdown_preserve_formatting(display_text if display_text else get_string('error_no_content', chat_lang))
             final_text = f"{header}\n\n{escaped_display_text}"
             
             await query.edit_message_text(final_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Error returning to main buttons for message {original_msg_id}: {e}", exc_info=True)
        await query.edit_message_text(get_string('error', chat_lang))
        
async def set_chat_default_mode_from_pin(update: Update, context: CallbackContext, original_msg_id: int, mode_to_pin: str):
    """Sets the chat default mode after user confirms via pin menu."""
    query = update.callback_query
    chat_id = query.message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)
    
    if mode_to_pin not in SUPPORTED_MODES:
         await query.answer("Invalid mode to pin.", show_alert=True)
         await back_to_main_buttons(update, context, original_msg_id) # Go back to main buttons
         return
         
    success = await set_chat_default_mode(pool, chat_id, mode_to_pin)
    if success:
         localized_mode_name = get_mode_name(mode_to_pin, chat_lang)
         await query.answer(f"Default mode set to '{localized_mode_name}'")
         # Return to the main action buttons view
         await back_to_main_buttons(update, context, original_msg_id)
    else:
         await query.answer(get_string('error_db', chat_lang), show_alert=True)
         # Optionally go back or stay on pin menu?
         await back_to_main_buttons(update, context, original_msg_id)

async def show_delete_history_confirmation(update: Update, context: CallbackContext, data_parts: list):
    """Shows confirmation buttons for deleting chat history."""
    # ["delete_history_confirm", context_message_id, current_offset]
    query = update.callback_query
    chat_id = query.message.chat_id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)
    
    if len(data_parts) < 3:
         logger.error("Invalid data for delete_history_confirm")
         await query.answer(get_string('error', chat_lang), show_alert=True)
         return

    context_message_id = int(data_parts[1])
    current_offset = int(data_parts[2]) # Keep offset to return if cancelled

    confirm_text = get_string('history_delete_confirm', chat_lang)
    yes_label = get_string('button_yes', chat_lang)
    no_label = get_string('button_no', chat_lang)
    
    keyboard = [
        [InlineKeyboardButton(yes_label, callback_data=f"delete_history_execute_all:{context_message_id}")],
        # No option to delete single item currently
        [InlineKeyboardButton(no_label, callback_data=f"history_nav:{context_message_id}:{current_offset}")] # Go back to the history view
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(confirm_text, reply_markup=reply_markup)
    await query.answer() # Acknowledge button press
    
async def execute_delete_history(update: Update, context: CallbackContext, data_parts: list):
    """Deletes a single history item (Not currently used)."""
    # Placeholder if we add single item deletion later
    query = update.callback_query
    await query.answer("Single item deletion not implemented.", show_alert=True)

async def execute_delete_all_history(update: Update, context: CallbackContext, data_parts: list):
    """Deletes all history items for the chat."""
    # ["delete_history_execute_all", context_message_id]
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id # For logging/confirmation
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)
    
    if len(data_parts) < 2:
        logger.error("Invalid data for delete_history_execute_all")
        await query.answer(get_string('error', chat_lang), show_alert=True)
        return
        
    context_message_id = int(data_parts[1])
    
    success = await delete_chat_history(pool, chat_id)
    if success:
         logger.info(f"User {user_id} deleted all history for chat {chat_id}")
         await query.edit_message_text(get_string('history_deleted_success', chat_lang), reply_markup=None) # Remove buttons
         await query.answer("History deleted.")
         # Optionally, could add a button to go back to settings?
    else:
         await query.answer(get_string('error_db', chat_lang), show_alert=True)
         # Try to go back to the confirmation screen or history view?
         # For now, just show error alert.
         
async def export_user_history(update: Update, context: CallbackContext, data_parts: list):
    """Exports user history as a text file."""
    # ["export_history", context_message_id]
    query = update.callback_query
    message = query.message
    chat_id = message.chat_id
    user_id = query.from_user.id
    pool = context.bot_data.get('db_pool')
    if not pool: return
    chat_lang = await get_chat_language(pool, chat_id)

    await query.answer(get_string('history_exporting', chat_lang))
    
    try:
        history_records = await get_all_chat_history(pool, chat_id)
        if not history_records:
            await query.edit_message_text(get_string('history_empty', chat_lang)) # Edit the message
            return

        export_content = io.StringIO()
        export_content.write(f"Chat History Export - Chat ID: {chat_id}\n")
        export_content.write(f"Exported by User ID: {user_id} on {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        export_content.write("="*40 + "\n\n")

        for i, record in enumerate(reversed(history_records)): # Show newest first
            author_name = "Unknown User"
            record_user_id = record.get('user_id')
            if record_user_id:
                 try:
                      author_chat = await context.bot.get_chat(record_user_id)
                      author_name = author_chat.full_name or author_name
                 except Exception:
                      pass # Ignore if user not found
                      
            formatted_msg = format_history_message(record, i + 1, len(history_records), chat_lang, author_name)
            # Remove MarkdownV2 formatting for plain text export
            plain_text_msg = re.sub(r'[\*_\[\]()~`>#+=|{}.!\\]', '', formatted_msg)
            export_content.write(f"Record {i+1}:\n{plain_text_msg}\n")
            export_content.write("-"*40 + "\n\n")

        export_content.seek(0)
        export_filename = f"chat_{chat_id}_history_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        await message.reply_document(
            document=io.BytesIO(export_content.getvalue().encode('utf-8')),
            filename=export_filename,
            caption=get_string('history_export_caption', chat_lang)
        )
        export_content.close()

    except Exception as e:
        logger.error(f"Error exporting history for chat {chat_id}: {e}", exc_info=True)
        await query.answer(get_string('error', chat_lang), show_alert=True)

async def redo(update: Update, context: CallbackContext, original_msg_id: int):
    """Re-processes the voice message with the current mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    pool = context.bot_data.get('db_pool')
    
    # Ensure pool is available
    if not pool:
        # Need chat_lang for error message, try getting it or default
        try: chat_lang = await get_chat_language(pool, chat_id)
        except: chat_lang = 'ru' # Default on error
        logger.error("Database pool not found for redo callback")
        await query.answer(get_string('error_db', chat_lang), show_alert=True)
        return
        
    chat_lang = await get_chat_language(pool, chat_id)
    
    # Show processing indicator (localized)
    processing_text = get_string('redo_processing', chat_lang)
    try:
        # Edit the existing message (text or photo caption)
        await context.bot.edit_message_caption(
             chat_id=chat_id, message_id=query.message.message_id, caption=processing_text
        )
    except Exception:
        try:
             await context.bot.edit_message_text(
                  processing_text, chat_id=chat_id, message_id=query.message.message_id
             )
        except Exception as edit_err:
             logger.warning(f"Could not edit message {query.message.message_id} for redo status: {edit_err}")
             await query.answer(processing_text) # Use answer as fallback indicator

    try:
        # Get the record from the database
        db_record = await get_summary_context_for_callback(pool, original_msg_id, chat_id)
        
        if not db_record:
            logger.error(f"Record not found for message {original_msg_id} during redo")
            await query.edit_message_text(
                get_string('error_record_not_found', chat_lang),
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
            return
        
        record_id = db_record['id']
        audio_file_id = db_record['telegram_audio_file_id']
        current_mode = db_record['mode']
        user_id = db_record['user_id']
        
        # Get user info for the header
        original_user = await context.bot.get_chat(user_id)
        # Use the original message date from DB if possible, else fallback to query message date
        original_message_date = db_record.get('created_at') or query.message.date 
        
        # Format header
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = original_message_date.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
        moscow_time_str = escape_markdown(moscow_time, version=2)
        user_name = escape_markdown(original_user.full_name, version=2)
        header = f"*{user_name}* \\| {moscow_time_str}"
        reply_markup = create_action_buttons(original_msg_id, chat_lang)
        current_summary_msg_id = db_record['summary_telegram_message_id'] # Get current message ID

        # --- Handle Diagram Mode Redo ---
        if current_mode == 'diagram':
            logger.info(f"Redoing diagram for message {original_msg_id}...")
            transcript_text = db_record['transcript_text'] # Need transcript for diagram
            author_name = original_user.full_name # Use fetched name
            
            diagram_data = await generate_diagram_data(transcript_text, chat_lang, author_name)
            if not diagram_data:
                error_msg = get_string('diagram_error_data', chat_lang)
                # Edit the current message back to show the error
                await context.bot.edit_message_caption(
                     chat_id=chat_id, message_id=current_summary_msg_id,
                     caption=f"{header}\n\n{escape_markdown_preserve_formatting(error_msg)}",
                     reply_markup=reply_markup, parse_mode='MarkdownV2'
                 )
                return
                
            mermaid_code_body = create_mermaid_syntax(diagram_data, chat_lang) # Changed variable name
            if mermaid_code_body is None: # Check for None explicitly
                error_msg = get_string('diagram_error_syntax', chat_lang)
                # Edit the current message back to show the error
                await context.bot.edit_message_caption(
                     chat_id=chat_id, message_id=current_summary_msg_id,
                     caption=f"{header}\n\n{escape_markdown_preserve_formatting(error_msg)}",
                     reply_markup=reply_markup, parse_mode='MarkdownV2'
                 )
                return
                
            diagram_png = render_mermaid_to_png(mermaid_code_body, diagram_data, chat_lang) # Pass mermaid_code_body
            if not diagram_png:
                error_msg = get_string('diagram_error_render', chat_lang)
                # Edit the current message back to show the error
                await context.bot.edit_message_caption(
                     chat_id=chat_id, message_id=current_summary_msg_id,
                     caption=f"{header}\n\n{escape_markdown_preserve_formatting(error_msg)}",
                     reply_markup=reply_markup, parse_mode='MarkdownV2'
                 )
                return
                
            # --- Send Diagram (Delete old, send new) ---
            # Note: Using the *query's* message ID which is the *summary message* ID
            new_summary_message = None
            try:
                # Delete the processing message (which was the previous summary message)
                await context.bot.delete_message(chat_id=chat_id, message_id=current_summary_msg_id)
                logger.info(f"Deleted previous message {current_summary_msg_id} before sending redone diagram")
                
                # Send the new photo message, replying to the original voice message
                new_summary_message = await context.bot.send_photo(
                    chat_id=chat_id, 
                    photo=diagram_png, 
                    caption=header, 
                    parse_mode='MarkdownV2',
                    reply_markup=reply_markup,
                    reply_to_message_id=original_msg_id # Reply to the voice message
                )
                # Update DB with the NEW message ID
                await update_summary_mode_and_text(
                    pool=pool, record_id=record_id, new_mode=current_mode, 
                    new_summary_text=mermaid_code_body, # Save mermaid code
                    new_transcript_text=transcript_text,
                    new_summary_message_id=new_summary_message.message_id # IMPORTANT: Update message ID
                )
                logger.info(f"Successfully redid diagram for message {original_msg_id}, new message ID: {new_summary_message.message_id}")
            except Exception as send_err:
                logger.error(f"Error deleting old message or sending redone diagram: {send_err}", exc_info=True)
                # Attempt to revert to an error text message if sending fails
                try:
                     # Try sending a new message as the old one might be deleted
                     await context.bot.send_message(
                          chat_id=chat_id,
                          text=f"{header}\n\n{escape_markdown_preserve_formatting(get_string('error', chat_lang))}",
                          reply_to_message_id=original_msg_id, # Reply to original voice message
                          reply_markup=reply_markup, # Add buttons back
                          parse_mode='MarkdownV2'
                     )
                except Exception as final_err:
                     logger.error(f"Failed to send fallback error message after redo diagram failure: {final_err}")
            await query.answer() # Acknowledge completion (or failure) of the action
            return # Finished diagram redo
            
        # --- Handle Text Mode Redo ---
        else:
            logger.info(f"Redoing text mode '{current_mode}' for message {original_msg_id}...")
            summary_text = None
            transcript_text = db_record['transcript_text'] # Keep original transcript
            
            # Re-download the audio file
            with tempfile.NamedTemporaryFile(suffix=".oga") as temp_audio_file:
                file = await context.bot.get_file(audio_file_id)
                await file.download_to_drive(custom_path=temp_audio_file.name)
                logger.info(f"Re-downloaded audio {audio_file_id} for redo.")
                
                # Process audio with current mode
                summary_text, new_transcript = await process_audio_with_gemini(temp_audio_file.name, current_mode, chat_lang)
                # It's possible Gemini gives a slightly different transcript on redo, update if needed
                if new_transcript:
                    transcript_text = new_transcript 
            
            if current_mode == 'as_is' or current_mode == 'transcript':
                display_text = transcript_text
            else:
                display_text = summary_text # Use the newly generated summary
            
            if not display_text:
                logger.error(f"Failed to generate content for mode {current_mode} during redo")
                await query.edit_message_text(
                    get_string('error_generating_summary', chat_lang), # Use generic summary error string
                    reply_markup=create_action_buttons(original_msg_id, chat_lang)
                )
                return
            
            escaped_display_text = escape_markdown_preserve_formatting(display_text)
            final_text = f"{header}\n\n{escaped_display_text}"
            
            # --- Edit Message (Text or Photo Caption) ---
            # Edit the *current* summary message ID
            sent_message = None
            try:
                 # Try editing as a text message first
                 sent_message = await context.bot.edit_message_text(
                      final_text,
                      chat_id=chat_id,
                      message_id=current_summary_msg_id,
                      reply_markup=reply_markup,
                      parse_mode=ParseMode.MARKDOWN_V2
                 )
                 logger.info(f"Edited text message {current_summary_msg_id} for redo mode '{current_mode}'")
            except Exception: # If that fails, assume it was a photo and try editing caption
                 try:
                      sent_message = await context.bot.edit_message_caption(
                           chat_id=chat_id,
                           message_id=current_summary_msg_id,
                           caption=final_text,
                           reply_markup=reply_markup,
                           parse_mode=ParseMode.MARKDOWN_V2
                      )
                      logger.info(f"Edited photo caption {current_summary_msg_id} for redo mode '{current_mode}'")
                 except Exception as edit_err:
                      logger.error(f"Failed to edit message text or caption for redo mode '{current_mode}': {edit_err}", exc_info=True)
                      await query.answer(get_string('error', chat_lang), show_alert=True)
                      return

            # Update database with new summary/transcript, keeping the same message ID
            await update_summary_mode_and_text(
                pool=pool,
                record_id=record_id,
                new_mode=current_mode, # Mode stays the same
                new_summary_text=summary_text,
                new_transcript_text=transcript_text,
                new_summary_message_id=sent_message.message_id # Update message ID
            )
            logger.info(f"Successfully redid text mode '{current_mode}' for message {original_msg_id}")
        
    except Exception as e:
        logger.error(f"Error in redo: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                get_string('error', chat_lang), # Use generic error
                reply_markup=create_action_buttons(original_msg_id, chat_lang)
            )
        except Exception as edit_e:
            logger.error(f"Failed to edit message after error in redo: {edit_e}")
    await query.answer() # Acknowledge completion or failure

if __name__ == "__main__":
    main() 
import logging
import os
import tempfile
from functools import wraps

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from telegram.constants import ChatAction

from pydub import AudioSegment
from config import BOT_TOKEN
from locales import get_dual_string

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def send_typing_action(func):
    """Sends typing action while processing."""
    @wraps(func)
    async def command_func(update: Update, context: CallbackContext, *args, **kwargs):
        if update.effective_message:
            await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.RECORD_VOICE)
        return await func(update, context, *args, **kwargs)
    return command_func

# --- Command Handlers ---

async def start(update: Update, context: CallbackContext) -> None:
    """Sends a bilingual welcome message."""
    await update.message.reply_text(get_dual_string('start'))

@send_typing_action
async def handle_audio(update: Update, context: CallbackContext) -> None:
    """Handles audio file uploads (MP3 and WAV) sent as Document or Audio."""
    message = update.message
    
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


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))

    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main() 
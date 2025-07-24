import openai
import asyncio
import logging
import os
from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# Initialize async client (reads OPENAI_API_KEY from environment)
# Make sure we're using the standard API endpoint
client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.openai.com/v1"  # Explicitly set base URL
)

async def gpt4o_transcribe_openai(path: str, lang: str = "auto") -> str | None:
    """
    Direct call to OpenAI gpt-4o-transcribe.
    Accepts .oga / .ogg / .mp3 ... - no conversion step needed.
    Returns raw text or None on error.
    
    Args:
        path: Path to audio file
        lang: ISO-639-1 language code (e.g., "ru", "en") or "auto"
        
    Returns:
        Transcribed text or None on error
    """
    try:
        # OpenAI requires a filename with extension
        # OpenAI doesn't recognize .oga, use .ogg instead
        filename = os.path.basename(path)
        if not filename:
            filename = "audio.ogg"
        elif filename.endswith('.oga'):
            filename = filename.replace('.oga', '.ogg')
        
        log.info(f"Attempting OpenAI transcription with file: {filename}")
            
        # Read the file content
        with open(path, "rb") as f:
            audio_content = f.read()
            
        # Create a file-like object with the correct filename
        from io import BytesIO
        audio_file = BytesIO(audio_content)
        audio_file.name = filename
        
        response = await client.audio.transcriptions.create(
            model="gpt-4o-transcribe",  # GPT-4o transcribe model
            file=audio_file,
            language=None if lang == "auto" else lang,
            response_format="text"  # fastest: just text, no JSON
        )
        
        log.info("OpenAI transcription done (%d chars)", len(response))
        return response
        
    except openai.RateLimitError as e:
        log.warning("OpenAI rate limited: %s", e)
        return None
        
    except openai.OpenAIError as e:
        log.error("OpenAI STT failed: %s", e)
        return None
        
    except Exception as e:
        log.exception("Unexpected error in OpenAI transcription:")
        return None
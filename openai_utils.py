import openai
import asyncio
import logging
from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# Initialize async client (reads OPENAI_API_KEY from environment)
client = AsyncOpenAI()

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
        with open(path, "rb") as audio_file:
            response = await client.audio.transcriptions.create(
                model="gpt-4o",  # GPT-4o model for transcription
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
import aiofiles
import os
import logging
from deepgram import Deepgram  # async SDK

log = logging.getLogger(__name__)
DG_KEY = os.getenv("DEEPGRAM_API_KEY")

async def transcribe_nova3(audio_path: str, lang: str = "ru") -> str | None:
    """
    Send the original OGA/OGG file to Deepgram Nova-3 and return the transcript.
    Expects DEEPGRAM_API_KEY to be set in env.
    
    Args:
        audio_path: Path to audio file (OGA/OGG/MP3/etc)
        lang: Language code (e.g., "ru", "en")
        
    Returns:
        Transcribed text or None on error
    """
    try:
        if not DG_KEY:
            log.error("DEEPGRAM_API_KEY missing")
            return None

        dg = Deepgram(DG_KEY)
        
        # Read binary into memory
        async with aiofiles.open(audio_path, "rb") as f:
            buf = await f.read()

        source = {"buffer": buf, "mimetype": "audio/ogg"}  # KEEP mime for OGA!
        opts = {
            "model": "nova-3",          # use the newest model
            "language": lang,           # e.g. 'ru'
            "punctuate": "true",
            "smart_format": "true"
        }
        
        log.info(f"Attempting Deepgram Nova-3 transcription for language: {lang}")
        resp = await dg.transcription.prerecorded(source, opts)
        
        # Extract transcript from response
        transcript = resp["results"]["channels"][0]["alternatives"][0]["transcript"]
        log.info(f"Deepgram transcription done ({len(transcript)} chars)")
        
        return transcript
        
    except Exception as e:
        log.error(f"Deepgram Nova-3 failed: {e}")
        return None
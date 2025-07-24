import aiofiles
import os
import logging
from deepgram import DeepgramClient, PrerecordedOptions, FileSource

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

        # Create client with v3 API
        deepgram = DeepgramClient(DG_KEY)
        
        # Read binary into memory
        with open(audio_path, "rb") as f:
            buffer_data = f.read()

        # Create payload
        payload: FileSource = {
            "buffer": buffer_data,
        }

        # Configure transcription options for Nova-3
        options = PrerecordedOptions(
            model="nova-3",
            language=lang,
            smart_format=True,
            punctuate=True,
        )
        
        log.info(f"Attempting Deepgram Nova-3 transcription for language: {lang}")
        
        # Transcribe using the sync/rest API (async not needed for file transcription)
        response = deepgram.listen.rest.v("1").transcribe_file(payload, options)
        
        # Extract transcript from response
        transcript = response.results.channels[0].alternatives[0].transcript
        log.info(f"Deepgram transcription done ({len(transcript)} chars)")
        
        return transcript
        
    except Exception as e:
        log.error(f"Deepgram Nova-3 failed: {e}")
        return None
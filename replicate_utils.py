import aiofiles
import replicate
import logging

log = logging.getLogger(__name__)

async def gpt4o_transcribe_replicate(audio_path: str, lang: str = "ru") -> str | None:
    """
    Upload local OGG/WAV, call Replicate gpt-4o-transcribe, return plain text.
    """
    try:
        # For older versions of replicate, we can pass the file directly
        # The model will handle the upload internally
        with open(audio_path, "rb") as f:
            output = replicate.run(
                "openai/gpt-4o-transcribe",
                input={
                    "audio_file": f,
                    "language": lang,            # ISO-639-1 code
                    "response_format": "text"
                }
            )
        # replicate returns the transcript string directly for this model
        return output if isinstance(output, str) else str(output) if output else None
    except Exception as e:
        log.exception("Replicate STT failed")
        return None
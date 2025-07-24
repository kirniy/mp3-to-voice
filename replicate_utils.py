import aiofiles
import replicate
import logging

log = logging.getLogger(__name__)

async def gpt4o_transcribe_replicate(audio_path: str, lang: str = "ru") -> str | None:
    """
    Upload local OGG/WAV, call Replicate gpt-4o-transcribe, return plain text.
    """
    try:
        # 1) push the file to Replicate's blob store → returns https URL
        upload_url = replicate.files.upload(open(audio_path, "rb"))
        # 2) run the model (field name is audio_file!)
        output = replicate.run(
            "openai/gpt-4o-transcribe",
            input={
                "audio_file": upload_url,
                "language": lang,            # ISO-639-1 code
                "response_format": "text"
            }
        )
        # replicate returns list; first item is the transcript string
        return output[0] if output else None
    except Exception as e:
        log.exception("Replicate STT failed")
        return None
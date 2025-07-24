import asyncio
import logging
import replicate
import aiofiles

log = logging.getLogger(__name__)

async def gpt4o_transcribe_replicate(audio_path: str, lang: str = "ru") -> str | None:
    """Return plaintext transcript or None on error."""
    try:
        # run the blocking Replicate call in a thread
        def _run():
            with open(audio_path, "rb") as audio:
                return replicate.run(
                    "openai/gpt-4o-transcribe",
                    input={
                        "audio_file": audio,        # field name per schema
                        "language": lang,
                        "response_format": "text"
                    }
                )
        output = await asyncio.to_thread(_run)
        # API returns a string for response_format="text"
        return output.strip() if isinstance(output, str) else "".join(map(str, output))
    except Exception as e:
        log.exception("Replicate STT failed:")
        return None
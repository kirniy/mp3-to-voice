import asyncio, replicate, logging
from audio_utils import to_wav                      # NEW
log = logging.getLogger(__name__)

async def gpt4o_transcribe_replicate(path: str, lang: str="ru") -> str | None:
    """Return transcript or None."""
    try:
        wav_path = await to_wav(path)               # convert first
        def _sync():
            return replicate.run(
                "openai/gpt-4o-transcribe",
                input={
                    "audio_file": open(wav_path, "rb"),
                    "language": lang,
                    "response_format": "text",
                    "temperature": 0
                },
                wait=True,
                use_file_output=False
            )
        output = await asyncio.to_thread(_sync)
        text = "".join(output).strip() if isinstance(output, list) else str(output).strip()
        # Replicate sometimes returns [""] – treat as failure
        return text or None
    except Exception as e:
        log.exception("Replicate STT failed:")
        return None
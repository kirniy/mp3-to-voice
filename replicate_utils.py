import asyncio, replicate, logging
log = logging.getLogger(__name__)

async def gpt4o_transcribe_replicate(path: str, lang: str="ru") -> str | None:
    """Return transcript or None."""
    try:
        def _sync():
            iterator = replicate.run(
                "openai/gpt-4o-transcribe",
                input={
                    "audio_file": open(path, "rb"),
                    "language": lang,
                    "response_format": "text"
                },
                wait=True,
                use_file_output=False        # get raw iterator, not FileOutput wrapper
            )
            # join all chunks into one string
            return "".join(iterator).strip()
        text = await asyncio.to_thread(_sync)
        return text or None
    except Exception as e:
        log.exception("Replicate STT failed:")
        return None
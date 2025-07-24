import asyncio, replicate, logging
log = logging.getLogger(__name__)

async def gpt4o_transcribe(path: str, lang: str = "ru") -> str | None:
    """
    GPT‑4o via Replicate, ~5‑6 s for a 75 s clip.
    • uploads local OGA
    • waits blocking (stream=False, wait=True)
    • returns full text or None
    """
    try:
        def _sync():
            output = replicate.run(
                "openai/gpt-4o-transcribe",
                input={
                    "audio_file": open(path, "rb"),   # ← original file
                    "language": lang,
                    "response_format": "text",
                    "temperature": 0
                },
                stream=False,          # MUST be False for local files
                wait=True,             # block until status=="succeeded"
                use_file_output=False
            )
            return "".join(output).strip()            # list[str] → str
        text = await asyncio.to_thread(_sync)
        log.info("Replicate transcript length: %s chars", len(text) if text else 0)
        return text or None
    except Exception as e:
        log.exception("Replicate STT failed:")
        return None

# Keep old function name for backward compatibility
async def gpt4o_transcribe_replicate(path: str, lang: str="ru") -> str | None:
    """Backward compatibility wrapper."""
    return await gpt4o_transcribe(path, lang)
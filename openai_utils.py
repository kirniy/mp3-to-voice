import os
import logging
import aiofiles
from openai import AsyncOpenAI

_log = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def gpt4o_transcribe(path: str, language: str = "ru") -> str | None:
    """
    Returns plain-text transcript from GPT-4o-Transcribe.
    On failure returns None (caller handles fallback).
    """
    try:
        async with aiofiles.open(path, "rb") as f:
            audio = await f.read()
        resp = await client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio,
            language=language
        )
        return resp.text
    except Exception as exc:
        _log.exception("GPT-4o transcription failed:")
        return None
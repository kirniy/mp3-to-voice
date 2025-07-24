import asyncio, replicate, logging, time
log = logging.getLogger(__name__)

async def gpt4o_transcribe(path: str, lang: str = "ru",
                           max_wait: int = 90) -> str | None:
    """
    Transcribe with Replicate GPT‑4o.
      • uploads local OGA
      • polls until status=="succeeded" or max_wait seconds
      • returns text or None
    """
    try:
        start_ts = time.time()

        # 1) create prediction (non‑blocking)
        # Upload file first, then use the URL
        with open(path, "rb") as f:
            file_url = replicate.files.create(f)
            
        pred = replicate.predictions.create(
            model="openai/gpt-4o-transcribe",
            input={
                "audio_file": file_url.urls["get"],
                "language": lang,
                "temperature": 0
            },
            stream=False
        )
        log.info("Replicate job %s queued (status=%s)", pred.id, pred.status)

        # 2) poll
        while pred.status not in ("succeeded", "failed", "canceled"):
            await asyncio.sleep(2)
            pred = replicate.predictions.get(pred.id)
            log.debug("Job %s status → %s", pred.id, pred.status)
            if time.time() - start_ts > max_wait:
                log.warning("Replicate job %s timed out after %s s", pred.id, max_wait)
                replicate.predictions.cancel(pred.id)
                return None

        if pred.status != "succeeded":
            error_msg = getattr(pred, 'error', None)
            log.error("Replicate job %s ended as %s: %s", pred.id, pred.status, error_msg)
            return None

        text = "".join(pred.output).strip() if pred.output else ""
        log.info("Replicate job %s done (%d chars)", pred.id, len(text))
        return text or None

    except Exception as exc:
        log.exception("Replicate STT hard error:")
        return None

# Keep old function name for backward compatibility
async def gpt4o_transcribe_replicate(path: str, lang: str="ru") -> str | None:
    """Backward compatibility wrapper."""
    return await gpt4o_transcribe(path, lang)
import subprocess
import tempfile
import pathlib
import asyncio
import logging

logger = logging.getLogger(__name__)

async def to_wav(source_path: str) -> str:
    """Return path to 16 kHz mono WAV file derived from `source_path`."""
    dst = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    dst_path = dst.name
    dst.close()
    
    # ffmpeg -loglevel error -y -i in.oga -ac 1 -ar 16000 out.wav
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-i", source_path, "-ac", "1", "-ar", "16000", dst_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.communicate()
        logger.info(f"Converted {source_path} to WAV at {dst_path}")
        return dst_path
    except Exception as e:
        logger.error(f"Failed to convert audio to WAV: {e}")
        raise
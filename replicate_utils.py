"""
Replicate API integration for transcription models
"""
import os
import logging
import replicate
from typing import Optional, List

logger = logging.getLogger(__name__)

def transcribe_audio_with_gpt4o(
    audio_file_path: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0
) -> Optional[str]:
    """
    Transcribe audio using GPT-4o Transcribe model on Replicate
    
    Args:
        audio_file_path: Path to the audio file or URL
        language: ISO-639-1 language code (e.g., 'en', 'ru', 'kk')
        prompt: Optional text to guide the model's style
        temperature: Sampling temperature (0-1)
    
    Returns:
        Transcribed text or None if error
    """
    try:
        # Check if API token is set
        api_token = os.environ.get("REPLICATE_API_TOKEN")
        if not api_token:
            logger.error("REPLICATE_API_TOKEN not set in environment")
            return None
        
        # Prepare input
        input_data = {
            "audio_file": audio_file_path,
            "temperature": temperature
        }
        
        if language:
            input_data["language"] = language
        
        if prompt:
            input_data["prompt"] = prompt
        
        logger.info(f"Calling GPT-4o Transcribe with language={language}, temperature={temperature}")
        
        # Run transcription - returns an iterator of string tokens
        output_tokens = []
        for token in replicate.stream(
            "openai/gpt-4o-transcribe",
            input=input_data
        ):
            output_tokens.append(token)
        
        # Join all tokens into final transcript
        transcript = "".join(output_tokens)
        
        logger.info(f"GPT-4o transcription successful, length: {len(transcript)}")
        return transcript
        
    except Exception as e:
        logger.error(f"Error transcribing with GPT-4o: {str(e)}", exc_info=True)
        return None

def get_language_code_for_replicate(language: str) -> Optional[str]:
    """
    Convert our language codes to ISO-639-1 format for Replicate
    
    Args:
        language: Our language code ('ru', 'en', 'kk')
    
    Returns:
        ISO-639-1 language code or None
    """
    # Our codes are already ISO-639-1 compatible
    language_map = {
        'ru': 'ru',  # Russian
        'en': 'en',  # English
        'kk': 'kk',  # Kazakh
    }
    
    return language_map.get(language)
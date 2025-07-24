"""
Model configuration for Voicio Bot - supports multiple Gemini models and protocols
"""

# Available models with their capabilities
MODELS = {
    # Gemini models
    "gemini-2.0-flash": {
        "name": "Gemini 2.0 Flash",
        "provider": "gemini",
        "supports_audio": True,
        "supports_video": True,
        "supports_thinking": False,
        "description": "Fast general-purpose model",
        "model_name": "models/gemini-2.0-flash"
    },
    
    "gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "supports_audio": True,
        "supports_video": True,
        "supports_thinking": True,
        "description": "Latest Flash model with thinking capabilities",
        "model_name": "models/gemini-2.5-flash",
        "thinking_budget_default": 1024,
        "thinking_budget_options": {
            "off": 0,
            "low": 512,
            "medium": 1024,
            "high": 2048,
            "dynamic": -1
        }
    },
    
    # OpenAI models (audio transcription only)
    "gpt-4o-transcribe": {
        "name": "GPT-4o Transcribe (Replicate)",
        "provider": "replicate",
        "supports_audio": True,
        "supports_thinking": False,
        "description": "High-accuracy speech-to-text model using GPT-4o via Replicate",
        "model_name": "gpt-4o-transcribe",
        "transcription_only": True,  # Can only be used for transcription, not processing
        "requires_api_key": "REPLICATE_API_TOKEN"
    },
    
    "gpt-4o-openai": {
        "name": "GPT-4o (OpenAI)",
        "provider": "openai",
        "supports_audio": True,
        "supports_thinking": False,
        "description": "Direct OpenAI GPT-4o transcription - fastest and most accurate",
        "model_name": "gpt-4o-transcribe",
        "transcription_only": True,  # Can only be used for transcription, not processing
        "requires_api_key": "OPENAI_API_KEY"
    },
    
    "deepgram-nova3": {
        "name": "Deepgram Nova-3",
        "provider": "deepgram",
        "supports_audio": True,
        "supports_thinking": False,
        "description": "Deepgram Nova-3 - Ultra-fast multilingual transcription (Russian, English, Spanish, French, German, Hindi, Portuguese, Japanese, Italian, Dutch)",
        "model_name": "nova-3",
        "transcription_only": True,  # Can only be used for transcription, not processing
        "requires_api_key": "DEEPGRAM_API_KEY"
    }
}

# Keep GEMINI_MODELS for backward compatibility
GEMINI_MODELS = {k: v for k, v in MODELS.items() if v.get("provider") == "gemini"}

# Protocol configurations
PROTOCOLS = {
    "direct": {
        "name": "Direct Audio Processing",
        "description": "Audio → Model → Output (current implementation)",
        "steps": ["audio_to_output"]
    },
    
    "transcript": {
        "name": "Transcript-Based Processing",
        "description": "Audio → Transcription → Mode Processing → Output",
        "steps": ["audio_to_transcript", "transcript_to_output"],
        "allows_model_selection": {
            "transcription_model": True,
            "processing_model": True
        }
    }
}

# Default configurations
DEFAULT_PROTOCOL = "transcript"
DEFAULT_TRANSCRIPTION_MODEL = "deepgram-nova3"  # For transcript protocol - Ultra-fast Deepgram Nova-3
DEFAULT_PROCESSING_MODEL = "gemini-2.5-flash"     # For mode processing in transcript protocol
DEFAULT_DIRECT_MODEL = "gemini-2.5-flash"         # For direct protocol

# Model selection constraints
MODEL_CONSTRAINTS = {
    # Which models can be used for transcription (audio input required)
    "transcription": ["gemini-2.0-flash", "gemini-2.5-flash", "gpt-4o-transcribe", "gpt-4o-openai", "deepgram-nova3"],
    
    # Which models can be used for text processing (mode formatting)
    "text_processing": ["gemini-2.0-flash", "gemini-2.5-flash"],
    
    # Which models can handle direct audio-to-mode processing
    "direct_audio": ["gemini-2.0-flash", "gemini-2.5-flash"],
    
    # Which models can handle direct video processing
    "direct_video": ["gemini-2.0-flash", "gemini-2.5-flash"]
}

def get_model_config(model_id: str) -> dict:
    """Get configuration for a specific model"""
    return MODELS.get(model_id, None)

def is_model_suitable_for_task(model_id: str, task: str) -> bool:
    """Check if a model can perform a specific task"""
    if task not in MODEL_CONSTRAINTS:
        return False
    return model_id in MODEL_CONSTRAINTS[task]

def get_thinking_budget(model_id: str, budget_level: str = "medium") -> int:
    """Get thinking budget for a model based on level"""
    model = get_model_config(model_id)
    if not model or not model.get("supports_thinking"):
        return 0
    
    if model.get("thinking_always_on"):
        return -1  # Dynamic for models like 2.5 Pro
    
    options = model.get("thinking_budget_options", {})
    return options.get(budget_level, model.get("thinking_budget_default", 1024))
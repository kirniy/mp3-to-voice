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
        "supports_thinking": False,
        "description": "Fast general-purpose model",
        "model_name": "models/gemini-2.0-flash"
    },
    
    "gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "supports_audio": True,
        "supports_thinking": True,
        "description": "Latest Flash model with thinking capabilities",
        "model_name": "models/gemini-2.5-flash-preview-04-17",
        "thinking_budget_default": 1024,
        "thinking_budget_options": {
            "off": 0,
            "low": 512,
            "medium": 1024,
            "high": 2048,
            "dynamic": -1
        }
    },
    
    # Replicate models (audio transcription only)
    "gpt-4o-transcribe": {
        "name": "GPT-4o Transcribe",
        "provider": "replicate",
        "supports_audio": True,
        "supports_thinking": False,
        "description": "High-accuracy speech-to-text model using GPT-4o",
        "model_name": "openai/gpt-4o-transcribe",
        "transcription_only": True,  # Can only be used for transcription, not processing
        "requires_api_key": "REPLICATE_API_TOKEN"
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
DEFAULT_PROTOCOL = "direct"
DEFAULT_TRANSCRIPTION_MODEL = "gemini-2.5-flash"  # For transcript protocol
DEFAULT_PROCESSING_MODEL = "gemini-2.5-flash"     # For mode processing in transcript protocol
DEFAULT_DIRECT_MODEL = "gemini-2.0-flash"         # For direct protocol

# Model selection constraints
MODEL_CONSTRAINTS = {
    # Which models can be used for transcription (audio input required)
    # Note: gpt-4o-transcribe temporarily disabled - requires URL upload implementation
    "transcription": ["gemini-2.0-flash", "gemini-2.5-flash"],
    
    # Which models can be used for text processing (mode formatting)
    "text_processing": ["gemini-2.0-flash", "gemini-2.5-flash"],
    
    # Which models can handle direct audio-to-mode processing
    "direct_audio": ["gemini-2.0-flash", "gemini-2.5-flash"]
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
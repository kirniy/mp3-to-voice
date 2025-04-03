STRINGS = {
    'en': {
        'choose_language': "Please choose your language:",
        'language_set': "Language set to English 🇬🇧.",
        'start': "🇬🇧 Hello! Send or forward me an MP3 or WAV file, and I'll convert it into a voice message.",
        'processing': "🇬🇧 Processing your file... ⏳",
        'error': "🇬🇧 An error occurred while processing your file. Please try again later.",
        'invalid_file': "🇬🇧 Please send an MP3 or WAV file.",
        'file_too_large': "🇬🇧 The file is too large. Please send a file smaller than 20MB."
    },
    'ru': {
        'choose_language': "Пожалуйста, выберите ваш язык:",
        'language_set': "Язык установлен на Русский 🇷🇺.",
        'start': "🇷🇺 Привет! Отправь или перешли мне MP3 или WAV файл, и я преобразую его в голосовое сообщение.",
        'processing': "🇷🇺 Обрабатываю ваш файл... ⏳",
        'error': "🇷🇺 Произошла ошибка при обработке вашего файла. Пожалуйста, попробуйте позже.",
        'invalid_file': "🇷🇺 Пожалуйста, отправьте файл в формате MP3 или WAV.",
        'file_too_large': "🇷🇺 Файл слишком большой. Пожалуйста, отправьте файл размером менее 20МБ."
    }
}

def get_dual_string(key: str) -> str:
    """Returns a string with both English and Russian versions."""
    en_text = STRINGS.get('en', {}).get(key, f"[{key}_en?]") # Fallback
    ru_text = STRINGS.get('ru', {}).get(key, f"[{key}_ru?]") # Fallback
    # Use a separator for clarity
    return f"{en_text}\n{'-'*20}\n{ru_text}" 
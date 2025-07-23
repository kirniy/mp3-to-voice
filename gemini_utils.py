import logging
import google.generativeai as genai
import time  # Added for retries
import random  # Added for jitter in retries
import asyncio  # Added for async sleep
import re  # Added for regular expressions
import subprocess
import tempfile
import os
import json
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

def format_output_with_header(summary_text: str, mode: str, username: str, timestamp: datetime, language: str = 'ru') -> str:
    """Format the output with username, timestamp, and mode header.
    
    Args:
        summary_text: The processed text to format
        mode: The processing mode used
        username: User's full name
        timestamp: Message timestamp
        language: Language code for localization
        
    Returns:
        Formatted text with header
    """
    # Convert timestamp to Moscow time
    moscow_tz = pytz.timezone('Europe/Moscow')
    moscow_time = timestamp.astimezone(moscow_tz).strftime('%d.%m.%Y %H:%M МСК')
    
    # Get localized mode name in lowercase
    mode_name = get_mode_name(mode, language).lower()
    
    # Create header
    header = f"{username} | {moscow_time} | {mode_name}\n\n"
    
    # Format the content based on mode
    if mode == 'bullet':
        # Extract main topic and points from the summary
        lines = summary_text.strip().split('\n')
        main_topic = ""
        points = []
        conclusion = ""
        
        # Parse the response to extract structured data
        section = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Detect sections by looking for keywords
            if any(marker in line.lower() for marker in ['основная тема:', 'main topic:', 'негізгі тақырып:']):
                section = 'topic'
                # Extract topic after the marker
                topic_match = re.search(r'[:\-]\s*(.+)', line)
                if topic_match:
                    main_topic = topic_match.group(1).strip()
            elif any(marker in line.lower() for marker in ['ключевое:', 'key points:', 'негізгі тұстары:', 'тезисы:', 'bullet points:']):
                section = 'points'
            elif any(marker in line.lower() for marker in ['итоги:', 'вывод:', 'conclusion:', 'қорытынды:']):
                section = 'conclusion'
            elif section == 'topic' and not main_topic:
                main_topic = line
            elif section == 'points' and line.startswith('-'):
                points.append(line)
            elif section == 'conclusion':
                conclusion += line + ' '
        
        # Build formatted output
        formatted = header
        formatted += "основная тема\n\n"
        formatted += f"{main_topic or '[не определена]'}\n\n"
        formatted += "тезисы\n\n"
        for point in points:
            formatted += f"{point}\n"
        if conclusion.strip():
            formatted += "\nвывод\n\n"
            formatted += conclusion.strip()
            
        return formatted
    else:
        # For other modes, just add the header
        return header + summary_text

# Define supported modes
SUPPORTED_MODES = {
    # Internal mode key: Display name in different languages
    "as_is": {
        "en": "original",
        "ru": "оригинал",
        "kk": "түпнұсқа"
    },
    "bullet": {
        "en": "thesis",
        "ru": "тезисно",
        "kk": "тезистер"
    },
    "brief": {
        "en": "brief",
        "ru": "кратко",
        "kk": "қысқаша"
    },
    "detailed": {
        "en": "detailed",
        "ru": "подробно",
        "kk": "толық"
    },
    "combined": {
        "en": "combo",
        "ru": "комбо",
        "kk": "біріктірілген"
    },
    "pasha": {
        "en": "unhinged 18+",
        "ru": "жестко 18+",
        "kk": "жестко 18+"
    },
    "diagram": {
        "en": "schema",
        "ru": "схема",
        "kk": "диаграмма"
    }
}

# Internal modes - not shown in the UI but used in processing
INTERNAL_MODES = {
    "transcript": "transcript"  # Used internally for transcript processing
}

DEFAULT_MODE = "bullet"

# Max retries for transient errors
MAX_RETRIES = 3

# Helper function to get the localized mode name
def get_mode_name(mode: str, language: str = 'ru') -> str:
    """Get the localized name for a mode.
    
    Args:
        mode: The mode key
        language: Language code ('en', 'ru', 'kk')
        
    Returns:
        The localized display name of the mode
    """
    if mode in SUPPORTED_MODES:
        # Default to Russian if language not supported
        if language not in ['en', 'ru', 'kk']:
            language = 'ru'
        
        return SUPPORTED_MODES[mode][language]
    elif mode in INTERNAL_MODES:
        return INTERNAL_MODES[mode]
    else:
        return mode

async def process_audio_with_gemini(audio_file_path: str, mode: str, language: str = 'ru') -> tuple[str | None, str | None]:
    """Processes audio using Gemini: transcription + requested mode.

    Args:
        audio_file_path: Path to the audio file.
        mode: The desired processing mode (e.g., 'brief', 'detailed').
        language: The language for the summary output ('en', 'ru', 'kk').

    Returns:
        A tuple containing (summary_text, transcript_text). 
        summary_text will be None if only transcript is requested.
        transcript_text will be None if processing fails.
        Returns (None, None) on error.
    """
    logger.info(f"Processing audio file {audio_file_path} with mode '{mode}' in language '{language}'")
    
    if mode not in SUPPORTED_MODES and mode not in INTERNAL_MODES:
        logger.error(f"Unsupported mode requested: {mode}")
        return None, None

    # Retry counter and uploaded file reference for cleanup
    retry_count = 0
    audio_file = None
    
    try:
        while retry_count <= MAX_RETRIES:
            try:
                # --- Upload and Process File ---
                # Only upload on first try or if previous attempt failed before upload completed
                if audio_file is None:
                    logger.debug("Uploading audio file to Gemini...")
                    audio_file = genai.upload_file(
                        path=audio_file_path, 
                        mime_type="audio/ogg"  # Specify MIME type for Telegram voice messages
                    )
                    logger.info(f"Audio file uploaded successfully: {audio_file.name} ({audio_file.uri})")
                
                # --- Ensure file is processed before proceeding --- 
                while audio_file.state.name == "PROCESSING":
                    logger.debug("File still processing...")
                    # Add small delay to avoid busy-waiting
                    await asyncio.sleep(1)
                    audio_file = genai.get_file(audio_file.name)

                if audio_file.state.name == "FAILED":
                    logger.error(f"Gemini file processing failed for {audio_file.name}")
                    # Cleanup and prepare for retry
                    try:
                        genai.delete_file(audio_file.name)
                        audio_file = None
                    except Exception:
                        pass  # Ignore deletion errors
                    
                    # Raise to trigger retry
                    raise ValueError("File processing failed on Gemini server")
                
                logger.debug("Audio file ready for use.")

                # --- Select Model ---
                # Using Gemini 2.0 Flash for fast processing
                model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")

                # Use the model to generate the raw transcript from the audio file
                logger.debug("Requesting raw transcript from audio file...")
                # Create content from the uploaded file with a clear transcription instruction
                content = [
                    "Transcribe the following audio exactly as spoken. Do not analyze or comment on the content, just provide the raw transcript:",
                    {"file_data": {"file_uri": audio_file.uri, "mime_type": "audio/ogg"}}
                ]
                raw_transcript_response = await model.generate_content_async(content)
                raw_transcript = raw_transcript_response.text
                logger.debug("Raw transcript obtained from audio file")

                # --- Define Language-specific instructions ---
                # Maps for localizing the output based on user language preference
                language_instructions = {
                    'en': "Provide the summary in English, regardless of the original audio language.",
                    'ru': "Provide the summary in Russian (русский), regardless of the original audio language.",
                    'kk': "Provide the summary in Kazakh (қазақша), regardless of the original audio language."
                }
                
                # Default to Russian if language not supported
                lang_instruction = language_instructions.get(language, language_instructions['ru'])
                
                # Define language-specific prompts for transcript mode
                transcript_prompts = {
                    'en': """
                    Provide a clean transcript of the audio that preserves the original wording as much as possible:
                    - Keep the exact words and phrases used by the speaker
                    - Fix only obvious mistakes in punctuation for readability
                    - Do NOT rephrase or reword the content
                    - DO NOT translate if in another language - keep the original language
                    - Preserve all names, company names, and technical terms exactly as spoken
                    - Keep sentence structure and word order as close to the original as possible
                    - DO NOT add any commentary or introductory text
                    - Simply provide the transcript text directly
                    """,
                    
                    'ru': """
                    Предоставь транскрипцию аудио, максимально сохраняя оригинальные формулировки:
                    - Сохрани точные слова и фразы, использованные говорящим
                    - Исправь только очевидные ошибки пунктуации для читабельности
                    - НЕ перефразируй и не изменяй содержание
                    - НЕ переводи, если запись на другом языке - сохрани оригинальный язык
                    - Сохрани все имена, названия компаний и технические термины точно так, как они произнесены
                    - Сохрани структуру предложений и порядок слов максимально близко к оригиналу
                    - НЕ добавляй комментарии или вводный текст
                    - Просто предоставь текст транскрипции напрямую
                    """,
                    
                    'kk': """
                    Түпнұсқа тұжырымдамаларды мүмкіндігінше сақтай отырып, аудионың транскрипциясын ұсыныңыз:
                    - Сөйлеуші қолданған нақты сөздер мен сөз тіркестерін сақтаңыз
                    - Оқуға болатындай етіп тыныс белгілерінің анық қателерін ғана түзетіңіз
                    - Мазмұнды қайта тұжырымдамаңыз және өзгертпеңіз
                    - Егер жазба басқа тілде болса, аударма ЖАСАМАҢЫЗ - түпнұсқа тілді сақтаңыз
                    - Барлық есімдер, компания атаулары және техникалық терминдерді дәл айтылғандай сақтаңыз
                    - Сөйлемдердің құрылымы мен сөздердің ретін түпнұсқаға мүмкіндігінше жақын сақтаңыз
                    - Түсініктеме немесе кіріспе мәтін ҚОСПАҢЫЗ
                    - Тек транскрипция мәтінін тікелей ұсыныңыз
                    """
                }
                
                # Define language-specific prompts for each mode
                mode_prompts = {
                    'brief': {
                        'en': """
                        Create a brief, informative summary (3-5 sentences) based on the following transcript.
                        Focus on key information, main ideas, and important details.
                        Use clear, concise language and a logical structure.
                        
                        IMPORTANT: Do not use emojis, asterisks, or any special formatting.
                        Just provide a plain text summary.
                        """,
                        
                        'ru': """
                        Создай краткую, но информативную сводку (3-5 предложений) на основе следующей транскрипции.
                        Сосредоточься на ключевой информации, основных идеях и важных деталях.
                        Используй ясный, лаконичный язык и логическую структуру.
                        
                        ВАЖНО: Не используй эмодзи, звездочки или специальное форматирование.
                        Просто предоставь обычный текст сводки.
                        """,
                        
                        'kk': """
                        Келесі транскрипция негізінде қысқаша, ақпараттық қорытынды (3-5 сөйлем) жасаңыз.
                        Негізгі ақпаратқа, басты идеяларға және маңызды мәліметтерге назар аударыңыз.
                        Анық, қысқа тіл мен логикалық құрылымды қолданыңыз.
                        
                        МАҢЫЗДЫ: Эмодзи, жұлдызша немесе арнайы форматтауды қолданбаңыз.
                        Тек қарапайым мәтін түйіндемесін беріңіз.
                        """
                    },
                    
                    'detailed': {
                        'en': """
                        Create a detailed summary of the transcript.
                        Include overview, key points, and details.
                        
                        IMPORTANT: Do not use emojis or special formatting.
                        Just provide plain text with clear structure.
                        """,
                        
                        'ru': """
                        Создай подробную сводку транскрипции.
                        Включи обзор, основные моменты и детали.
                        
                        ВАЖНО: Не используй эмодзи или специальное форматирование.
                        Просто предоставь обычный текст с четкой структурой.
                        """,
                        
                        'kk': """
                        Транскрипцияның толық түйіндемесін жасаңыз.
                        Шолу, негізгі тұстары және мәліметтерді қосыңыз.
                        
                        МАҢЫЗДЫ: Эмодзи немесе арнайы форматтауды қолданбаңыз.
                        Анық құрылымы бар қарапайым мәтінді беріңіз.
                        """
                    },
                    
                    'bullet': {
                        'en': """
                        Analyze the transcript and extract key thesis points.
                        Output structure:
                        - Main topic (one sentence)
                        - Thesis points (only the most important, 3-7 points)
                        - Conclusion (1-2 sentences if applicable)

                        Use concise style without unnecessary words.
                        Format your response EXACTLY like this:

                        main topic

                        [one sentence describing the main topic]

                        key points

                        - [point 1]
                        - [point 2]
                        - [point 3]

                        conclusion

                        [brief conclusion if applicable]

                        IMPORTANT: Do not use emojis, asterisks, or any special formatting.
                        """,
                        
                        'ru': """
                        Проанализируй аудио и выдели только ключевые тезисы.
                        Структура ответа:
                        - Основная тема (одно предложение)
                        - Тезисы (только самое важное, 3-7 пунктов)
                        - Вывод (1-2 предложения, если применимо)

                        Используй лаконичный стиль без воды.
                        Форматируй ответ СТРОГО так:

                        основная тема

                        [одно предложение с описанием основной темы]

                        тезисы

                        - [тезис 1]
                        - [тезис 2]
                        - [тезис 3]

                        вывод

                        [краткий вывод, если применимо]

                        ВАЖНО: Не используй эмодзи, звездочки или специальное форматирование.
                        """,
                        
                        'kk': """
                        Аудионы талдап, негізгі тезистерді бөліп алыңыз.
                        Жауап құрылымы:
                        - Негізгі тақырып (бір сөйлем)
                        - Тезистер (тек ең маңыздысы, 3-7 тармақ)
                        - Қорытынды (1-2 сөйлем, егер қолданылса)

                        Артық сөзсіз қысқа стиль қолданыңыз.
                        Жауабыңызды ДӘЛ осылай форматтаңыз:

                        негізгі тақырып

                        [негізгі тақырыпты сипаттайтын бір сөйлем]

                        тезистер

                        - [тезис 1]
                        - [тезис 2]
                        - [тезис 3]

                        қорытынды

                        [қысқаша қорытынды, егер қолданылса]

                        МАҢЫЗДЫ: Эмодзи, жұлдызша немесе арнайы форматтауды қолданбаңыз.
                        
                        МАҢЫЗДЫ: Telegram-да Markdown қолдауы шектеулі. Мына ережелерді орындаңыз:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз (оларды жұлдызшаларға салмаңыз)
                        - Тақырыптар үшін # белгілерін қолданбаңыз, олар Telegram-да қолдау көрсетілмейді
                        
                        Дұрыс форматтау мысалы:
                        
                        📋 ТЕЗИСТІК ДАУЫСТЫҚ ТҮЙІНДЕМЕ:
                        
                        📌 НЕГІЗГІ ТАҚЫРЫП:
                        Алексей мен Мария арасындағы X жобасын талқылау, мерзімдер мен тапсырмаларды бөлу мәселелерін қоса алғанда.
                        
                        🔑 НЕГІЗГІ ТҰСТАРЫ:
                        - Алексей ағымдағы тапсырмалардың күйін сұрады
                        - Мария дизайн макеттерінің аяқталғаны туралы хабарлады
                        - Алексей интеграцияны талқылау үшін Дмитриймен кездесу белгілеуді ұсынды
                        - Backend әзірлеу үшін Сергейді тарту қажеттілігін талқылады
                        
                        📎 ҚОСЫМША:
                        - Қаржы бөлімінен Иринамен бюджетті келісу қажет
                        
                        Тізім түпнұсқа хабарламаның барлық негізгі тұстарын қамтитынына көз жеткізіңіз.
                        Әр тезис үшін қысқа, анық тұжырымдамаларды қолданыңыз.
                        """
                    },
                    
                    'combined': {
                        'en': """Provide a combined analysis with both thesis summary and detailed breakdown.
Start with the main topic, then list key theses, followed by detailed analysis, and end with a conclusion.
Use plain text without emojis or special formatting.""",
                        'ru': """Предоставьте комбинированный анализ с тезисным резюме и подробным разбором.
Начните с основной темы, затем перечислите ключевые тезисы, далее подробный анализ, и завершите выводом.
Используйте обычный текст без эмодзи или специального форматирования.""",
                        'kk': """Тезистік қорытынды мен толық талдауды біріктірген талдау жасаңыз.
Негізгі тақырыптан бастап, негізгі тезистерді тізімдеп, толық талдау жасап, қорытындымен аяқтаңыз.
Эмодзи немесе арнайы форматтаусыз қарапайым мәтін қолданыңыз."""
                    },
                    
                    'pasha': {
                        'en': """Provide uncensored and raw analysis of the content.
Be direct and honest, include all explicit details and controversial aspects without any censorship.
Format with main topic, key points, and conclusion.
Use plain text without emojis or special formatting.""",
                        'ru': """Предоставьте анализ без цензуры и фильтров.
Будьте прямы и честны, включайте все явные детали и спорные аспекты без цензуры.
Форматируйте с основной темой, ключевыми моментами и выводом.
Используйте обычный текст без эмодзи или специального форматирования.""",
                        'kk': """Цензурасыз және сүзгісіз талдау жасаңыз.
Тікелей және адал болыңыз, барлық нақты мәліметтер мен даулы аспектілерді цензурасыз қосыңыз.
Негізгі тақырып, негізгі тұстар және қорытындымен пішімдеңіз.
Эмодзи немесе арнайы форматтаусыз қарапайым мәтін қолданыңыз."""
                    }
                }
                
                # First, get the original language transcript regardless of mode
                logger.debug(f"Requesting cleaned transcript in original language...")
                
                # Get the transcript in the original language
                original_transcript_prompt = """
                Provide a clean transcript of the audio that preserves the original wording as much as possible:
                - Keep the exact words and phrases used by the speaker
                - Fix only obvious mistakes in punctuation for readability
                - Do NOT translate - keep the original language
                - Preserve all names, company names, and technical terms exactly as spoken
                - Keep sentence structure and word order as close to the original as possible
                - DO NOT add any commentary or introductory text
                - Simply provide the transcript text directly
                
                Additionally, at the end of your transcript, indicate what language the audio is in using the format: 
                [LANGUAGE: <language name>]
                """
                
                original_response = await model.generate_content_async([original_transcript_prompt, raw_transcript])
                original_transcript = original_response.text
                
                # Extract language identifier if present
                original_language = None
                language_match = re.search(r'\[LANGUAGE:\s*([^\]]+)\]', original_transcript)
                if language_match:
                    original_language = language_match.group(1).strip().lower()
                    # Remove the language tag from the transcript
                    original_transcript = original_transcript.replace(language_match.group(0), '').strip()
                
                logger.info(f"Original transcript generated (detected language: {original_language or 'unknown'}).")
                
                # Normalize language names for comparison
                normalized_orig_lang = original_language
                normalized_user_lang = language
                
                # Simple normalization for common language names
                lang_map = {
                    'russian': 'ru', 'русский': 'ru', 'рус': 'ru', 'rus': 'ru',
                    'english': 'en', 'английский': 'en', 'eng': 'en',
                    'kazakh': 'kk', 'казахский': 'kk', 'қазақша': 'kk', 'kaz': 'kk'
                }
                
                if normalized_orig_lang in lang_map:
                    normalized_orig_lang = lang_map[normalized_orig_lang]
                if normalized_user_lang in lang_map:
                    normalized_user_lang = lang_map[normalized_user_lang]
                
                # Languages match when they're the same or when one is a variant of the other
                # For example, 'ru' matches 'russian' or 'русский'
                languages_match = False
                if normalized_orig_lang and normalized_user_lang:
                    languages_match = normalized_orig_lang == normalized_user_lang or \
                                     normalized_orig_lang.startswith(normalized_user_lang) or \
                                     normalized_user_lang.startswith(normalized_orig_lang)
                
                if mode == "as_is":
                    # For "as_is" mode - provide the original transcript with translation only if languages differ
                    if original_language and not languages_match:
                        # Languages are different, provide both original and translation
                        translation_prompt = f"""
                        Translate the following transcript from {original_language} to {language} while preserving:
                        - All original names, places, companies and technical terms
                        - The same tone and style as the original
                        - All information conveyed in the original
                        
                        IMPORTANT: Telegram has limited Markdown support. Follow these rules:
                        - Use ONLY emojis at the beginning of each section (don't enclose them in asterisks)
                        - Don't use # signs for headers, they are not supported in Telegram
                        
                        Format your response as:
                        
                        📝 ORIGINAL (this word in {language}) ({original_language.upper()}):
                        [Original transcript]
                        
                        🔄 TRANSLATION (this word in {language}) ({language.upper()}):
                        [Translated transcript]
                        """
                        
                        translation_response = await model.generate_content_async([translation_prompt, original_transcript])
                        transcript_text = translation_response.text
                        logger.info(f"Transcript with translation generated from {original_language} to {language}.")
                    else:
                        # Languages match or couldn't be detected - show only the original
                        # Format with a simple header
                        lang_display = original_language.upper() if original_language else "ORIGINAL"
                        # Get the localized mode name for "as_is" mode
                        mode_name = get_mode_name("as_is", language)
                        transcript_text = f"📝 {mode_name} ({lang_display}):\n\n{original_transcript}"
                        logger.info(f"Original transcript used without translation.")
                    
                    # For as_is mode, summary_text should be None so transcript_text is displayed
                    summary_text = None
                elif mode == "transcript":
                    # Legacy transcript mode - just provide the cleaned transcript
                    transcript_prompt = transcript_prompts.get(language, transcript_prompts['en'])
                    cleaned_response = await model.generate_content_async([transcript_prompt, raw_transcript])
                    transcript_text = cleaned_response.text
                    logger.info(f"Cleaned transcript generated in {language}.")
                    summary_text = None
                else:
                    # For other modes, generate the summary based on the raw transcript
                    prompt_map = {
                        "brief": mode_prompts['brief'].get(language, mode_prompts['brief']['en']),
                        "detailed": mode_prompts['detailed'].get(language, mode_prompts['detailed']['en']),
                        "bullet": mode_prompts['bullet'].get(language, mode_prompts['bullet']['en']),
                        "combined": mode_prompts['combined'].get(language, mode_prompts['combined']['en']),
                        "pasha": mode_prompts['pasha'].get(language, mode_prompts['pasha']['ru']), # Corrected: Get prompt based on language, default to Russian
                    }
                    
                    # Special handling for diagram mode - it doesn't use prompt templates
                    # because diagrams are processed by diagram_utils.py functions
                    if mode == "diagram":
                        # For diagram mode we only need the transcript text
                        summary_text = None
                        transcript_text = original_transcript
                        logger.info(f"Transcript extracted for diagram mode in {language}.")
                    else:
                        summary_prompt = prompt_map.get(mode)
                        if not summary_prompt:
                             logger.error(f"Internal error: No prompt found for mode {mode}")
                             return None, None

                        logger.debug(f"Requesting {mode} summary in {language}...")
                        summary_response = await model.generate_content_async([summary_prompt, raw_transcript])
                        summary_text = summary_response.text
                        transcript_text = original_transcript
                        logger.info(f"{mode.capitalize()} summary generated in {language}.")

                # --- Cleanup ---
                # Delete the uploaded file from Gemini (important for managing storage/costs)
                try:
                    logger.debug(f"Deleting Gemini file: {audio_file.name}")
                    genai.delete_file(audio_file.name)
                    logger.info(f"Successfully deleted Gemini file: {audio_file.name}")
                except Exception as e:
                    logger.warning(f"Could not delete Gemini file {audio_file.name}: {e}")

                # Success - return the result
                return summary_text, transcript_text
                
            except Exception as e:
                retry_count += 1
                if retry_count > MAX_RETRIES:
                    logger.error(f"Max retries ({MAX_RETRIES}) exceeded for Gemini API call. Final error: {str(e)}")
                    raise  # Re-raise to be caught by the outer try-except
                
                # Log retry attempt
                logger.warning(f"Gemini API call failed (attempt {retry_count}/{MAX_RETRIES}): {str(e)}. Retrying...")
                
                # Exponential backoff with jitter
                wait_time = (2 ** retry_count) + random.uniform(0, 1)
                logger.info(f"Waiting {wait_time:.2f} seconds before retry...")
                time.sleep(wait_time)
                
                # If we had an uploaded file that might be causing issues, try to delete it
                if audio_file is not None:
                    try:
                        genai.delete_file(audio_file.name)
                        logger.info(f"Deleted potentially problematic file {audio_file.name} before retry")
                    except Exception:
                        pass  # Ignore deletion errors
                    audio_file = None  # Reset for re-upload
        
        # Should never reach here due to the raise in the loop
        return None, None

    except Exception as e:
        logger.error(f"Error processing audio with Gemini: {e}", exc_info=True)
        # Attempt to clean up uploaded file if it exists
        if audio_file is not None:
             try:
                 genai.delete_file(audio_file.name)
                 logger.info(f"Cleaned up Gemini file {audio_file.name} after error.")
             except Exception as delete_e:
                 logger.warning(f"Could not delete Gemini file {audio_file.name} during error cleanup: {delete_e}")
        return None, None 

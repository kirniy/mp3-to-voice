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

# Define supported modes
SUPPORTED_MODES = {
    # Internal mode key: Display name in different languages
    "brief": {
        "en": "Brief",
        "ru": "Кратко",
        "kk": "Қысқаша"
    },
    "detailed": {
        "en": "Detailed",
        "ru": "Подробно",
        "kk": "Толық"
    },
    "bullet": {
        "en": "Bullet Points",
        "ru": "Тезисно",
        "kk": "Тезистер"
    },
    "combined": {
        "en": "Combined",
        "ru": "Комбо",
        "kk": "Біріктірілген"
    },
    "as_is": {
        "en": "As is",
        "ru": "Как есть",
        "kk": "Бар күйінде"
    },
    "pasha": {
        "en": "Unhinged 18+",
        "ru": "Жестко 18+",
        "kk": "Жестко 18+"
    },
    "diagram": {
        "en": "Diagram",
        "ru": "Схема",
        "kk": "Диаграмма"
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
<task>Provide a clean transcript of the audio</task>

<requirements>
- Preserve exact words and phrases used by the speaker
- Fix only obvious punctuation errors for readability
- DO NOT rephrase or reword content
- DO NOT translate - keep original language
- Preserve all names, companies, and technical terms exactly
- Maintain original sentence structure and word order
- NO commentary or introductory text
</requirements>

<output>Direct transcript text only</output>
""",
                    
                    'ru': """
<task>Предоставь транскрипцию аудио</task>

<requirements>
- Сохрани точные слова и фразы говорящего
- Исправь только явные ошибки пунктуации
- НЕ перефразируй и не изменяй содержание
- НЕ переводи - сохрани оригинальный язык
- Сохрани все имена, компании и термины точно
- Сохрани структуру предложений и порядок слов
- БЕЗ комментариев или вводного текста
</requirements>

<output>Только текст транскрипции</output>
""",
                    
                    'kk': """
<task>Аудионың транскрипциясын ұсыныңыз</task>

<requirements>
- Сөйлеушінің нақты сөздері мен тіркестерін сақтаңыз
- Тек анық пунктуация қателерін түзетіңіз
- Мазмұнды қайта тұжырымдамаңыз
- Аудармаңыз - түпнұсқа тілді сақтаңыз
- Барлық есімдер, компаниялар және терминдерді дәл сақтаңыз
- Сөйлем құрылымы мен сөз ретін сақтаңыз
- Түсініктемесіз және кіріспесіз
</requirements>

<output>Тек транскрипция мәтіні</output>
"""
                }
                
                # Define language-specific prompts for each mode
                mode_prompts = {
                    'brief': {
                        'en': """
<task>Create a brief summary (3-5 sentences) of the transcript</task>

<requirements>
- Focus on key information and main ideas
- Preserve ALL person and company names exactly as mentioned
- Keep original perspective (if "I will call you", write "Will call you", not "The speaker will call")
- Maintain original pronouns (you, we, they) as they appear
- NO third-person references like "the speaker", "the person"
- Use clear, concise language
</requirements>

<format>
BRIEF VOICE SUMMARY:

[Your 3-5 sentence summary here]
</format>
""",
                        
                        'ru': """
<task>Создай краткую сводку (3-5 предложений) на основе транскрипции</task>

<requirements>
- Сосредоточься на ключевой информации и основных идеях
- Сохрани ВСЕ имена людей и компаний точно как упомянуто
- Сохрани оригинальную перспективу (если "Я тебе позвоню", пиши "Позвонит", не "Говорящий позвонит")
- Сохрани местоимения (ты, вы, мы, они) как в оригинале
- НЕ используй третье лицо типа "говорящий", "собеседник"
- Используй ясный, лаконичный язык
- Если данных недостаточно, так и укажи
</requirements>

<format>
КРАТКИЙ САММАРИ ВОЙСА:

[Твоя сводка из 3-5 предложений здесь]
</format>
""",
                        
                        'kk': """
<task>Транскрипция негізінде қысқаша түйіндеме (3-5 сөйлем) жаса</task>

<requirements>
- Негізгі ақпарат пен басты идеяларға назар аудар
- БАРЛЫҚ адамдар мен компания атауларын дәл сақта
- Бастапқы көзқарасты сақта (егер "Мен сізге қоңырау шаламын" болса, "Қоңырау шалады" деп жаз)
- Есімдіктерді (сіз, біз, олар) түпнұсқадағыдай сақта
- "Сөйлеуші", "адам" сияқты үшінші жақты ҚОЛДАНБА
- Анық, қысқа тіл қолдан
</requirements>

<format>
ДАУЫСТЫҚ ХАБАРЛАМАНЫҢ ҚЫСҚАША ТҮЙІНДЕМЕСІ:

[Сіздің 3-5 сөйлемдік түйіндемеңіз осында]
</format>
"""
                    },
                    
                    'detailed': {
                        'en': """
<task>Create a detailed, well-structured summary of the transcript</task>

<requirements>
- Include main sections with comprehensive details
- Preserve ALL person and company names exactly
- Keep original perspective (no third-person references)
- Maintain original pronouns (you, we, they)
- Structure information logically
</requirements>

<format>
DETAILED VOICE SUMMARY:

OVERVIEW:
[Main topic and participants]

KEY POINTS:
[Detailed description of important moments and discussions]

DETAILS:
- [First detailed point]
- [Second detailed point]
- [Additional details as needed]

OUTCOMES:
[Conclusions or action items if applicable]
</format>
""",
                        
                        'ru': """
<task>Создай подробную, структурированную сводку транскрипции</task>

<requirements>
- Включи основные разделы с подробной информацией
- Сохрани ВСЕ имена людей и компаний точно
- Сохрани оригинальную перспективу (без третьего лица)
- Сохрани местоимения (ты, вы, мы, они)
- Структурируй информацию логично
</requirements>

<format>
ПОДРОБНЫЙ САММАРИ ВОЙСА:

ОБЗОР:
[Основная тема и участники]

ОСНОВНЫЕ МОМЕНТЫ:
[Подробное описание важных моментов и обсуждений]

ДЕТАЛИ:
- [Первый подробный пункт]
- [Второй подробный пункт]
- [Дополнительные детали по необходимости]

ИТОГИ:
[Выводы или план действий, если применимо]
</format>
""",
                        
                        'kk': """
<task>Транскрипция негізінде толық, құрылымдалған түйіндеме жаса</task>

<requirements>
- Толық ақпараты бар негізгі бөлімдерді қос
- БАРЛЫҚ адамдар мен компания атауларын дәл сақта
- Бастапқы көзқарасты сақта (үшінші жақсыз)
- Есімдіктерді (сіз, біз, олар) сақта
- Ақпаратты логикалық құрылымда
</requirements>

<format>
ТОЛЫҚ ДАУЫСТЫҚ ТҮЙІНДЕМЕ:

ШОЛУ:
[Негізгі тақырып және қатысушылар]

НЕГІЗГІ ТҰСТАРЫ:
[Маңызды сәттер мен талқылаулардың толық сипаттамасы]

ТОЛЫҒЫРАҚ:
- [Бірінші толық тармақ]
- [Екінші толық тармақ]
- [Қажет болса қосымша мәліметтер]

ҚОРЫТЫНДЫ:
[Тұжырымдар немесе іс-шаралар жоспары, егер қолданылса]
</format>
"""
                    },
                    
                    'bullet': {
                        'en': """
<task>Transform the transcript into a bulleted list of key points</task>

<requirements>
- Preserve ALL person and company names exactly
- Keep original perspective (no third-person references)
- Maintain original pronouns (you, we, they)
- Cover all key points from the message
- Use short, clear wording for each point
</requirements>

<format>
BULLET POINT SUMMARY:

MAIN TOPIC:
[Brief description of the main discussion topic]

KEY POINTS:
- [First key point]
- [Second key point]
- [Third key point]
- [Continue as needed]

ADDITIONAL:
- [Any secondary or additional points]
</format>
""",
                        
                        'ru': """
<task>Преобразуй транскрипцию в маркированный список ключевых тезисов</task>

<requirements>
- Сохрани ВСЕ имена людей и компаний точно
- Сохрани оригинальную перспективу (без третьего лица)
- Сохрани местоимения (ты, вы, мы, они)
- Охвати все ключевые моменты сообщения
- Используй короткие, четкие формулировки
</requirements>

<format>
ТЕЗИСНЫЙ САММАРИ ВОЙСА:

ОСНОВНАЯ ТЕМА:
[Краткое описание основной темы обсуждения]

КЛЮЧЕВОЕ:
- [Первый ключевой пункт]
- [Второй ключевой пункт]
- [Третий ключевой пункт]
- [Продолжить по необходимости]

ДОПОЛНИТЕЛЬНОЕ:
- [Любые второстепенные или дополнительные пункты]
</format>
""",
                        
                        'kk': """
<task>Транскрипцияны негізгі тезистер тізіміне айналдыр</task>

<requirements>
- БАРЛЫҚ адамдар мен компания атауларын дәл сақта
- Бастапқы көзқарасты сақта (үшінші жақсыз)
- Есімдіктерді (сіз, біз, олар) сақта
- Хабарламаның барлық негізгі тұстарын қамты
- Әр тармақ үшін қысқа, анық сөздер қолдан
</requirements>

<format>
ТЕЗИСТІК ДАУЫСТЫҚ ТҮЙІНДЕМЕ:

НЕГІЗГІ ТАҚЫРЫП:
[Негізгі талқылау тақырыбының қысқаша сипаттамасы]

НЕГІЗГІ ТҰСТАРЫ:
- [Бірінші негізгі тармақ]
- [Екінші негізгі тармақ]
- [Үшінші негізгі тармақ]
- [Қажет болса жалғастыру]

ҚОСЫМША:
- [Кез келген қосымша немесе екінші дәрежелі тармақтар]
</format>
"""
                    },
                    
                    'combined': {
                        'en': """
<task>Create a combined summary with brief overview and detailed sections</task>

<requirements>
- Start with brief overview (2-3 sentences)
- Follow with bullet points of key information
- Include detailed sections for important topics
- Preserve ALL names and companies exactly
- Keep original perspective and pronouns
</requirements>

<format>
VOICE SUMMARY:
[Brief 2-3 sentence overview]

KEY POINTS:
- [First key point]
- [Second key point]
- [Third key point]
- [Continue as needed]

DETAILS:

[SECTION NAME]:
[Detailed information about this topic]

[ANOTHER SECTION]:
[Detailed information about this topic]

[Continue with additional sections as needed]
</format>
""",
                        
                        'ru': """
<task>Создай комбинированную сводку с кратким обзором и детальными разделами</task>

<requirements>
- Начни с краткого обзора (2-3 предложения)
- Далее маркированный список ключевой информации
- Включи детальные разделы для важных тем
- Сохрани ВСЕ имена и компании точно
- Сохрани оригинальную перспективу и местоимения
</requirements>

<format>
САММАРИ ВОЙСА:
[Краткий обзор из 2-3 предложений]

КЛЮЧЕВОЕ:
- [Первый ключевой пункт]
- [Второй ключевой пункт]
- [Третий ключевой пункт]
- [Продолжить по необходимости]

ПОДРОБНОСТИ:

[НАЗВАНИЕ РАЗДЕЛА]:
[Подробная информация по этой теме]

[ДРУГОЙ РАЗДЕЛ]:
[Подробная информация по этой теме]

[Продолжить с дополнительными разделами по необходимости]
</format>
""",
                        
                        'kk': """
<task>Қысқаша шолу мен толық бөлімдері бар біріктірілген түйіндеме жаса</task>

<requirements>
- Қысқаша шолудан баста (2-3 сөйлем)
- Негізгі ақпараттың тармақталған тізімін жалғастыр
- Маңызды тақырыптар үшін толық бөлімдерді қос
- БАРЛЫҚ есімдер мен компанияларды дәл сақта
- Бастапқы көзқарас пен есімдіктерді сақта
</requirements>

<format>
ДАУЫСТЫҚ ТҮЙІНДЕМЕ:
[2-3 сөйлемдік қысқаша шолу]

НЕГІЗГІ ТҰСТАРЫ:
- [Бірінші негізгі тармақ]
- [Екінші негізгі тармақ]
- [Үшінші негізгі тармақ]
- [Қажет болса жалғастыру]

ТОЛЫҒЫРАҚ:

[БӨЛІМ АТАУЫ]:
[Бұл тақырып бойынша толық ақпарат]

[БАСҚА БӨЛІМ]:
[Бұл тақырып бойынша толық ақпарат]

[Қажет болса қосымша бөлімдермен жалғастыру]
</format>
"""
                    },
                    
                    'pasha': {
                        'en': """
<task>Transform the transcript into uncensored text in Pasha Technik style</task>

<requirements>
- Preserve ALL person and company names exactly as mentioned
- Use profanity, slang, vulgar vocabulary extensively
- Add signature phrases: "xanax", "what", "on speed", references to drugs
- Very short sentences with ellipses
- Chaotic but decisive speech pattern
- Reference parties, cops, stress when contextually appropriate
</requirements>

<style>
- Excessive profanity and vulgar language
- Stream of consciousness with interruptions
- Drug references and altered state implications
- Dismissive attitude toward conventional concerns
</style>

<format>
LISTENED TO THIS SHIT, HERE'S MY TRANSCRIPT YO:
[Chaotic, profanity-laden retelling of the main content]

THE FUCKING POINT:
- [First key point with profanity]
- [Second key point with slang]
- [Continue with vulgar style]

REMEMBER:
[Dismissive conclusion with signature phrases and attitude]
</format>
""",
                        
                        'ru': """
<task>Преобразуй транскрипцию в нецензурный текст в стиле Паши Техника</task>

<requirements>
- Сохрани ВСЕ имена людей и компаний точно как упомянуто
- Используй мат, жаргон, вульгарную лексику максимально
- Добавь фирменные фразы: "ксанакс", "шё", "на спидах", упоминания веществ
- Очень короткие предложения с многоточиями
- Хаотичная но решительная манера речи
- Упоминай тусовки, ментов, стресс где уместно
</requirements>

<style>
- Избыточный мат и вульгарная лексика
- Поток сознания с прерываниями
- Упоминания веществ и измененных состояний
- Пренебрежительное отношение к условностям
</style>

<format>
ПОСЛУШАЛ ЭТУ ХУЙНЮ, ВОТ МОЙ ТРАНСКРИПТ ЙОПТА:
[Хаотичный, матерный пересказ основного содержания]

СУТЬ БЛЯДЬ:
- [Первый ключевой пункт с матом]
- [Второй ключевой пункт с жаргоном]
- [Продолжить в вульгарном стиле]

ЗАПОМНИ:
[Пренебрежительное заключение с фирменными фразами]
</format>
""",
                        
                        'kk': """
<task>Транскрипцияны Паша Техник стиліндегі цензурасыз мәтінге айналдыр</task>

<requirements>
- БАРЛЫҚ адамдар мен компания атауларын дәл сақта
- Боқтық, жаргон, дөрекі лексиканы көп қолдан
- Фирмалық тіркестер қос: есірткі атаулары, "йопта", "бля"
- Өте қысқа сөйлемдер, көп нүктелер
- Хаосты бірақ батыл сөйлеу мәнері
- Орынды жерде кештер, полиция, стресс туралы айт
</requirements>

<style>
- Шамадан тыс боқтық және дөрекі лексика
- Үзіліспен сана ағымы
- Есірткі сілтемелері
- Дәстүрлі нәрселерге менсінбеу
</style>

<format>
БҰЛ БОҚТЫ ТЫҢДАДЫМ, МІНЕ ТРАНСКРИПТ:
[Хаосты, боқтықпен толы негізгі мазмұнды қайта айту]

МӘНІСІ:
- [Боқтықпен бірінші негізгі тармақ]
- [Жаргонмен екінші негізгі тармақ]
- [Дөрекі стильде жалғастыру]

ЕСІҢДЕ БОЛСЫН:
[Фирмалық тіркестермен менсінбейтін қорытынды]
</format>
"""
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
                        
                        Format your response as:
                        
                        ORIGINAL ({original_language.upper()}):
                        [Original transcript]
                        
                        TRANSLATION ({language.upper()}):
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
                        transcript_text = f"{mode_name.upper()} ({lang_display}):\n\n{original_transcript}"
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
                        "pasha": mode_prompts['pasha'].get(language, mode_prompts['pasha']['en']), # Get prompt based on language, default to English
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

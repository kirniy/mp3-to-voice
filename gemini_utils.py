import logging
import google.generativeai as genai
import time  # Added for retries
import random  # Added for jitter in retries
import asyncio  # Added for async sleep
import re  # Added for regular expressions

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
        "ru": "Паша Техник 18+",
        "kk": "Паша Техник 18+"
    },
    "diagram": {
        "en": "📈 Diagram",
        "ru": "📈 Схема",
        "kk": "📈 Диаграмма"
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
                        
                        VERY IMPORTANT: 
                        - Preserve ALL person and company names mentioned in the original transcript
                        - Keep the original perspective/voice (if someone says "I will call you" say "Will call you" not "The speaker will call the listener")
                        - Maintain the original pronouns and references (use "you", "we", "they" as they appear in the original) 
                        - DO NOT use third-person references like "the speaker", "the person", etc.
                        
                        IMPORTANT: Telegram has limited Markdown support. Follow these rules:
                        - Use ONLY emojis at the beginning of each section (don't enclose them in asterisks)
                        - Don't use # signs for headers, they are not supported in Telegram
                        
                        Example of correct formatting:
                        
                        📝 BRIEF VOICE SUMMARY:
                        
                        🗣️ Asks Mike if he was editing something overnight to understand the status. Interested in whether Mike wants to go through the call at 10:30 to supplement what's been done, or if Mike wants to provide edits after viewing the version. Notes that it's important to make a decision by the end of the week.
                        """,
                        
                        'ru': """
                        Создай краткую, но информативную сводку (3-5 предложений) на основе следующей транскрипции на русском языке.
                        Сосредоточься на ключевой информации, основных идеях и важных деталях.
                        Используй ясный, лаконичный язык и логическую структуру.
                        
                        ОЧЕНЬ ВАЖНО: 
                        - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                        - Сохраняй оригинальную перспективу/голос (если кто-то говорит "Я тебе позвоню", пиши "Позвонит", а не "Говорящий позвонит слушателю")
                        - Сохраняй оригинальные местоимения и обращения (используй "ты", "вы", "мы", "они" как в оригинале)
                        - НЕ используй обращения в третьем лице типа "говорящий", "собеседник", "участник" и т.п.
                        - Если недостаточно данных, то так и скажи, а не используй бездумно пример. Не используй ничего из примера напрямую, твои ответы всегда должны включать лишь то, что в транскрипте ты получил
                        ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                        - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                        - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                        
                        Пример корректного форматирования:
                        
                        📝 КРАТКИЙ САММАРИ ВОЙСА:
                        
                        🗣️ Спрашивает Мишу, монтировал ли он что-то ночью для понимания статуса. Интересуется, хочет ли Миша пройтись по звонку в 10:30 по сделанному, чтобы дальше дополнить, или Миша предпочитает, посмотрев версию, дать правки. Отмечает, что важно принять решение до конца недели.
                        """,
                        
                        'kk': """
                        Келесі транскрипция негізінде қысқаша, ақпараттық қорытынды (3-5 сөйлем) жасаңыз.
                        Негізгі ақпаратқа, басты идеяларға және маңызды мәліметтерге назар аударыңыз.
                        Анық, қысқа тіл мен логикалық құрылымды қолданыңыз.
                        
                        ӨТЕ МАҢЫЗДЫ:
                        - Түпнұсқа транскрипцияда аталған БАРЛЫҚ адамдар мен компаниялардың атауларын сақтаңыз
                        - Бастапқы көзқарасты/дауысты сақтаңыз (егер біреу "Мен сізге қоңырау шаламын" десе, "Қоңырау шалады" деп жазыңыз, "Сөйлеуші тыңдаушыға қоңырау шалады" емес)
                        - Түпнұсқа есімдіктер мен сілтемелерді сақтаңыз (түпнұсқада көрсетілгендей "сіз", "біз", "олар" қолданыңыз)
                        - "Сөйлеуші", "адам" сияқты үшінші жақтағы сілтемелерді ПАЙДАЛАНБАҢЫЗ
                        
                        МАҢЫЗДЫ: Telegram-да Markdown қолдауы шектеулі. Мына ережелерді орындаңыз:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз (оларды жұлдызшаларға салмаңыз)
                        - Тақырыптар үшін # белгілерін қолданбаңыз, олар Telegram-да қолдау көрсетілмейді
                        
                        Дұрыс форматтау мысалы:
                        
                        📝 ДАУЫСТЫҚ ХАБАРЛАМАНЫҢ ҚЫСҚАША ТҮЙІНДЕМЕСІ:
                        
                        🗣️ Мишадан статусты түсіну үшін түнде бірдеңе монтаждағанын сұрайды. Мишаның 10:30-да қоңырау бойынша жасалғанды толықтыру үшін өтуді қалайтынын немесе нұсқаны көргеннен кейін өзгерістер енгізгісі келетінін білгісі келеді. Аптаның соңына дейін шешім қабылдау маңызды екенін атап өтеді.
                        """
                    },
                    
                    'detailed': {
                        'en': """
                        Create a detailed, well-structured summary based on the following transcript.
                        Your summary should include main sections and details.
                        
                        VERY IMPORTANT: 
                        - Preserve ALL person and company names mentioned in the original transcript
                        - Keep the original perspective/voice (if someone says "I will call you" say "Will call you" not "The speaker will call the listener")
                        - Maintain the original pronouns and references (use "you", "we", "they" as they appear in the original) 
                        - DO NOT use third-person references like "the speaker", "the person", etc.
                        
                        IMPORTANT: Telegram has limited Markdown support. Follow these rules:
                        - Use ONLY emojis at the beginning of each section (don't enclose them in asterisks)
                        - Don't use # signs for headers, they are not supported in Telegram
                        
                        Example of correct formatting:
                        
                        📋 DETAILED VOICE SUMMARY:
                        
                        📌 OVERVIEW:
                        Michael and Elena discussed the current status of the "Alpha" project and distributed tasks for the coming week.
                        
                        🔑 KEY POINTS:
                        [Here is a detailed description of key moments, arguments, and details with all names preserved]
                        
                        📊 DETAILS:
                        - Michael reported completing the first stage of development
                        - Elena suggested involving Anton for testing
                        - Discussed the project budget and set a goal to complete the work by Friday
                        
                        ✅ OUTCOMES:
                        [Brief conclusion or summary, if applicable]
                        """,
                        
                        'ru': """
                        Создай подробную, хорошо структурированную сводку на основе следующей транскрипции на русском языке.
                        Твоя сводка должна включать основные разделы и детали.
                        
                        ОЧЕНЬ ВАЖНО: 
                        - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                        - Сохраняй оригинальную перспективу/голос (если кто-то говорит "Я тебе позвоню", пиши "Позвонит", а не "Говорящий позвонит слушателю")
                        - Сохраняй оригинальные местоимения и обращения (используй "ты", "вы", "мы", "они" как в оригинале)
                        - НЕ используй обращения в третьем лице типа "говорящий", "собеседник", "участник" и т.п.
                        
                        ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                        - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                        - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                        
                        Пример корректного форматирования:
                        
                        📋 ПОДРОБНЫЙ САММАРИ ВОЙСА:
                        
                        📌 ОБЗОР:
                        Михаил и Елена обсудили текущий статус проекта "Альфа" и распределили задачи на ближайшую неделю.
                        
                        🔑 ОСНОВНЫЕ МОМЕНТЫ:
                        [Здесь подробное описание ключевых моментов, аргументов и деталей с сохранением всех имен]
                        
                        📊 ДЕТАЛИ:
                        - Михаил сообщил о завершении первого этапа разработки
                        - Елена предложила привлечь Антона для тестирования
                        - Обсудили бюджет проекта и поставили цель завершить работу к пятнице
                        
                        ✅ ИТОГИ:
                        [Краткий вывод или заключение, если применимо]
                        """,
                        
                        'kk': """
                        Келесі транскрипция негізінде толық, жақсы құрылымдалған қорытынды жасаңыз.
                        Сіздің қорытындыңыз негізгі бөлімдер мен мәліметтерді қамтуы керек.
                        
                        ӨТЕ МАҢЫЗДЫ:
                        - Түпнұсқа транскрипцияда аталған БАРЛЫҚ адамдар мен компаниялардың атауларын сақтаңыз
                        - Бастапқы көзқарасты/дауысты сақтаңыз (егер біреу "Мен сізге қоңырау шаламын" десе, "Қоңырау шалады" деп жазыңыз, "Сөйлеуші тыңдаушыға қоңырау шалады" емес)
                        - Түпнұсқа есімдіктер мен сілтемелерді сақтаңыз (түпнұсқада көрсетілгендей "сіз", "біз", "олар" қолданыңыз)
                        - "Сөйлеуші", "адам" сияқты үшінші жақтағы сілтемелерді ПАЙДАЛАНБАҢЫЗ
                        
                        МАҢЫЗДЫ: Telegram-да Markdown қолдауы шектеулі. Мына ережелерді орындаңыз:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз (оларды жұлдызшаларға салмаңыз)
                        - Тақырыптар үшін # белгілерін қолданбаңыз, олар Telegram-да қолдау көрсетілмейді
                        
                        Дұрыс форматтау мысалы:
                        
                        📋 ТОЛЫҚ ДАУЫСТЫҚ ТҮЙІНДЕМЕ:
                        
                        📌 ШОЛУ:
                        Михаил мен Елена "Альфа" жобасының ағымдағы күйін талқылап, алдағы аптаға арналған тапсырмаларды бөлді.
                        
                        🔑 НЕГІЗГІ ТҰСТАРЫ:
                        [Мұнда барлық есімдері сақталған негізгі сәттердің, дәлелдердің және мәліметтердің толық сипаттамасы берілген]
                        
                        📊 ТОЛЫҒЫРАҚ:
                        - Михаил әзірлеудің бірінші кезеңінің аяқталғаны туралы хабарлады
                        - Елена тестілеу үшін Антонды тартуды ұсынды
                        - Жоба бюджетін талқылап, жұманың соңына дейін жұмысты аяқтау мақсатын қойдық
                        
                        ✅ ҚОРЫТЫНДЫ:
                        [Қысқаша тұжырым немесе түйіндеме, егер қолданылатын болса]
                        """
                    },
                    
                    'bullet': {
                        'en': """
                        Transform the following transcript into a well-organized bulleted list of key points.
                        
                        VERY IMPORTANT: 
                        - Preserve ALL person and company names mentioned in the original transcript
                        - Keep the original perspective/voice (if someone says "I will call you" say "Will call you" not "The speaker will call the listener")
                        - Maintain the original pronouns and references (use "you", "we", "they" as they appear in the original) 
                        - DO NOT use third-person references like "the speaker", "the person", etc.
                        
                        IMPORTANT: Telegram has limited Markdown support. Follow these rules:
                        - Use ONLY emojis at the beginning of each section (don't enclose them in asterisks)
                        - Don't use # signs for headers, they are not supported in Telegram
                        
                        Example of correct formatting:
                        
                        📋 BULLET POINT SUMMARY:
                        
                        📌 MAIN TOPIC:
                        Discussion between Alexey and Maria about Project X, including deadlines and task distribution.
                        
                        🔑 KEY POINTS:
                        - Alexey asked about the status of current tasks
                        - Maria reported completing the design layouts
                        - Alexey suggested scheduling a meeting with Dmitry to discuss integration
                        - Discussed involving Sergey for backend development
                        
                        📎 ADDITIONAL:
                        - Need to coordinate the budget with Irina from the finance department
                        
                        Make sure the list covers all key points of the original message.
                        Use short, clear wording for each point.
                        """,
                        
                        'ru': """
                        Преобразуй следующую транскрипцию в хорошо организованный маркированный список ключевых тезисов на русском языке.
                        
                        ОЧЕНЬ ВАЖНО: 
                        - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                        - Сохраняй оригинальную перспективу/голос (если кто-то говорит "Я тебе позвоню", пиши "Позвонит", а не "Говорящий позвонит слушателю")
                        - Сохраняй оригинальные местоимения и обращения (используй "ты", "вы", "мы", "они" как в оригинале)
                        - НЕ используй обращения в третьем лице типа "говорящий", "собеседник", "участник" и т.п.
                        
                        ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                        - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                        - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                        
                        Пример корректного форматирования:
                        
                        📋 ТЕЗИСНЫЙ САММАРИ ВОЙСА:
                        
                        📌 ОСНОВНАЯ ТЕМА:
                        Обсуждение между Алексеем и Марией проекта X, включая вопросы сроков и распределения задач.
                        
                        🔑 КЛЮЧЕВОЕ:
                        - Алексей спросил о статусе текущих задач
                        - Мария сообщила о завершении дизайн-макетов
                        - Алексей предложил назначить встречу с Дмитрием для обсуждения интеграции
                        - Обсудили необходимость привлечения Сергея для backend-разработки
                        
                        📎 ДОПОЛНИТЕЛЬНОЕ:
                        - Необходимо согласовать бюджет с Ириной из финансового отдела
                        
                        Убедись, что список охватывает все ключевые моменты оригинального сообщения.
                        Используй короткие, четкие формулировки для каждого пункта.
                        """,
                        
                        'kk': """
                        Келесі транскрипцияны жақсы ұйымдастырылған негізгі тезистердің тізіміне айналдырыңыз.
                        
                        ӨТЕ МАҢЫЗДЫ:
                        - Түпнұсқа транскрипцияда аталған БАРЛЫҚ адамдар мен компаниялардың атауларын сақтаңыз
                        - Бастапқы көзқарасты/дауысты сақтаңыз (егер біреу "Мен сізге қоңырау шаламын" десе, "Қоңырау шалады" деп жазыңыз, "Сөйлеуші тыңдаушыға қоңырау шалады" емес)
                        - Түпнұсқа есімдіктер мен сілтемелерді сақтаңыз (түпнұсқада көрсетілгендей "сіз", "біз", "олар" қолданыңыз)
                        - "Сөйлеуші", "адам" сияқты үшінші жақтағы сілтемелерді ПАЙДАЛАНБАҢЫЗ
                        
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
                        'en': """
                        Create a combined summary based on the following transcript.
                        
                        VERY IMPORTANT: 
                        - Preserve ALL person and company names mentioned in the original transcript
                        - Keep the original perspective/voice (if someone says "I will call you" say "Will call you" not "The speaker will call the listener")
                        - Maintain the original pronouns and references (use "you", "we", "they" as they appear in the original) 
                        - DO NOT use third-person references like "the speaker", "the person", etc.
                        
                        IMPORTANT: Telegram has limited Markdown support. Follow these rules:
                        - Use ONLY emojis at the beginning of each section (don't enclose them in asterisks)
                        - Don't use # signs for headers, they are not supported in Telegram
                        
                        Example of correct formatting:
                        
                        📋 VOICE SUMMARY:
                        Elena talks about a meeting with a client from "X-Tech" company, where requirements for a new project were discussed.
                        
                        🔑 KEY POINTS:
                        - Elena met with Denis from X-Tech in the morning
                        - Denis asked to speed up the development of the first prototype
                        - Need to contact Oleg to clarify technical specifications
                        - Natasha should prepare a presentation by tomorrow
                        - Andrey will be responsible for testing the new functionality
                        
                        📊 DETAILS:
                        
                        📌 MEETING WITH CLIENT:
                        Elena had an hour-long meeting with Denis from X-Tech. Denis expressed concern about the current deadlines and suggested revising task priorities.
                        
                        📌 TEAM TASKS:
                        Oleg needs to clarify technical requirements. Natasha needs to prepare a presentation for the next meeting. Andrey is responsible for testing.
                        
                        📌 DEADLINES AND EXPECTATIONS:
                        The client expects the first prototype by the end of the month. Elena believes the team can meet these deadlines if the plan is adjusted.
                        """,
                        
                        'ru': """
                        Создай комбинированную сводку на основе следующей транскрипции на русском языке.
                        
                        ОЧЕНЬ ВАЖНО: 
                        - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                        - Сохраняй оригинальную перспективу/голос (если кто-то говорит "Я тебе позвоню", пиши "Позвонит", а не "Говорящий позвонит слушателю")
                        - Сохраняй оригинальные местоимения и обращения (используй "ты", "вы", "мы", "они" как в оригинале)
                        - НЕ используй обращения в третьем лице типа "говорящий", "собеседник", "участник" и т.п.
                        
                        ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                        - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                        - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                        
                        Пример корректного форматирования:
                        
                        📋 САММАРИ ВОЙСА:
                        Елена рассказывает о встрече с клиентом из компании "X-Tech", где обсуждались требования к новому проекту.
                        
                        🔑 КЛЮЧЕВОЕ:
                        - Елена встретилась с Денисом из X-Tech утром
                        - Денис попросил ускорить разработку первого прототипа
                        - Необходимо связаться с Олегом для уточнения технической спецификации
                        - Наташа должна подготовить презентацию до завтра
                        - Андрей будет отвечать за тестирование новой функциональности
                        
                        📊 ПОДРОБНОСТИ:
                        
                        📌 ВСТРЕЧА С КЛИЕНТОМ:
                        Елена провела часовую встречу с Денисом из X-Tech. Денис выразил обеспокоенность текущими сроками и предложил пересмотреть приоритеты задач.
                        
                        📌 ЗАДАЧИ ДЛЯ КОМАНДЫ:
                        Олег должен уточнить технические требования. Наташе необходимо подготовить презентацию для следующей встречи. Андрей отвечает за тестирование.
                        
                        📌 СРОКИ И ОЖИДАНИЯ:
                        Клиент ожидает первый прототип к концу месяца. Елена считает, что команда способна уложиться в эти сроки при условии корректировки плана.
                        """,
                        
                        'kk': """
                        Келесі транскрипция негізінде біріктірілген түйіндеме жасаңыз.
                        
                        ӨТЕ МАҢЫЗДЫ:
                        - Түпнұсқа транскрипцияда аталған БАРЛЫҚ адамдар мен компаниялардың атауларын сақтаңыз
                        - Бастапқы көзқарасты/дауысты сақтаңыз (егер біреу "Мен сізге қоңырау шаламын" десе, "Қоңырау шалады" деп жазыңыз, "Сөйлеуші тыңдаушыға қоңырау шалады" емес)
                        - Түпнұсқа есімдіктер мен сілтемелерді сақтаңыз (түпнұсқада көрсетілгендей "сіз", "біз", "олар" қолданыңыз)
                        - "Сөйлеуші", "адам" сияқты үшінші жақтағы сілтемелерді ПАЙДАЛАНБАҢЫЗ
                        
                        МАҢЫЗДЫ: Telegram-да Markdown қолдауы шектеулі. Мына ережелерді орындаңыз:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз (оларды жұлдызшаларға салмаңыз)
                        - Тақырыптар үшін # белгілерін қолданбаңыз, олар Telegram-да қолдау көрсетілмейді
                        
                        Дұрыс форматтау мысалы:
                        
                        📋 ДАУЫСТЫҚ ТҮЙІНДЕМЕ:
                        Елена "X-Tech" компаниясының клиентімен кездесу туралы айтады, онда жаңа жобаға қойылатын талаптар талқыланды.
                        
                        🔑 НЕГІЗГІ ТҰСТАРЫ:
                        - Елена таңертең X-Tech компаниясынан Денисмен кездесті
                        - Денис алғашқы прототипті дамытуды жеделдетуді сұрады
                        - Техникалық ерекшеліктерді нақтылау үшін Олегпен байланысу қажет
                        - Наташа ертеңге дейін презентация дайындауы керек
                        - Андрей жаңа функционалды тестілеуге жауапты болады
                        
                        📊 ТОЛЫҒЫРАҚ:
                        
                        📌 КЛИЕНТПЕН КЕЗДЕСУ:
                        Елена X-Tech компаниясынан Денисмен бір сағаттық кездесу өткізді. Денис ағымдағы мерзімдерге алаңдаушылық білдіріп, тапсырмалардың басымдықтарын қайта қарауды ұсынды.
                        
                        📌 КОМАНДА ТАПСЫРМАЛАРЫ:
                        Олег техникалық талаптарды нақтылауы керек. Наташа келесі кездесуге презентация дайындауы керек. Андрей тестілеуге жауапты.
                        
                        📌 МЕРЗІМДЕР ЖӘНЕ КҮТІЛІМДЕР:
                        Клиент айдың соңына дейін алғашқы прототипті күтеді. Елена жоспар түзетілген жағдайда, команда осы мерзімдерге сәйкес келе алады деп есептейді.
                        """
                    },
                    
                    'pasha': {
                        'en': """
                        Transform the transcript into an uncensored text in the distinctive style of Pasha Technik (Pavel Ivlev).
                        
                        VERY IMPORTANT: 
                        - Preserve ALL person and company names mentioned in the original transcript
                        - Include the exact names used in the text (Mike, Alex, Kate, etc.)
                        - DO NOT replace names with generic terms, use the names specified in the transcript
                        
                        IMPORTANT:
                        - Use obscene, rude, vulgar and slang vocabulary (profanity, jargon, interjections, filler words)
                        - Add signature phrases like "xanax", "what", "on speed", mentions of tranquilizers and weed
                        - Use very short sentences and ellipses occasionally, speak decisively
                        - Include mentions of sex, parties, cops if relevant
                        
                        IMPORTANT for Telegram:
                        - Use ONLY emojis at the beginning of each section
                        - Don't use # signs for headers
                        
                        Example:
                        
                        💊 LISTENED TO THIS SHIT, HERE'S MY TRANSCRIPT YO:
                        Well... listen, fuck... in short. What the fuck is this... Misha, did you, fuck, edit something overnight or what? Status, like project, yeah? Deadlines... fuck... are burning! Shit... I'm, fuck... my head's spinning from this salt... and you're telling me about deadlines... yo! Why worry... huh?

                        🔥 THE FUCKING POINT:
                        - basically... Andrey asks Misha... well... if he edited at night
                        - Andrey wants to go through the call at 10:30... to, fuck, add to it later
                        - Misha needs to decide... fuck... give edits or what
                        
                        ⚡ REMEMBER:
                        Remember... fuck... dude... he doesn't give a shit about all this... about your deadlines... Don't care at all! Did some salt... there... well... came up with a plan... bam-bam... and did it! Everything... fuck... is spot on! Cops... well... they suck, basically. And Technik... Pasha Technik... he, bitch... doesn't fuck up. Never... yo.
                        """,
                        
                        'ru': """
                        Преобразуй транскрипцию в нецензурный текст в фирменном стиле Паши Техника (Павла Ивлева).
                        
                        ОЧЕНЬ ВАЖНО: 
                        - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                        - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                        - НЕ заменяй имена на обобщенные слова, используй те имена, которые указаны в транскрипции
                        
                        МАҢЫЗДЫ:
                        - Былапыт, дөрекі, әдепсіз және жаргон лексикасын қолданыңыз (боқтық, жаргон, одағайлар, паразит сөздер)
                        - "Ксанакс", "шё", "спидте" сияқты фирмалық сөз тіркестерін, транквилизаторлар мен шөптің атауын қосыңыз
                        - Өте қысқа сөйлемдерді және кей жерлерде көп нүктелерді қолданыңыз, шешімді түрде сөйлеңіз
                        - Орынды болса, секс, кештер, ментор туралы айтыңыз
                        
                        Telegram үшін МАҢЫЗДЫ:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз
                        - Тақырыптар үшін # белгілерін қолданбаңыз
                        
                        Мысал:
                        
                        💊 БҰЛ ШЕШЕКТІ ТЫҢДАДЫМ, МІНЕ МЕНІҢ ТРАНСКРИПЦИЯМ, ЙОПТА:
                        Ну... тыңдаш, бля... қысқасы. Бұл не хуйня... Миша, сен, нахуй, түнде бірдеңе монтаждадың ба, әлде қалай? Статус, типа проект, иә? Мерзімдер... блядь... жанып жатыр! Пиздец... Мен, нахуй... басым осы тұздан айналып жатыр... ал сен маған дедлайндар туралы айтып жатырсың... йопта! Не үшін қиналу керек... а?

                        🔥 МӘСЕЛЕНІҢ СУТІ:
                        - қысқасы... Андрей Мишадан сұрайды... ну... түнде монтаждады ма
                        - Андрей 10:30-да қоңырау арқылы өтіп... бля, кейін толықтыру үшін
                        - Миша шешім қабылдауы керек... нахуй... өзгертулер беру керек пе, әлде қалай
                        
                        ⚡ ЕСТЕН ШЫҒАРМА:
                        Есіңде болсын... бля... пацан... ол осының бәріне хуй салады... сенің дедлайндарыңа... Вообще похуй! Соль жасады... сонда... ну... жоспар ойлап тапты... хуяк-хуяк... және жасады! Барлығы... блядь... нақты! Менттер... ну... олар сорады, қысқасы. Ал Техник... Паша Техник... ол, сука... проебывать етпейді. Ешқашан... йопта.
                        """,
                        
                        'kk': """
                        Транскрипцияны Паша Техниктің (Павел Ивлев) ерекше стиліндегі цензурасыз мәтінге айналдырыңыз.
                        
                        ӨТЕ МАҢЫЗДЫ:
                        - Түпнұсқа транскрипцияда аталған БАРЛЫҚ адамдар мен компаниялардың атауларын сақтаңыз
                        - Мәтінде қолданылған нақты есімдерді қосыңыз (Миша, Саша, Катя, т.б.)
                        - Есімдерді жалпы терминдермен АЛМАСТЫРМАҢЫЗ, транскрипцияда көрсетілген есімдерді қолданыңыз
                        
                        МАҢЫЗДЫ:
                        - Былапыт, дөрекі, әдепсіз және жаргон лексикасын қолданыңыз (боқтық, жаргон, одағайлар, паразит сөздер)
                        - "Ксанакс", "шё", "спидте" сияқты фирмалық сөз тіркестерін, транквилизаторлар мен шөптің атауын қосыңыз
                        - Өте қысқа сөйлемдерді және кей жерлерде көп нүктелерді қолданыңыз, шешімді түрде сөйлеңіз
                        - Орынды болса, секс, кештер, ментор туралы айтыңыз
                        
                        Telegram үшін МАҢЫЗДЫ:
                        - Әр бөлімнің басында ТЕК эмодзи қолданыңыз
                        - Тақырыптар үшін # белгілерін қолданбаңыз
                        
                        Мысал:
                        
                        💊 БҰЛ ШЕШЕКТІ ТЫҢДАДЫМ, МІНЕ МЕНІҢ ТРАНСКРИПЦИЯМ, ЙОПТА:
                        Ну... тыңдаш, бля... қысқасы. Бұл не хуйня... Миша, сен, нахуй, түнде бірдеңе монтаждадың ба, әлде қалай? Статус, типа проект, иә? Мерзімдер... блядь... жанып жатыр! Пиздец... Мен, нахуй... басым осы тұздан айналып жатыр... ал сен маған дедлайндар туралы айтып жатырсың... йопта! Не үшін қиналу керек... а?

                        🔥 МӘСЕЛЕНІҢ СУТІ:
                        - қысқасы... Андрей Мишадан сұрайды... ну... түнде монтаждады ма
                        - Андрей 10:30-да қоңырау арқылы өтіп... бля, кейін толықтыру үшін
                        - Миша шешім қабылдауы керек... нахуй... өзгертулер беру керек пе, әлде қалай
                        
                        ⚡ ЕСТЕН ШЫҒАРМА:
                        Есіңде болсын... бля... пацан... ол осының бәріне хуй салады... сенің дедлайндарыңа... Вообще похуй! Соль жасады... сонда... ну... жоспар ойлап тапты... хуяк-хуяк... және жасады! Барлығы... блядь... нақты! Менттер... ну... олар сорады, қысқасы. Ал Техник... Паша Техник... ол, сука... проебывать етпейді. Ешқашан... йопта.
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

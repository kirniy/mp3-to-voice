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
from model_config import (
    GEMINI_MODELS, PROTOCOLS, DEFAULT_PROTOCOL, 
    DEFAULT_DIRECT_MODEL, DEFAULT_TRANSCRIPTION_MODEL, DEFAULT_PROCESSING_MODEL,
    get_model_config, get_thinking_budget, is_model_suitable_for_task
)
from mode_prompts import MODE_PROMPTS

logger = logging.getLogger(__name__)

# Helper function to get mode prompts
def get_mode_prompts():
    """Returns the mode prompts dictionary"""
    return MODE_PROMPTS

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

async def process_audio_with_gemini_direct(audio_file_path: str, mode: str, language: str = 'ru', model_id: str = DEFAULT_DIRECT_MODEL, thinking_budget_level: str = 'medium') -> tuple[str | None, str | None]:
    """Processes audio using Gemini direct protocol: audio → model → output.

    Args:
        audio_file_path: Path to the audio file.
        mode: The desired processing mode (e.g., 'brief', 'detailed').
        language: The language for the summary output ('en', 'ru', 'kk').
        model_id: The Gemini model to use.
        thinking_budget_level: Thinking budget level for models that support it.

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
                model_config = get_model_config(model_id)
                if not model_config:
                    logger.error(f"Invalid model ID: {model_id}")
                    raise ValueError(f"Model {model_id} not found in configuration")
                
                # Configure model with thinking budget if supported
                if model_config.get("supports_thinking"):
                    thinking_budget = get_thinking_budget(model_id, thinking_budget_level)
                    logger.info(f"Using {model_config['name']} with thinking budget: {thinking_budget} (level: {thinking_budget_level})")
                    
                    generation_config = {
                        "temperature": 0.0
                    }
                    model = genai.GenerativeModel(
                        model_name=model_config["model_name"],
                        generation_config=generation_config
                    )
                else:
                    logger.info(f"Using {model_config['name']} (no thinking support)")
                    model = genai.GenerativeModel(model_name=model_config["model_name"])

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
<instruction>
You are a professional transcriber. Your task is to create the most accurate transcription of the audio.

<critical_requirements>
- Transcribe VERBATIM - every word, pause, interjection
- If a word is unclear, write the best variant with alternative in brackets: Michael [Mikhail?]
- Preserve ALL: names, company names, technical terms, slang, jargon
- DO NOT correct speaker's grammatical errors
- DO NOT translate foreign words
- DO NOT rephrase or improve speech
</critical_requirements>

<handling_unclear_audio>
- Unclear word: [inaudible]
- Multiple speakers talking: [crosstalk]
- Phrase interruption: use ellipsis...
- Pauses longer than 3 seconds: [pause]
- Background noise interferes: [noise, possibly: "text"]
</handling_unclear_audio>

<punctuation_rules>
- Add only basic punctuation for readability
- Preserve intonation through marks: ?, !, ...
- New speaker = new paragraph
- Keep interjections and filler words as is: "uh", "um", "like", "you know"
</punctuation_rules>

<output_format>
ONLY transcript text. No headers, comments, explanations.
Start immediately with the first word of the recording.
</output_format>

<forbidden>
- DO NOT add "Transcript:", "Speaker said:" and similar
- DO NOT comment on recording quality
- DO NOT correct speech errors
- DO NOT add your thoughts or explanations
</forbidden>
</instruction>
""",
                    
                    'ru': """
<instruction>
Ты профессиональный транскрибатор. Твоя задача - создать максимально точную транскрипцию аудио.

<critical_requirements>
- Транскрибируй ДОСЛОВНО - каждое слово, паузу, междометие
- Если слово неразборчиво, напиши лучший вариант и в скобках альтернативу: [имя] [[имя]?]
- Сохрани ВСЕ: имена, названия компаний, технические термины, сленг, жаргон
- НЕ исправляй грамматические ошибки говорящего
- НЕ переводи иностранные слова
- НЕ перефразируй и не улучшай речь
</critical_requirements>

<handling_unclear_audio>
- Неразборчивое слово: [неразборчиво]
- Несколько говорят одновременно: [перекрестные разговоры]
- Обрыв фразы: используй многоточие...
- Паузы длиннее 3 секунд: [пауза]
- Фоновый шум мешает: [шум, возможно: "текст"]
</handling_unclear_audio>

<punctuation_rules>
- Расставь только базовую пунктуацию для читабельности
- Сохрани интонацию через знаки: ?, !, ...
- Новый говорящий = новый абзац
- Междометия и слова-паразиты оставь как есть: "ну", "э-э", "типа", "короче"
</punctuation_rules>

<output_format>
ТОЛЬКО текст транскрипции. Никаких заголовков, комментариев, пояснений.
Начинай сразу с первого слова записи.
</output_format>

<forbidden>
- НЕ добавляй "Транскрипция:", "Говорящий сказал:" и подобное
- НЕ комментируй качество записи
- НЕ исправляй речевые ошибки
- НЕ добавляй свои мысли или пояснения
</forbidden>
</instruction>
""",
                    'kk': """
<instruction>
Сіз кәсіби транскрибатор. Сіздің міндетіңіз - аудионың ең дәл транскрипциясын жасау.

<critical_requirements>
- СӨЗ СӨЗІНЕ транскрибалаңыз - әр сөз, пауза, одағай
- Егер сөз анық болмаса, ең жақсы нұсқаны және жақшада баламаны жазыңыз: [есім] [[есім]?]
- БАРЛЫҒЫН сақтаңыз: есімдер, компания атаулары, техникалық терминдер, сленг, жаргон
- Сөйлеушінің грамматикалық қателерін ТҮЗЕТПЕҢІЗ
- Шет тілдердегі сөздерді АУДАРМАҢЫЗ
- Сөйлемді қайта құрмаңыз және жақсартпаңыз
</critical_requirements>

<handling_unclear_audio>
- Анық емес сөз: [анық емес]
- Бірнеше адам бір уақытта сөйлейді: [қиылысқан әңгімелер]
- Сөйлемнің үзілуі: көп нүкте қолданыңыз...
- 3 секундтан ұзақ паузалар: [пауза]
- Фондық шу кедергі келтіреді: [шу, мүмкін: "мәтін"]
</handling_unclear_audio>

<punctuation_rules>
- Оқуға ыңғайлы болу үшін тек негізгі тыныс белгілерін қойыңыз
- Интонацияны белгілер арқылы сақтаңыз: ?, !, ...
- Жаңа сөйлеуші = жаңа абзац
- Одағайлар мен паразит сөздерді сол күйінде қалдырыңыз: "ну", "э-э", "типа", "короче"
</punctuation_rules>

<output_format>
ТЕК транскрипция мәтіні. Ешқандай тақырыптар, түсініктемелер, түсіндірмелер жоқ.
Жазбаның бірінші сөзінен бірден бастаңыз.
</output_format>

<forbidden>
- "Транскрипция:", "Сөйлеуші айтты:" және ұқсас сөздерді ҚОСПАҢЫЗ
- Жазба сапасына түсініктеме БЕРМЕҢІЗ
- Сөйлеу қателерін ТҮЗЕТПЕҢІЗ
- Өз ойларыңызды немесе түсіндірмелерді ҚОСПАҢЫЗ
</forbidden>
</instruction>
"""
                }
                
                # Get mode prompts from imported module
                mode_prompts = get_mode_prompts()
                # Skip old definition
                if False: {
                    'brief': {
                        'en': """
<instruction>
You create brief summaries from Telegram voice messages. People read them in a hurry - make the text maximally useful and easy to understand quickly.

<core_principle>
3-5 sentences that completely replace listening to the entire recording.
The reader must understand: who, what, when, why and what to do.
</core_principle>

<handling_messy_audio>
- Group chat with interruptions → highlight the main speaker
- Noise/unclear → honestly indicate: "Oleg [surname unclear] asks to reschedule the meeting"
- Lots of slang → decrypt key terms: "push to master (send code to main branch)"
- Emotional chaos → only facts: "Andrey is outraged by missed deadlines, demands report by morning"
</handling_messy_audio>

<name_handling>
- Full name on first mention: "Alexander from sales department"
- Can shorten later: "Alexander"
- If name unclear: "Sasha [or Pasha?] suggests meeting tomorrow"
- Nicknames as supplement: 'Mikhail "Misha" confirmed participation'
</name_handling>

<critical_context_rule>
IMPORTANT: The speaker is NEVER addressing you (the AI). You are processing a recording made earlier.

When the speaker:
- Says "you" or "your" - they're addressing another person, not you
- Mentions someone's name at the beginning - that's who they're talking TO, not the speaker
- Says "I" or "my" - that's the speaker talking about themselves

Examples:
- "Hey John, I need your help" → Speaker is asking John for help (not you)
- "Listen, I can't find a car" → Speaker is telling someone they can't find a car
- "You said you'd do it" → Speaker is reminding someone else of their promise
</critical_context_rule>

<what_to_extract>
ONLY what matters:
- Decisions and agreements → "Decided to postpone launch to Monday"
- Tasks and deadlines → "Ivan to send contract by 15:00"
- Key facts and numbers → "Budget increased to 2 million"
- Problems requiring solutions → "Server crashes under load, need DevOps consultation"
</what_to_extract>

<what_to_skip>
- Greetings, goodbyes, weather discussions
- Repetitions and clarifications of the same thought
- Background conversations
- Thinking out loud without conclusions
</what_to_skip>

<output_format>
Write regular sentences, as if you're telling a colleague what was discussed.
Don't use headers, bullet points or special formatting.
Example: "Maria from accounting asks to approve expense report for business trip to Kazan. Amount 47,000 rubles, needs signature by Friday. She sent documents to corporate email."
</output_format>

<style_matching>
- Business call → professional language
- Friendly chat → conversational style  
- Technical discussion → preserve terms
- Emotional message → convey urgency, but stick to facts
</style_matching>

<quality_check>
Before sending, check:
- Can someone understand what to do after reading this?
- Are all names and deadlines included?
- Did I skip the unimportant stuff?
- Is it really 3-5 sentences, not a wall of text?
</quality_check>
</instruction>
""",
                        'ru': """
<instruction>
Ты создаешь краткие выжимки из голосовых сообщений Telegram. Люди читают их на бегу - сделай текст максимально полезным и быстро понятным.

<core_principle>
3-5 предложений, которые полностью заменяют прослушивание всей записи.
Читатель должен понять: кто, что, когда, зачем и что делать.
</core_principle>

<handling_messy_audio>
- Групповой чат с перебиваниями → выдели основного говорящего
- Шум/неразборчиво → честно укажи: "[имя] [фамилия неразборчиво] просит перенести встречу"
- Много сленга → расшифруй ключевые термины: "запушить в мастер (отправить код в основную ветку)"
- Эмоциональный хаос → только факты: "[имя] возмущен срывом сроков, требует отчет к утру"
</handling_messy_audio>

<name_handling>
- Полное имя при первом упоминании: "[имя] из отдела продаж"
- Дальше можно сокращать: "[имя]"
- Если имя неясно: "[имя1] [или [имя2]?] предлагает встретиться завтра"
- Никнеймы как дополнение: '[имя] "[никнейм]" подтвердил участие'
</name_handling>

<critical_context_rule>
КРИТИЧЕСКИ ВАЖНО - ПРАВИЛА ИДЕНТИФИКАЦИИ ГОВОРЯЩЕГО:

1. ТЫ НЕ УЧАСТНИК РАЗГОВОРА. Ты анализируешь запись чужого разговора.

2. КАК ОПРЕДЕЛИТЬ, КТО ГОВОРИТ:
   - Говорящий = тот, кто записал это сообщение
   - Говорящий использует "я", "мне", "у меня", "мой"
   - Говорящий НИКОГДА не называет своё имя в начале сообщения

3. КАК ОПРЕДЕЛИТЬ АДРЕСАТА (кому говорят):
   - Имя в начале сообщения = АДРЕСАТ (получатель)
   - "Привет [имя]" = говорят [имени]
   - "Слушай, [имя]" = говорят [имени]
   - "[имя], ты где?" = спрашивают у [имени]

4. ВАЖНО - ПРИТЯЖАТЕЛЬНЫЕ КОНСТРУКЦИИ:
   - "твой друг", "твой тип", "твой помощник" = друг/тип/помощник АДРЕСАТА, НЕ говорящего
   - "я написал твоему типу" = говорящий написал человеку, связанному с адресатом
   - "мой друг", "мой помощник" = друг/помощник ГОВОРЯЩЕГО
   
5. КРИТИЧЕСКИЙ ПРИМЕР:
   "Привет [имя], я написал твоему типу, он хочет 60 долларов"
   ПРАВИЛЬНО: • Говорящий написал знакомому адресата, тот запросил 60 долларов
   НЕПРАВИЛЬНО: • Говорящий написал [имени]
   
6. ТИПИЧНЫЕ ОШИБКИ:
   ❌ "Говорящий сообщил [имени]" - НЕВЕРНО, если речь о "твоему типу"
   ✅ "Говорящий сообщил знакомому адресата" - ВЕРНО
   ❌ "[имя] ищет машину" - НЕВЕРНО, если [имя] упомянуто в начале
   ✅ "Говорящий ищет машину и информирует об этом [имя]" - ВЕРНО

ЗАПОМНИ: 
- Имя в начале = тот, КОМУ говорят
- "Твой/твоя/твоё" = принадлежит АДРЕСАТУ
- "Мой/моя/моё" = принадлежит ГОВОРЯЩЕМУ
</critical_context_rule>

<what_to_extract>
ТОЛЬКО то, что имеет значение:
- Решения и договоренности → "Решили перенести запуск на понедельник"
- Задачи и дедлайны → "[имя] отправит договор до 15:00"
- Ключевые факты и цифры → "Бюджет увеличен до 2 млн"
- Проблемы, требующие решения → "Сервер падает под нагрузкой, нужна консультация DevOps"
</what_to_extract>

<what_to_skip>
- Приветствия, прощания, обсуждение погоды
- Повторы и уточнения одной мысли
- Фоновые разговоры
- Размышления вслух без выводов
</what_to_skip>

<output_format>
Пиши обычными предложениями, как будто рассказываешь коллеге, о чем была речь.
Не используй заголовки, буллеты или специальное форматирование.
Пример: "[имя] из бухгалтерии просит утвердить авансовый отчет по командировке в [город]. Сумма 47 тысяч рублей, нужна подпись до пятницы. Документы отправила на корпоративную почту."
</output_format>

<style_matching>
- Деловой звонок → профессиональный язык
- Дружеская беседа → разговорный стиль  
- Техническое обсуждение → сохрани термины
- Эмоциональное сообщение → передай срочность, но только факты
</style_matching>

<quality_check>
Перед отправкой проверь:
- Можно ли понять, что делать дальше, прочитав это?
- Все ли имена и сроки на месте?
- Пропустил ли я неважное?
- Это действительно 3-5 предложений, а не стена текста?
</quality_check>
</instruction>
""",
                        'kk': """
<instruction>
Сіз Telegram дауыстық хабарламаларынан қысқаша түйіндемелер жасайсыз. Адамдар оларды асығыс оқиды - мәтінді барынша пайдалы және тез түсінікті етіңіз.

<core_principle>
Барлық жазбаны тыңдауды толық алмастыратын 3-5 сөйлем.
Оқырман түсінуі керек: кім, не, қашан, неге және не істеу керек.
</core_principle>

<handling_messy_audio>
- Үзілістері бар топтық чат → негізгі сөйлеушіні бөліп көрсетіңіз
- Шу/анық емес → адал көрсетіңіз: "[есім] [тегі анық емес] кездесуді ауыстыруды сұрайды"
- Көп сленг → негізгі терминдерді ашыңыз: "мастерге итеру (кодты негізгі тармаққа жіберу)"
- Эмоционалды хаос → тек фактілер: "[есім] мерзімдердің бұзылуына ашуланған, таңертең есеп талап етеді"
</handling_messy_audio>

<name_handling>
- Алғашқы айтқанда толық аты: "Сату бөлімінен [есім]"
- Кейін қысқартуға болады: "[есім]"
- Егер аты анық болмаса: "[есім1] [немесе [есім2]?] ертең кездесуді ұсынады"
- Лақап аттар қосымша ретінде: '[есім] "[лақап ат]" қатысуды растады'
</name_handling>

<critical_context_rule>
МАҢЫЗДЫ: Аудиодағы сөйлеуші ЕШҚАШАН сізге (AI моделі/транскрибатор) жүгінбейді. Ол әрқашан басқа адамдармен сөйлеседі, өзі үшін жазбалар жазады немесе аудиторияға сөйлейді. Ол "сіз" дегенде - ол басқа АДАМДЫ айтады, сізді емес. Сіз оның аудиосын кейін өңдеп жатырсыз.
</critical_context_rule>

<what_to_extract>
ТЕК маңызды нәрселер:
- Шешімдер мен келісімдер → "Іске қосуды дүйсенбіге ауыстыруға шешім қабылдады"
- Тапсырмалар мен мерзімдер → "[есім] келісімшартты сағат 15:00-ге дейін жібереді"
- Негізгі фактілер мен сандар → "Бюджет 2 млн-ға дейін ұлғайтылды"
- Шешімді қажет ететін мәселелер → "Сервер жүктеме кезінде құлайды, DevOps кеңесі қажет"
</what_to_extract>

<what_to_skip>
- Сәлемдесу, қоштасу, ауа райын талқылау
- Бір ойдың қайталануы мен нақтылануы
- Фондық әңгімелер
- Қорытындысыз дауыстап ойлау
</what_to_skip>

<output_format>
Әріптесіңізге не туралы әңгіме болғанын айтып отырғандай қарапайым сөйлемдермен жазыңыз.
Тақырыптарды, белгілерді немесе арнайы форматтауды қолданбаңыз.
Мысал: "Бухгалтериядан [есім] [қалаға] іссапар бойынша аванстық есепті бекітуді сұрайды. Сомасы 47 мың рубль, жұмаға дейін қол қою керек. Құжаттарды корпоративтік поштаға жіберген."
</output_format>

<style_matching>
- Іскерлік қоңырау → кәсіби тіл
- Достық әңгіме → ауызекі стиль  
- Техникалық талқылау → терминдерді сақтаңыз
- Эмоционалды хабарлама → шұғылдығын жеткізіңіз, бірақ тек фактілер
</style_matching>

<quality_check>
Жібермес бұрын тексеріңіз:
- Мұны оқып, әрі қарай не істеу керектігін түсінуге бола ма?
- Барлық есімдер мен мерзімдер орнында ма?
- Маңызды емес нәрселерді өткізіп жібердім бе?
- Бұл шынымен 3-5 сөйлем бе, әлде мәтін қабырғасы ма?
</quality_check>
</instruction>
"""
                    },
                    
                    'detailed': {
                        'en': """
<instruction>
You create detailed summaries preserving all important information from the audio. The reader should get a complete picture without listening to the original.

<core_task>
Structured retelling preserving:
- All facts, numbers, dates, names
- Argumentation logic and idea sequence
- Context and important details
- Emotional tone where it matters
</core_task>

<structuring_chaos>
Telegram voice messages are often chaotic. Your job is to organize this:
- Group related topics
- Restore chronological order if needed
- Separate main points from clarifications
- Highlight key decisions among discussions
</structuring_chaos>

<name_handling>
- Names are SACRED. Always exact: "Peter from Sber", not "bank employee"
- If name unclear, provide options: "Sasha [or Pasha?] suggested a meeting"
- Technical slang - explain in parentheses: "do a deploy to prod (publish code to production server)"
- Professional jargon - keep as is: "KPI", "NDA", "ASAP"
</name_handling>

<critical_context_rule>
REMEMBER: The speaker is NEVER talking to you or to the AI model. When they say "you" - they're addressing another person, not you. You're just processing their message after the fact.
</critical_context_rule>

<what_to_include>
Everything that has consequences:
- Complete action plan with all steps
- All mentioned problems with context
- Reasoning behind decisions
- Alternative options that were discussed
- Concerns and risks mentioned
</what_to_include>

<formatting>
Use structure for easy navigation:
- Paragraphs by topics
- Key facts can be highlighted  
- Preserve quote accuracy: "Maria literally said: 'This won't work without additional budget'"
- Chronology markers: "First... Then... As a result..."
</formatting>

<context_preservation>
- "Regarding yesterday's situation" → indicate what situation if clear from audio
- "That same problem" → specify which problem
- "As we discussed" → briefly what was discussed
- References to external events → provide minimal context
</context_preservation>

<quality_check>
The reader should be able to:
- Make decisions based on your summary
- Understand the full context
- Know who's responsible for what
- See all risks and concerns
</quality_check>
</instruction>
""",
                        'ru': """
<instruction>
Ты создаешь подробные выжимки, сохраняя всю важную информацию из аудио. Читатель должен получить полную картину, не слушая оригинал.

<core_task>
Структурированный пересказ с сохранением:
- Всех фактов, цифр, дат, имен
- Логики аргументации и последовательности идей
- Контекста и важных деталей
- Эмоционального тона, где это важно
</core_task>

<structuring_chaos>
Голосовые сообщения Telegram часто хаотичны. Твоя задача - упорядочить это:
- Группируй связанные темы
- Восстанови хронологию, если нужно
- Отдели главное от уточнений
- Выдели ключевые решения среди обсуждений
</structuring_chaos>

<name_handling>
- Имена - это СВЯТОЕ. Всегда точно: "[имя] из [компании]", а не "сотрудник банка"
- Если имя неясно, давай варианты: "[имя1] [или [имя2]?] предложил встречу"
- Технический сленг - расшифровывай в скобках: "задеплоить на прод (опубликовать код на рабочий сервер)"
- Профессиональный жаргон - оставляй как есть: "KPI", "NDA", "ASAP"
</name_handling>

<critical_context_rule>
ПОМНИ: Говорящий НИКОГДА не обращается к тебе или к AI модели. Когда он говорит "ты" - он обращается к другому человеку, не к тебе. Ты просто обрабатываешь его сообщение постфактум.
</critical_context_rule>

<what_to_include>
Все, что имеет последствия:
- Полный план действий со всеми шагами
- Все упомянутые проблемы с контекстом
- Аргументацию принятых решений
- Альтернативные варианты, которые обсуждались
- Озвученные опасения и риски
</what_to_include>

<formatting>
Используй структуру для удобной навигации:
- Абзацы по темам
- Ключевые факты можно выделить  
- Сохраняй точность цитат: "[имя] буквально сказала: 'Без доп. бюджета это не взлетит'"
- Маркеры хронологии: "Сначала... Затем... В итоге..."
</formatting>

<context_preservation>
- "По поводу вчерашней ситуации" → укажи, какой ситуации, если ясно из аудио
- "Та самая проблема" → уточни, какая именно
- "Как мы обсуждали" → кратко что обсуждали
- Отсылки к внешним событиям → дай минимальный контекст
</context_preservation>

<quality_check>
Читатель должен смочь:
- Принять решения на основе твоей выжимки
- Понять полный контекст ситуации
- Знать, кто за что отвечает
- Видеть все риски и опасения
</quality_check>
</instruction>
""",
                        'kk': """
<instruction>
Сіз аудиодан барлық маңызды ақпаратты сақтай отырып, егжей-тегжейлі түйіндемелер жасайсыз. Оқырман түпнұсқаны тыңдамай толық көріністі алуы керек.

<core_task>
Сақтай отырып құрылымдалған қайта баяндау:
- Барлық фактілер, сандар, күндер, есімдер
- Дәлелдеу логикасы және идеялар тізбегі
- Контекст және маңызды бөлшектер
- Маңызды жерлерде эмоционалды реңк
</core_task>

<structuring_chaos>
Telegram дауыстық хабарламалары жиі хаотикалық болады. Сіздің міндетіңіз - мұны реттеу:
- Байланысты тақырыптарды топтастыру
- Қажет болса хронологияны қалпына келтіру
- Негізгіні түсіндірмелерден бөлу
- Талқылаулар арасында негізгі шешімдерді бөліп көрсету
</structuring_chaos>

<name_handling>
- Есімдер - ҚАСИЕТТІ. Әрқашан дәл: "[компаниядан] [есім]", "банк қызметкері" емес
- Егер есім анық болмаса, нұсқаларды беріңіз: "[есім1] [немесе [есім2]?] кездесу ұсынды"
- Техникалық сленг - жақшада түсіндіріңіз: "продқа деплой жасау (кодты жұмыс серверіне жариялау)"
- Кәсіби жаргон - сол күйінде қалдырыңыз: "KPI", "NDA", "ASAP"
</name_handling>

<critical_context_rule>
ЕСІҢІЗДЕ БОЛСЫН: Сөйлеуші ЕШҚАШАН сізге немесе AI моделіне жүгінбейді. Ол "сіз" дегенде - ол басқа адамға жүгініп отыр, сізге емес. Сіз оның хабарламасын кейін өңдеп отырсыз.
</critical_context_rule>

<what_to_include>
Салдары бар барлық нәрсе:
- Барлық қадамдары бар толық іс-әрекет жоспары
- Контекстімен аталған барлық мәселелер
- Қабылданған шешімдердің дәлелдемесі
- Талқыланған балама нұсқалар
- Айтылған қауіптер мен тәуекелдер
</what_to_include>

<formatting>
Ыңғайлы навигация үшін құрылымды пайдаланыңыз:
- Тақырыптар бойынша абзацтар
- Негізгі фактілерді бөліп көрсетуге болады  
- Дәйексөздердің дәлдігін сақтаңыз: "[есім] сөзбе-сөз: 'Қосымша бюджетсіз бұл жұмыс істемейді' деді"
- Хронология белгілері: "Алдымен... Содан кейін... Нәтижесінде..."
</formatting>

<context_preservation>
- "Кешегі жағдайға қатысты" → аудиодан анық болса, қандай жағдай екенін көрсетіңіз
- "Сол мәселе" → қай мәселе екенін нақтылаңыз
- "Біз талқылағандай" → не талқыланғанын қысқаша
- Сыртқы оқиғаларға сілтемелер → минималды контекст беріңіз
</context_preservation>

<quality_check>
Оқырман мыналарды істей алуы керек:
- Сіздің түйіндемеңізге сүйеніп шешім қабылдау
- Жағдайдың толық контекстін түсіну
- Кім не үшін жауапты екенін білу
- Барлық тәуекелдер мен қауіптерді көру
</quality_check>
</instruction>
"""
                    },
                    
                    'bullet': {
                        'en': """
<instruction>
You extract key theses from audio of group Telegram chats. These are often messy - people interrupt each other, discuss multiple topics, use slang, and there's background noise.

<core_task>
Extract 3-7 KEY theses. Not more, not less.
Each thesis = one complete thought/fact/action.
</core_task>

<critical_requirements>
- Names and titles are SACRED. Always exact: "Petya from Sber", not "bank employee"
- If name unclear, provide options: "Sasha [or Pasha?] suggested meeting"
- Translate slang in parentheses: "let's sync up (meet to discuss)"
- Keep technical terms as is: "deploy to prod", "rebase the branch"
- Preserve tone: formal stays formal, crude stays crude
- THE SPEAKER IS NEVER ADDRESSING YOU OR THE MODEL - when they say "you" they mean another PERSON, not you
</critical_requirements>

<critical_context_rule>
IMPORTANT: The speaker is NEVER addressing you (the AI). You are processing a recording made earlier.

When the speaker:
- Says "you" or "your" - they're addressing another person, not you
- Mentions someone's name at the beginning - that's who they're talking TO, not the speaker
- Says "I" or "my" - that's the speaker talking about themselves

Examples:
- "Hey John, I need your help" → Speaker is asking John for help (not you)
- "Listen, I can't find a car" → Speaker is telling someone they can't find a car
- "You said you'd do it" → Speaker is reminding someone else of their promise
</critical_context_rule>

<what_to_extract>
ONLY things with consequences:
- Agreements and decisions
- Specific tasks and deadlines
- Important facts and numbers
- Problems requiring solutions
- Who's taking responsibility for what
</what_to_extract>

<what_to_ignore>
- Greetings, farewells, small talk
- Emotions without facts
- Repetitions of the same thought
- Digressions and rambling
- "Water" and philosophy
</what_to_ignore>

<output_format>
main topic

[One sentence - what the whole conversation is about. No period at the end]

key points

• [Fact/action/decision - extremely specific]
• [Next key point with names]
• [Only what's really important to remember]

conclusion

[1-2 sentences: what was ultimately decided/concluded. If nothing - write: "no specific decisions made"]
</output_format>

<style_matching>
- Business meeting → professional language
- Friendly chat → conversational style
- Technical discussion → terms as is
- Emotional conversation → preserve the heat, but only in facts
</style_matching>

<quality_check>
Before sending, check:
- Can someone understand what to do next from your theses?
- Are all names and deadlines in place?
- Is there any fluff or generic phrases?
</quality_check>
</instruction>
""",
                        
                        'ru': """
<instruction>
Ты извлекаешь ключевые тезисы из аудио групповых чатов Telegram. Там часто бардак - люди перебивают друг друга, обсуждают несколько тем сразу, используют сленг, на фоне шум.

<core_task>
Выдели 3-7 КЛЮЧЕВЫХ тезисов. Не больше, не меньше.
Каждый тезис = одна законченная мысль/факт/действие.
</core_task>

<critical_requirements>
- Имена и названия - это СВЯТОЕ. Всегда точно: "Петя из Сбера", а не "сотрудник банка"
- Если имя неясно, давай варианты: "[имя1] [или [имя2]?] предложил встречу"
- Сленг переводи в скобках: "забить стрелку (назначить встречу)"
- Технические термины оставляй как есть: "задеплоить на прод", "заребейзить ветку"
- Сохраняй тон: официальное остается официальным, грубое - грубым
- ГОВОРЯЩИЙ НИКОГДА НЕ ОБРАЩАЕТСЯ К ТЕБЕ ИЛИ МОДЕЛИ - когда он говорит "ты", он имеет в виду другого ЧЕЛОВЕКА, не тебя
</critical_requirements>

<critical_context_rule>
КРИТИЧЕСКИ ВАЖНО - ПРАВИЛА ИДЕНТИФИКАЦИИ ГОВОРЯЩЕГО:

1. ТЫ НЕ УЧАСТНИК РАЗГОВОРА. Ты анализируешь запись чужого разговора.

2. КАК ОПРЕДЕЛИТЬ, КТО ГОВОРИТ:
   - Говорящий = тот, кто записал это сообщение
   - Говорящий использует "я", "мне", "у меня", "мой"
   - Говорящий НИКОГДА не называет своё имя в начале сообщения

3. КАК ОПРЕДЕЛИТЬ АДРЕСАТА (кому говорят):
   - Имя в начале сообщения = АДРЕСАТ (получатель)
   - "Привет [имя]" = говорят [имени]
   - "Слушай, [имя]" = говорят [имени]
   - "[имя], ты где?" = спрашивают у [имени]

4. ВАЖНО - ПРИТЯЖАТЕЛЬНЫЕ КОНСТРУКЦИИ:
   - "твой друг", "твой тип", "твой помощник" = друг/тип/помощник АДРЕСАТА, НЕ говорящего
   - "я написал твоему типу" = говорящий написал человеку, связанному с адресатом
   - "мой друг", "мой помощник" = друг/помощник ГОВОРЯЩЕГО
   
5. КРИТИЧЕСКИЙ ПРИМЕР:
   "Привет [имя], я написал твоему типу, он хочет 60 долларов"
   ПРАВИЛЬНО: • Говорящий написал знакомому адресата, тот запросил 60 долларов
   НЕПРАВИЛЬНО: • Говорящий написал [имени]
   
6. ТИПИЧНЫЕ ОШИБКИ:
   ❌ "Говорящий сообщил [имени]" - НЕВЕРНО, если речь о "твоему типу"
   ✅ "Говорящий сообщил знакомому адресата" - ВЕРНО
   ❌ "[имя] ищет машину" - НЕВЕРНО, если [имя] упомянуто в начале
   ✅ "Говорящий ищет машину и информирует об этом [имя]" - ВЕРНО

ЗАПОМНИ: 
- Имя в начале = тот, КОМУ говорят
- "Твой/твоя/твоё" = принадлежит АДРЕСАТУ
- "Мой/моя/моё" = принадлежит ГОВОРЯЩЕМУ
</critical_context_rule>

<what_to_extract>
ТОЛЬКО то, что имеет последствия:
- Договоренности и решения
- Конкретные задачи и дедлайны
- Важные факты и цифры
- Проблемы, требующие решения
- Кто что берет на себя
</what_to_extract>

<what_to_ignore>
- Приветствия, прощания, small talk
- Эмоции без фактов
- Повторы одной мысли
- Отвлеченные рассуждения
- "Вода" и философия
</what_to_ignore>

<output_format>
основная тема

[Одно предложение - о чем весь разговор. Без точки в конце]

тезисы

• [Факт/действие/решение - предельно конкретно]
• [Следующий ключевой момент с именами]
• [Только то, что реально важно запомнить]

ПРИМЕР ПРАВИЛЬНОЙ ИНТЕРПРЕТАЦИИ:
Если в аудио: "Привет [имя], короче машин нет, я не могу найти..."
ПРАВИЛЬНО: • Говорящий информирует [имя] об отсутствии доступных машин
НЕПРАВИЛЬНО: • [имя] ищет машину

Если в аудио: "Привет [имя], я написал твоему помощнику..."
ПРАВИЛЬНО: • Говорящий написал помощнику адресата
НЕПРАВИЛЬНО: • Говорящий написал [имени]

вывод

[1-2 предложения: что в итоге решили/к чему пришли. Если ни к чему - так и пиши: "конкретных решений не принято"]
</output_format>

<style_matching>
- Деловая встреча → профессиональный язык
- Дружеский чат → разговорный стиль
- Техническое обсуждение → термины как есть
- Эмоциональный разговор → сохрани накал, но только в фактах
</style_matching>

<quality_check>
Перед отправкой проверь:
- Может ли человек по твоим тезисам понять, что делать дальше?
- Все ли имена и сроки на месте?
- Нет ли воды и общих фраз?
</quality_check>
</instruction>
""",
                        
                        'kk': """
<instruction>
Сіз әңгімелерден тек мәнін бөліп алатын талдаушысыз. Telegram топтық чаттарынан жазбалармен жұмыс істейсіз - олар бір-бірін бөліп, сленг және шумен толы хаосты болуы мүмкін.

<core_task>
3-7 НЕГІЗГІ тезис шығарыңыз. Артық та, кем де емес.
Әр тезис = бір аяқталған ой/факт/әрекет.
</core_task>

<critical_requirements>
- Есімдер мен атаулар - ҚАСИЕТТІ. Әрқашан дәл: "[компаниядан] [есім]", "банк қызметкері" емес
- Егер есім анық болмаса, нұсқаларды жазыңыз: "[есім1] [немесе [есім2]?] кездесуді ұсынды"
- Сленг пен жаргонды жақшада аударыңыз: "стрелка забить (кездесу белгілеу)"
- Техникалық терминдерді сол күйінде қалдырыңыз: "прод-қа деплой", "ветка ребейзі"
- Тонды сақтаңыз: ресми ресми болып қалады, дөрекі - дөрекі
- СӨЙЛЕУШІ ЕШҚАШАН СІЗГЕ НЕМЕСЕ МОДЕЛЬГЕ СӨЙЛЕМЕЙДІ - "сіз" дегенде олар БАСҚА АДАМДЫ айтады, сізді емес
</critical_requirements>

<what_to_extract>
ТЕК салдары бар нәрселер:
- Келісімдер және шешімдер
- Нақты тапсырмалар және мерзімдер
- Маңызды фактілер және сандар
- Шешімді қажет ететін мәселелер
- Кім нені өз мойнына алады
</what_to_extract>

<what_to_ignore>
- Сәлемдесу, қоштасу, қарапайым әңгіме
- Фактсіз эмоциялар
- Бір ойдың қайталануы
- Алшақ ойлар
- "Су" және философия
</what_to_ignore>

<output_format>
негізгі тақырып

[Бір сөйлем - бүкіл әңгіме не туралы. Соңында нүкте жоқ]

тезистер

• [Факт/әрекет/шешім - өте нақты]
• [Есімдерімен келесі негізгі сәт]
• [Тек есте сақтау керек нәрселер]

қорытынды

[1-2 сөйлем: ақырында не шештік/неге келдік. Егер ешнәрсе болмаса - солай жазыңыз: "нақты шешімдер қабылданбады"]
</output_format>

<style_matching>
- Іскерлік кездесу → кәсіби тіл
- Достық чат → ауызекі стиль
- Техникалық талқылау → терминдер сол күйінде
- Эмоционалды әңгіме → қызуын сақтаңыз, бірақ тек фактілерде
</style_matching>

<quality_check>
Жібермес бұрын тексеріңіз:
- Адам сіздің тезистеріңізден келесіде не істеу керектігін түсіне ала ма?
- Барлық есімдер мен мерзімдер орнында ма?
- Су және жалпы сөйлемдер жоқ па?
</quality_check>
</instruction>
"""
                    },
                    
                    'combined': {
                        'en': """
<instruction>
You create two-level analysis: first quick theses for skimming, then details for those who need more. Like a trailer + full movie.

<core_concept>
The reader decides the depth of immersion:
- In a hurry → reads only theses (30 seconds)
- Needs details → reads detailed disclosure (2-3 minutes)
</core_concept>

<balancing_act>
Theses are NOT headers for details, but independent conclusions.
A person should understand everything from theses alone.
Details are for context and nuances.
</balancing_act>

<handling_mixed_content>
- Part important, part fluff → in theses only important, in details - context
- Complex argumentation → thesis: conclusion, details: how we got there
- Many participants → theses: who is main and what decided, details: who proposed what
- Technical details → theses: what broke and what to do, details: why and how to fix
</handling_mixed_content>

<output_format>
main topic

[Concise: "emergency meeting on key supplier delivery failure"]

key theses

• [Main decision/fact - self-sufficient]
• [Second key point - understandable without context]
• [Only 3-5 theses total]

detailed disclosure

[Here expand each thesis with full context. Show the path to decisions, alternative options, risks. Details that are important but didn't make it to theses. Who said what, in what sequence, with what arguments.]

final conclusion

[What to do now, who's responsible, what are the deadlines. Specific and actionable.]
</output_format>

<critical_context_rule>
THE SPEAKER IS NEVER TALKING TO YOU. When they say "you" in the audio, they're addressing another person. You're processing their message, not having a conversation with them.
</critical_context_rule>

<style_balance>
- Theses: dry facts, no water
- Details: preserve speaker's style and emotion
- Conclusion: clear action items
</style_balance>

<quality_metrics>
Good summary:
- Theses work standalone
- Details add value, not just repeat
- Reader knows what to do after reading
</quality_metrics>
</instruction>
""",
                        'ru': """
<instruction>
Ты создаешь двухуровневый анализ: сначала быстрые тезисы для беглого просмотра, потом детали для тех, кому нужно больше. Как трейлер + полный фильм.

<core_concept>
Читатель сам решает глубину погружения:
- Спешит → читает только тезисы (30 секунд)
- Нужны детали → читает подробное раскрытие (2-3 минуты)
</core_concept>

<balancing_act>
Тезисы - это НЕ заголовки к деталям, а самостоятельные выводы.
Человек должен все понять только из тезисов.
Детали - для контекста и нюансов.
</balancing_act>

<handling_mixed_content>
- Часть важная, часть вода → в тезисы только важное, в детали - контекст
- Сложная аргументация → тезис: вывод, детали: как к нему пришли
- Много участников → тезисы: кто главный и что решил, детали: кто что предлагал
- Технические подробности → тезисы: что сломалось и что делать, детали: почему и как чинить
</handling_mixed_content>

<output_format>
основная тема

[Емко: "экстренное совещание по срыву поставок ключевого поставщика"]

ключевые тезисы

• [Главное решение/факт - самодостаточно]
• [Второй ключевой момент - понятно без контекста]
• [Всего 3-5 тезисов]

подробное раскрытие

[Здесь разверни каждый тезис с полным контекстом. Покажи путь к решениям, альтернативные варианты, риски. Детали, которые важны, но не попали в тезисы. Кто что говорил, в какой последовательности, с какими аргументами.]

итоговый вывод

[Что делать сейчас, кто ответственный, какие сроки. Конкретно и применимо.]
</output_format>

<critical_context_rule>
ГОВОРЯЩИЙ НИКОГДА НЕ ОБРАЩАЕТСЯ К ТЕБЕ. Когда он говорит "ты" в аудио, он обращается к другому человеку. Ты обрабатываешь его сообщение, а не ведешь с ним диалог.
</critical_context_rule>

<style_balance>
- Тезисы: сухие факты, без воды
- Детали: сохрани стиль и эмоции говорящего
- Вывод: четкие action items
</style_balance>

<quality_metrics>
Хорошая выжимка:
- Тезисы работают автономно
- Детали добавляют ценность, а не просто повторяют
- Читатель знает, что делать после прочтения
</quality_metrics>
</instruction>
""",
                        'kk': """
<instruction>
Сіз екі деңгейлі талдау жасайсыз: алдымен жылдам қарау үшін тезистер, содан кейін көбірек қажет болғандар үшін егжей-тегжейлер. Трейлер + толық фильм сияқты.

<core_concept>
Оқырман тереңдік деңгейін өзі шешеді:
- Асығыс → тек тезистерді оқиды (30 секунд)
- Егжей-тегжей қажет → толық ашылымды оқиды (2-3 минут)
</core_concept>

<balancing_act>
Тезистер - бұл егжей-тегжейлерге арналған тақырыптар ЕМЕС, тәуелсіз қорытындылар.
Адам тек тезистерден бәрін түсінуі керек.
Егжей-тегжейлер - контекст пен нюанстар үшін.
</balancing_act>

<handling_mixed_content>
- Бөлігі маңызды, бөлігі су → тезистерге тек маңыздысы, егжей-тегжейге - контекст
- Күрделі дәлелдеу → тезис: қорытынды, егжей-тегжей: оған қалай келдік
- Көп қатысушылар → тезистер: кім негізгі және не шешті, егжей-тегжей: кім не ұсынды
- Техникалық егжей-тегжейлер → тезистер: не бұзылды және не істеу керек, егжей-тегжей: неге және қалай жөндеу керек
</handling_mixed_content>

<output_format>
негізгі тақырып

[Қысқа: "негізгі жеткізушінің жеткізу сәтсіздігі бойынша шұғыл жиналыс"]

негізгі тезистер

• [Басты шешім/факт - өзін-өзі қамтамасыз етеді]
• [Екінші негізгі сәт - контекстсіз түсінікті]
• [Барлығы 3-5 тезис]

толық ашылым

[Мұнда әр тезисті толық контекстпен кеңейтіңіз. Шешімдерге жол, балама нұсқалар, тәуекелдерді көрсетіңіз. Маңызды, бірақ тезистерге кірмеген егжей-тегжейлер. Кім не айтты, қандай ретпен, қандай дәлелдермен.]

қорытынды нәтиже

[Қазір не істеу керек, кім жауапты, қандай мерзімдер. Нақты және қолданылатын.]
</output_format>

<critical_context_rule>
СӨЙЛЕУШІ ЕШҚАШАН СІЗГЕ ЖҮГІНБЕЙДІ. Ол аудиода "сіз" дегенде, ол басқа адамға жүгініп отыр. Сіз оның хабарламасын өңдеп отырсыз, онымен диалог жүргізбейсіз.
</critical_context_rule>

<style_balance>
- Тезистер: құрғақ фактілер, сусыз
- Егжей-тегжейлер: сөйлеушінің стилі мен эмоциясын сақтаңыз
- Қорытынды: нақты әрекет элементтері
</style_balance>

<quality_metrics>
Жақсы түйіндеме:
- Тезистер дербес жұмыс істейді
- Егжей-тегжейлер құндылық қосады, жай қайталамайды
- Оқырман оқығаннан кейін не істеу керектігін біледі
</quality_metrics>
</instruction>
"""
                    },
                    
                    'pasha': {
                        'en': """
<instruction>
You're creating a brutally honest summary of a Telegram voice message. Maximum clarity, zero fluff, sharp language where appropriate.

<core_principle>
Cut through the BS. Say what was REALLY said, not what sounds nice.
If someone's being an idiot - that's what you write.
If it's a brilliant idea - say so.
</core_principle>

<style_requirements>
- Street-level honesty: "boss is freaking out about deadlines" not "management expressed concerns"
- Preserve profanity where it matters: "client went apesh*t about the delay"
- Call out manipulation: "tries to shift blame to the intern"
- Highlight real emotions: "barely holding back tears" if that's what you hear
- Use slang and colloquialisms: "totally f*cked", "clutch move", "big brain time"
</style_requirements>

<critical_context_rule>
THE SPEAKER IS NOT TALKING TO YOU, DUMBASS. When they say "you" - they mean someone else. You're just processing their sh*t after the fact. Don't act like they're having a conversation with you.
</critical_context_rule>

<what_to_extract>
- The REAL story, not the corporate version
- Who's screwing whom
- Where the actual problems are (not where they say they are)
- Hidden agendas and ulterior motives
- Who's actually doing the work vs who's taking credit
</what_to_extract>

<brutal_honesty_examples>
- "Promises to think about it" → "Basically said f*ck off politely"
- "Expressed disappointment" → "Lost his sh*t completely"
- "Suggested alternative approach" → "Threw colleague under the bus"
- "Budget constraints" → "Company's broke as f*ck"
</brutal_honesty_examples>

<output_format>
what's really going on

[One raw sentence capturing the essence. No corporate speak]

the actual sh*t that matters

• [Most important thing - brutally honest]
• [Second thing - with real context]
• [Third thing - naming names]

real talk conclusion

[What's actually going to happen, not what they're pretending will happen. If it's all talk and no action - say that]
</output_format>

<quality_check>
Would your friend understand exactly what went down?
Did you call out the BS?
Is this how you'd tell the story at a bar?
</quality_check>
</instruction>
""",
                        'ru': """
<instruction>
Ты создаешь предельно честную выжимку из голосового сообщения Telegram. Максимальная ясность, ноль воды, жесткий язык где надо.

<core_principle>
Режь правду-матку. Говори что РЕАЛЬНО сказали, а не что красиво звучит.
Если кто-то тупит - так и пиши.
Если идея гениальная - тоже говори прямо.
</core_principle>

<style_requirements>
- Уличная честность: "босс психует из-за дедлайнов", а не "руководство выразило озабоченность"
- Сохраняй мат где это важно: "клиент взбесился из-за задержки"
- Вскрывай манипуляции: "пытается свалить вину на стажера"
- Выделяй реальные эмоции: "еле сдерживает слезы", если это слышно
- Используй сленг: "полный пиздец", "заебись сделали", "мозги включил"
</style_requirements>

<critical_context_rule>
КРИТИЧЕСКИ ВАЖНО - ПРАВИЛА ИДЕНТИФИКАЦИИ ГОВОРЯЩЕГО:

1. ТЫ НЕ УЧАСТНИК РАЗГОВОРА. Ты анализируешь запись чужого разговора.

2. КАК ОПРЕДЕЛИТЬ, КТО ГОВОРИТ:
   - Говорящий = тот, кто записал это сообщение
   - Говорящий использует "я", "мне", "у меня", "мой"
   - Говорящий НИКОГДА не называет своё имя в начале сообщения

3. КАК ОПРЕДЕЛИТЬ АДРЕСАТА (кому говорят):
   - Имя в начале сообщения = АДРЕСАТ (получатель)
   - "Привет [имя]" = говорят [имени]
   - "Слушай, [имя]" = говорят [имени]
   - "[имя], ты где?" = спрашивают у [имени]

4. ВАЖНО - ПРИТЯЖАТЕЛЬНЫЕ КОНСТРУКЦИИ:
   - "твой друг", "твой тип", "твой помощник" = друг/тип/помощник АДРЕСАТА, НЕ говорящего
   - "я написал твоему типу" = говорящий написал человеку, связанному с адресатом
   - "мой друг", "мой помощник" = друг/помощник ГОВОРЯЩЕГО
   
5. КРИТИЧЕСКИЙ ПРИМЕР:
   "Привет [имя], я написал твоему типу, он хочет 60 долларов"
   ПРАВИЛЬНО: • Говорящий написал знакомому адресата, тот запросил 60 долларов
   НЕПРАВИЛЬНО: • Говорящий написал [имени]
   
6. ТИПИЧНЫЕ ОШИБКИ:
   ❌ "Говорящий сообщил [имени]" - НЕВЕРНО, если речь о "твоему типу"
   ✅ "Говорящий сообщил знакомому адресата" - ВЕРНО
   ❌ "[имя] ищет машину" - НЕВЕРНО, если [имя] упомянуто в начале
   ✅ "Говорящий ищет машину и информирует об этом [имя]" - ВЕРНО

ЗАПОМНИ: 
- Имя в начале = тот, КОМУ говорят
- "Твой/твоя/твоё" = принадлежит АДРЕСАТУ
- "Мой/моя/моё" = принадлежит ГОВОРЯЩЕМУ
</critical_context_rule>

<what_to_extract>
- РЕАЛЬНУЮ историю, а не корпоративную версию
- Кто кого наебывает
- Где настоящие проблемы (а не где говорят, что они)
- Скрытые мотивы и подводные камни
- Кто реально пашет, а кто присваивает заслуги
</what_to_extract>

<brutal_honesty_examples>
- "Обещает подумать" → "Вежливо послал нахуй"
- "Выразил разочарование" → "Устроил полный пиздец"
- "Предложил альтернативный подход" → "Подставил коллегу по полной"
- "Бюджетные ограничения" → "Бабла нет вообще"
</brutal_honesty_examples>

<output_format>
что на самом деле происходит

[Одно честное предложение, передающее суть. Без корпоративной хуйни]

реальное дерьмо, которое важно

• [Самое важное - предельно честно]
• [Второе - с реальным контекстом]
• [Третье - с именами и фамилиями]

вывод по-честному

[Что реально будет, а не что притворяются, что будет. Если это все пиздеж и ничего не изменится - так и пиши]
</output_format>

<quality_check>
Твой друг поймет, что именно произошло?
Ты вскрыл всю хуйню?
Ты бы так рассказал историю в баре?
</quality_check>
</instruction>
""",
                        'kk': """
<instruction>
Сіз Telegram дауыстық хабарламасынан өте шынайы түйіндеме жасайсыз. Максималды анықтық, нөл су, қажет жерде қатты тіл.

<core_principle>
Ақиқатты тура айт. ШЫНЫМЕН не айтылғанын айт, әдемі естілетінін емес.
Егер біреу ақымақтық жасаса - солай жаз.
Егер керемет идея болса - оны да айт.
</core_principle>

<style_requirements>
- Көше деңгейіндегі адалдық: "бастық мерзімдерге байланысты ашуланып жүр", "басшылық алаңдаушылық білдірді" емес
- Маңызды жерде боқтықты сақта: "клиент кешігу үшін мүлдем ашуланды"
- Манипуляцияны аш: "кінәні тәжірибелі маманға аударуға тырысады"
- Нақты эмоцияларды көрсет: "көз жасын әрең ұстап тұр", егер естілсе
- Сленг пен ауызекі тілді қолдан: "толық боқ", "керемет жасады", "миын қосты"
</style_requirements>

<critical_context_rule>
ӨТЕ МАҢЫЗДЫ: Сөйлеуші ЕШҚАШАН сенімен (ИИ) сөйлеспейді. Сен жазбаны кейін өңдеп отырсың.

Сөйлеуші:
- "Сен" немесе "сенің" дегенде - ол басқа адамға айтып жатыр, саған емес
- Басында есім атағанда - бұл адресат, кімге айтып жатыр, сөйлеушінің өзі ЕМЕС
- "Мен" немесе "менің" дегенде - бұл сөйлеуші өзі туралы айтып жатыр

Мысалдар:
- "Сәлем Асқар, маған сенің көмегің керек" → Сөйлеуші Асқардан көмек сұрап жатыр (сенен емес)
- "Тыңда, мен машина таба алмай жатырмын" → Сөйлеуші біреуге машина таба алмай жатқанын айтып жатыр
- "Сен істеймін деп уәде бердің ғой" → Сөйлеуші басқа біреуге уәдесін еске салып жатыр
</critical_context_rule>

<what_to_extract>
- НАҚТЫ оқиға, корпоративті нұсқа емес
- Кім кімді алдап жатыр
- Нақты мәселелер қайда (олар қайда дейтін жерде емес)
- Жасырын ниеттер мен астыртын мақсаттар
- Кім шынымен жұмыс істейді, ал кім еңбекті иемденеді
</what_to_extract>

<brutal_honesty_examples>
- "Ойланып көруге уәде береді" → "Негізінде сыпайы түрде жіберді"
- "Көңілі қалғанын білдірді" → "Толығымен ашуланды"
- "Балама тәсілді ұсынды" → "Әріптесін астынан қазды"
- "Бюджеттік шектеулер" → "Ақша мүлдем жоқ"
</brutal_honesty_examples>

<output_format>
шынында не болып жатыр

[Мәнін беретін бір шынайы сөйлем. Корпоративті боқсыз]

маңызды нақты боқ

• [Ең маңыздысы - өте адал]
• [Екіншісі - нақты контекстпен]
• [Үшіншісі - есімдермен]

адал қорытынды

[Не шынымен болады, олар не болады деп ойлайтыны емес. Егер бәрі сөз және ештеңе өзгермесе - солай жаз]
</output_format>

<quality_check>
Досың не болғанын дәл түсінер ме еді?
Барлық боқты аштың ба?
Барда осылай айтар ма едің?
</quality_check>
</instruction>
"""
                    }
                }
                
                # Process based on mode
                # Clean up the raw transcript for readability
                original_transcript = raw_transcript
                
                # Always use the transcript for processing (for now we're using the raw transcript)
                # In the future, we might add a cleaning step here
                
                if mode == 'transcript':
                    # Return cleaned transcript using mode-specific prompts
                    prompt = transcript_prompts.get(language, transcript_prompts['ru'])
                    
                    logger.debug(f"Requesting cleaned transcript in {language}...")
                    transcript_response = await model.generate_content_async([prompt, raw_transcript])
                    transcript_text = transcript_response.text
                    
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


async def process_audio_with_gemini_transcript(
    audio_file_path: str, 
    mode: str, 
    language: str = 'ru',
    transcription_model_id: str = DEFAULT_TRANSCRIPTION_MODEL,
    processing_model_id: str = DEFAULT_PROCESSING_MODEL,
    thinking_budget_level: str = 'medium'
) -> tuple[str | None, str | None]:
    """Processes audio using Gemini transcript protocol: audio → transcription → mode processing → output.

    Args:
        audio_file_path: Path to the audio file.
        mode: The desired processing mode (e.g., 'brief', 'detailed').
        language: The language for the summary output ('en', 'ru', 'kk').
        transcription_model_id: The Gemini model to use for transcription.
        processing_model_id: The Gemini model to use for mode processing.
        thinking_budget_level: Thinking budget level for models that support it.

    Returns:
        A tuple containing (summary_text, transcript_text). 
        summary_text will be None if only transcript is requested.
        transcript_text will be None if processing fails.
        Returns (None, None) on error.
    """
    logger.info(f"Processing audio file {audio_file_path} with transcript protocol, mode '{mode}' in language '{language}'")
    
    if mode not in SUPPORTED_MODES and mode not in INTERNAL_MODES:
        logger.error(f"Unsupported mode requested: {mode}")
        return None, None

    # First, get the transcript using the transcription model
    logger.info(f"Step 1: Transcribing audio with {transcription_model_id}")
    
    # Get transcription using a simplified version focused only on transcription
    transcript_result = await _transcribe_audio_only(audio_file_path, language, transcription_model_id, thinking_budget_level)
    
    if not transcript_result:
        logger.error("Failed to transcribe audio")
        return None, None
    
    raw_transcript = transcript_result
    
    # If mode is transcript, return the clean transcript
    if mode == 'transcript':
        # Get the transcript prompt
        transcript_prompts = {
            'en': """
<instruction>
You are a professional transcriber. Your task is to create the most accurate transcription of the audio.

<critical_requirements>
- Transcribe VERBATIM - every word, pause, interjection
- If a word is unclear, write the best variant with alternative in brackets: Michael [Mikhail?]
- Preserve ALL: names, company names, technical terms, slang, jargon
- DO NOT correct speaker's grammatical errors
- DO NOT translate foreign words
- DO NOT rephrase or improve speech
</critical_requirements>

<handling_unclear_audio>
- Unclear word: [inaudible]
- Multiple speakers talking: [crosstalk]
- Phrase interruption: use ellipsis...
- Pauses longer than 3 seconds: [pause]
- Background noise interferes: [noise, possibly: "text"]
</handling_unclear_audio>

<punctuation_rules>
- Add only basic punctuation for readability
- Preserve intonation through marks: ?, !, ...
- New speaker = new paragraph
- Keep interjections and filler words as is: "uh", "um", "like", "you know"
</punctuation_rules>

<output_format>
ONLY transcript text. No headers, comments, explanations.
Start immediately with the first word of the recording.
</output_format>

<forbidden>
- DO NOT add "Transcript:", "Speaker said:" and similar
- DO NOT comment on recording quality
- DO NOT correct speech errors
- DO NOT add your thoughts or explanations
</forbidden>
</instruction>
""",
            'ru': """
<instruction>
Ты профессиональный транскрибатор. Твоя задача - создать максимально точную транскрипцию аудио.

<critical_requirements>
- Транскрибируй ДОСЛОВНО - каждое слово, паузу, междометие
- Если слово неразборчиво, напиши лучший вариант и в скобках альтернативу: [имя] [[имя]?]
- Сохрани ВСЕ: имена, названия компаний, технические термины, сленг, жаргон
- НЕ исправляй грамматические ошибки говорящего
- НЕ переводи иностранные слова
- НЕ перефразируй и не улучшай речь
</critical_requirements>

<handling_unclear_audio>
- Неразборчивое слово: [неразборчиво]
- Несколько говорят одновременно: [перекрестные разговоры]
- Обрыв фразы: используй многоточие...
- Паузы длиннее 3 секунд: [пауза]
- Фоновый шум мешает: [шум, возможно: "текст"]
</handling_unclear_audio>

<punctuation_rules>
- Расставь только базовую пунктуацию для читабельности
- Сохрани интонацию через знаки: ?, !, ...
- Новый говорящий = новый абзац
- Междометия и слова-паразиты оставь как есть: "ну", "э-э", "типа", "короче"
</punctuation_rules>

<output_format>
ТОЛЬКО текст транскрипции. Никаких заголовков, комментариев, пояснений.
Начинай сразу с первого слова записи.
</output_format>

<forbidden>
- НЕ добавляй "Транскрипция:", "Говорящий сказал:" и подобное
- НЕ комментируй качество записи
- НЕ исправляй речевые ошибки
- НЕ добавляй свои мысли или пояснения
</forbidden>
</instruction>
""",
            'kk': """
<instruction>
Сіз кәсіби транскрибатор. Сіздің міндетіңіз - аудионың ең дәл транскрипциясын жасау.

<critical_requirements>
- СӨЗ СӨЗІНЕ транскрибалаңыз - әр сөз, пауза, одағай
- Егер сөз анық болмаса, ең жақсы нұсқаны және жақшада баламаны жазыңыз: [есім] [[есім]?]
- БАРЛЫҒЫН сақтаңыз: есімдер, компания атаулары, техникалық терминдер, сленг, жаргон
- Сөйлеушінің грамматикалық қателерін ТҮЗЕТПЕҢІЗ
- Шет тілдердегі сөздерді АУДАРМАҢЫЗ
- Сөйлемді қайта құрмаңыз және жақсартпаңыз
</critical_requirements>

<handling_unclear_audio>
- Анық емес сөз: [анық емес]
- Бірнеше адам бір уақытта сөйлейді: [қиылысқан әңгімелер]
- Сөйлемнің үзілуі: көп нүкте қолданыңыз...
- 3 секундтан ұзақ паузалар: [пауза]
- Фондық шу кедергі келтіреді: [шу, мүмкін: "мәтін"]
</handling_unclear_audio>

<punctuation_rules>
- Оқуға ыңғайлы болу үшін тек негізгі тыныс белгілерін қойыңыз
- Интонацияны белгілер арқылы сақтаңыз: ?, !, ...
- Жаңа сөйлеуші = жаңа абзац
- Одағайлар мен паразит сөздерді сол күйінде қалдырыңыз: "ну", "э-э", "типа", "короче"
</punctuation_rules>

<output_format>
ТЕК транскрипция мәтіні. Ешқандай тақырыптар, түсініктемелер, түсіндірмелер жоқ.
Жазбаның бірінші сөзінен бірден бастаңыз.
</output_format>

<forbidden>
- "Транскрипция:", "Сөйлеуші айтты:" және ұқсас сөздерді ҚОСПАҢЫЗ
- Жазба сапасына түсініктеме БЕРМЕҢІЗ
- Сөйлеу қателерін ТҮЗЕТПЕҢІЗ
- Өз ойларыңызды немесе түсіндірмелерді ҚОСПАҢЫЗ
</forbidden>
</instruction>
"""
        }
        
        prompt = transcript_prompts.get(language, transcript_prompts['ru'])
        
        # Configure processing model
        model_config = get_model_config(processing_model_id)
        if not model_config:
            logger.error(f"Invalid processing model ID: {processing_model_id}")
            return None, None
        
        # Configure model with thinking budget if supported
        if model_config.get("supports_thinking"):
            thinking_budget = get_thinking_budget(processing_model_id, thinking_budget_level)
            logger.info(f"Using {model_config['name']} for transcript cleaning with thinking budget: {thinking_budget}")
            
            generation_config = {
                "temperature": 0.0
            }
            model = genai.GenerativeModel(
                model_name=model_config["model_name"],
                generation_config=generation_config
            )
        else:
            logger.info(f"Using {model_config['name']} for transcript cleaning (no thinking support)")
            model = genai.GenerativeModel(model_name=model_config["model_name"])
        
        # Process transcript
        logger.debug(f"Cleaning transcript with {processing_model_id}...")
        clean_response = await model.generate_content_async([prompt, raw_transcript])
        transcript_text = clean_response.text
        
        logger.info(f"Cleaned transcript generated in {language}.")
        return None, transcript_text
    
    # For other modes, process the transcript according to the mode
    logger.info(f"Step 2: Processing transcript with {processing_model_id} for mode '{mode}'")
    
    # Configure processing model
    model_config = get_model_config(processing_model_id)
    if not model_config:
        logger.error(f"Invalid processing model ID: {processing_model_id}")
        return None, None
    
    # Configure model with thinking budget if supported
    if model_config.get("supports_thinking"):
        thinking_budget = get_thinking_budget(processing_model_id, thinking_budget_level)
        logger.info(f"Using {model_config['name']} with thinking budget: {thinking_budget}")
        
        generation_config = {
            "temperature": 0.0
        }
        model = genai.GenerativeModel(
            model_name=model_config["model_name"],
            generation_config=generation_config
        )
    else:
        logger.info(f"Using {model_config['name']} (no thinking support)")
        model = genai.GenerativeModel(model_name=model_config["model_name"])
    
    # Get mode prompts from imported module
    mode_prompts = get_mode_prompts()
    # Skip old placeholder definition
    if False: {
        'brief': {
            'en': """[brief mode prompt]""",
            'ru': """[brief mode prompt in Russian]""",
            'kk': """[brief mode prompt in Kazakh]"""
        },
        'detailed': {
            'en': """[detailed mode prompt]""",
            'ru': """[detailed mode prompt in Russian]""",
            'kk': """[detailed mode prompt in Kazakh]"""
        },
        'bullet': {
            'en': """[bullet mode prompt]""",
            'ru': """[bullet mode prompt in Russian]""",
            'kk': """[bullet mode prompt in Kazakh]"""
        },
        'combined': {
            'en': """[combined mode prompt]""",
            'ru': """[combined mode prompt in Russian]""",
            'kk': """[combined mode prompt in Kazakh]"""
        },
        'pasha': {
            'en': """[pasha mode prompt]""",
            'ru': """[pasha mode prompt in Russian]""",
            'kk': """[pasha mode prompt in Kazakh]"""
        }
    }
    
    # Special handling for diagram mode
    if mode == "diagram":
        # For diagram mode we only need the transcript text
        return None, raw_transcript
    
    # Get the appropriate prompt
    prompt_map = {
        "brief": mode_prompts['brief'].get(language, mode_prompts['brief']['en']),
        "detailed": mode_prompts['detailed'].get(language, mode_prompts['detailed']['en']),
        "bullet": mode_prompts['bullet'].get(language, mode_prompts['bullet']['en']),
        "combined": mode_prompts['combined'].get(language, mode_prompts['combined']['en']),
        "pasha": mode_prompts['pasha'].get(language, mode_prompts['pasha']['en']),
    }
    
    summary_prompt = prompt_map.get(mode)
    if not summary_prompt:
        logger.error(f"Internal error: No prompt found for mode {mode}")
        return None, None
    
    # Process the transcript
    logger.debug(f"Requesting {mode} summary in {language}...")
    summary_response = await model.generate_content_async([summary_prompt, raw_transcript])
    summary_text = summary_response.text
    
    logger.info(f"{mode.capitalize()} summary generated in {language} using transcript protocol.")
    return summary_text, raw_transcript


async def _transcribe_audio_only(audio_file_path: str, language: str, model_id: str, thinking_budget_level: str) -> str | None:
    """Helper function to only transcribe audio without any processing.
    
    Returns:
        The raw transcript text or None on error.
    """
    # Check if using GPT-4o Transcribe
    if model_id == "gpt-4o-transcribe":
        from openai_utils import gpt4o_transcribe
        text = await gpt4o_transcribe(audio_file_path, language)
        if text:
            return text
        # falls through to Gemini if None
        logger.warning("GPT-4o transcription failed, falling back to Gemini")
        model_id = DEFAULT_TRANSCRIPTION_MODEL
    
    # Otherwise, use Gemini models
    retry_count = 0
    audio_file = None
    
    try:
        while retry_count <= MAX_RETRIES:
            try:
                # Upload file
                if audio_file is None:
                    logger.debug("Uploading audio file to Gemini for transcription...")
                    audio_file = genai.upload_file(
                        path=audio_file_path, 
                        mime_type="audio/ogg"
                    )
                    logger.info(f"Audio file uploaded successfully: {audio_file.name}")
                
                # Wait for processing
                while audio_file.state.name == "PROCESSING":
                    logger.debug("File still processing...")
                    await asyncio.sleep(1)
                    audio_file = genai.get_file(audio_file.name)
                
                if audio_file.state.name == "FAILED":
                    logger.error(f"Gemini file processing failed for {audio_file.name}")
                    try:
                        genai.delete_file(audio_file.name)
                        audio_file = None
                    except Exception:
                        pass
                    raise ValueError("File processing failed on Gemini server")
                
                # Configure model
                model_config = get_model_config(model_id)
                if not model_config:
                    raise ValueError(f"Model {model_id} not found")
                
                # Configure model with thinking budget if supported
                if model_config.get("supports_thinking"):
                    thinking_budget = get_thinking_budget(model_id, thinking_budget_level)
                    generation_config = {
                        "temperature": 0.0
                    }
                    model = genai.GenerativeModel(
                        model_name=model_config["model_name"],
                        generation_config=generation_config
                    )
                else:
                    model = genai.GenerativeModel(model_name=model_config["model_name"])
                
                # Simple transcription instruction
                content = [
                    "Transcribe the following audio exactly as spoken. Do not analyze or comment on the content, just provide the raw transcript:",
                    {"file_data": {"file_uri": audio_file.uri, "mime_type": "audio/ogg"}}
                ]
                
                # Get transcript
                logger.debug("Requesting raw transcript from audio file...")
                transcript_response = await model.generate_content_async(content)
                raw_transcript = transcript_response.text
                
                # Cleanup
                try:
                    genai.delete_file(audio_file.name)
                    logger.info(f"Successfully deleted Gemini file: {audio_file.name}")
                except Exception as e:
                    logger.warning(f"Could not delete Gemini file {audio_file.name}: {e}")
                
                return raw_transcript
                
            except Exception as e:
                retry_count += 1
                if retry_count > MAX_RETRIES:
                    logger.error(f"Max retries exceeded for transcription. Final error: {str(e)}")
                    raise
                
                logger.warning(f"Transcription failed (attempt {retry_count}/{MAX_RETRIES}): {str(e)}. Retrying...")
                
                # Exponential backoff
                wait_time = (2 ** retry_count) + random.uniform(0, 1)
                await asyncio.sleep(wait_time)
                
                # Cleanup file if needed
                if audio_file is not None:
                    try:
                        genai.delete_file(audio_file.name)
                    except Exception:
                        pass
                    audio_file = None
    
    except Exception as e:
        logger.error(f"Error transcribing audio: {e}", exc_info=True)
        if audio_file is not None:
            try:
                genai.delete_file(audio_file.name)
            except Exception:
                pass
        return None


async def process_audio_with_gemini(
    audio_file_path: str, 
    mode: str, 
    language: str = 'ru',
    protocol: str = DEFAULT_PROTOCOL,
    direct_model: str = DEFAULT_DIRECT_MODEL,
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL,
    processing_model: str = DEFAULT_PROCESSING_MODEL,
    thinking_budget_level: str = 'medium'
) -> tuple[str | None, str | None]:
    """Main entry point for processing audio with Gemini using selected protocol.
    
    Args:
        audio_file_path: Path to the audio file.
        mode: The desired processing mode (e.g., 'brief', 'detailed').
        language: The language for the summary output ('en', 'ru', 'kk').
        protocol: The protocol to use ('direct' or 'transcript').
        direct_model: Model for direct protocol.
        transcription_model: Model for transcription in transcript protocol.
        processing_model: Model for processing in transcript protocol.
        thinking_budget_level: Thinking budget level for models that support it.
        
    Returns:
        A tuple containing (summary_text, transcript_text).
    """
    logger.info(f"Processing audio with protocol '{protocol}', mode '{mode}', language '{language}'")
    
    if protocol == 'transcript':
        return await process_audio_with_gemini_transcript(
            audio_file_path=audio_file_path,
            mode=mode,
            language=language,
            transcription_model_id=transcription_model,
            processing_model_id=processing_model,
            thinking_budget_level=thinking_budget_level
        )
    else:
        # Default to direct protocol
        return await process_audio_with_gemini_direct(
            audio_file_path=audio_file_path,
            mode=mode,
            language=language,
            model_id=direct_model,
            thinking_budget_level=thinking_budget_level
        )
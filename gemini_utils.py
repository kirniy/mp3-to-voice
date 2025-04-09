import logging
import google.generativeai as genai
import time  # Added for retries
import random  # Added for jitter in retries
import asyncio  # Added for async sleep

logger = logging.getLogger(__name__)

# Define supported modes
SUPPORTED_MODES = {
    "brief": "Кратко",
    "detailed": "Подробно",
    "bullet": "Тезисно",
    "combined": "Комбо",
    "transcript": "Как есть",
    "pasha": "Паша Техник"
}
DEFAULT_MODE = "brief"

# Max retries for transient errors
MAX_RETRIES = 3

async def process_audio_with_gemini(audio_file_path: str, mode: str) -> tuple[str | None, str | None]:
    """Processes audio using Gemini: transcription + requested mode.

    Args:
        audio_file_path: Path to the audio file.
        mode: The desired processing mode (e.g., 'brief', 'detailed').

    Returns:
        A tuple containing (summary_text, transcript_text). 
        summary_text will be None if only transcript is requested.
        transcript_text will be None if processing fails.
        Returns (None, None) on error.
    """
    logger.info(f"Processing audio file {audio_file_path} with mode '{mode}'")
    
    if mode not in SUPPORTED_MODES:
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
                # Using Gemini 1.5 Flash for fast processing
                model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")

                # --- Define Improved Prompts ---
                # Transcript first - this is our base for everything else
                transcription_prompt = """
                Transcribe the following audio file accurately in Russian. 
                Preserve the speaker's exact words, but you may add punctuation and paragraphs for readability.
                """
                
                # Enhanced prompts for better summaries
                brief_summary_prompt = """
                Создай краткую, но информативную сводку (3-5 предложений) на основе следующей транскрипции на русском языке.
                Сосредоточься на ключевой информации, основных идеях и важных деталях.
                Используй ясный, лаконичный язык и логическую структуру.
                
                ОЧЕНЬ ВАЖНО: 
                - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                - НЕ заменяй имена на обобщенные слова типа "говорящий", "собеседник" или "участник"
                - Если кто-то обращается к конкретному человеку, обязательно включи это имя
                
                ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                - Если невозможно определить пол говорящего на 100%, используй мужской род по умолчанию, 
                  но добавляй женские окончания в скобках: например, "отметил(а)", "сказал(а)"
                
                Пример корректного форматирования:
                
                📝 КРАТКИЙ САММАРИ ВОЙСА:
                
                🗣️ Андрей спрашивает Мишу, монтировал ли он что-то ночью для понимания статуса. Андрей интересуется, хочет ли Миша пройтись с ним по звонку в 10:30 по сделанному, чтобы они это дальше дополнили, или Миша хочет, посмотрев версию, дать ему правки. Говорящий отметил(а), что важно принять решение до конца недели.
                """
                
                detailed_summary_prompt = """
                Создай подробную, хорошо структурированную сводку на основе следующей транскрипции на русском языке.
                Твоя сводка должна включать основные разделы и детали.
                
                ОЧЕНЬ ВАЖНО: 
                - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                - НЕ заменяй имена на обобщенные слова типа "говорящий", "собеседник" или "участник"
                - Если кто-то обращается к конкретному человеку, обязательно включи это имя
                
                ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                - Если невозможно определить пол говорящего на 100%, используй мужской род по умолчанию, 
                  но добавляй женские окончания в скобках: например, "отметил(а)", "сказал(а)"
                
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
                """
                
                bullet_points_prompt = """
                Преобразуй следующую транскрипцию в хорошо организованный маркированный список ключевых тезисов на русском языке.
                
                ОЧЕНЬ ВАЖНО: 
                - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                - НЕ заменяй имена на обобщенные слова типа "говорящий", "собеседник" или "участник"
                - Если кто-то обращается к конкретному человеку, обязательно включи это имя
                
                ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                - Если невозможно определить пол говорящего на 100%, используй мужской род по умолчанию, 
                  но добавляй женские окончания в скобках: например, "отметил(а)", "сказал(а)"
                
                Пример корректного форматирования:
                
                📋 ТЕЗИСНЫЙ САММАРИ ВОЙСА:
                
                📌 ОСНОВНАЯ ТЕМА:
                Обсуждение проекта X между Алексеем и Марией, включающее вопросы сроков и распределения задач.
                
                🔑 КЛЮЧЕВОЕ:
                - Алексей спросил Марию о статусе текущих задач
                - Мария сообщила о завершении дизайн-макетов
                - Алексей предложил назначить встречу с Дмитрием для обсуждения интеграции
                - Обсудили необходимость привлечения Сергея для backend-разработки
                
                📎 ДОПОЛНИТЕЛЬНОЕ:
                - Необходимо согласовать бюджет с Ириной из финансового отдела
                
                Убедись, что список охватывает все ключевые моменты оригинального сообщения.
                Используй короткие, четкие формулировки для каждого пункта.
                """
                
                combined_summary_prompt = """
                Создай комбинированную сводку на основе следующей транскрипции на русском языке.
                
                ОЧЕНЬ ВАЖНО: 
                - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                - НЕ заменяй имена на обобщенные слова типа "говорящий", "собеседник" или "участник"
                - Если кто-то обращается к конкретному человеку, обязательно включи это имя
                
                ВАЖНО: Telegram имеет ограниченную поддержку Markdown. Соблюдай следующие правила:
                - Используй ТОЛЬКО эмодзи в начале каждого раздела (не заключай их в звездочки)
                - Не используй знаки # для заголовков, они не поддерживаются в Telegram
                - Если невозможно определить пол говорящего на 100%, используй мужской род по умолчанию, 
                  но добавляй женские окончания в скобках: например, "отметил(а)", "сказал(а)"
                - Полностью match tone-of-voice говорящего

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
                """
                
                cleaned_transcript_prompt = """
                Очисти следующую транскрипцию на русском языке:
                - Удали слова-паразиты (например, 'эм', 'ээ', 'ну', 'вот', 'как бы', 'типа')
                - Исправь грамматику, пунктуацию и синтаксис для улучшения читаемости
                - Сохрани все значимое содержание
                - ОБЯЗАТЕЛЬНО сохрани все имена людей и компаний точно как в оригинале
                - Разбей на логические абзацы, полностью match tone-of-voice говорящего
                - Для неизвестных или неразборчивых слов, указывай вероятный вариант, а затем альтернативу в квадратных скобках [как здесь]
                - Исправь очевидные оговорки
                - Если невозможно определить пол говорящего на 100%, используй мужской род по умолчанию, 
                  но добавляй женские окончания в скобках: например, "отметил(а)", "сказал(а)"
                
                Результат должен быть ясным, читабельным текстом, который сохраняет оригинальный смысл и информацию.
                """
                
                pasha_technik_prompt = """
                Преобразуй транскрипцию в нецензурный текст в фирменном стиле Паши Техника (Павла Ивлева).
                
                ОЧЕНЬ ВАЖНО: 
                - Сохраняй ВСЕ имена людей и названия компаний, упомянутые в оригинальной транскрипции
                - Включай именно те имена, которые используются в тексте (Миша, Саша, Катя и т.д.)
                - НЕ заменяй имена на обобщенные слова, используй те имена, которые указаны в транскрипции
                
                ВАЖНО:
                - Используй нецензурную, грубую, пошлую и жаргонную лексику (мат, жаргон, междометия, слова-паразиты)
                - Добавляй фирменные словечки: "ксанакс", "чё", "на спидах", упоминания транков и шишек
                - Используй очень короткие предложения и три точки кое-где, говори решительно 
                - Включи если к месту упоминания секса, вечеринок, ментов 
                
                ВАЖНО для Telegram:
                - Используй ТОЛЬКО эмодзи в начале каждого раздела
                - Не используй знаки # для заголовков
                
                Пример:
                
                💊 ПРОСЛУШАЛ ХУЙНЮ, ЭТО МОЯ РАСШИФРОВКА ЁПТА:
                Ну... слышь, бля... короче. Чё это за хуйня... Миша, ты, нахуй, что-то монтировал ночью или как? Статус, типа проект, да? Сроки... блядь... горят! Пиздец... У меня, нахуй... от соли башка едет... а ты мне про дедлайны... ёпта! Хули париться-то... а?

                🔥 СУТЬ БЛЯДСКОГО ВОПРОСА:
                - короче... Андрей спрашивает Мишу... ну... монтировал ли он ночью
                - Андрей хочет пройтись по звонку в 10:30... чтобы, бля, потом дополнить
                - Миша должен решить... нахуй... дать правки или чё
                
                ⚡ ЗАПОМНИ:
                Запомни... бля... пацан... он хуй кладёт на это всё... на дедлайны твои... Похую ваще! Въебал соли... там... ну... план придумал... хуяк-хуяк... и сделал! Всё... блядь... чётко! Мусора... ну... они сосут, короче. А Техник... Паша Техник... он, сука... не проёбывает. Никогда... ёпта.
                """

                # --- API Calls --- 
                # We always need the transcript first
                logger.debug("Requesting transcription from Gemini...")
                transcript_response = await model.generate_content_async([transcription_prompt, audio_file])
                raw_transcript = transcript_response.text
                logger.info("Transcription received.")
                logger.debug(f"Raw Transcript: {raw_transcript[:100]}...")

                summary_text = None
                transcript_text = None

                if mode == "transcript":
                    logger.debug("Requesting cleaned transcript...")
                    cleaned_response = await model.generate_content_async([cleaned_transcript_prompt, raw_transcript])
                    transcript_text = cleaned_response.text
                    logger.info("Cleaned transcript generated.")
                else:
                    # For other modes, generate the summary based on the raw transcript
                    prompt_map = {
                        "brief": brief_summary_prompt,
                        "detailed": detailed_summary_prompt,
                        "bullet": bullet_points_prompt,
                        "combined": combined_summary_prompt,
                        "pasha": pasha_technik_prompt,
                    }
                    summary_prompt = prompt_map.get(mode)
                    if not summary_prompt:
                         logger.error(f"Internal error: No prompt found for mode {mode}")
                         return None, None

                    logger.debug(f"Requesting {mode} summary...")
                    summary_response = await model.generate_content_async([summary_prompt, raw_transcript])
                    summary_text = summary_response.text
                    transcript_text = raw_transcript
                    logger.info(f"{mode.capitalize()} summary generated.")

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

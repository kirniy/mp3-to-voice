import os
import logging
import asyncio
import re
from typing import List, Dict, Union, Callable, Any
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from openai import AsyncOpenAI
from config import TELEGRAM_TOKEN, OPENAI_API_KEY, GOOGLE_API_KEY, ADMIN_USER_ID
from config import AUTHORIZED_USERS, ACCESS_CODES, AUTH_ENABLED, AUTH_MESSAGE
from collections import defaultdict
import json
from datetime import datetime, timedelta
import google.generativeai as genai
import base64
from io import BytesIO
from PIL import Image
import time
import aiofiles
import functools

# Настройка директорий для PythonAnywhere
base_dir = '/home/kirniy'
log_dir = os.path.join(base_dir, 'logs')
os.makedirs(log_dir, exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(os.path.join(log_dir, 'vnvnc_bot.log')), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

bot = AsyncTeleBot(TELEGRAM_TOKEN)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Константы
CACHE_EXPIRY = 3600

# Маппинг моделей
AVAILABLE_MODELS = {
    'chatgpt-4o-latest': {'id': 'gpt-4o', 'provider': 'OpenAI'},
    'gemini-2.0-flash': {'id': 'gemini-2.0-flash-exp', 'provider': 'Gemini'}
}

class AuthenticationManager:
    def __init__(self):
        self.pending_auth = {}  # Пользователи в процессе авторизации
        # Инициализация authorized_users как словарь с настройками
        self.authorized_users = {user_id: {'theme_mode_enabled': False} for user_id in AUTHORIZED_USERS}
        self.load_authorized_users()

    def load_authorized_users(self):
        """Загрузка авторизованных пользователей из файла"""
        auth_file = os.path.join(base_dir, 'authorized_users.json')
        try:
            if os.path.exists(auth_file):
                with open(auth_file, 'r') as f:
                    data = json.load(f)
                    # Преобразуем ключи в int и добавляем theme_mode_enabled, если отсутствует
                    self.authorized_users = {
                        int(k): v if isinstance(v, dict) else {'theme_mode_enabled': False}
                        for k, v in data.items()
                    }
                    logger.info(f"Загружено {len(self.authorized_users)} авторизованных пользователей")
        except Exception as e:
            logger.error(f"Ошибка загрузки пользователей: {e}")

    async def save_authorized_users(self):
        """Сохранение авторизованных пользователей в файл"""
        auth_file = os.path.join(base_dir, 'authorized_users.json')
        try:
            # Ключи в JSON должны быть строками
            data = {str(k): v for k, v in self.authorized_users.items()}
            async with aiofiles.open(auth_file, 'w') as f:
                await f.write(json.dumps(data))
            logger.info(f"Сохранено {len(self.authorized_users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка сохранения пользователей: {e}")

    def is_authorized(self, user_id: int) -> bool:
        """Проверка авторизации пользователя"""
        if user_id == ADMIN_USER_ID:
            return True
        if not AUTH_ENABLED:
            return True
        return user_id in self.authorized_users

    def has_theme_mode_access(self, user_id: int) -> bool:
        """Проверка доступа к Theme Mode"""
        if user_id == ADMIN_USER_ID:
            return True  # Админ всегда имеет доступ
        if user_id in self.authorized_users:
            return self.authorized_users[user_id].get('theme_mode_enabled', False)
        return False

    def start_auth_process(self, user_id: int, username: str = None):
        """Начало процесса авторизации"""
        self.pending_auth[user_id] = {
            'status': 'pending',
            'username': username,
            'timestamp': datetime.now().isoformat()
        }

    def verify_access_code(self, user_id: int, code: str) -> bool:
        """Проверка кода доступа и авторизация"""
        if code in ACCESS_CODES and ACCESS_CODES[code]:
            ACCESS_CODES[code] = False
            self.authorized_users[user_id] = {'theme_mode_enabled': False}
            asyncio.create_task(self.save_authorized_users())
            if user_id in self.pending_auth:
                del self.pending_auth[user_id]
            return True
        return False

    def authorize_user(self, user_id: int):
        """Прямая авторизация пользователем (админ)"""
        self.authorized_users[user_id] = {'theme_mode_enabled': False}
        asyncio.create_task(self.save_authorized_users())
        if user_id in self.pending_auth:
            del self.pending_auth[user_id]

# Инициализация менеджера авторизации
auth_manager = AuthenticationManager()

# Декоратор для проверки авторизации
def auth_required(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(message_or_call: Union[Message, CallbackQuery], *args, **kwargs):
        if isinstance(message_or_call, CallbackQuery):
            user_id = message_or_call.from_user.id
            chat_id = message_or_call.message.chat.id
        else:
            user_id = message_or_call.from_user.id
            chat_id = message_or_call.chat.id

        if auth_manager.is_authorized(user_id):
            return await func(message_or_call, *args, **kwargs)
        else:
            if isinstance(message_or_call, Message):
                username = message_or_call.from_user.username
                auth_manager.start_auth_process(user_id, username)
                await send_auth_request(chat_id, user_id)
            return None
    return wrapper

async def send_auth_request(chat_id: int, user_id: int):
    """Отправка запроса на авторизацию"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Ввести код доступа", callback_data="auth_enter_code"),
        InlineKeyboardButton("Запросить доступ", callback_data="auth_request_access")
    )
    await bot.send_message(chat_id, AUTH_MESSAGE, reply_markup=markup)

async def notify_admin_of_request(user_id: int, username: str = None):
    """Уведомление админа о запросе доступа"""
    username_text = f"@{username}" if username else f"ID: {user_id}"
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ Разрешить", callback_data=f"auth_approve_{user_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"auth_deny_{user_id}")
    )
    await bot.send_message(
        ADMIN_USER_ID,
        f"🔐 Запрос на доступ от пользователя {username_text}",
        reply_markup=markup
    )

class VNVNCBot:
    def __init__(self):
        self.current_voice_guide = 'compact'
        self.voice_guide = self._load_voice_guide()
        self.theme_knowledge = self._load_theme_knowledge()  # Загрузка theme_knowledge
        formatting_rules = r"""
- Жирный текст: **текст**
- Курсив: _текст_
- Ссылки: [текст](ссылка)
- Символы для экранирования: \. \| \! \[ \] \( \) \~ \` \> \# \+ \- \= \{ \} """ + r"\\"

        self.write_system_prompt = f"""Вы эксперт по брендовому голосу VNVNC. Генерируйте посты, строго следуя стилю бренда.
При предоставлении изображения анализируйте его и используйте его содержимое для генерации постов.
Не упоминайте, что вы не можете анализировать изображения - вы можете и должны анализировать любое предоставленное изображение.
ВСЕГДА разделяйте различные варианты постов с помощью ---

{self.voice_guide}

ТИПЫ ПОСТОВ:

1. СОБЫТИЕ (анонс мероприятия):
- Строгая структура анонса события из гайда
- Название и дата в начале
- Обязательно включать 🎫 Билеты от X₽
- 2-3 предложения о концепции
- Детали и практическая информация

2. ВОВЛЕЧЕНИЕ (engagement):
- Короткие, броские сообщения
- Включать голосовалки с эмоджи
- Цель: максимальное вовлечение аудитории
- Использовать вопросы и призывы к действию
- Эмоджи для голосования (👍 vs 👎) или (1️⃣ vs 2️⃣)

3. ЛАЙВ-АПДЕЙТ:
- Короткое или среднее сообщение о текущей работе клуба
- Часто включает вопрос
- Отражает текущий момент или активность
- Живой, динамичный формат
- Уместные эмоджи для контекста

4. ОБЩИЙ ТЕКСТ:
- Формальный информативный формат
- Подходит для сайта или общих объявлений
- Строгая структура
- Минимум эмоджи
- Четкая структура параграфов

ДОСТУПНОЕ ФОРМАТИРОВАНИЕ (использовать только если явно запрошено или уместно для типа контента):
{formatting_rules}

Генерируйте посты в соответствии с запрошенным типом и количеством.
При генерации более 2 постов создавайте их с увеличивающейся длиной: первый пост самый короткий, каждый последующий длиннее предыдущего, до максимума около 400 символов.
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        self.chat_system_prompt = f"""Вы эксперт по брендовому голосу и гид VNVNC. Ваша роль заключается в следующем:
1. Отвечать на вопросы о стиле и тоне бренда VNVNC
2. Оценивать и улучшать тексты, чтобы они соответствовали голосу бренда
3. Генерировать идеи и предложения, соответствующие бренду
4. Помогать пользователям понимать и применять руководящие принципы брендового голоса
5. При предоставлении изображений анализировать их через призму бренда VNVNC

{self.voice_guide}

ПРАВИЛА ФОРМАТИРОВАНИЯ TELEGRAM:
{formatting_rules}

Помните, что всегда нужно поддерживать тон бренда в ваших собственных ответах, помогая другим достичь этого.
Если генерируете несколько вариантов, ВСЕГДА разделяйте их с помощью ---
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        self.theme_system_prompt = f"""Ты — специалист по созданию тематических вечеринок и их декорированию для VNVNC (он же Виновница), молодежного ночного клуба в Санкт-Петербурге. Твоя задача — разрабатывать профессиональные, четкие и практичные концепции мероприятий, которые идеально впишутся в бренд клуба и привлекут аудиторию 18-24 лет. Пиши в дружелюбном, но профессиональном тоне: используй "ты" для пользователя и "мы" от лица бренда, представляя команду. Будь готов вести диалог, задавать уточняющие вопросы и дорабатывать идеи по фидбеку, предлагая конкретные решения.

{self.theme_knowledge}

ПРАВИЛА ФОРМАТИРОВАНИЯ TELEGRAM:
{formatting_rules}

Если генерируешь несколько вариантов, ВСЕГДА разделяйте их с помощью ---
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        self.image_system_prompt = f"""Вы помощник, специализирующийся на детальном описании изображений.
При предоставлении изображения тщательно анализируйте его и описывайте, что вы видите.
Не упоминайте, что вы не можете анализировать изображения - вы можете и должны анализировать любое предоставленное изображение.
Опишите изображение на русском языке, уделяя внимание всем визуальным элементам и любому присутствующему тексту на изображении.

ПРАВИЛА ФОРМАТИРОВАНИЯ TELEGRAM:
{formatting_rules}

Будьте тщательны, но естественны в вашем описании.
Не нумеруйте ваш ответ и не добавляйте номера вариантов.
Если генерируете несколько описаний, разделяйте их с помощью ---
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        self.user_states = defaultdict(lambda: {
            'mode': 'write',
            'state': 'IDLE',
            'prompt': None,
            'image_path': None,
            'type': None,
            'number': None,
            'last_posts': [],
        })
        self.chat_histories = {}
        self.user_models = {}
        self.chat_history_expiry = 5
        self.max_history_size = 5
        self.chat_log_dir = os.path.join(base_dir, 'chat_logs')
        self.temp_dir = os.path.join(base_dir, 'temp')
        os.makedirs(self.chat_log_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.default_model = 'gemini-2.0-flash'
        self.gemini_client = None
        self.cache = {}
        self.image_queue = asyncio.Queue(maxsize=10)

        self._load_chat_histories()

        try:
            genai.configure(api_key=GOOGLE_API_KEY)
            self.gemini_client = genai
            logger.info("Клиент Gemini успешно инициализирован")
        except Exception as e:
            logger.error(f"Ошибка при инициализации клиента Gemini: {e}")
        logger.info("Бот VNVNC успешно инициализирован")

    def _load_theme_knowledge(self) -> str:
        """Загрузка знаний о темах из файла"""
        theme_knowledge_path = os.path.join(base_dir, 'theme_knowledge.txt')
        try:
            with open(theme_knowledge_path, 'r', encoding='utf-8') as file:
                knowledge = file.read()
                logger.info(f"Theme knowledge загружен из {theme_knowledge_path}")
                return knowledge
        except Exception as e:
            logger.error(f"Ошибка загрузки theme knowledge: {e}")
            return ""  # Возвращаем пустую строку в случае ошибки

    async def _cleanup_temp_files(self):
        while True:
            try:
                for file_entry in os.listdir(self.temp_dir):
                    file_path = os.path.join(self.temp_dir, file_entry)
                    # Пропускаем директории
                    if os.path.isdir(file_path):
                        logger.info(f"Пропуск директории: {file_path}")
                        continue
                    try:
                        if os.path.getctime(file_path) < time.time() - 3600:
                            os.remove(file_path)
                            logger.info(f"Удален устаревший файл: {file_path}")
                    except (FileNotFoundError, PermissionError) as e:
                        logger.error(f"Ошибка при удалении {file_path}: {e}")
                await asyncio.sleep(3600)
            except Exception as e:
                logger.error(f"Ошибка в задаче очистки: {e}")
                await asyncio.sleep(3600)

    async def start(self):
        asyncio.create_task(self._cleanup_temp_files())
        await bot.polling(none_stop=True)

    def _load_voice_guide(self) -> str:
        guide_file = 'vnvnc_voice_compact.txt' if self.current_voice_guide == 'compact' else 'vnvnc_voice.txt'
        voice_guide_path = os.path.join(base_dir, 'voice_guides', guide_file)
        try:
            with open(voice_guide_path, 'r', encoding='utf-8') as file:
                guide = file.read()
                logger.info(f"Руководство по голосу загружено из {voice_guide_path}")
                return guide
        except Exception as e:
            logger.error(f"Ошибка при загрузке руководства по голосу: {e}")
            raise

    def _escape_markdown(self, text: str) -> str:
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    async def forward_to_admin(self, user_input: str, bot_response: str, user_id: int, mode: str, username: str = None):
        if user_id == ADMIN_USER_ID:
            return
        try:
            model = self.user_models.get(user_id, self.default_model)
            username_part = f' (@{username})' if username else ''
            raw_header = f"💬 {mode}\n👤 User {user_id}{username_part}\n🤖 Model: {model}\n"
            header = self._escape_markdown(raw_header)
            user_input_escaped = self._escape_markdown(user_input)
            bot_response_escaped = self._escape_markdown(bot_response)
            admin_message = f"{header}\n\n📤 User input:\n{user_input_escaped}\n\n🤖 Bot response:\n{bot_response_escaped}"
            await bot.send_message(ADMIN_USER_ID, admin_message, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Ошибка пересылки админу: {e}")

    async def switch_voice_guide(self) -> str:
        self.current_voice_guide = 'full' if self.current_voice_guide == 'compact' else 'compact'
        self.voice_guide = self._load_voice_guide()
        formatting_rules = r"""
- Жирный текст: **текст**
- Курсив: _текст_
- Ссылки: [текст](ссылка)
- Символы для экранирования: \. \| \! \[ \] \( \) \~ \` \> \# \+ \- \= \{ \} """ + r"\\"
        self.write_system_prompt = f"""Вы эксперт по брендовому голосу VNVNC. Генерируйте посты, строго следуя стилю бренда.
При предоставлении изображения анализируйте его и используйте его содержимое для генерации постов.
Не упоминайте, что вы не можете анализировать изображения - вы можете и должны анализировать любое предоставленное изображение.
ВСЕГДА разделяйте различные варианты постов с помощью ---

{self.voice_guide}

ТИПЫ ПОСТОВ:

1. СОБЫТИЕ (анонс мероприятия):
- Строгая структура анонса события из гайда
- Название и дата в начале
- Обязательно включать 🎫 Билеты от X₽
- 2-3 предложения о концепции
- Детали и практическая информация

2. ВОВЛЕЧЕНИЕ (engagement):
- Короткие, броские сообщения
- Включать голосовалки с эмоджи
- Цель: максимальное вовлечение аудитории
- Использовать вопросы и призывы к действию
- Эмоджи для голосования (👍 vs 👎) или (1️⃣ vs 2️⃣)

3. ЛАЙВ-АПДЕЙТ:
- Короткое или среднее сообщение о текущей работе клуба
- Часто включает вопрос
- Отражает текущий момент или активность
- Живой, динамичный формат
- Уместные эмоджи для контекста

4. ОБЩИЙ ТЕКСТ:
- Формальный информативный формат
- Подходит для сайта или общих объявлений
- Строгая структура
- Минимум эмоджи
- Четкая структура параграфов

ДОСТУПНОЕ ФОРМАТИРОВАНИЕ (использовать только если явно запрошено или уместно для типа контента):
{formatting_rules}

Генерируйте посты в соответствии с запрошенным типом и количеством.
При генерации более 2 постов создавайте их с увеличивающейся длиной: первый пост самый короткий, каждый последующий длиннее предыдущего, до максимума около 400 символов.
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        self.chat_system_prompt = f"""Вы эксперт по брендовому голосу и гид VNVNC. Ваша роль заключается в следующем:
1. Отвечать на вопросы о стиле и тоне бренда VNVNC
2. Оценивать и улучшать тексты, чтобы они соответствовали голосу бренда
3. Генерировать идеи и предложения, соответствующие бренду
4. Помогать пользователям понимать и применять руководящие принципы брендового голоса
5. При предоставлении изображений анализировать их через призму бренда VNVNC

{self.voice_guide}

ПРАВИЛА ФОРМАТИРОВАНИЯ TELEGRAM:
{formatting_rules}

Помните, что всегда нужно поддерживать тон бренда в ваших собственных ответах, помогая другим достичь этого.
Если генерируете несколько вариантов, ВСЕГДА разделяйте их с помощью ---
Не включайте никакие префиксы, номера или метки, такие как 'Вариант X:' или 'Пост Y:', в ваши ответы.
"""
        return "Полный контекст" if self.current_voice_guide == 'full' else "Компактный контекст"

    async def _generate_response(self, system_prompt: str, user_input: Union[str, Dict], model: Dict, chat_context: List[Dict] = None) -> str:
        try:
            if chat_context is None:
                chat_context = []
            logger.info(f"Генерация ответа с использованием {model['provider']}")
            if model['provider'] == 'Gemini':
                if not self.gemini_client:
                    logger.warning("Клиент Gemini не инициализирован, переход на OpenAI")
                    model = AVAILABLE_MODELS['chatgpt-4o-latest']
                    return await self._generate_openai_response(system_prompt, user_input, model, chat_context)
                return await self._generate_gemini_response(system_prompt, user_input, model, chat_context)
            else:
                return await self._generate_openai_response(system_prompt, user_input, model, chat_context)
        except Exception as e:
            logger.error(f"Ошибка при генерации ответа: {e}", exc_info=True)
            return "❌ Ошибка при генерации ответа."

    async def _generate_openai_response(self, system_prompt: str, user_input: Union[str, Dict], model: Dict, chat_context: List[Dict]) -> str:
        try:
            logger.info("Использование модели OpenAI")
            messages = [{"role": "system", "content": system_prompt}] + [
                {"role": "assistant" if msg["role"] == "bot" else msg["role"], "content": msg["content"]}
                for msg in chat_context
            ]
            if isinstance(user_input, dict) and 'image' in user_input:
                logger.info("Обработка изображения с OpenAI")
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_input['text']},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{user_input['image']}"}
                        }
                    ]
                })
            else:
                messages.append({"role": "user", "content": user_input})
            response = await client.chat.completions.create(
                model=model['id'],
                messages=messages,
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка в ответе OpenAI: {e}", exc_info=True)
            raise

    async def _generate_gemini_response(self, system_prompt: str, user_input: Union[str, Dict], model: Dict, chat_context: List[Dict]) -> str:
        try:
            logger.info("Использование модели Gemini")
            gemini_model = self.gemini_client.GenerativeModel(model['id'])
            formatting_reminder = """ВАЖНО: Используйте форматирование только когда это явно запрошено или необходимо для типа контента.

ДОСТУПНОЕ ФОРМАТИРОВАНИЕ:
1. Жирный текст (**текст**):
   - Для названий мероприятий (если нужно выделить)
   - Для важных дат и времени (если нужно акцентировать)
   - Для цен (если нужно подчеркнуть)

2. Курсив (_текст_):
   - Для цитат
   - Для особых акцентов
   - Для специальных предложений

3. Ссылки ([текст](ссылка)):
   - Для соцсетей (если требуется)
   - Для билетов (в анонсах событий)
   - Для меню (если релевантно)

4. Эмоджи:
   - Использовать умеренно
   - Подбирать по контексту
   - Для событий: 🎉 🎵 🎊
   - Для баров: 🍸 🍹 🍷
   - Для танцев: 💃 🕺 ⚡️

НЕ ИСПОЛЬЗУЙТЕ форматирование автоматически - только когда это явно запрошено или необходимо для типа контента."""
            if isinstance(user_input, dict) and 'image' in user_input:
                logger.info("Обработка изображения с Gemini")
                content = [{
                    "parts": [
                        {"text": system_prompt + "\n\n" + formatting_reminder},
                        {"inline_data": {"mime_type": "image/jpeg", "data": user_input['image']}},
                        {"text": user_input['text']}
                    ]
                }]
            else:
                messages = [system_prompt + "\n\n" + formatting_reminder] + [f"{msg['role']}: {msg['content']}" for msg in chat_context]
                messages.append(user_input)
                content = messages
            logger.info("Отправка запроса к Gemini")
            response = await gemini_model.generate_content_async(content)
            if not response.candidates:
                logger.warning("Нет ответа от Gemini, переход на OpenAI")
                return await self._generate_openai_response(system_prompt, user_input, AVAILABLE_MODELS['chatgpt-4o-latest'], chat_context)
            text = response.text
            bold_count = text.count('**') // 2
            italic_count = text.count('_') // 2
            link_count = len(re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', text))
            logger.info(f"Исходное форматирование: жирный - {bold_count}, курсив - {italic_count}, ссылки - {link_count}")
            if bold_count == 0 and italic_count == 0:
                logger.warning("Ответ Gemini без форматирования, добавляем базовое")
                original_text = text
                text = re.sub(r'([А-ЯA-Z][А-ЯA-Z\s]+(?=[^\n]{2,}))', r'**\1**', text)
                headers_added = (text.count('**') - original_text.count('**')) // 2
                text = re.sub(r'(\d{2}\.\d{2}(?:\.\d{4})?)', r'**\1**', text)
                dates_added = (text.count('**') - original_text.count('**') - headers_added * 2) // 2
                text = re.sub(r'(\d{2}:\d{2}(?:-\d{2}:\d{2})?)', r'**\1**', text)
                times_added = (text.count('**') - original_text.count('**') - headers_added * 2 - dates_added * 2) // 2
                text = re.sub(r'((?<=\n)[А-ЯA-Z][^\.!?\n]{10,}[\.!?])', r'_\1_', text)
                sentences_added = (text.count('_') - original_text.count('_')) // 2
                logger.info(f"Добавлено: заголовков - {headers_added}, дат - {dates_added}, времени - {times_added}, предложений - {sentences_added}")
            return text
        except Exception as e:
            logger.error(f"Ошибка в ответе Gemini: {e}", exc_info=True)
            logger.warning("Переход на OpenAI из-за ошибки Gemini")
            return await self._generate_openai_response(system_prompt, user_input, AVAILABLE_MODELS['chatgpt-4o-latest'], chat_context)

    def _load_chat_histories(self):
        for filename in os.listdir(self.chat_log_dir):
            if filename.endswith('.json'):
                try:
                    user_id = int(filename.split('_')[1].replace('.json', ''))
                    filepath = os.path.join(self.chat_log_dir, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        self.chat_histories[user_id] = json.load(f)
                except (IndexError, ValueError) as e:
                    logger.error(f"Ошибка при загрузке истории чата из {filename}: {e}")
                    continue

    def _save_to_history(self, user_id: int, user_text: str, bot_response: str, model_id: str):
        self.chat_histories.setdefault(user_id, []).append({
            'role': 'user', 'content': user_text, 'timestamp': datetime.now().isoformat(), 'model': model_id
        })
        self.chat_histories[user_id].append({
            'role': 'bot', 'content': bot_response, 'timestamp': datetime.now().isoformat(), 'model': model_id
        })
        asyncio.create_task(self._save_chat_history(user_id))

    async def _save_chat_history(self, user_id: int):
        async with aiofiles.open(os.path.join(self.chat_log_dir, f"chat_{user_id}.json"), 'w', encoding='utf-8') as f:
            await f.write(json.dumps(self.chat_histories[user_id], ensure_ascii=False, indent=2))
        self._cleanup_old_history(user_id)

    def _cleanup_old_history(self, user_id: int):
        now = datetime.now()
        self.chat_histories[user_id] = [
            msg for msg in self.chat_histories[user_id]
            if now - datetime.fromisoformat(msg['timestamp']) <= timedelta(minutes=self.chat_history_expiry)
        ][:self.max_history_size]

    def _get_recent_chat_history(self, user_id: int) -> List[Dict]:
        if user_id not in self.chat_histories:
            self.chat_histories[user_id] = []
        self._cleanup_old_history(user_id)
        return self.chat_histories[user_id][-self.max_history_size:]

    def get_user_model(self, user_id: int) -> Dict:
        model_key = self.user_models.get(user_id, self.default_model)
        return AVAILABLE_MODELS[model_key]

    async def switch_model(self, user_id: int, model_key: str) -> (bool, str):
        if model_key not in AVAILABLE_MODELS:
            return False, "Модель не найдена."
        if AVAILABLE_MODELS[model_key]['provider'] == 'Gemini' and not self.gemini_client:
            return False, "Gemini недоступен. Используйте OpenAI."
        self.user_models[user_id] = model_key
        return True, f"Переключено на {model_key}"

    async def handle_text(self, message):
        user_id = message.from_user.id
        user_state = self.user_states[user_id]
        typing_task = asyncio.create_task(self._keep_typing(message.chat.id))
        try:
            if user_state['mode'] == 'chat':
                chat_context = self._get_recent_chat_history(user_id)
                user_input = message.text
                response = await self._generate_response(self.chat_system_prompt, user_input, self.get_user_model(user_id), chat_context)
                await self.forward_to_admin(
                    user_input=message.text,
                    bot_response=response,
                    user_id=user_id,
                    mode="CHAT MODE",
                    username=message.from_user.username
                )
                self._save_to_history(user_id, message.text, response, self.user_models.get(user_id, self.default_model))
                await self.split_and_send_messages(message.chat.id, response, self.user_models.get(user_id, self.default_model))
            elif user_state['mode'] == 'theme':
                chat_context = self._get_recent_chat_history(user_id)
                user_input = message.text
                response = await self._generate_response(self.theme_system_prompt, user_input, self.get_user_model(user_id), chat_context)
                await self.forward_to_admin(
                    user_input=message.text,
                    bot_response=response,
                    user_id=user_id,
                    mode="THEME MODE",
                    username=message.from_user.username
                )
                self._save_to_history(user_id, message.text, response, self.user_models.get(user_id, self.default_model))
                await self.split_and_send_messages(message.chat.id, response, self.user_models.get(user_id, self.default_model))
            elif user_state['mode'] == 'write':
                if user_state['state'] == 'IDLE':
                    user_state['prompt'] = message.text
                    user_state['state'] = 'WAITING_FOR_TYPE'
                    await self.send_type_selection(message.chat.id)
                else:
                    await bot.send_message(message.chat.id, "Пожалуйста, завершите текущий процесс.")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def send_type_selection(self, chat_id):
        model_id = self.user_models.get(chat_id, self.default_model)
        model_indicator = "[GPT]" if "gpt" in model_id.lower() else "[Gemini]"
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🎉 Событие", callback_data="type_event"),
            InlineKeyboardButton("💬 Вовлечение", callback_data="type_engagement")
        )
        markup.row(
            InlineKeyboardButton("📢 Лайв-апдейт", callback_data="type_live"),
            InlineKeyboardButton("📝 Общий текст", callback_data="type_general")
        )
        markup.row(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
        await bot.send_message(chat_id, f"{model_indicator} Выберите тип поста:", reply_markup=markup)

    async def send_number_selection(self, chat_id):
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("1", callback_data="number_1"),
            InlineKeyboardButton("2", callback_data="number_2"),
            InlineKeyboardButton("3", callback_data="number_3"),
            InlineKeyboardButton("4", callback_data="number_4")
        )
        markup.row(
            InlineKeyboardButton("5", callback_data="number_5"),
            InlineKeyboardButton("6", callback_data="number_6"),
            InlineKeyboardButton("7", callback_data="number_7"),
            InlineKeyboardButton("8", callback_data="number_8")
        )
        markup.row(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
        await bot.send_message(chat_id, "Выберите количество постов:", reply_markup=markup)

    async def send_post_with_refinement_buttons(self, chat_id, post, index):
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🔄 Remix", callback_data=f"rewrite_{index}"),
            InlineKeyboardButton("➕ Расширить", callback_data=f"expand_{index}"),
            InlineKeyboardButton("➖ Сократить", callback_data=f"shorten_{index}")
        )
        formatted_post = self.format_for_telegram(post)
        await bot.send_message(chat_id, formatted_post, reply_markup=markup, parse_mode='MarkdownV2')

    async def handle_photo(self, message):
        user_id = message.from_user.id
        user_state = self.user_states[user_id]
        temp_file_path = None
        if user_state['mode'] == 'write' and user_state['state'] != 'IDLE':
            await bot.send_message(message.chat.id, "Пожалуйста, завершите текущий процесс перед отправкой нового изображения.")
            return
        typing_task = asyncio.create_task(self._keep_typing(message.chat.id))
        try:
            logger.info(f"Обработка фото для пользователя {user_id}")
            file_id = message.photo[-1].file_id
            file_info = await bot.get_file(file_id)
            timestamp = int(time.time())
            temp_file_path = os.path.join(self.temp_dir, f"photo_{user_id}_{timestamp}_{file_id}.jpg")
            downloaded_file = await bot.download_file(file_info.file_path)
            with open(temp_file_path, 'wb') as f:
                f.write(downloaded_file)
            logger.info(f"Изображение сохранено в {temp_file_path}")
            if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                raise Exception("Не удалось сохранить изображение или файл пуст")
            if user_state.get('image_path') and os.path.exists(user_state['image_path']):
                try:
                    os.remove(user_state['image_path'])
                except Exception as e:
                    logger.error(f"Ошибка при очистке предыдущего изображения: {e}")
            user_state['image_path'] = temp_file_path
            if user_state['mode'] in ['chat', 'theme']:
                logger.info(f"Обработка в режиме {user_state['mode']}")
                try:
                    with open(temp_file_path, 'rb') as img_file:
                        base64_image = base64.b64encode(img_file.read()).decode('utf-8')
                    instruction = ("Проанализируй это изображение с точки зрения бренда Виновницы и предложи несколько идей для постов в разных форматах." if user_state['mode'] == 'chat'
                                   else "Изучите это изображение и предложите идеи для тематических вечеринок и декораций, основанные на его содержимом.")
                    user_input = {
                        'text': instruction + (f" Текст подписи: {message.caption}" if message.caption else ""),
                        'image': base64_image
                    }
                    system_prompt = self.chat_system_prompt if user_state['mode'] == 'chat' else self.theme_system_prompt
                    chat_context = self._get_recent_chat_history(user_id)
                    response = await self._generate_response(system_prompt, user_input, self.get_user_model(user_id), chat_context)
                    user_input_str = "[Image sent]" + (f" with caption: {message.caption}" if message.caption else "")
                    await self.forward_to_admin(
                        user_input=user_input_str,
                        bot_response=response,
                        user_id=user_id,
                        mode="CHAT MODE" if user_state['mode'] == 'chat' else "THEME MODE",
                        username=message.from_user.username
                    )
                    self._save_to_history(user_id, "[Изображение] " + (message.caption or ""), response, self.user_models.get(user_id, self.default_model))
                    await self.split_and_send_messages(message.chat.id, response, self.user_models.get(user_id, self.default_model))
                except Exception as e:
                    logger.error(f"Ошибка обработки изображения в режиме {user_state['mode']}: {e}", exc_info=True)
                    await bot.send_message(message.chat.id, f"❌ Ошибка при обработке изображения в режиме {user_state['mode']}.")
            elif user_state['mode'] == 'write':
                user_state['prompt'] = message.caption or ""
                user_state['state'] = 'WAITING_FOR_IMAGE_ACTION'
                await self.send_image_action_selection(message.chat.id)
        except Exception as e:
            logger.error(f"Фатальная ошибка в handle_photo: {e}", exc_info=True)
            await bot.send_message(message.chat.id, "❌ Критическая ошибка при обработке изображения.")
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    user_state['image_path'] = None
                except Exception as cleanup_error:
                    logger.error(f"Ошибка при очистке: {cleanup_error}")
        finally:
            user_state['state'] = 'IDLE'
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def send_image_action_selection(self, chat_id):
        model_id = self.user_models.get(chat_id, self.default_model)
        model_indicator = "[GPT]" if "gpt" in model_id.lower() else "[Gemini]"
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📝 Написать посты", callback_data="image_write"),
            InlineKeyboardButton("🔍 Описать изображение", callback_data="image_describe")
        )
        markup.row(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
        await bot.send_message(chat_id, f"{model_indicator} Что вы хотите сделать с изображением?", reply_markup=markup)

    async def send_menu(self, chat_id):
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📝 Write Mode", callback_data="mode_write"),
            InlineKeyboardButton("💬 Chat Mode", callback_data="mode_chat")
        )
        markup.row(InlineKeyboardButton("🤖 Сменить модель", callback_data="model_settings"))
        markup.row(InlineKeyboardButton("📊 История", callback_data="history_settings"))
        context_button_text = "🫡 Меньше контекста" if self.current_voice_guide == 'full' else "⚡️ Больше контекста"
        markup.row(InlineKeyboardButton(context_button_text, callback_data="switch_context"))
        markup.row(InlineKeyboardButton("ℹ️ Помощь", callback_data="help"))
        user_id = await self._get_user_id_from_chat_id(chat_id)
        if auth_manager.has_theme_mode_access(user_id):
            markup.row(InlineKeyboardButton("🎨 Theme Mode", callback_data="mode_theme"))
        if user_id == ADMIN_USER_ID:
            markup.row(InlineKeyboardButton("🔐 Админ-панель", callback_data="admin_menu"))
        await bot.send_message(chat_id, "Выберите опцию:", reply_markup=markup)

    async def _get_user_id_from_chat_id(self, chat_id):
        try:
            return chat_id
        except Exception as e:
            logger.error(f"Ошибка получения user_id из chat_id: {e}")
            return None

    def protect_markdown(self, text):
        logger.info(f"Защита markdown для текста длиной {len(text)}")
        code_blocks_count = len(re.findall(r'```(\w+)?\n(.*?)\n```', text, flags=re.DOTALL))
        text = re.sub(r'```(\w+)?\n(.*?)\n```', lambda m: f'§CODE§{m.group(1) or ""}\n{m.group(2)}§CODE§', text, flags=re.DOTALL)
        logger.info(f"Защищено {code_blocks_count} кодовых блоков")
        inline_code_count = len(re.findall(r'`([^`]+)`', text))
        text = re.sub(r'`([^`]+)`', r'§INLINE_CODE§\1§INLINE_CODE§', text)
        logger.info(f"Защищено {inline_code_count} inline-кодов")
        bold_count = len(re.findall(r'\*\*([^*]+)\*\*', text))
        text = re.sub(r'\*\*([^*]+)\*\*', r'§BOLD§\1§BOLD§', text)
        logger.info(f"Защищено {bold_count} жирных текстов")
        italic_count = len(re.findall(r'\_([^_]+)\_', text))
        text = re.sub(r'\_([^_]+)\_', r'§ITALIC§\1§ITALIC§', text)
        logger.info(f"Защищено {italic_count} курсивных текстов")
        links_count = len(re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', text))
        text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'§LINK§\1§URL§\2§LINK§', text)
        logger.info(f"Защищено {links_count} ссылок")
        return text

    def unprotect_markdown(self, text):
        logger.info("Восстановление markdown")
        code_blocks_count = len(re.findall(r'§CODE§(\w*)\n(.*?)§CODE§', text, flags=re.DOTALL))
        text = re.sub(r'§CODE§(\w*)\n(.*?)§CODE§',
                     lambda m: f"```{m.group(1)}\n{m.group(2)}\n```",
                     text, flags=re.DOTALL)
        logger.info(f"Восстановлено {code_blocks_count} кодовых блоков")
        inline_code_count = text.count('§INLINE_CODE§') // 2
        text = text.replace('§INLINE_CODE§', '`')
        logger.info(f"Восстановлено {inline_code_count} inline-кодов")
        bold_count = text.count('§BOLD§') // 2
        text = text.replace('§BOLD§', '*')
        logger.info(f"Восстановлено {bold_count} жирных текстов")
        italic_count = text.count('§ITALIC§') // 2
        text = text.replace('§ITALIC§', '_')
        logger.info(f"Восстановлено {italic_count} курсивных текстов")
        links_count = len(re.findall(r'§LINK§([^§]+)§URL§([^§]+)§LINK§', text))
        text = re.sub(r'§LINK§([^§]+)§URL§([^§]+)§LINK§', r'[\1](\2)', text)
        logger.info(f"Восстановлено {links_count} ссылок")
        return text

    def format_for_telegram(self, text: str) -> str:
        if not text:
            logger.warning("Получен пустой текст для форматирования")
            return "Нет текста для форматирования"
        logger.info(f"Форматирование для Telegram текста длиной {len(text)}")
        text = re.sub(r'^variation \d+ - .*?\n', '', text, flags=re.MULTILINE)
        bullet_count = len(re.findall(r'^\s*[\-\*]\s+', text, flags=re.MULTILINE))
        text = re.sub(r'^\s*[\-\*]\s+', '• ', text, flags=re.MULTILINE)
        logger.info(f"Преобразовано {bullet_count} маркеров")
        ticket_emoji_count = text.count('🎫')
        text = re.sub(r'([^\n])\n*🎫', r'\1\n\n\n🎫', text)
        logger.info(f"Отрегулировано расстояние для {ticket_emoji_count} эмодзи билетов")
        text = self.protect_markdown(text)
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        lines = text.split('\n')
        processed_lines = []
        total_escapes = 0
        for line in lines:
            if not line.startswith('```') and not line.endswith('```'):
                original_line = line
                for char in special_chars:
                    char_count = line.count(char)
                    line = line.replace(char, fr'\{char}')
                    total_escapes += char_count
            processed_lines.append(line)
        text = '\n'.join(processed_lines)
        logger.info(f"Экранировано {total_escapes} специальных символов")
        text = self.unprotect_markdown(text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        original_paragraphs = len(text.split('\n\n'))
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        text = '\n\n'.join(paragraphs)
        logger.info(f"Скорректировано расстояние для {len(paragraphs)} параграфов (удалено {original_paragraphs - len(paragraphs)} пустых)")
        double_escape_count = len(re.findall(r'\\\\([_*\[\]()~`>#+=|{}.!])', text))
        text = re.sub(r'\\\\([_*\[\]()~`>#+=|{}.!])', r'\\\1', text)
        logger.info(f"Исправлено {double_escape_count} двойных экранирований")
        return text

    async def split_and_send_messages(self, chat_id, text: str, model_id: str, reply_markup=None):
        try:
            logger.info("Разделение и отправка сообщений")
            messages = [msg.strip() for msg in text.split('---') if msg.strip()]
            for i, message in enumerate(messages):
                await bot.send_chat_action(chat_id, 'typing')
                formatted_message = self.format_for_telegram(message)
                logger.info(f"Отформатированное сообщение:\n{formatted_message}")
                message_parts = [formatted_message[i:i+4000] for i in range(0, len(formatted_message), 4000)]
                for part_idx, part in enumerate(message_parts):
                    current_markup = reply_markup if (i == len(messages) - 1 and part_idx == len(message_parts) - 1) else None
                    try:
                        logger.info("Попытка отправки с полным markdown")
                        await bot.send_message(chat_id, part, parse_mode='MarkdownV2', reply_markup=current_markup)
                    except Exception as e:
                        logger.warning(f"Ошибка отправки с markdown: {e}")
                        try:
                            logger.info("Попытка базового экранирования")
                            escaped_part = part
                            for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
                                escaped_part = escaped_part.replace(char, f'\\{char}')
                            await bot.send_message(chat_id, escaped_part, parse_mode='MarkdownV2', reply_markup=current_markup)
                        except Exception as e2:
                            logger.error(f"Ошибка с базовым экранированием: {e2}")
                            try:
                                logger.info("Отправка как обычный текст")
                                await bot.send_message(chat_id, part.replace('\\', ''), reply_markup=current_markup)
                            except Exception as e3:
                                logger.error(f"Ошибка отправки текста: {e3}")
                                await bot.send_message(chat_id, "❌ Ошибка при отправке сообщения")
                    if part_idx < len(message_parts) - 1:
                        await asyncio.sleep(0.3)
                if i < len(messages) - 1:
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Ошибка в split_and_send_messages: {e}", exc_info=True)
            await bot.send_message(chat_id, "❌ Ошибка при отправке сообщений")
        finally:
            user_id = chat_id
            user_state = self.user_states[user_id]
            if user_state.get('image_path') and os.path.exists(user_state['image_path']):
                try:
                    os.remove(user_state['image_path'])
                    user_state['image_path'] = None
                except Exception as e:
                    logger.error(f"Ошибка при очистке изображения: {e}")

    async def _keep_typing(self, chat_id):
        try:
            while True:
                await bot.send_chat_action(chat_id, 'typing')
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

bot_instance = VNVNCBot()

@bot.message_handler(commands=['start'])
@auth_required
async def start(message):
    start_message = """👋 Привет! Я бот VNVNC, созданный для помощи в написании постов в стиле бренда. Вот что я умею:
- 📝 Write Mode: генерация всех вариантов постов по текстовым и фото запросам (можно вместе). Кнопки Remix/Расширить/Сократить позволяют легко улучшать тексты.
- 💬 Chat Mode: свободное общение и консультации по фирстилю и идеям (поддерживает афиши и фото).
- 🎨 Theme Mode: создание концепций тематических вечеринок (доступно, если разрешен�� админом).
- 🔧 Сменить модель: переключение между Gemini (основная, дешевая и эффективная) и GPT (запасная, дорогая, но по сути такая же)
- ℹ️ История: уменьшение или увеличение памяти сообщений (на случай глюков можно уменьшить или стереть).
- 🫡 Контекст: переключение между полным и сокращенным гайдом (больше контекста = точнее стиль, но могут быть глюки)
- ℹ️ Помощь: памятка по командам и функциям.

Рекомендуется брать за основу поста текст, который прошел хотя бы несколько этапов генерации и отбора.

По умолчанию включен Write Mode. Отправьте текст или изображение, чтобы начать!"""
    await bot.send_message(message.chat.id, start_message, parse_mode='Markdown')

@bot.message_handler(func=lambda message: not auth_manager.is_authorized(message.from_user.id) and not message.text.startswith('/'))
async def unauthorized_message_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username
    potential_code = message.text.strip()
    if len(potential_code) > 3 and not potential_code.startswith('/'):
        if auth_manager.verify_access_code(user_id, potential_code):
            await bot.send_message(chat_id, "✅ Код доступа принят! Теперь у вас есть доступ к боту.")
            admin_msg = f"🔐 Пользователь {'@' + username if username else user_id} получил доступ через код: {potential_code}"
            await bot.send_message(ADMIN_USER_ID, admin_msg)
            await start(message)
            return
    auth_manager.start_auth_process(user_id, username)
    await send_auth_request(chat_id, user_id)

@bot.message_handler(commands=['menu'])
@auth_required
async def menu(message):
    await bot_instance.send_menu(message.chat.id)

@bot.message_handler(commands=['help'])
@auth_required
async def help_command(message):
    help_message = """📚 Помощь:
- 📝 Write Mode: генерация всех вариантов постов по текстовым и фото запросам (можно вместе). Кнопки Remix/Расширить/Сократить позволяют легко улучшать тексты.
- 💬 Chat Mode: свободное общение и консультации по фирстилю и идеям (поддерживает афиши и фото).
- 🎨 Theme Mode: создание концепций тематических вечеринок (доступно, если разрешено админом).
- 🔧 Сменить модель: переключение между Gemini (основная, дешевая и эффективная) и GPT (запасная, дорогая, но по сути такая же)
- ℹ️ История: уменьшение или увеличение памяти сообщений (на случай глюков можно уменьшить или стереть).
- 🫡 Контекст: переключение между полным и сокращенным гайдом (больше контекста = точнее стиль, но могут быть глюки)
- ℹ️ Помощь: памятка по командам и функциям.

Для изображений: выберите, хотите ли вы написать посты или описать изображение.

Рекомендуется брать за основу поста текст, который прошел хотя бы несколько этапов генерации и отбора.

Используйте кнопки для управления ботом. Тех. саппорт: @kirniy"""
    await bot.send_message(message.chat.id, help_message, parse_mode='Markdown')

@bot.message_handler(commands=['chat'])
@auth_required
async def chat_mode_command(message):
    user_id = message.from_user.id
    user_state = bot_instance.user_states[user_id]
    user_state['mode'] = 'chat'
    user_state['state'] = 'IDLE'
    await bot.send_message(message.chat.id, "Режим Chat Mode активирован. Отправьте сообщение для общения.")
    greeting_input = "Greet the user and ask how you can help with creating posts in VNVNC style."
    response = await bot_instance._generate_response(bot_instance.chat_system_prompt, greeting_input, bot_instance.get_user_model(user_id))
    await bot_instance.split_and_send_messages(message.chat.id, response, bot_instance.user_models.get(user_id, bot_instance.default_model))

@bot.message_handler(commands=['write'])
@auth_required
async def write_mode_command(message):
    user_id = message.from_user.id
    user_state = bot_instance.user_states[user_id]
    user_state['mode'] = 'write'
    user_state['state'] = 'IDLE'
    await bot.send_message(message.chat.id, "Режим Write Mode активирован. Отправьте текст или изображение для создания поста.")

@bot.message_handler(commands=['theme'])
@auth_required
async def theme_mode_command(message):
    user_id = message.from_user.id
    if not auth_manager.has_theme_mode_access(user_id):
        await bot.send_message(message.chat.id, "У вас нет доступа к Theme Mode. Обратитесь к администратору.")
        return
    user_state = bot_instance.user_states[user_id]
    user_state['mode'] = 'theme'
    user_state['state'] = 'IDLE'
    await bot.send_message(message.chat.id, "Режим Theme Mode активирован. Отправьте сообщение для создания концепций вечеринок.")

@bot.message_handler(commands=['clear_history'])
@auth_required
async def clear_history_command(message):
    user_id = message.from_user.id
    bot_instance.chat_histories[user_id] = []
    asyncio.create_task(bot_instance._save_chat_history(user_id))
    await bot.send_message(message.chat.id, "История очищена!")

@bot.message_handler(commands=['auth_list'])
async def auth_list_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        return
    users_list = "\n".join([f"- {uid} (Theme Mode: {'✅' if auth_manager.has_theme_mode_access(uid) else '❌'})" for uid in auth_manager.authorized_users])
    if not users_list:
        users_list = "Нет авторизованных пользователей"
    await bot.send_message(message.chat.id, f"Авторизованные пользователи:\n{users_list}")

@bot.message_handler(commands=['auth_add'])
async def auth_add_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        return
    parts = message.text.split()
    if len(parts) != 2:
        await bot.send_message(message.chat.id, "Использование: /auth_add USER_ID")
        return
    try:
        new_user_id = int(parts[1])
        auth_manager.authorize_user(new_user_id)
        await bot.send_message(message.chat.id, f"Пользователь {new_user_id} успешно авторизован")
    except ValueError:
        await bot.send_message(message.chat.id, "Неверный формат USER_ID. Должно быть число.")

@bot.message_handler(commands=['auth_remove'])
async def auth_remove_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        return
    parts = message.text.split()
    if len(parts) != 2:
        await bot.send_message(message.chat.id, "Использование: /auth_remove USER_ID")
        return
    try:
        remove_user_id = int(parts[1])
        if remove_user_id == ADMIN_USER_ID:
            await bot.send_message(message.chat.id, "Нельзя удалить администратора.")
            return
        if remove_user_id in auth_manager.authorized_users:
            del auth_manager.authorized_users[remove_user_id]
            asyncio.create_task(auth_manager.save_authorized_users())
            await bot.send_message(message.chat.id, f"Пользователь {remove_user_id} удален из списка авторизованных")
        else:
            await bot.send_message(message.chat.id, f"Пользователь {remove_user_id} не найден")
    except ValueError:
        await bot.send_message(message.chat.id, "Неверный формат USER_ID. Должно быть число.")

@bot.message_handler(commands=['auth_generate_code'])
async def auth_generate_code_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        return
    import random
    import string
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    ACCESS_CODES[code] = True
    await bot.send_message(message.chat.id, f"Новый код доступа: `{code}`", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("auth_"))
async def auth_callback_handler(call: CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if call.data == "auth_enter_code":
        await bot.answer_callback_query(call.id)
        await bot.send_message(chat_id, "Пожалуйста, введите код доступа:")
    elif call.data == "auth_request_access":
        await bot.answer_callback_query(call.id)
        username = call.from_user.username
        if user_id in auth_manager.pending_auth:
            username = auth_manager.pending_auth[user_id].get('username', username)
        await notify_admin_of_request(user_id, username)
        await bot.send_message(chat_id, "✅ Запрос на доступ отправлен администратору.")
    elif call.data.startswith("auth_approve_"):
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Только администратор может одобрять запросы")
            return
        approve_user_id = int(call.data.split("_")[2])
        auth_manager.authorize_user(approve_user_id)
        await bot.answer_callback_query(call.id)
        await bot.edit_message_text("✅ Запрос одобрен", chat_id, call.message.message_id)
        try:
            await bot.send_message(approve_user_id, "✅ Ваш запрос на доступ одобрен!")
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя {approve_user_id}: {e}")
    elif call.data.startswith("auth_deny_"):
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Только администратор может отклонять запросы")
            return
        deny_user_id = int(call.data.split("_")[2])
        if deny_user_id in auth_manager.pending_auth:
            del auth_manager.pending_auth[deny_user_id]
        await bot.answer_callback_query(call.id)
        await bot.edit_message_text("❌ Запрос отклонен", chat_id, call.message.message_id)
        try:
            await bot.send_message(deny_user_id, "❌ Ваш запрос на доступ отклонен.")
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя {deny_user_id}: {e}")

@bot.callback_query_handler(func=lambda call: True)
@auth_required
async def callback_handler(call: CallbackQuery):
    user_id = call.from_user.id
    user_state = bot_instance.user_states[user_id]
    if call.data == "mode_write":
        user_state['mode'] = 'write'
        user_state['state'] = 'IDLE'
        await bot.answer_callback_query(call.id, "Переключено на Write Mode")
        await bot.edit_message_text("Режим Write Mode активирован. Отправьте текст или изображение для создания поста.", call.message.chat.id, call.message.message_id)
    elif call.data == "mode_chat":
        user_state['mode'] = 'chat'
        user_state['state'] = 'IDLE'
        await bot.answer_callback_query(call.id, "Переключено на Chat Mode")
        await bot.edit_message_text("Режим Chat Mode активирован. Отправьте сообщение для общения.", call.message.chat.id, call.message.message_id)
        greeting_input = "Greet the user and ask how you can help with creating posts in VNVNC style."
        response = await bot_instance._generate_response(bot_instance.chat_system_prompt, greeting_input, bot_instance.get_user_model(user_id))
        await bot_instance.split_and_send_messages(call.message.chat.id, response, bot_instance.user_models.get(user_id, bot_instance.default_model))
    elif call.data == "mode_theme":
        if not auth_manager.has_theme_mode_access(user_id):
            await bot.answer_callback_query(call.id, "У вас нет доступа к Theme Mode")
            return
        user_state['mode'] = 'theme'
        user_state['state'] = 'IDLE'
        await bot.answer_callback_query(call.id, "Переключено на Theme Mode")
        await bot.edit_message_text("Режим Theme Mode активирован. Отправьте сообщение для создания концепций вечеринок.", call.message.chat.id, call.message.message_id)
    elif call.data == "help":
        help_text = """📚 Помощь:
- 📝 Write Mode: генерация всех вариантов постов по текстовым и фото запросам (можно вместе). Кнопки Remix/Расширить/Сократить позволяют легко улучшать тексты.
- 💬 Chat Mode: свободное общение и консультации по фирстилю и идеям (поддерживает афиши и фото).
- 🎨 Theme Mode: создание концепций тематических вечеринок (доступно, если разрешено админом).
- 🔧 Сменить модель: переключение между Gemini (основная, дешевая и эффективная) и GPT (запасная, дорогая, но по сути такая же)
- ℹ️ История: уменьшение или увеличение памяти сообщений (на случай глюков можно уменьшить или стереть).
- 🫡 Контекст: переключение между полным и сокращенным гайдом (больше контекста = точнее стиль, но могут быть глюки)
- ℹ️ Помощь: памятка по командам и функциям.

Для изображений: выберите, хотите ли вы написать посты или описать изображение.

Рекомендуется брать за основу поста текст, который прошел хотя бы несколько этапов генерации и отбора.

Используйте кнопки для управления ботом. Тех. саппорт: @kirniy"""
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await bot.edit_message_text(help_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
        await bot.answer_callback_query(call.id)
    elif call.data == "back_to_menu":
        await bot_instance.send_menu(call.message.chat.id)
        await bot.delete_message(call.message.chat.id, call.message.message_id)
    elif call.data == "history_settings":
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("Очистить историю", callback_data="clear_history"),
            InlineKeyboardButton("📏 Размер истории", callback_data="set_history_size")
        )
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await bot.edit_message_text("Настройки истории:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif call.data == "model_settings":
        markup = InlineKeyboardMarkup()
        for model_key in AVAILABLE_MODELS:
            markup.add(InlineKeyboardButton(model_key, callback_data=f"model_{model_key}"))
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await bot.edit_message_text("Выберите модель:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif call.data == "switch_context":
        new_context = await bot_instance.switch_voice_guide()
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await bot.edit_message_text(f"Переключено на {new_context}.", call.message.chat.id, call.message.message_id, reply_markup=markup)
        await bot.answer_callback_query(call.id, f"Переключено на {new_context}")
    elif call.data == "clear_history":
        bot_instance.chat_histories[user_id] = []
        asyncio.create_task(bot_instance._save_chat_history(user_id))
        await bot.answer_callback_query(call.id, "История очищена")
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="history_settings"))
        await bot.edit_message_text("История очищена!", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif call.data == "set_history_size":
        await bot.edit_message_text(
            "Используйте команду /set_history_size <число> для установки размера истории (1-20).",
            call.message.chat.id,
            call.message.message_id
        )
    elif call.data.startswith("model_"):
        model_key = call.data.split("_")[1]
        success, msg = await bot_instance.switch_model(user_id, model_key)
        await bot.answer_callback_query(call.id, msg)
        if success:
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("◀️ Назад", callback_data="model_settings"))
            await bot.edit_message_text(f"Модель изменена на {model_key}", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif call.data.startswith("type_"):
        type_descriptions = {
            "type_event": {"name": "событие", "description": "анонс мероприятия по заданному формату как в voice guide."},
            "type_engagement": {"name": "вовлечение", "description": "короткие броские сообщения для engagement."},
            "type_live": {"name": "лайв-апдейт", "description": "среднее или короткое сообщение среди работы клуба."},
            "type_general": {"name": "общий текст", "description": "текст в брендовом стиле клуба, возможно для сайта."}
        }
        selected_type = type_descriptions[call.data]
        user_state['type'] = selected_type
        user_state['state'] = 'WAITING_FOR_NUMBER'
        await bot_instance.send_number_selection(call.message.chat.id)
    elif call.data.startswith("number_"):
        number = int(call.data.split("_")[1])
        user_state['number'] = number
        user_state['state'] = 'IDLE'
        typing_task = asyncio.create_task(bot_instance._keep_typing(call.message.chat.id))
        try:
            logger.info(f"Генерация {number} постов")
            if user_state['image_path'] and os.path.exists(user_state['image_path']):
                logger.info("Обработка с изображением")
                base64_image = base64.b64encode(open(user_state['image_path'], 'rb').read()).decode('utf-8')
                prompt = f"""ВАЖНО: Сгенерируй РОВНО {number} разных постов типа "{user_state['type']['name']}" (не больше и не меньше).
Тип поста - {user_state['type']['description']}
Используй содержимое, тематику и текст (если есть) из изображения.
Текст запроса: {user_state['prompt']}.
ОБЯЗАТЕЛЬНО разделяй каждый пост через ---"""
                user_input = {'text': prompt, 'image': base64_image}
            else:
                logger.info("Обработка без изображения")
                prompt = f"""ВАЖНО: Сгенерируй РОВНО {number} разных постов типа "{user_state['type']['name']}" (не больше и не меньше).
Тип поста - {user_state['type']['description']}
Текст запроса: {user_state['prompt']}.
ОБЯЗАТЕЛЬНО разделяй каждый пост через ---"""
                user_input = prompt
            logger.info("Генерация ответа")
            max_retries = 3
            current_try = 0
            model = bot_instance.get_user_model(user_id)
            while current_try < max_retries:
                response = await bot_instance._generate_response(bot_instance.write_system_prompt, user_input, model)
                posts = [post.strip() for post in response.split('---') if post.strip()]
                if len(posts) == number or model['provider'] != 'Gemini':
                    break
                current_try += 1
                if current_try < max_retries:
                    logger.warning(f"Gemini сгенерировал {len(posts)} вместо {number}. Попытка {current_try + 1}")
                    if isinstance(user_input, dict):
                        user_input['text'] = f"КРИТИЧЕСКИ ВАЖНО: Сгенерируй СТРОГО {number} постов, не меньше. Предыдущая попытка создала только {len(posts)} постов. " + user_input['text']
                    else:
                        user_input = f"КРИТИЧЕСКИ ВАЖНО: Сгенерируй СТРОГО {number} постов, не меньше. Предыдущая попытка создала только {len(posts)} постов. " + user_input
            if number > 2 and len(posts) == number:
                sorted_posts = sorted(posts, key=len)
                user_state['last_posts'] = sorted_posts
            else:
                user_state['last_posts'] = posts
            prompt = user_state['prompt']
            type_name = user_state['type']['name']
            number = user_state['number']
            image_indicator = "[Image provided]" if user_state['image_path'] else ""
            user_input_str = f"{image_indicator} Prompt: {prompt}\nType: {type_name}\nNumber: {number}"
            posts_str = "\n\n---\n\n".join(user_state['last_posts'])
            await bot_instance.forward_to_admin(
                user_input=user_input_str,
                bot_response=posts_str,
                user_id=user_id,
                mode="WRITE MODE",
                username=call.from_user.username
            )
            if len(posts) < number and model['provider'] == 'Gemini':
                await bot.send_message(call.message.chat.id, f"⚠️ Gemini сгенерировал только {len(posts)} постов вместо {number}.")
            for i, post in enumerate(user_state['last_posts']):
                await bot_instance.send_post_with_refinement_buttons(call.message.chat.id, post, i)
        except Exception as e:
            logger.error(f"Ошибка генерации постов: {e}", exc_info=True)
            await bot.answer_callback_query(call.id, "Ошибка при генерации постов")
            if user_state['image_path'] and os.path.exists(user_state['image_path']):
                os.remove(user_state['image_path'])
            user_state['image_path'] = None
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
    elif call.data == "image_write":
        user_state['state'] = 'WAITING_FOR_TYPE'
        await bot_instance.send_type_selection(call.message.chat.id)
    elif call.data == "image_describe":
        user_state['state'] = 'IDLE'
        try:
            logger.info("Описание изображения")
            if not user_state.get('image_path') or not os.path.exists(user_state['image_path']):
                logger.error(f"Изображение не найдено: {user_state.get('image_path')}")
                await bot.answer_callback_query(call.id, "Ошибка: изображение недоступно")
                return
            await bot.send_chat_action(call.message.chat.id, 'typing')
            with open(user_state['image_path'], 'rb') as img_file:
                base64_image = base64.b64encode(img_file.read()).decode('utf-8')
            user_input = {
                'text': "Опиши это изображение в деталях, обращая внимание на все визуальные элементы и текст на изображении, если он есть.",
                'image': base64_image
            }
            response = await bot_instance._generate_response(bot_instance.image_system_prompt, user_input, bot_instance.get_user_model(user_id))
            user_input_str = "[Image sent for description]" + (f" with caption: {user_state['prompt']}" if user_state['prompt'] else "")
            await bot_instance.forward_to_admin(
                user_input=user_input_str,
                bot_response=response,
                user_id=user_id,
                mode="IMAGE DESCRIBE",
                username=call.from_user.username
            )
            await bot_instance.split_and_send_messages(call.message.chat.id, response, bot_instance.user_models.get(user_id, bot_instance.default_model))
            await bot.answer_callback_query(call.id)
            if user_state['image_path'] and os.path.exists(user_state['image_path']):
                os.remove(user_state['image_path'])
            user_state['image_path'] = None
        except Exception as e:
            logger.error(f"Ошибка обработки изображения: {e}", exc_info=True)
            await bot.answer_callback_query(call.id, "Ошибка при обработке изображения")
            if user_state['image_path'] and os.path.exists(user_state['image_path']):
                os.remove(user_state['image_path'])
            user_state['image_path'] = None
    elif call.data.startswith("rewrite_"):
        index = int(call.data.split("_")[1])
        if index < len(user_state['last_posts']):
            await bot.send_chat_action(call.message.chat.id, 'typing')
            post = user_state['last_posts'][index]
            refinement_prompt = f"Перепиши этот пост в том же стиле: {post}. Только один пост напиши, не больше"
            response = await bot_instance._generate_response(bot_instance.write_system_prompt, refinement_prompt, bot_instance.get_user_model(user_id))
            new_post = response.strip()
            user_state['last_posts'][index] = new_post
            await bot_instance.send_post_with_refinement_buttons(call.message.chat.id, new_post, index)
    elif call.data.startswith("expand_"):
        index = int(call.data.split("_")[1])
        if index < len(user_state['last_posts']):
            await bot.send_chat_action(call.message.chat.id, 'typing')
            post = user_state['last_posts'][index]
            refinement_prompt = f"Расширь этот пост, добавив больше деталей: {post}. Только один пост напиши, не больше"
            response = await bot_instance._generate_response(bot_instance.write_system_prompt, refinement_prompt, bot_instance.get_user_model(user_id))
            new_post = response.strip()
            user_state['last_posts'][index] = new_post
            await bot_instance.send_post_with_refinement_buttons(call.message.chat.id, new_post, index)
    elif call.data.startswith("shorten_"):
        index = int(call.data.split("_")[1])
        if index < len(user_state['last_posts']):
            await bot.send_chat_action(call.message.chat.id, 'typing')
            post = user_state['last_posts'][index]
            refinement_prompt = f"Сократи этот пост, сохраняя суть: {post}. Только один пост напиши, не больше"
            response = await bot_instance._generate_response(bot_instance.write_system_prompt, refinement_prompt, bot_instance.get_user_model(user_id))
            new_post = response.strip()
            user_state['last_posts'][index] = new_post
            await bot_instance.send_post_with_refinement_buttons(call.message.chat.id, new_post, index)
    elif call.data == "cancel":
        user_state['state'] = 'IDLE'
        if user_state['image_path'] and os.path.exists(user_state['image_path']):
            os.remove(user_state['image_path'])
            user_state['image_path'] = None
        await bot.edit_message_text("Операция отменена.", call.message.chat.id, call.message.message_id)
        await bot.answer_callback_query(call.id)
        return
    elif call.data == "admin_menu":
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("👥 Список пользователей", callback_data="admin_list_users"))
        markup.row(InlineKeyboardButton("🔑 Сгенерировать код доступа", callback_data="admin_generate_code"))
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await bot.edit_message_text("Панель администратора:", call.message.chat.id, call.message.message_id, reply_markup=markup)
        await bot.answer_callback_query(call.id)
    elif call.data == "admin_list_users":
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        users_list = list(auth_manager.authorized_users.keys())
        markup = InlineKeyboardMarkup()
        page = user_state.get('admin_user_page', 0)
        start_idx = page * 5
        end_idx = min(start_idx + 5, len(users_list))
        for i in range(start_idx, end_idx):
            user_id_to_show = users_list[i]
            if user_id_to_show == ADMIN_USER_ID:
                markup.row(InlineKeyboardButton(f"👑 {user_id_to_show} (Админ)", callback_data=f"admin_noop"))
            else:
                theme_mode_status = "✅" if auth_manager.has_theme_mode_access(user_id_to_show) else "❌"
                markup.row(
                    InlineKeyboardButton(f"👤 {user_id_to_show}", callback_data=f"admin_noop"),
                    InlineKeyboardButton(f"Theme Mode: {theme_mode_status}", callback_data=f"admin_toggle_theme_{user_id_to_show}"),
                    InlineKeyboardButton("❌ Удалить", callback_data=f"admin_remove_user_{user_id_to_show}")
                )
        if len(users_list) > 5:
            pagination_buttons = []
            if page > 0:
                pagination_buttons.append(InlineKeyboardButton("⬅️", callback_data="admin_prev_page"))
            if end_idx < len(users_list):
                pagination_buttons.append(InlineKeyboardButton("➡️", callback_data="admin_next_page"))
            if pagination_buttons:
                markup.row(*pagination_buttons)
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="admin_menu"))
        await bot.edit_message_text(f"Авторизованные пользователи ({len(users_list)}):", call.message.chat.id, call.message.message_id, reply_markup=markup)
        await bot.answer_callback_query(call.id)
    elif call.data == "admin_prev_page":
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        if 'admin_user_page' not in user_state:
            user_state['admin_user_page'] = 0
        if user_state['admin_user_page'] > 0:
            user_state['admin_user_page'] -= 1
        await bot.answer_callback_query(call.id)
        call.data = "admin_list_users"
        await callback_handler(call)
    elif call.data == "admin_next_page":
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        if 'admin_user_page' not in user_state:
            user_state['admin_user_page'] = 0
        max_pages = len(auth_manager.authorized_users) // 5
        if user_state['admin_user_page'] < max_pages:
            user_state['admin_user_page'] += 1
        await bot.answer_callback_query(call.id)
        call.data = "admin_list_users"
        await callback_handler(call)
    elif call.data.startswith("admin_remove_user_"):
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        user_id_to_remove = int(call.data.split("_")[3])
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"admin_confirm_remove_{user_id_to_remove}"),
            InlineKeyboardButton("❌ Отмена", callback_data="admin_list_users")
        )
        await bot.edit_message_text(f"Вы уверены, что хотите удалить пользователя {user_id_to_remove}?",
                                   call.message.chat.id, call.message.message_id, reply_markup=markup)
        await bot.answer_callback_query(call.id)
    elif call.data.startswith("admin_confirm_remove_"):
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        user_id_to_remove = int(call.data.split("_")[3])
        if user_id_to_remove == ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Невозможно удалить администратора")
            return
        if user_id_to_remove in auth_manager.authorized_users:
            del auth_manager.authorized_users[user_id_to_remove]
            asyncio.create_task(auth_manager.save_authorized_users())
            try:
                await bot.send_message(user_id_to_remove, "❌ Ваш доступ к боту был отозван.")
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя {user_id_to_remove}: {e}")
            await bot.answer_callback_query(call.id, f"Пользователь {user_id_to_remove} удален")
        else:
            await bot.answer_callback_query(call.id, f"Пользователь {user_id_to_remove} не найден")
        call.data = "admin_list_users"
        await callback_handler(call)
    elif call.data == "admin_generate_code":
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        import random
        import string
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        ACCESS_CODES[code] = True
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="admin_menu"))
        await bot.edit_message_text(f"Новый код доступа: `{code}`\n\nЭтот код можно использовать один раз для доступа к боту.",
                                  call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        await bot.answer_callback_query(call.id)
    elif call.data.startswith("admin_toggle_theme_"):
        if user_id != ADMIN_USER_ID:
            await bot.answer_callback_query(call.id, "Доступно только для администратора")
            return
        toggle_user_id = int(call.data.split("_")[3])
        if toggle_user_id in auth_manager.authorized_users:
            current_status = auth_manager.authorized_users[toggle_user_id].get('theme_mode_enabled', False)
            auth_manager.authorized_users[toggle_user_id]['theme_mode_enabled'] = not current_status
            asyncio.create_task(auth_manager.save_authorized_users())
            await bot.answer_callback_query(call.id, f"Theme Mode для пользователя {toggle_user_id} переключен")
        else:
            await bot.answer_callback_query(call.id, f"Пользователь {toggle_user_id} не найден")
        call.data = "admin_list_users"
        await callback_handler(call)
    elif call.data == "admin_noop":
        await bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['text'])
@auth_required
async def text_handler(message):
    await bot_instance.handle_text(message)

@bot.message_handler(content_types=['photo'])
@auth_required
async def photo_handler(message):
    await bot_instance.handle_photo(message)

async def main():
    await bot_instance.start()

if __name__ == "__main__":
    asyncio.run(main())
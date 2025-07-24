Below is a **surgical, line‑level checklist** for dropping GPT‑4o‑Transcribe into your bot, keeping Gemini for summarising, and letting the user switch engines from the existing settings menu.

---

## 1 · Add the dependency & key

1. **requirements.txt**

   ```text
   openai>=1.25.0        # new
   ```

   Then run `pip install -r requirements.txt`.

2. **Environment / hosting panel**

   ```
   export OPENAI_API_KEY=sk‑************************
   ```

3. **config.py** – append:

   ```python
   OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
   if not OPENAI_API_KEY:
       print("OPENAI_API_KEY not set", file=sys.stderr); sys.exit(1)
   ```

---

## 2 · Create *openai\_utils.py*

```python
# openai_utils.py
import os, logging, aiofiles
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def transcribe_gpt4o(audio_path: str, language: str = "ru") -> str | None:
    """Return plain‑text transcript or None on error."""
    try:
        async with aiofiles.open(audio_path, "rb") as f:
            resp = await client.audio.transcriptions.create(
                file=await f.read(),
                model="gpt-4o-transcribe",
                language=language           # auto‑detect if empty
            )
        return resp.text
    except Exception as e:
        logger.exception("GPT‑4o transcription failed: %s", e)
        return None
```

*Accuracy & speed refs: OpenAI docs list gpt‑4o‑transcribe as the current SOTA STT model ([OpenAI Platform][1]); independent tests show \~9 % WER—30 % better than Whisper v3 ([VentureBeat][2]).*

---

## 3 · DB: store the preferred STT engine

1. **Migration (once):**

   ```sql
   ALTER TABLE chat_preferences
   ADD COLUMN IF NOT EXISTS stt_engine TEXT DEFAULT 'gpt4o';
   ```

2. **db\_utils.py** – helper wrappers

   ```python
   async def get_chat_stt_engine(pool, chat_id):
       rec = await pool.fetchrow("SELECT stt_engine FROM chat_preferences WHERE chat_id=$1", chat_id)
       return rec["stt_engine"] if rec else "gpt4o"

   async def set_chat_stt_engine(pool, chat_id, engine):
       await pool.execute("""
           INSERT INTO chat_preferences (chat_id, stt_engine)
                VALUES ($1,$2)
           ON CONFLICT (chat_id)
                DO UPDATE SET stt_engine = EXCLUDED.stt_engine, updated_at = NOW();
       """, chat_id, engine)
   ```

---

## 4 · Let the user pick: extend the settings keyboard

### a) **bot.py** – new button creator

```python
def create_stt_engine_buttons(chat_lang: str, current: str):
    gpt_label = "🎙️ GPT‑4o" + (" ✅" if current == "gpt4o" else "")
    gem_label = "🎙️ Gemini" + (" ✅" if current == "gemini" else "")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(gpt_label, callback_data="stt:set:gpt4o"),
        InlineKeyboardButton(gem_label, callback_data="stt:set:gemini")
    ]])
```

### b) Hook it into the existing **settings** callback

Locate the current `settings:` callback handler; just before it returns the keyboard, append:

```python
stt_engine = await get_chat_stt_engine(pool, chat_id)
keyboard.inline_keyboard.append(  # add new row
    create_stt_engine_buttons(chat_lang, stt_engine).inline_keyboard[0]
)
```

### c) **CallbackQueryHandler** for updates

```python
async def handle_stt_choice(update: Update, context: CallbackContext):
    _, _, engine = update.callback_query.data.split(":")   # "stt:set:gpt4o"
    await set_chat_stt_engine(context.bot_data['db_pool'], update.effective_chat.id, engine)
    await update.callback_query.answer("Engine switched ✔︎", show_alert=False)
    await update.callback_query.edit_reply_markup(
        reply_markup=create_stt_engine_buttons('ru', engine)   # language can be passed in
    )

application.add_handler(CallbackQueryHandler(handle_stt_choice, pattern=r"^stt:set:"))
```

---

## 5 · Routing logic in **handle\_voice\_message**

Replace the **“Pass to Gemini”** block with:

```python
# 2. Decide STT engine
stt_engine = await get_chat_stt_engine(pool, message.chat_id)

# 3. Transcribe
transcript_text = None
if stt_engine == "gpt4o":
    from openai_utils import transcribe_gpt4o
    transcript_text = await transcribe_gpt4o(temp_audio_file.name, chat_lang)

# fallback if GPT‑4o failed or Gemini chosen
if transcript_text is None:
    summary_text, transcript_text = await process_audio_with_gemini(
        temp_audio_file.name, mode, chat_lang
    )
else:
    # Only summarise (fast) – reuse Gemini for text‑only summary
    from gemini_utils import summarise_text_with_gemini   # see next step
    summary_text = await summarise_text_with_gemini(transcript_text, mode, chat_lang)
```

---

## 6 · Add a **text‑only summariser** (tiny change in *gemini\_utils.py*)

Right after `process_audio_with_gemini`, drop in:

```python
async def summarise_text_with_gemini(transcript: str, mode: str, language: str='ru') -> str:
    model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")
    prompt = build_summary_prompt(transcript, mode, language)  # you already have this logic
    resp = await model.generate_content(
        contents=[prompt],
        generation_config={"temperature": 0.3}
    )
    return resp.text.strip()
```

*(Reuse your existing `build_summary_prompt` or copy the fragment from `process_audio_with_gemini`.)*

---

## 7 · Done – quick test

```bash
python bot.py
# in Telegram:
#  /settings ➜ tap 🎙️ GPT‑4o
#  send a voice message ➜ should transcribe via GPT‑4o and summarise via Gemini
#  switch back to 🎙️ Gemini to force the legacy path
```

You now have:

* **GPT‑4o‑Transcribe** for best‑in‑class accuracy (multilingual inc. Russian).
* **Gemini Flash** as automatic fallback and as a user‑selectable option.
* One extra dependency and \~70 new lines of code—no other architecture changes.


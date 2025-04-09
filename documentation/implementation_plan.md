# Implementation Plan: Gemini Voice Summarizer Telegram Bot

This plan outlines the steps to enhance the existing `mp3tovoice-bot` with Gemini-powered voice summarization features, deployed on Fly.io.

## Phase 1: Setup & Base Integration (Extend Existing Bot)

1.  **Verify Existing Setup:** Clone the `mp3tovoice-bot` repository. Ensure it builds and runs locally and deploys successfully to Fly.io with its current MP3->Voice functionality working.
2.  **Install Dependencies:** Add `google-generativeai`, `asyncpg`, `pytz` (or rely on `zoneinfo` in Python 3.9+) to `requirements.txt`. Run `pip install -r requirements.txt`. Update `Dockerfile` if new system dependencies are needed (likely just `ffmpeg`, confirm it's present).
3.  **Configure Secrets:** Obtain a Google Gemini API key. Set it as a Fly secret: `fly secrets set GEMINI_API_KEY="your_key_here"`. Ensure `BOT_TOKEN` is also set. Update `.env` file for local development.
4.  **Initialize Gemini Client:** In `config.py` (or a new `services/gemini_service.py`), add code to initialize the `google.generativeai` client using the API key from environment variables.
5.  **Update Locales:** Add new keys and initial Russian placeholder strings to `locales.py` for new messages (e.g., processing status, button labels, error messages).
6.  **Basic Structure Refactor:** Create new directories (`handlers/`, `services/`, `ui/`, `utils/`) if they don't exist. Move relevant existing logic (e.g., MP3 handling) into appropriate handlers if desired, or keep `bot.py` larger initially.
7.  **Validation**:
    * Run the bot locally using `python bot.py`. Check for initialization errors.
    * Deploy to Fly.io (`fly deploy`).
    * Verify the original MP3->Voice functionality still works on the deployed bot.
    * Check logs to ensure the Gemini client initializes without errors.

## Phase 2: Core Gemini Summarization Workflow

8.  **Create Audio Handler:** Create `handlers/audio_handler.py`. Add a `MessageHandler` in `bot.py` to route `filters.VOICE` messages to a function in this handler (e.g., `process_voice_message`).
9.  **Gemini Service Function:** In `services/gemini_service.py`, create a function `get_summary(audio_file_path, mime_type, mode='Краткий')`. This function should:
    * Prepare the audio data for the Gemini API (check SDK docs - likely upload file directly).
    * Call Gemini API for transcription (if needed separately) and summarization using the specified mode's prompt (use default "Краткий" prompt initially).
    * Handle potential Gemini API errors gracefully.
    * Return the summary text or raise an exception.
10. **Integrate Handler & Service:** In `handlers/audio_handler.py`:
    * Download the voice message audio file using `await voice.get_file()` and `await file.download_to_drive()`.
    * Send a "Processing..." status message (`await context.bot.send_message(...)`).
    * Call `services.gemini_service.get_summary()` with the downloaded file path and MIME type.
    * Receive the summary text.
    * Send the raw summary text back to the user (`await update.message.reply_text(summary_text)`).
    * Implement basic `try...except` around the service call to catch errors and send a simple error message from `locales.py`.
    * Clean up the downloaded audio file.
11. **Validation**:
    * Send a voice message to the bot.
    * Verify that a "Processing..." message appears.
    * Verify that a plain text summary (default "Краткий" mode) is received.
    * Test sending different lengths of voice messages (within reason).
    * Test error handling by temporarily using an invalid API key or simulating an API error.

## Phase 3: UI Implementation (Buttons & Formatting)

12. **Create UI Modules:**
    * `ui/keyboards.py`: Create functions like `get_initial_buttons()`, `get_mode_selection_buttons()`, `get_history_pagination_buttons(current_page, total_pages)`. These functions return `InlineKeyboardMarkup` objects.
    * `ui/messages.py`: Create a function `format_summary_message(user_name, timestamp, summary_text)` that constructs the final message string including the header (with Moscow Time conversion using `utils/time_utils.py`) and the summary within a code block.
13. **Implement Timezone Util:** Create `utils/time_utils.py` with a function `to_moscow_time(dt_object)` using `zoneinfo` or `pytz`.
14. **Update Audio Handler Response:** Modify `handlers/audio_handler.py` to:
    * Call `ui.messages.format_summary_message()` to format the output.
    * Send the formatted message using `reply_markdown_v2` (or `reply_html` if chosen) and attach the keyboard from `ui.keyboards.get_initial_buttons()`. Store the sent message ID for later edits.
15. **Create Callback Handler:** Create `handlers/callback_handler.py`. Add a `CallbackQueryHandler` in `bot.py` routing button presses to functions in this handler. Use distinct `callback_data` strings for each button type (e.g., `mode_change`, `select_mode:Подробный`, `redo`, `history`, `confirm`, `history_page:next`).
16. **Implement Button Logic:** In `handlers/callback_handler.py`:
    * **Mode Change:** Handle `mode_change`. Edit the message keyboard to show mode selection buttons (`ui.keyboards.get_mode_selection_buttons()`).
    * **Select Mode:** Handle `select_mode:<mode_name>`. Extract mode name. Re-call `services.gemini_service.get_summary()` with the new mode for the *original* audio (need to store/retrieve context, perhaps using `context.user_data` or associating with message ID). Format the new summary. Edit the message content and restore initial buttons.
    * **Redo:** Handle `redo`. Re-call `services.gemini_service.get_summary()` with the *last used* mode. Edit message content.
    * **Confirm:** Handle `confirm`. Edit the message to remove the keyboard (`reply_markup=None`).
17. **Validation**:
    * Send voice message. Verify formatted output (Header with MSK time, code block) and initial buttons appear.
    * Click `[Сменить режим]`. Verify mode buttons appear.
    * Click a mode button. Verify summary updates and initial buttons reappear.
    * Click `[Переделать]`. Verify summary potentially updates.
    * Click `[✅ Готово]`. Verify buttons disappear.
    * Test with multiple messages to ensure context is handled correctly.

## Phase 4: History & Database Integration

18. **Setup Fly Postgres:** Add the Fly Postgres addon to your app: `fly postgres create` and `fly postgres attach`. Get connection string/details. Set DB credentials as Fly secrets.
19. **Define DB Schema:** Plan the `summaries` table (e.g., `id SERIAL PRIMARY KEY`, `user_id BIGINT`, `chat_id BIGINT`, `message_id BIGINT`, `summary_mode TEXT`, `summary_text TEXT`, `created_at TIMESTAMPTZ DEFAULT NOW()`, `original_audio_ref TEXT NULL`).
20. **Create DB Service:** Create `services/db_service.py`. Initialize `asyncpg` connection pool using credentials from `config.py`. Create async functions: `save_summary(user_id, chat_id, message_id, mode, text)`, `get_summary_history(user_id, chat_id, limit, offset)`, `get_history_count(user_id, chat_id)`.
21. **Integrate Saving:** In `handlers/audio_handler.py` (and callback handler where summaries are regenerated), after successfully getting a summary, call `services.db_service.save_summary()`.
22. **Implement History Command:** Create `handlers/command_handler.py`. Add `/history` `CommandHandler`. This handler should:
    * Call `services.db_service.get_history_count()` and `services.db_service.get_summary_history()` (with limit=1, offset=0 initially).
    * Format the retrieved summary using `ui.messages.format_summary_message()`.
    * Send the message with pagination buttons from `ui.keyboards.get_history_pagination_buttons()`.
23. **Implement History Button & Pagination:**
    * In `handlers/callback_handler.py`, handle `history` callback (similar logic to `/history` command).
    * Handle `history_page:<page_num>` or `history_page:next/prev` callbacks. Calculate new offset based on current page/direction. Fetch data using `get_summary_history()`. Edit the existing history message content and update pagination buttons.
24. **Validation**:
    * Send several voice messages.
    * Check the Fly Postgres database to verify summaries are being saved correctly.
    * Use `/history` command. Verify the latest summary appears with pagination.
    * Use pagination buttons. Verify navigation works and content updates.
    * Click the `[История]` button. Verify it triggers the history view.

## Phase 5: Advanced Features & Refinements

25. **Implement Cleaned Transcript:**
    * Add "Транскрипт" mode logic in `services/gemini_service.py`. This might involve specific prompts to Gemini or post-processing (regex for fillers, potentially another library for basic grammar if needed).
    * Implement careful name/term handling (prompt engineering for Gemini, or regex/dictionary lookup for bracketed alternatives).
26. **Implement Audio Chunking (If Necessary):**
    * In `utils/audio_utils.py`, create functions using `pydub` to split long audio files into manageable chunks (respecting potential sentence boundaries if possible).
    * Modify `services/gemini_service.py` to process chunks sequentially or in parallel (if API allows), then combine the results (transcripts/summaries). This is complex.
27. **Implement Group Usage Tracking:** In `services/db_service.py`, add a table `group_usage` (`user_id`, `group_id`, `message_count`) or similar. In `handlers/audio_handler.py`, if `update.message.chat.type` is 'group' or 'supergroup', increment the count in the DB for the `user_id`.
28. **Refine Error Handling:** Add specific `try...except` blocks for DB errors, audio processing errors. Map error types to specific user-friendly Russian messages in `locales.py`. Implement the global error handler in `bot.py` for uncaught exceptions.
29. **Add Logging:** Integrate Python's `logging` module throughout. Log key events (bot start, message received, API call start/end, summary saved, history accessed) and errors with context (user ID, chat ID). Configure logging level via environment variable.
30. **Validation**:
    * Test "Транскрипт" mode. Verify cleaning and name handling.
    * Test with very long voice messages (if chunking implemented).
    * Use the bot in a group chat. Verify usage is tracked in the database.
    * Trigger various errors (disconnect DB, use bad API key, send invalid audio) and verify specific Russian error messages are shown.
    * Check Fly.io logs for informative logging output.

## Phase 6: Deployment & Final Testing

31. **Code Review & Refactoring:** Clean up code, ensure consistency, add comments.
32. **Final Fly.io Configuration:** Ensure `fly.toml` is optimized (scaling settings if needed). Double-check secrets.
33. **Thorough Testing:**
    * Test all summarization modes extensively.
    * Test history navigation thoroughly.
    * Test in direct chats and group chats.
    * Test edge cases (empty voice message, very short messages, different audio qualities).
    * Test concurrent usage simulation (if possible).
    * Review all Russian UI text for clarity and correctness.
34. **Deploy Production Version:** `fly deploy`.
35. **Monitor:** Monitor logs and performance on Fly.io after deployment.
36. **Validation**: Bot operates reliably and correctly handles all features in the production environment.

This plan provides a structured approach. Phases can overlap, and iterative refinement (especially for prompts and cleaning logic) is expected.

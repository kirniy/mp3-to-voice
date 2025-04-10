# Voicio Bot Improvement & Feature Suggestions

This document outlines potential improvements and new features for the Voicio Telegram bot, focusing on stability, efficiency, API usage optimization, and scalability for deployment on Fly.io.

## 1. Stability Enhancements

*   **Comprehensive Error Handling:**
    *   Implement more specific `try...except` blocks in message handlers (`handle_audio`, `handle_voice_message`) and the `button_callback` function. Catch specific exceptions from `python-telegram-bot` (e.g., `telegram.error.BadRequest`, `telegram.error.RetryAfter`), `asyncpg` (e.g., `asyncpg.PostgresError`), and the `google-generativeai` library (e.g., `google.api_core.exceptions.ResourceExhausted`, `google.api_core.exceptions.DeadlineExceeded`).
    *   Provide more informative error messages to the user or log errors appropriately.
    *   Implement robust retry logic with exponential backoff and jitter for transient network or API errors (especially for Gemini calls), beyond the current basic retry loop.
    *   Handle potential errors during audio processing with `pydub` (e.g., invalid format, file corruption).
*   **Database Connection Management:**
    *   While `post_init` and `pre_shutdown` handle pool creation/closing, ensure connections acquired from the pool (`pool.acquire()`) are always released, even in case of exceptions within request handlers (use `async with pool.acquire() as connection:` which handles release automatically).
    *   Consider adding connection validation or retry logic during initial pool creation (`asyncpg.create_pool`).
*   **State Consistency:**
    *   **Remove Redundant Cache:** Eliminate the in-memory `user_language` dictionary and related functions (`set_user_language`, `get_user_language`) in `locales.py`. Rely *solely* on the database functions in `db_utils.py` (`get_user_language`, `set_user_language`, `get_chat_language`, `set_chat_language`) for language preferences to ensure consistency, especially when scaling horizontally.
    *   Ensure chat/user state (like paused status) is consistently read from the database at the beginning of relevant handlers.
*   **Resource Management:**
    *   Ensure temporary audio files created using `tempfile` are always deleted using `try...finally` blocks to prevent disk space leaks, especially if errors occur during processing.
    *   Strengthen the Gemini file cleanup logic in `gemini_utils.py` to ensure `genai.delete_file` is called reliably in a `finally` block after processing or failure.

## 2. Efficiency Optimizations

*   **Gemini API Usage (High Impact):**
    *   **Combine API Calls:** Modify `process_audio_with_gemini` to generate *both* the transcript and the requested summary/mode output (brief, detailed, etc.) in a **single** `model.generate_content_async` call. This is the most significant optimization, potentially halving Gemini API costs and reducing latency per voice message. This requires careful prompt engineering (e.g., instructing the model to provide transcript first, then the summary in a specific format) and parsing the combined response.
    *   **Optimize File Upload/Polling:** Investigate if Gemini offers streaming input or other mechanisms to avoid re-uploading the same audio for actions like "Redo". Cache the `audio_file.uri` returned by `genai.upload_file` in the `summaries` table; if the URI is still valid, you might reuse it for subsequent operations on the same audio. Replace the `asyncio.sleep(1)` polling loop for file processing status with potentially longer, increasing sleep intervals, or check if Gemini offers more efficient status notifications (e.g., webhooks, although less likely for file processing status).
*   **Database Interaction:**
    *   **Selective Fetches:** In functions like `get_summary_context_for_callback`, fetch only the specific columns needed for the callback action instead of the entire record (`SELECT id, user_id, ...`).
    *   **Query Analysis:** Use `EXPLAIN ANALYZE` in PostgreSQL to check the performance of key queries (especially history fetching) under load and ensure indices are effective.
    *   **(Optional) Caching:** For frequently accessed, rarely changing data like chat preferences (`get_chat_default_mode`, `get_chat_language`), consider implementing a short-lived, in-memory cache (e.g., using `cachetools` library with a Time-To-Live) that is explicitly invalidated when settings are updated via `set_` functions. This can reduce database load but adds complexity.
*   **Telegram API Calls:**
    *   Minimize redundant message edits in `button_callback`. Attempt to update message text and reply markup in a single API call (`edit_message_text` with `reply_markup` parameter) when both change.
    *   Review usage of `send_chat_action` to ensure it's not excessive.
*   **Code Refactoring:**
    *   **Simplify Markdown:** Re-evaluate the custom markdown protection/escaping functions (`protect_markdown`, `unprotect_markdown`, `format_for_telegram`). Test if `telegram.helpers.escape_markdown(text, version=2, entity_type=...)` combined with Telegram's `parse_mode=ParseMode.MARKDOWN_V2` can handle most formatting needs, reducing custom code complexity and potential bugs.
    *   **Refactor `button_callback`:** Break down the large `button_callback` function into smaller, focused async helper functions based on the callback data prefix (e.g., `handle_mode_selection_callback`, `handle_history_nav_callback`, `handle_settings_callback`). This improves readability and maintainability.

## 3. Scalability & Fly.io Deployment

*   **Concurrency:**
    *   The use of `asyncio`, `python-telegram-bot`, and `asyncpg` provides a good foundation. Double-check for any remaining synchronous blocking calls within async functions.
*   **Fly.io Configuration (`fly.toml`):**
    *   **Horizontal Scaling:** To handle hundreds of users, configure Fly.io to run multiple instances of the bot. Use `fly scale count N` (e.g., `N=3` or more, monitor load to determine the right number). The stateless nature (using the database for shared state) makes horizontal scaling feasible.
    *   **Vertical Scaling:** Monitor CPU and Memory usage on the `shared-cpu-1x` VMs. Audio processing and AI model inference can be resource-intensive. Scale up VM size (`fly scale vm SIZE`) if necessary (e.g., `shared-cpu-2x`, `dedicated-cpu-1x`). Remember to adjust memory (`-m MEMORY_MB`) accordingly.
    *   **Continuous Operation:** For a responsive bot handling many users, ensure machines run continuously. If `auto_stop_machines` is used, set `min_machines_running` to at least 1 (or your desired baseline scale count) to avoid cold starts. Alternatively, remove the `auto_start_machines` and `auto_stop_machines` settings entirely.
    *   **Health Checks:** Add basic health checks in `fly.toml` using `[[services.tcp_checks]]` (if the bot listens on a port, even if not for HTTP) or expose a minimal HTTP health check endpoint (requires adding `[http_service]` back and a minimal web server component) using `[[services.http_checks]]`. This allows Fly.io to automatically detect and restart unhealthy instances.
    *   **Database Scaling:** Monitor the performance of your PostgreSQL instance (Fly Postgres or external). Be prepared to scale the database resources (CPU, RAM, IOPS) as bot usage grows. Ensure the database connection limit can accommodate the total number of connections from all scaled bot instances (adjust `asyncpg` pool size per instance accordingly: `pool_size = max_db_connections / num_bot_instances`).
*   **Rate Limiting:** Be mindful of Telegram's API rate limits, especially when operating in group chats or sending many messages quickly. `python-telegram-bot` provides some handling, but consider adding explicit delays or queuing if hitting limits. Also, handle Gemini's rate limit errors (`ResourceExhausted`) gracefully.

## 4. New Features & Expansions

*   **Webhook Support:** Switch from polling (`run_polling`) to webhooks (`run_webhook`). This is generally more efficient for high-traffic bots. Requires:
    *   Adding a minimal web server component (e.g., using `aiohttp`, `FastAPI`, or `Flask` with an async runner like `hypercorn`) to handle incoming updates from Telegram.
    *   Configuring the `[http_service]` section in `fly.toml` to route external traffic to the bot's web server port.
    *   Setting the webhook URL with Telegram.
*   **Improved History Management:**
    *   Implement keyword search within a chat's history.
    *   Allow filtering history by date range.
    *   Add an option to delete individual history entries via buttons.
*   **Usage Tracking & Limiting:**
    *   Track API calls (e.g., Gemini processing time or call count) per user or chat in the database.
    *   Potentially implement usage limits for free users or different subscription tiers.
*   **Admin Interface:** Create a simple web dashboard (could be a separate Fly app) for monitoring bot usage, viewing errors, managing subscriptions (if applicable), etc.
*   **Enhanced Language Handling:**
    *   Attempt to auto-detect the language of the voice message *after* transcription (potentially using a simple language detection library or another small AI call) to provide more accurate default summarization language.
    *   Consider using a standard localization library like `GNU gettext` (via Python's `gettext` module) or `fluent-python` if the number of strings/languages grows significantly.
*   **Automated Subscription Management:** Integrate with a payment provider (like Stripe) via their API to handle Pro plan subscriptions automatically, rather than relying on manual contact.
*   **Error Monitoring Service:** Integrate with a service like Sentry (`sentry-sdk`) to automatically capture, aggregate, and report exceptions in production, making debugging easier.
*   **Transcript Caching:** If a user requests different summary modes for the *same* voice message quickly, cache the initial transcript text (e.g., in the `summaries` table or a separate cache like Redis) to avoid redundant Gemini transcription calls when only the summarization prompt needs changing.
*   **(NEW) Referral Program:**
    *   **Mechanics:** Implement a referral system where existing users receive a unique referral code/link. When a new user signs up using this code/link (or potentially via a `/referral` command), both the referrer and the referred user receive a reward (e.g., one free month of a base subscription tier, or extending the referrer's current plan).
    *   **Database:** Requires additions to the user table or a dedicated `referrals` table to store `referral_code` (unique per user), `referred_by_user_id` (for new users), `reward_applied_referrer_at`, `reward_applied_referred_at`.
    *   **Implementation:** Generate unique, user-friendly codes. Handle incoming links/commands to associate users. Logic to validate referrals and apply rewards (e.g., extend subscription `end_date` in the `subscriptions` table). Ensure rewards are applied only once per unique referral. 
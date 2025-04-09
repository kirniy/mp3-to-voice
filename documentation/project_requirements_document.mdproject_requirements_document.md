# Project Requirements Document: Gemini Voice Summarizer Telegram Bot

## 1. Project Overview

This project involves enhancing an existing Telegram bot (`mp3tovoice-bot`) deployed on Fly.io. The core goal is to add new functionality enabling the bot to process voice messages using Google Gemini AI (specifically Gemini Flash, with potential for Pro). Upon receiving a voice message, the bot will transcribe it and generate summaries in various modes (Combined, Brief, Detailed, Bullet Points, Cleaned Transcript).

The bot's user interface and all interactions will be exclusively in Russian. It will leverage modern Telegram UI features like inline buttons for mode selection and interaction, and formatted messages (MarkdownV2, code blocks) for clarity and ease of use. Summaries will be stored persistently (using Fly Postgres) and accessible via a history feature. The bot must retain its original functionality of converting MP3 files to voice messages.

While future plans include subscription tiers for group chat usage, the initial phase will allow free usage in individual chats and groups (with usage tracking and an admin toggle planned for later control). The primary objective is to deliver a robust, reliable, and user-friendly voice summarization tool within the Telegram ecosystem.

## 2. In-Scope vs. Out-of-Scope

**In-Scope:**

* **Extend Existing Bot:** Build upon the current `mp3tovoice-bot` codebase and Fly.io deployment.
* **Retain Existing Functionality:** The bot's original MP3-to-voice conversion feature must remain active.
* **Gemini Integration:** Utilize Google Gemini API (Flash/Pro models via `google-generativeai` library) for audio transcription and summarization.
* **Voice Message Processing:** Handle voice messages sent directly to the bot or in groups where it's present. Handle maximum Telegram audio length/size, potentially using chunking.
* **Summarization Modes:** Implement multiple summary modes:
    * Комбинированный (Combined)
    * Краткий (Brief - Default)
    * Подробный (Detailed)
    * Тезисный (Bullet Points)
    * Транскрипт (Cleaned Transcript)
    * *All modes must strive for maximum information retention.*
* **Cleaned Transcript:** Provide a transcript with filler words removed, punctuation/grammar corrected, and careful handling of names/terms (using bracketed alternatives for ambiguity).
* **Telegram UI:**
    * Russian language interface exclusively.
    * Inline buttons for mode selection, redoing summaries, accessing history, and confirming (`✅`).
    * Dynamic button behavior (e.g., disappearing after confirmation).
    * Formatted output messages (MarkdownV2) including speaker name (from Telegram), message timestamp (in Moscow Time), and summary/transcript within a code block.
* **Summary History:** Store generated summaries/transcripts in Fly Postgres. Allow users to retrieve and navigate their history via a button and `/history` command (paginated view).
* **Basic User Tracking:** Track usage per Telegram User ID, including counts of group chat usage (for future subscription logic).
* **Initial Free Access:** Allow usage in individual chats and group chats freely in this phase (controlled by a future admin setting, enabled by default initially).
* **Error Handling:** Graceful handling of common errors (API issues, audio limits, etc.) with user-friendly Russian messages.
* **Deployment:** Continue deployment on Fly.io, managing secrets securely.

**Out-of-Scope (for Initial Implementation):**

* **Payment Processing:** Implementation of Telegram Stars or Bank Card payments for subscriptions.
* **Subscription Enforcement:** Strict enforcement of group chat limits based on subscription tiers (tracking is in scope, enforcement is not).
* **Admin Dashboard:** A web-based or in-bot dashboard for administrators to manage users, settings, or view stats.
* **Advanced Real-time Collaboration Features:** Features beyond standard bot interactions.
* **Support for Languages other than Russian:** While the underlying code *might* support localization via `locales.py`, the active UI and processing focus is solely Russian.
* **Complex Speaker Diarization:** Advanced identification and labeling of multiple speakers within a single voice message for the transcript.

## 3. User Flow

1.  **Initiation:** A user sends a voice message directly to the bot or in a group chat where the bot is a member. The bot also continues to listen for MP3 files for its original conversion function.
2.  **Processing:** The bot acknowledges receipt (e.g., "Обрабатываю ваше сообщение...") and processes the voice message using the Gemini API for transcription and default summarization ("Краткий").
3.  **Response:** The bot sends a message containing:
    * Header: User's Name, Message Timestamp (Moscow Time).
    * Content: The "Краткий" summary within a ```code block```.
    * Buttons: `[Сменить режим]`, `[Переделать]`, `[История]`, `[✅ Готово]`.
4.  **Interaction:**
    * **Change Mode:** User clicks `[Сменить режим]`. Buttons are replaced with mode choices (`[Комбинированный]`, `[Краткий]`, etc.). User clicks a mode button. Bot re-processes the *same* voice message with the *new* mode and sends an updated response message with the action buttons restored.
    * **Redo:** User clicks `[Переделать]`. Bot re-processes the *same* voice message using the *last selected* mode and sends an updated response message.
    * **History:** User clicks `[История]` or types `/history`. Bot responds with the most recent summary from history and provides pagination buttons (`[< Пред.]`, `[След. >]`) to navigate older summaries.
    * **Confirm:** User clicks `[✅ Готово]`. The bot edits its last message to remove the action buttons (`[Сменить режим]`, `[Переделать]`, `[История]`, `[✅ Готово]`).
5.  **Group Chats:** The flow is similar in group chats, but the bot processes voice messages sent by any member. Usage is tracked per user ID. Access is currently unrestricted.
6.  **Error:** If an error occurs (e.g., Gemini API unavailable, audio too long and chunking fails), the bot sends a user-friendly error message in Russian.

## 4. Core Features

* **Gemini Audio Processing & Summarization:**
    * Utilizes Gemini API (Flash/Pro) via `google-generativeai`.
    * Handles transcription and summarization tasks.
    * Supports multiple, distinct summary modes with a focus on comprehensiveness.
    * Handles potentially long audio via chunking strategies.
* **Cleaned Transcript Generation:**
    * Provides a transcript beyond raw output.
    * Includes removal of filler words (e.g., "эм", "ээ").
    * Applies punctuation and basic grammar correction.
    * Identifies potential ambiguities in names/terms and notes alternatives in brackets.
* **Interactive Telegram UI (Russian):**
    * All user-facing text is in Russian, managed via `locales.py`.
    * Uses Inline Keyboards for interactive buttons.
    * Provides clear workflows for changing modes, redoing summaries, and accessing history.
    * Includes a confirmation button (`✅`) to finalize interaction with a summary message.
    * Formats output messages clearly using MarkdownV2, including headers (User Name, Moscow Time) and code blocks for summaries/transcripts.
* **Summary History & Persistence:**
    * Stores generated summaries/transcripts associated with user/chat context in a Fly Postgres database.
    * Allows retrieval via `/history` command and an inline button.
    * Implements pagination for navigating history.
    * Default retention: Last 100 summaries per user/chat (configurable).
* **User & Usage Tracking:**
    * Identifies users via their Telegram User ID.
    * Tracks bot usage in group chats per user ID (for future monetization).
* **Existing Feature Retention:**
    * Maintains the original `mp3tovoice-bot` functionality of converting MP3 files sent as documents into Telegram voice messages.
* **Configuration & Deployment:**
    * Deployed on Fly.io using Docker.
    * Configuration (`BOT_TOKEN`, `GEMINI_API_KEY`, etc.) managed via Fly secrets.
    * Utilizes `fly.toml` for deployment configuration.

## 5. Tech Stack & Tools

* **Language:** Python (3.9+ recommended)
* **Core Libraries:**
    * `python-telegram-bot` (for Telegram API interaction)
    * `google-generativeai` (for Gemini API interaction)
    * `pydub` (for audio manipulation, chunking, format checks)
    * `python-dotenv` (for local development environment variables)
    * `asyncpg` or `SQLAlchemy` (for interacting with Fly Postgres)
    * `pytz` or `zoneinfo` (for Moscow timezone conversion)
* **Database:** Fly Postgres
* **Infrastructure:** Fly.io (App Hosting, Postgres Hosting)
* **Dependencies:** `ffmpeg` (required by `pydub`)
* **Version Control:** Git (e.g., GitHub, GitLab)
* **Development Environment:** VS Code / Cursor IDE recommended

## 6. Non-Functional Requirements

* **Performance:** Bot should be responsive, handling multiple simultaneous requests efficiently. Gemini API latency should be considered and communicated to the user (e.g., "Processing..." messages). Async architecture is crucial.
* **Scalability:** Architecture should leverage Fly.io's scaling capabilities to handle increasing load. Database interactions should be optimized.
* **Reliability:** Implement robust error handling for API calls, audio processing, and database operations. Provide informative feedback to users. Aim for high availability.
* **Security:** API keys and sensitive configuration must be stored securely using Fly secrets. Input validation should be performed where necessary.
* **Usability:** The Russian interface must be clear, intuitive, and follow standard Telegram bot interaction patterns.
* **Maintainability:** Code should be well-structured, commented, typed (using Python type hints), and follow PEP 8 guidelines. Use `locales.py` for all user-facing strings.

## 7. Constraints & Assumptions

* **Constraints:**
    * Dependent on Telegram Bot API features and limitations (message size, rate limits).
    * Dependent on Google Gemini API availability, quotas, pricing, and performance.
    * Must operate within the Fly.io platform constraints.
    * Audio processing complexity increases significantly with duration (chunking).
    * Timezone conversion relies on accurate system time or reliable timezone libraries.
    * Must extend the existing `mp3tovoice-bot` codebase.
* **Assumptions:**
    * Users have basic familiarity with Telegram bots and voice messages.
    * The existing `mp3tovoice-bot` codebase provides a suitable foundation.
    * Fly Postgres is sufficient for storing summary history and user tracking data.
    * Gemini API provides adequate quality for transcription and summarization tasks in Russian.
    * Initial free access to group features is acceptable.
    * Moscow Time is the single target timezone for display.

## 8. Known Issues & Potential Pitfalls

* **Gemini API Costs & Quotas:** Usage costs can escalate, and API quotas/rate limits might be hit under heavy load.
* **API Latency:** Gemini processing can take time, potentially leading to user perception of slowness. Clear feedback ("Processing...") is essential.
* **Summarization Accuracy/Bias:** AI summaries might occasionally miss nuances, misinterpret context, or exhibit biases. The "comprehensiveness" goal needs careful prompt engineering.
* **Transcription Errors:** Especially with noisy audio, uncommon names, or technical terms. The "careful spelling" requirement adds complexity.
* **Chunking Complexity:** Splitting audio accurately without losing context at boundaries for very long messages is challenging.
* **Telegram Rate Limits:** High traffic might trigger Telegram's rate limiting for sending messages or editing buttons.
* **Database Scalability:** Poorly optimized queries or schema could lead to performance issues as history grows.
* **Timezone Accuracy:** Incorrect server configuration or library issues could lead to inaccurate Moscow Time display.
* **Dependency Management:** Ensuring `ffmpeg` is correctly installed in the Docker container.

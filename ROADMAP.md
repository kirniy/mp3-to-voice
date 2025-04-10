# Voicio Bot Development Roadmap

This roadmap outlines the suggested stages for implementing improvements and new features for the Voicio Telegram bot, based on the `IMPROVEMENTS.md` document. It prioritizes stability, core functionality, and then advanced features.

## Stage 1: Foundational Stability & Efficiency

*Goal: Enhance bot reliability, performance, and reduce operational costs before adding major features.*

1.  **Enhance Error Handling:** Implement comprehensive and specific error handling across `bot.py` (message handlers, callbacks) and `gemini_utils.py`, including retries with backoff for API calls (Improvement 1.1).
2.  **Improve Resource Management:** Ensure robust `tempfile` cleanup and Gemini file deletion using `try...finally` (Improvement 1.4).
3.  **Address State Consistency:** Remove the in-memory language cache from `locales.py` and rely solely on `db_utils.py` (Improvement 1.3).
4.  **Optimize Gemini API Usage (Combine Calls):** Refactor `gemini_utils.py` to perform transcription and summarization in a single API call (High-Impact Improvement 2.1a). Requires significant prompt engineering and response parsing.
5.  **Refactor `button_callback`:** Break down the large function into smaller, maintainable helper functions (Improvement 2.5b).
6.  **Optimize Database Interactions:** Implement selective column fetching and review connection management (Improvements 1.2, 2.2a).
7.  **(Optional) Simplify Markdown Handling:** Evaluate replacing custom markdown functions with standard `python-telegram-bot` methods (Improvement 2.5a).

## Stage 2: Core Features - Subscription & Basic Admin Access

*Goal: Introduce monetization and basic administrative oversight.*

1.  **Implement Webhook Support:** Transition from polling to webhooks for better scalability. Requires adding a minimal web server (e.g., `aiohttp`, `FastAPI`) and updating `fly.toml` (Improvement 4.1).
2.  **Subscription System - Database Schema:** Design and implement database changes (`subscriptions` table, potentially linking users, tiers, start/end dates, chat limits, associated chat IDs) (Part of Improvement 4.7).
3.  **Subscription System - Chat Tracking:** Implement logic in `bot.py` (and potentially `db_utils.py`) to track which chats a subscribed user has added the bot to. Add checks in message handlers to enforce the chat limits based on the user's subscription tier (Part of Improvement 4.7).
4.  **Subscription System - Payment Integration (ЮKassa):** Integrate with ЮKassa via BotFather for handling payments. This involves setting up payment tokens, handling payment callbacks/invoices, and updating the user's subscription status in the database upon successful payment (Part of Improvement 4.7).
5.  **Basic Admin Telegram Mini App (TMA):**
    *   Set up the basic structure for a TMA.
    *   Implement admin authentication (e.g., check `user_id` against a predefined list).
    *   Display basic user information and subscription status fetched from the database (Initial part of Improvement 4.4).
6.  **Fly.io Scaling Preparation:** Configure horizontal scaling (`fly scale count N`), ensure continuous operation (`min_machines_running`), and add basic health checks in `fly.toml` (Improvement 3.2).

## Stage 3: Feature Expansion & Growth

*Goal: Enhance user experience, add growth mechanisms, and improve monitoring.*

1.  **Full Admin Dashboard Features (TMA):** Expand the Admin TMA to include detailed statistics: bot usage metrics, revenue tracking, user base overview, API/Fly.io cost estimations (if possible via logging/approximations) (Improvement 4.4).
2.  **Referral Program:** Implement the referral code generation, tracking, reward application logic, and necessary database changes (Improvement 4.9).
3.  **Improved History Management:** Add features like keyword search, date filtering, and potentially individual entry deletion (Improvement 4.2).
4.  **Usage Tracking & Limiting (Optional):** Implement detailed tracking of API calls/processing time per user/chat, potentially for future tiered limiting or analytics (Improvement 4.3).
5.  **Error Monitoring Service Integration:** Integrate Sentry or a similar service for automated production error reporting (Improvement 4.8).
6.  **Optimize Gemini File Handling:** Implement caching of `audio_file.uri` to potentially reuse uploads for actions like "Redo" (Improvement 2.1b).
7.  **Transcript Caching:** Implement caching for transcripts when users switch modes for the same audio file (Improvement 4.10).

## Stage 4: Continuous Improvement & Advanced Features

*Goal: Ongoing refinement, optimization, and potential addition of advanced capabilities.*

1.  **Enhanced Language Handling:** Implement language auto-detection and consider adopting standard localization libraries (Improvement 4.5).
2.  **Database Performance Tuning:** Regularly analyze query performance (`EXPLAIN ANALYZE`) under load and optimize indices or queries as needed (Improvement 2.2b).
3.  **Fly.io Monitoring & Scaling:** Continuously monitor resource usage and adjust vertical/horizontal scaling as the user base grows. Refine health checks (Improvement 3.2).
4.  **Explore Advanced Caching:** Consider implementing more sophisticated caching for database queries if bottlenecks appear (Improvement 2.2c).
5.  **A/B Testing & Feature Rollouts:** Implement mechanisms for testing new features or prompts with subsets of users.

*This roadmap is a suggestion and can be adapted based on development priorities and user feedback.* 
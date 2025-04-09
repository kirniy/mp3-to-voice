# Tech Stack Document: Gemini Voice Summarizer Telegram Bot

This document outlines the technology stack chosen for the Gemini Voice Summarizer Telegram Bot. The stack builds upon the existing `mp3tovoice-bot` foundation and incorporates technologies necessary for AI processing, data persistence, and robust operation within the Telegram environment, deployed on Fly.io.

## Core Technologies

* **Language & Runtime:**
    * **Python:** Version 3.9 or higher. Chosen for its mature ecosystem, excellent support for AI/ML libraries, and strong asynchronous capabilities.
* **Bot Framework:**
    * **`python-telegram-bot`:** The primary library for interacting with the Telegram Bot API. It provides an asynchronous, handler-based structure for receiving messages, handling commands, processing callbacks (inline buttons), and sending responses. Version 20+ (async-based) is required.
* **AI Integration:**
    * **`google-generativeai`:** The official Google Python client library for interacting with the Gemini API. Used for sending audio data for transcription and text prompts for summarization using models like Gemini Flash 2.0 or 2.5 Pro.
* **Audio Processing:**
    * **`pydub`:** Used for handling audio files. While Gemini might handle formats directly, `pydub` remains essential for the original MP3->Voice feature and will be crucial if audio chunking (splitting large files) or pre-processing is required before sending to Gemini.
    * **`ffmpeg`:** A core multimedia framework. It's a system dependency required by `pydub` for handling various audio formats and conversions. Must be available in the deployment environment (Docker container).

## Database

* **Fly Postgres:** A managed PostgreSQL service provided by Fly.io. Chosen for:
    * **Persistence:** Storing summary history, user information (Telegram ID), and group chat usage tracking data.
    * **Querying:** Relational structure allows for efficient querying of history (e.g., pagination, filtering by user/chat).
    * **Scalability:** Managed service that can scale with the application.
    * **Integration:** Easily attached to Fly apps.
* **Database Interaction:**
    * **`asyncpg`:** Recommended high-performance asynchronous library for interacting with PostgreSQL from Python's `asyncio` environment, suitable for `python-telegram-bot`.
    * Alternatively, **`SQLAlchemy`** (with async support) could be used for an ORM approach, potentially beneficial if data structures become more complex or for a future admin dashboard.

## Infrastructure and Deployment

* **Hosting Platform:**
    * **Fly.io:** Used for deploying the bot application container and hosting the Fly Postgres database. Provides global distribution, secret management, and infrastructure-as-code configuration via `fly.toml`.
* **Containerization:**
    * **Docker:** The bot application is packaged into a Docker container for consistent deployment. The `Dockerfile` defines the environment, installs Python, system dependencies (`ffmpeg`), Python libraries (`requirements.txt`), and specifies the entry point (`python bot.py`).
* **Configuration Management:**
    * **`fly.toml`:** Defines the Fly app configuration (name, builder, environment variables, deployment strategy, processes, services, Postgres attachment).
    * **Fly Secrets:** Used to securely store sensitive information like `BOT_TOKEN`, `GEMINI_API_KEY`, and database credentials. Accessed as environment variables within the running container.

## Third-Party Services & APIs

* **Telegram Bot API:** The fundamental interface for all bot interactions.
* **Google Cloud Platform (Gemini API):** Provides the AI capabilities for transcription and summarization. Requires API key and potentially billing setup.

## Development Tools & Practices

* **Version Control:** Git, hosted on platforms like GitHub or GitLab.
* **Local Development:**
    * `python-dotenv`: To manage local environment variables (API keys, tokens) in a `.env` file, mimicking Fly secrets.
    * Virtual Environments (`venv`, `conda`): To isolate project dependencies.
* **IDE:** VS Code / Cursor recommended, with Python extensions for linting, formatting, debugging, and type checking.
* **Code Quality:**
    * PEP 8 compliance (using tools like `flake8` or `black`).
    * Type Hinting (using Python's `typing` module, checked with `mypy`).
    * Logging (using Python's built-in `logging` module).

## Security Considerations

* **API Key Management:** Never commit secrets to Git. Use Fly secrets for deployment and `.env` files (added to `.gitignore`) for local development.
* **Input Validation:** Sanitize or validate user input where necessary, although primary interaction is via voice/buttons. Be mindful of potential abuse patterns.
* **Rate Limiting:** Implement bot-side rate limiting if necessary to prevent user spam. Be prepared to handle Telegram API rate limits and Gemini API quotas/limits gracefully (e.g., exponential backoff).
* **Database Security:** Use strong database credentials managed via Fly secrets. Configure appropriate access controls if multiple services interact with the DB (though likely only the bot needs access initially).

## Performance Considerations

* **Asynchronous Operations:** Leverage `asyncio` throughout the application (provided by `python-telegram-bot` and `asyncpg`) to handle concurrent users and I/O-bound tasks (API calls, DB operations) efficiently.
* **Gemini API Latency:** Acknowledge that AI processing takes time. Provide immediate feedback to the user ("Processing...") and manage expectations. Avoid blocking the main bot loop during long API calls.
* **Database Queries:** Optimize database queries for history retrieval, especially pagination. Use indexes appropriately.
* **Audio Chunking:** If implemented, chunking logic needs to be efficient to avoid adding significant overhead to processing time.
* **Background Tasks:** For potentially very long processing tasks (long audio, complex analysis), consider using a background task queue (e.g., Celery with Redis/RabbitMQ, or simpler solutions like `arq`) although this adds complexity. Assess if needed based on typical Gemini API response times.

## Fly.io Implementation Details

* **`fly.toml`:**
    * `app`: Unique application name.
    * `build`: Specifies Dockerfile builder.
    * `env`: Non-sensitive environment variables (e.g., `TZ=Europe/Moscow` if needed system-wide, though Python's `zoneinfo` is preferred). Secrets are managed separately.
    * `processes`: Defines the command to run the bot (e.g., `app = "python bot.py"`).
    * `services`: Defines network exposure (usually minimal for a bot unless an admin dashboard webhook is added later). Health checks can be configured here.
    * Postgres attachment configuration.
* **`Dockerfile`:**
    * Starts from a suitable Python base image (e.g., `python:3.11-slim`).
    * Installs system dependencies (`apt-get update && apt-get install -y ffmpeg`).
    * Copies `requirements.txt` and installs Python dependencies (`pip install --no-cache-dir -r requirements.txt`).
    * Copies the application code.
    * Sets the `CMD` or `ENTRYPOINT` to run the bot script.

## Conclusion and Overall Tech Stack Summary

This tech stack provides a robust and scalable foundation for the Gemini Voice Summarizer Bot. It leverages Python's strengths for bot development and AI integration, utilizes the asynchronous capabilities of `python-telegram-bot` and `asyncpg` for performance, relies on the powerful Gemini API for its core functionality, and uses Fly.io with Docker and Postgres for reliable deployment and data persistence. The stack is well-suited to handle the project's requirements while building upon the existing bot's structure.

import logging
import asyncpg
from datetime import datetime

logger = logging.getLogger(__name__)

async def create_tables(pool: asyncpg.Pool):
    """Creates the necessary database tables if they don't exist."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    original_telegram_message_id BIGINT NOT NULL,
                    summary_telegram_message_id BIGINT, -- Can be null initially or if sending fails
                    telegram_audio_file_id TEXT NOT NULL,
                    mode VARCHAR(50) NOT NULL, -- e.g., 'brief', 'detailed', 'transcript'
                    summary_text TEXT,
                    transcript_text TEXT, -- Store cleaned transcript separately if needed
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # Add index for faster history lookups
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_summaries_user_chat_created ON summaries (user_id, chat_id, created_at DESC);
            """)
            # Add index for looking up by the bot's message ID (for button callbacks)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_summaries_summary_message_id ON summaries (summary_telegram_message_id);
            """)
            # Add trigger to update updated_at timestamp
            await connection.execute("""
                CREATE OR REPLACE FUNCTION update_updated_at_column()
                RETURNS TRIGGER AS $$
                BEGIN
                   NEW.updated_at = NOW(); 
                   RETURN NEW;
                END;
                $$ language 'plpgsql';
            """)
            await connection.execute("""
                DROP TRIGGER IF EXISTS update_summaries_updated_at ON summaries;
                CREATE TRIGGER update_summaries_updated_at
                BEFORE UPDATE ON summaries
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            # Create chat_preferences table for storing default mode and language settings
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS chat_preferences (
                    chat_id BIGINT PRIMARY KEY,
                    default_mode VARCHAR(50) NOT NULL,
                    language VARCHAR(10) NOT NULL DEFAULT 'ru',
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            # Добавляем проверку на существование столбца language и добавляем его, если отсутствует
            await connection.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS(
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='chat_preferences' AND column_name='language'
                    ) THEN
                        ALTER TABLE chat_preferences ADD COLUMN language VARCHAR(10) NOT NULL DEFAULT 'ru';
                    END IF;
                END $$;
            """)
            
            # Create user_preferences table for storing user settings like language
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id BIGINT PRIMARY KEY,
                    language VARCHAR(10) NOT NULL DEFAULT 'ru',
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            logger.info("Database tables checked/created successfully.")
        except Exception as e:
            logger.error(f"Error creating database tables: {e}", exc_info=True)
            # Re-raise the exception to potentially halt startup if DB is critical
            raise

async def save_summary(
    pool: asyncpg.Pool,
    user_id: int,
    chat_id: int,
    original_message_id: int,
    summary_message_id: int | None,
    audio_file_id: str,
    mode: str,
    summary_text: str | None,
    transcript_text: str | None = None,
) -> int | None:
    """Saves summary details to the database. Returns the new record ID or None on failure."""
    async with pool.acquire() as connection:
        try:
            record_id = await connection.fetchval("""
                INSERT INTO summaries (user_id, chat_id, original_telegram_message_id, summary_telegram_message_id, telegram_audio_file_id, mode, summary_text, transcript_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id;
            """, user_id, chat_id, original_message_id, summary_message_id, audio_file_id, mode, summary_text, transcript_text)
            logger.info(f"Saved summary record with ID: {record_id} for user {user_id}, original msg {original_message_id}")
            return record_id
        except Exception as e:
            logger.error(f"Error saving summary to database: {e}", exc_info=True)
            return None

async def get_summary_context_for_callback(pool: asyncpg.Pool, original_message_id: int, chat_id: int) -> asyncpg.Record | None:
    """Fetches the context needed for button callbacks using the original message ID."""
    async with pool.acquire() as connection:
        # Fetch the latest record for this original message in this chat
        # This assumes original_message_id might not be unique across chats if bot is reused
        # If original_message_id is globally unique (within the bot's scope), chat_id is not strictly needed here
        # Ordering by ID DESC ensures we get the latest record if multiple exist (shouldn't happen in normal flow)
        return await connection.fetchrow("""
            SELECT id, user_id, chat_id, original_telegram_message_id, summary_telegram_message_id, 
                   telegram_audio_file_id, mode, summary_text, transcript_text
            FROM summaries 
            WHERE original_telegram_message_id = $1 AND chat_id = $2
            ORDER BY id DESC
            LIMIT 1;
            """, original_message_id, chat_id)

async def update_summary_mode_and_text(
    pool: asyncpg.Pool, 
    record_id: int, 
    new_mode: str, 
    new_summary_text: str | None, 
    new_transcript_text: str | None
):
    """Updates the mode and text of a summary record."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                UPDATE summaries
                SET mode = $1, summary_text = $2, transcript_text = $3
                WHERE id = $4;
            """, new_mode, new_summary_text, new_transcript_text, record_id)
            logger.info(f"Updated summary record {record_id} to mode '{new_mode}'")
            return True
        except Exception as e:
            logger.error(f"Error updating summary record {record_id}: {e}", exc_info=True)
            return False

# --- User preferences functions ---

async def get_user_language(pool: asyncpg.Pool, user_id: int, default_language: str = 'ru') -> str:
    """Gets the language preference for a user."""
    async with pool.acquire() as connection:
        try:
            language = await connection.fetchval("""
                SELECT language FROM user_preferences
                WHERE user_id = $1;
            """, user_id)
            
            # Return the stored language or the default if not found
            return language or default_language
        except Exception as e:
            logger.error(f"Error getting language for user {user_id}: {e}", exc_info=True)
            return default_language

async def set_user_language(pool: asyncpg.Pool, user_id: int, language: str) -> bool:
    """Sets the language preference for a user."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                INSERT INTO user_preferences (user_id, language, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET language = $2, updated_at = NOW();
            """, user_id, language)
            logger.info(f"Set language for user {user_id} to '{language}'")
            return True
        except Exception as e:
            logger.error(f"Error setting language for user {user_id}: {e}", exc_info=True)
            return False

# --- Chat preferences functions ---

async def get_chat_default_mode(pool: asyncpg.Pool, chat_id: int, default_mode: str = "brief") -> str:
    """Gets the default mode for a chat."""
    async with pool.acquire() as connection:
        try:
            mode = await connection.fetchval("""
                SELECT default_mode FROM chat_preferences
                WHERE chat_id = $1;
            """, chat_id)
            
            # Return the stored mode or the default if not found
            return mode or default_mode
        except Exception as e:
            logger.error(f"Error getting default mode for chat {chat_id}: {e}", exc_info=True)
            return default_mode

async def set_chat_default_mode(pool: asyncpg.Pool, chat_id: int, mode: str) -> bool:
    """Sets the default mode for a chat."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                INSERT INTO chat_preferences (chat_id, default_mode, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET default_mode = $2, updated_at = NOW();
            """, chat_id, mode)
            logger.info(f"Set default mode for chat {chat_id} to '{mode}'")
            return True
        except Exception as e:
            logger.error(f"Error setting default mode for chat {chat_id}: {e}", exc_info=True)
            return False

async def get_chat_language(pool: asyncpg.Pool, chat_id: int, default_language: str = 'ru') -> str:
    """Gets the language preference for a chat."""
    async with pool.acquire() as connection:
        try:
            language = await connection.fetchval("""
                SELECT language FROM chat_preferences
                WHERE chat_id = $1;
            """, chat_id)
            
            # Return the stored language or the default if not found
            return language or default_language
        except Exception as e:
            logger.error(f"Error getting language for chat {chat_id}: {e}", exc_info=True)
            return default_language

async def set_chat_language(pool: asyncpg.Pool, chat_id: int, language: str) -> bool:
    """Sets the language preference for a chat."""
    async with pool.acquire() as connection:
        try:
            # Check if the chat already has a record
            existing_record = await connection.fetchrow("""
                SELECT chat_id, default_mode FROM chat_preferences
                WHERE chat_id = $1;
            """, chat_id)
            
            if existing_record:
                # Update only the language field if record exists
                await connection.execute("""
                    UPDATE chat_preferences
                    SET language = $2, updated_at = NOW()
                    WHERE chat_id = $1;
                """, chat_id, language)
            else:
                # Insert new record with default mode if it doesn't exist
                await connection.execute("""
                    INSERT INTO chat_preferences (chat_id, default_mode, language, updated_at)
                    VALUES ($1, 'brief', $2, NOW());
                """, chat_id, language)
            
            logger.info(f"Set language for chat {chat_id} to '{language}'")
            return True
        except Exception as e:
            logger.error(f"Error setting language for chat {chat_id}: {e}", exc_info=True)
            return False

async def clear_chat_default_mode(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Clears the default mode setting for a chat."""
    async with pool.acquire() as connection:
        try:
            # Use the system default mode instead of completely removing the record
            # to preserve other settings like language
            await connection.execute("""
                UPDATE chat_preferences
                SET default_mode = 'brief', updated_at = NOW()
                WHERE chat_id = $1;
            """, chat_id)
            logger.info(f"Reset default mode for chat {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error resetting default mode for chat {chat_id}: {e}", exc_info=True)
            return False

# --- History functions ---

async def get_user_history(pool: asyncpg.Pool, user_id: int, chat_id: int, limit: int = 5, offset: int = 0) -> tuple[list[asyncpg.Record], int]:
    """Retrieves paginated summary history for a user in a specific chat.

    Args:
        pool: The database connection pool.
        user_id: The Telegram User ID.
        chat_id: The Telegram Chat ID.
        limit: Maximum number of records per page.
        offset: Number of records to skip for pagination.

    Returns:
        A tuple containing: 
         - A list of asyncpg.Record objects for the history items.
         - The total count of history items for this user/chat.
    """
    async with pool.acquire() as connection:
        try:
            # Get the total count first for pagination calculation
            total_count = await connection.fetchval("""
                SELECT COUNT(*) FROM summaries
                WHERE user_id = $1 AND chat_id = $2;
            """, user_id, chat_id)

            if total_count == 0:
                return [], 0

            # Fetch the paginated records
            records = await connection.fetch("""
                SELECT id, original_telegram_message_id, summary_telegram_message_id, mode, summary_text, transcript_text, created_at
                FROM summaries
                WHERE user_id = $1 AND chat_id = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4;
            """, user_id, chat_id, limit, offset)
            
            logger.info(f"Fetched {len(records)} history records for user {user_id}, chat {chat_id} (offset={offset}, limit={limit}, total={total_count})")
            return records, total_count
        except Exception as e:
            logger.error(f"Error fetching history for user {user_id}, chat {chat_id}: {e}", exc_info=True)
            return [], 0 
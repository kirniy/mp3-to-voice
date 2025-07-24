import logging
import asyncpg
from datetime import datetime
from model_config import DEFAULT_PROTOCOL, DEFAULT_TRANSCRIPTION_MODEL, DEFAULT_PROCESSING_MODEL, DEFAULT_DIRECT_MODEL

logger = logging.getLogger(__name__)

async def create_tables(pool: asyncpg.Pool) -> None:
    """Creates necessary database tables if they don't exist."""
    async with pool.acquire() as connection:
        try:
            # Summaries table stores voice message transcriptions and summaries
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    original_telegram_message_id BIGINT NOT NULL,
                    summary_telegram_message_id BIGINT NOT NULL,
                    telegram_audio_file_id TEXT NOT NULL,
                    telegram_video_file_id TEXT,
                    mode TEXT NOT NULL,
                    transcript_text TEXT NOT NULL,
                    summary_text TEXT,
                    summary_brief TEXT,
                    summary_detailed TEXT,
                    summary_bullet TEXT,
                    summary_combined TEXT,
                    summary_pasha TEXT,
                    summary_diagram TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)
            
            # Add indexes for faster querying
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS summaries_user_id_idx ON summaries(user_id);
                CREATE INDEX IF NOT EXISTS summaries_chat_id_idx ON summaries(chat_id);
                CREATE INDEX IF NOT EXISTS summaries_original_message_id_idx ON summaries(original_telegram_message_id);
                CREATE INDEX IF NOT EXISTS summaries_created_at_idx ON summaries(created_at);
            """)
            
            # Chat preferences for storing chat-specific settings
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS chat_preferences (
                    chat_id BIGINT PRIMARY KEY,
                    default_mode TEXT DEFAULT 'brief',
                    language TEXT DEFAULT 'ru',
                    is_paused BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)
            
            # User preferences for storing user-specific settings
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id BIGINT PRIMARY KEY,
                    language TEXT DEFAULT 'ru',
                    protocol TEXT DEFAULT 'direct',
                    direct_model TEXT DEFAULT 'gemini-2.0-flash',
                    transcription_model TEXT DEFAULT 'gemini-2.5-flash',
                    processing_model TEXT DEFAULT 'gemini-2.5-flash',
                    thinking_budget_level TEXT DEFAULT 'medium',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)
            
            # Automatically update 'updated_at' timestamp when records are updated
            await connection.execute("""
                CREATE OR REPLACE FUNCTION update_updated_at_column()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$ LANGUAGE 'plpgsql';
            """)
            
            # Add the trigger to each table with 'updated_at' column
            await connection.execute("""
                DROP TRIGGER IF EXISTS update_summaries_updated_at ON summaries;
                CREATE TRIGGER update_summaries_updated_at
                BEFORE UPDATE ON summaries
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            await connection.execute("""
                DROP TRIGGER IF EXISTS update_chat_preferences_updated_at ON chat_preferences;
                CREATE TRIGGER update_chat_preferences_updated_at
                BEFORE UPDATE ON chat_preferences
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            await connection.execute("""
                DROP TRIGGER IF EXISTS update_user_preferences_updated_at ON user_preferences;
                CREATE TRIGGER update_user_preferences_updated_at
                BEFORE UPDATE ON user_preferences
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            # Add is_paused column if it doesn't exist
            try:
                await connection.execute("""
                    ALTER TABLE chat_preferences 
                    ADD COLUMN IF NOT EXISTS is_paused BOOLEAN DEFAULT FALSE;
                """)
            except Exception as e:
                logger.warning(f"Error adding is_paused column (likely already exists): {e}")
                
            # Add summary_diagram column if it doesn't exist
            try:
                await connection.execute("""
                    ALTER TABLE summaries 
                    ADD COLUMN IF NOT EXISTS summary_diagram TEXT;
                """)
                logger.info("Added summary_diagram column to summaries table or verified it exists")
            except Exception as e:
                logger.warning(f"Error adding summary_diagram column (likely already exists): {e}")
                
            # Add model configuration columns to user_preferences if they don't exist
            try:
                await connection.execute("""
                    ALTER TABLE user_preferences 
                    ADD COLUMN IF NOT EXISTS protocol TEXT DEFAULT 'direct';
                """)
                await connection.execute("""
                    ALTER TABLE user_preferences 
                    ADD COLUMN IF NOT EXISTS direct_model TEXT DEFAULT 'gemini-2.0-flash';
                """)
                await connection.execute("""
                    ALTER TABLE user_preferences 
                    ADD COLUMN IF NOT EXISTS transcription_model TEXT DEFAULT 'gemini-2.5-flash';
                """)
                await connection.execute("""
                    ALTER TABLE user_preferences 
                    ADD COLUMN IF NOT EXISTS processing_model TEXT DEFAULT 'gemini-2.5-flash';
                """)
                await connection.execute("""
                    ALTER TABLE user_preferences 
                    ADD COLUMN IF NOT EXISTS thinking_budget_level TEXT DEFAULT 'medium';
                """)
                logger.info("Added model configuration columns to user_preferences table")
            except Exception as e:
                logger.warning(f"Error adding model configuration columns (likely already exist): {e}")
                
            # Add telegram_video_file_id column if it doesn't exist
            try:
                await connection.execute("""
                    ALTER TABLE summaries 
                    ADD COLUMN IF NOT EXISTS telegram_video_file_id TEXT;
                """)
                logger.info("Added telegram_video_file_id column to summaries table or verified it exists")
            except Exception as e:
                logger.warning(f"Error adding telegram_video_file_id column (likely already exists): {e}")

            logger.info("Database tables created or verified")
            
        except Exception as e:
            logger.error(f"Error creating database tables: {e}", exc_info=True)
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
    video_file_id: str | None = None,
) -> int | None:
    """Saves summary details to the database. Returns the new record ID or None on failure."""
    async with pool.acquire() as connection:
        try:
            record_id = await connection.fetchval("""
                INSERT INTO summaries (user_id, chat_id, original_telegram_message_id, summary_telegram_message_id, telegram_audio_file_id, mode, summary_text, transcript_text, telegram_video_file_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id;
            """, user_id, chat_id, original_message_id, summary_message_id, audio_file_id, mode, summary_text, transcript_text, video_file_id)
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

async def update_summary_diagram_and_message_id(
    pool: asyncpg.Pool, 
    record_id: int, 
    new_message_id: int, 
    diagram_data: str | None
):
    """Updates the message ID and diagram data for a diagram summary record."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                UPDATE summaries
                SET summary_telegram_message_id = $1, summary_diagram = $2
                WHERE id = $3;
            """, new_message_id, diagram_data, record_id)
            logger.info(f"Updated summary record {record_id} with new message ID {new_message_id} and diagram data")
            return True
        except Exception as e:
            logger.error(f"Error updating summary record {record_id} with diagram data: {e}", exc_info=True)
            return False

async def update_summary_message_id(
    pool: asyncpg.Pool,
    record_id: int,
    new_message_id: int
):
    """Updates only the summary_telegram_message_id for a record."""
    async with pool.acquire() as connection:
        try:
            await connection.execute("""
                UPDATE summaries
                SET summary_telegram_message_id = $1
                WHERE id = $2;
            """, new_message_id, record_id)
            logger.info(f"Updated summary record {record_id} with new message ID {new_message_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating message ID for summary record {record_id}: {e}", exc_info=True)
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
            logger.info(f"Set default mode for chat {chat_id} to {mode}")
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

async def get_chat_paused_status(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Gets the paused status for a chat.
    
    Args:
        pool: The database connection pool.
        chat_id: The Telegram Chat ID.
        
    Returns:
        True if the chat is paused, False otherwise.
    """
    async with pool.acquire() as connection:
        try:
            is_paused = await connection.fetchval("""
                SELECT is_paused FROM chat_preferences
                WHERE chat_id = $1;
            """, chat_id)
            
            # Return True if is_paused is explicitly set to True, otherwise False
            return is_paused is True
        except Exception as e:
            logger.error(f"Error getting paused status for chat {chat_id}: {e}", exc_info=True)
            return False  # Default to not paused if there's an error

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

            # Fetch the paginated records, including user_id
            records = await connection.fetch("""
                SELECT id, user_id, original_telegram_message_id, summary_telegram_message_id, mode, summary_text, transcript_text, created_at
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

async def delete_chat_history(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Deletes all summary records for a specific chat."""
    try:
        async with pool.acquire() as connection:
            result = await connection.execute("""
                DELETE FROM summaries WHERE chat_id = $1;
            """, chat_id)
            logger.info(f"Deleted history for chat {chat_id}. Result: {result}")
            return True
    except Exception as e:
        logger.error(f"Error deleting history for chat {chat_id}: {e}", exc_info=True)
        return False

async def get_all_chat_history(pool: asyncpg.Pool, chat_id: int) -> list[asyncpg.Record]:
    """Fetches all summary records for a specific chat, ordered by creation date."""
    try:
        async with pool.acquire() as connection:
            records = await connection.fetch("""
                SELECT user_id, mode, summary_text, transcript_text, created_at 
                FROM summaries 
                WHERE chat_id = $1 
                ORDER BY created_at ASC;
            """, chat_id)
            logger.info(f"Fetched {len(records)} history records for export for chat {chat_id}")
            return records
    except Exception as e:
        logger.error(f"Error fetching all history for export for chat {chat_id}: {e}", exc_info=True)
        return []

async def get_user_model_preference(pool: asyncpg.Pool, user_id: int, preference_key: str) -> str:
    """Get a specific model preference for a user."""
    from model_config import (
        DEFAULT_PROTOCOL, DEFAULT_DIRECT_MODEL, 
        DEFAULT_TRANSCRIPTION_MODEL, DEFAULT_PROCESSING_MODEL
    )
    
    # Default values
    defaults = {
        "protocol": DEFAULT_PROTOCOL,
        "direct_model": DEFAULT_DIRECT_MODEL,
        "transcription_model": DEFAULT_TRANSCRIPTION_MODEL,
        "processing_model": DEFAULT_PROCESSING_MODEL,
        "thinking_budget_level": "medium"
    }
    
    if preference_key not in defaults:
        logger.error(f"Unknown preference key: {preference_key}")
        return None
    
    try:
        async with pool.acquire() as connection:
            # Get the preference value
            value = await connection.fetchval(f"""
                SELECT {preference_key} FROM user_preferences
                WHERE user_id = $1;
            """, user_id)
            
            # Return value or default
            return value if value is not None else defaults[preference_key]
    except Exception as e:
        logger.error(f"Error getting model preference {preference_key} for user {user_id}: {e}", exc_info=True)
        return defaults[preference_key]

async def set_user_model_preference(pool: asyncpg.Pool, user_id: int, preference_key: str, value: str) -> bool:
    """Set a specific model preference for a user."""
    valid_keys = ["protocol", "direct_model", "transcription_model", "processing_model", "thinking_budget_level"]
    
    if preference_key not in valid_keys:
        logger.error(f"Invalid preference key: {preference_key}")
        return False
    
    try:
        async with pool.acquire() as connection:
            # Update or insert the preference
            await connection.execute(f"""
                INSERT INTO user_preferences (user_id, {preference_key})
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                SET {preference_key} = $2;
            """, user_id, value)
            
            logger.info(f"Updated {preference_key} to {value} for user {user_id}")
            return True
    except Exception as e:
        logger.error(f"Error setting model preference {preference_key} for user {user_id}: {e}", exc_info=True)
        return False 
# App Flow Document: Gemini Voice Summarizer Telegram Bot

## Onboarding and Initial Interaction
Users interact with the bot primarily by sending it voice messages. There is no formal sign-up or login process; the bot identifies users via their unique Telegram User ID. Users can start a direct chat with the bot or add it to a group chat. In the initial phase, adding the bot to group chats is unrestricted, though usage is tracked. The bot also retains its current ability to process MP3 files sent as documents for conversion to voice messages.

## Main Interaction Loop: Voice Message Summarization

1.  **Receive Voice Message:** The user sends a voice message in a direct chat or a group chat where the bot is present.
2.  **Acknowledge & Process:** The bot immediately sends a temporary status message like "Обрабатываю ваше голосовое сообщение..." and potentially uses Telegram's 'typing' or 'recording audio' status indicator. It downloads the audio and sends it to the Google Gemini API for transcription and default summarization (Mode: "Краткий").
3.  **Send Summary:** Once processing is complete, the bot replaces the status message (or sends a new message) containing:
    * **Header:** The sender's display name (obtained from the Telegram message object) and the original message's timestamp, converted to Moscow Time (e.g., "Иван Петров - 09.04.2025 20:31 МСК").
    * **Content:** The generated "Краткий" summary, enclosed within a ```code block``` for easy copying. The summary text itself uses MarkdownV2 for any internal formatting (like lists).
    * **Inline Buttons:** A row of buttons appears below the message: `[Сменить режим]`, `[Переделать]`, `[История]`, `[✅ Готово]`.

## Detailed Feature Flows and Page Transitions

* **Changing Summary Mode:**
    1.  User clicks the `[Сменить режим]` button attached to a summary message.
    2.  The bot edits the message, replacing the initial buttons with a row of mode selection buttons: `[Комбинированный]`, `[Краткий]`, `[Подробный]`, `[Тезисный]`, `[Транскрипт]`.
    3.  User clicks one of the mode buttons (e.g., `[Подробный]`).
    4.  The bot re-processes the *original voice message* associated with this interaction using the newly selected mode ("Подробный").
    5.  The bot edits the message again, updating the content in the code block with the new summary and restoring the original action buttons: `[Сменить режим]`, `[Переделать]`, `[История]`, `[✅ Готово]`.

* **Redoing Summary:**
    1.  User clicks the `[Переделать]` button.
    2.  The bot re-processes the *original voice message* using the *mode that was last used* to generate the currently displayed summary.
    3.  The bot edits the message, updating the content in the code block with the potentially slightly different summary result and ensuring the action buttons are present.

* **Confirming Summary:**
    1.  User clicks the `[✅ Готово]` button.
    2.  The bot edits the message one last time, removing the entire row of action buttons (`[Сменить режим]`, `[Переделать]`, `[История]`, `[✅ Готово]`). The summary remains visible.

* **Accessing History:**
    1.  **Via Button:** User clicks the `[История]` button.
    2.  **Via Command:** User sends the `/history` command.
    3.  The bot responds with the most recent summary stored for that user/chat context.
    4.  The history message includes pagination buttons like `[< Пред. (1/15)]` and `[След. > (3/15)]` (if applicable).
    5.  Clicking pagination buttons allows the user to navigate through their stored summaries one by one. The message content updates to show the selected historical summary.

* **MP3 to Voice Conversion (Existing Flow):**
    1.  User sends an MP3 file as a document.
    2.  Bot validates type and size.
    3.  Bot downloads the MP3, converts it to Ogg Opus using `pydub`.
    4.  Bot replies with the audio as a native Telegram voice message. (This flow does *not* involve Gemini).

## Settings and Account Management
There are no user-facing account management screens within the bot itself in the initial phase. User identity is managed solely via Telegram User IDs. Future development includes an admin panel (out of scope initially) which would allow administrators to manage settings, potentially including the toggle for group chat access.

## Error States and Alternate Paths

* **API Errors:** If the Gemini API call fails (e.g., network issue, quota exceeded, invalid request), the bot sends a message like: "К сожалению, произошла ошибка при обработке вашего запроса к AI. Пожалуйста, попробуйте позже. [Код ошибки: GEM-500]" (Error message text TBD).
* **Audio Too Long/Large:** If audio exceeds Telegram limits or internal processing limits (even with chunking attempts), the bot responds: "Это аудио слишком длинное/большое для обработки. Пожалуйста, попробуйте отправить более короткое сообщение." (Error message text TBD).
* **Unsupported Format (for MP3->Voice):** If a non-MP3/WAV file is sent for the *original* conversion feature: "Пожалуйста, отправьте файл в формате MP3 или WAV."
* **Timeout:** If processing takes excessively long, the bot might send an update ("Все еще работаю...") or eventually time out with an error message.
* **Rate Limits:** If Telegram rate limits are hit, the bot might become temporarily unresponsive or fail to send/edit messages. Error handling should attempt retries where appropriate (e.g., using tenacity library).

The bot always aims to provide a response, even if it's an error message, rather than failing silently.

## Conclusion and Overall App Journey
The user's journey centers around sending voice messages and receiving intelligent summaries. The interaction model uses standard Telegram inline buttons for intuitive control over summarization modes and history access. The flow is designed to be quick and efficient, providing immediate value through the default summary and offering deeper analysis via different modes or the cleaned transcript upon request. Error handling aims to be informative, guiding the user when issues arise. The retention of the original MP3 conversion feature provides continuity while the new Gemini-powered summarization significantly enhances the bot's capabilities.

Telegram Bot API Markdown Formatting: Specification and Usage Guide (March 2025)
1. Introduction
Telegram bots have become integral tools for automation, information delivery, and interactive services within the Telegram ecosystem. A key aspect of creating effective and user-friendly bot interactions is the ability to format messages, enhancing readability and visual appeal. The Telegram Bot API provides mechanisms for developers to apply text formatting using specific markup styles. This report provides a comprehensive technical guide detailing how Markdown formatting is handled by the Telegram Bot API, specifically focusing on the specifications expected to be valid in March 2025. It outlines the supported Markdown modes, the precise syntax for various formatting elements within the primary MarkdownV2 mode, rules for escaping special characters, and relevant API versioning information based on official documentation and recent updates preceding the target date.   

2. Locating Official Telegram Bot API Documentation
The definitive source for information regarding the Telegram Bot API is the official documentation hosted on the Telegram Core website. While various third-party resources, libraries, and wrappers exist , the primary reference for API methods, types, parameters, and behavior, including message formatting, is found at https://core.telegram.org/bots/api. This site details the HTTP-based interface designed for developers building bots. Developers should prioritize this official source, supplemented by the official Bot API changelog (https://core.telegram.org/bots/api-changelog ) and the official news channel (@BotNews ) for the most current information. Obtaining an API token via @BotFather is the prerequisite for interacting with the API.   

3. Message Formatting Overview: The parse_mode Parameter
The Telegram Bot API allows developers to format text within messages sent via methods like sendMessage, editMessageText, etc. This is controlled using the optional parse_mode parameter in the API request. When this parameter is included, the Telegram servers parse the text for supported formatting entities. As of the documentation relevant for March 2025, the Bot API supports three values for the parse_mode parameter:   

MarkdownV2: An enhanced version of Markdown with support for a wider range of formatting options, including nested entities, underline, strikethrough, spoilers, blockquotes, and custom emoji. It requires careful escaping of special characters.   
HTML: Allows formatting using a subset of HTML tags. This mode is outside the scope of this report.   
Markdown: A legacy Markdown mode with a more limited set of supported syntax and simpler escaping rules. It is maintained primarily for backward compatibility.   
If the parse_mode parameter is omitted, the message text is treated as plain text, and no formatting is applied. Incorrectly formatted text within a specified parse_mode may result in the message being sent as plain text or potentially trigger an API error.

4. MarkdownV2 Style (parse_mode: 'MarkdownV2')
For developers seeking the richest formatting capabilities, MarkdownV2 is the recommended mode. It offers a comprehensive set of styling options beyond basic bold and italics. To utilize this mode, the parse_mode parameter in the API call must be explicitly set to the string MarkdownV2.   

4.1. Supported MarkdownV2 Syntax Elements
The following table details the specific syntax elements supported by MarkdownV2 as documented in the API specification relevant for March 2025. Adherence to this exact syntax is crucial for correct rendering in Telegram clients.   

Feature	Syntax	Example	Notes
Bold	*bold \*text*	*This is bold*	Requires asterisks *. Inner * must be escaped.
Italic	_italic \*text_	_This is italic_	Requires underscores _. Inner _ must be escaped.
Underline	__underline__	__This is underlined__	Requires double underscores __.
Strikethrough	~strikethrough~	~This is strikethrough~	Requires tildes ~.
Spoiler	`	spoiler	`
Inline Link	[link text](URL)	(https://telegram.org)	Square brackets for text, parentheses for URL. ) and \ inside URL must be escaped.
Inline User Mention	[mention text](tg://user?id=USER_ID)	[Mention User](tg://user?id=123456789)	USER_ID must be a valid integer user identifier.
Custom Emoji	![alt text](tg://emoji?id=CUSTOM_EMOJI_ID)	![👍](tg://emoji?id=5368324170671202286)	CUSTOM_EMOJI_ID is the unique ID of a custom emoji. alt text is required as fallback. ) and \ inside definition must be escaped.
Inline Code	`inline code`	`print("Hello")`	Requires single backticks `. ` and \ inside must be escaped.
Code Block (Pre)	\ncode block\n	\nfunction greet() {\n console.log("Hi!");\n}\n	Requires triple backticks ```.
Code Block (Pre+Lang)	language\ncode block\n	python\nprint("Hello")\n	Specify language after opening triple backticks. ` and \ inside must be escaped.
Block Quotation	>Block quotation	>This is a quote.\n>It spans multiple lines.	Starts with >. Can span multiple consecutive lines. Added in API 7.0.
Expandable Blockquote	`**>\n>Expandable quote\n>Hidden part	\n**`	`**>\n>Visible part\n>Hidden part
  
Table 1: MarkdownV2 Syntax Summary (Based on )   

Nested entities (e.g., bold text within an italic span) are generally supported in MarkdownV2, provided the syntax rules and escaping requirements are correctly followed.

5. Escaping Special Characters in MarkdownV2
A critical aspect of using MarkdownV2 is the correct handling of special characters. Telegram interprets certain characters as formatting indicators. To display these characters literally within MarkdownV2 formatted text, they must be escaped by preceding them with a backslash \.   

5.1. General Escaping Rule
According to the official documentation, any character with an ASCII code between 1 and 126 that is part of the Markdown syntax must be escaped to be treated as literal text. The characters explicitly requiring escaping in all contexts outside of code and pre entities are:
_ * [ ] ( ) ~ ` > # + - = | { } . !.   

Failure to escape these characters when they are intended as literal text will lead to unintended formatting or parsing errors. For instance, sending 1*2=2 without escaping the * and = would likely result in incorrect rendering or the message being sent as plain text. The correct way to send this literally is 1\*2\=2.

5.2. Context-Specific Escaping Rules
Beyond the general rule, MarkdownV2 imposes stricter escaping requirements within specific contexts :   

Inside Inline Code (`...`) and Code Blocks ( ... ): Within these entities, all occurrences of the backtick character (`) and the backslash character (\) must themselves be escaped with a preceding backslash. For example, to display the literal code `` `\n` `` inside an inline code span, one must write: ``` \\n\\ ```.
Inside Inline Link URLs ([...] (...)): Within the parentheses () defining the URL for an inline link, any literal closing parenthesis ) character and any literal backslash \ character must be escaped. For example: [Link](https://example.com/page\(1\))
Inside Custom Emoji Definitions (![...] (...)): Similarly, within the parentheses () defining the custom emoji, any literal closing parenthesis ) and any literal backslash \ must be escaped.   
The necessity for these context-specific rules, particularly within code blocks and URLs, adds a layer of complexity for developers. Dynamically generating formatted messages, especially those incorporating user input or external data, requires robust escaping logic to prevent syntax errors. A simple search-and-replace for the general list of special characters is insufficient; the escaping logic must account for whether the character appears inside a code block, a URL definition, or regular text.

5.3. Escaping Summary Table
The following table summarizes the escaping rules for key characters in MarkdownV2.

Character	ASCII	Requires General Escape?	Context-Specific Rules	Example (Literal Display)
_	95	Yes	-	\_
*	42	Yes	-	\*
[	91	Yes	-	\[
]	93	Yes	-	\]
(	40	Yes	-	\(
)	41	Yes	Escape inside Link/Emoji URLs (...)	\)
~	126	Yes	-	\~
`	96	Yes	Escape inside code/pre entities	\`
>	62	Yes	-	\>
#	35	Yes	-	\#
+	43	Yes	-	\+
-	45	Yes	-	\-
=	61	Yes	-	\=
`	`	124	Yes	-
{	123	Yes	-	\{
}	125	Yes	-	\}
.	46	Yes	-	\.
!	33	Yes	-	\!
\	92	No (Is the escape char)	Escape inside code/pre entities & Link/Emoji URLs (...)	\\

Export to Sheets
Table 2: MarkdownV2 Escaping Rules Summary (Based on )   

6. Legacy Markdown Mode (parse_mode: 'Markdown')
For simpler formatting needs or maintaining compatibility with older bot code, the legacy Markdown mode is available. It is activated by setting parse_mode: 'Markdown'.   

6.1. Supported Legacy Markdown Syntax
This mode supports a significantly reduced set of formatting options compared to MarkdownV2 :   

Bold: *bold text* (Uses single asterisks)
Italic: _italic text_ (Uses single underscores)
Inline URL: (url)
Inline User Mention: [mention text](tg://user?id=USER_ID)
Inline Code: `inline fixed-width code`
Code Block (Pre): \npre-formatted fixed-width code block\n
Code Block (Pre+Lang): language\npre-formatted code block\n
Notably absent from legacy Markdown are underline, strikethrough, spoilers, blockquotes, and custom emoji support. Nested formatting behavior might also differ or be less reliable than in MarkdownV2.   

6.2. Escaping in Legacy Markdown
Escaping rules are simpler in legacy Markdown. Only the characters _, *, `, and [ need to be escaped with a preceding backslash \ when intended as literal characters outside of an entity. This reduced complexity might make it suitable for bots requiring only basic formatting, but its limitations make MarkdownV2 the preferred choice for new development.   

7. API Versioning and Status for March 2025
The Telegram Bot API undergoes periodic updates, introducing new features, methods, and occasionally modifying existing behavior. As of the latest documented update preceding March 2025, Bot API 8.3, released on February 12, 2025, represents the current specification.   

The updates associated with API 8.3 focused primarily on features like enhanced Gift sending capabilities (e.g., sendGift to channel chats, can_send_gift field), additions related to video handling (covers, start timestamps in Video, sendVideo, InputMediaVideo, forwardMessage, copyMessage), and allowing reactions on more service message types. Critically, no changes to the parsing rules or supported syntax for either MarkdownV2 or legacy Markdown were documented in the API 8.3 release or other updates immediately preceding March 2025. Blockquote support, for instance, was introduced earlier in API 7.0 (December 29, 2023).   

Therefore, the detailed specifications for MarkdownV2 and legacy Markdown, including syntax and escaping rules outlined in sections 4, 5, and 6 of this report (based on ), are considered the accurate standard for Telegram Bot API interactions in March 2025.   

However, it is crucial for developers to distinguish between the core Bot API specification provided by Telegram (core.telegram.org) and the various third-party libraries or SDKs available for different programming languages (e.g., python-telegram-bot , PHP SDKs ,.NET libraries , etc.). These libraries often receive updates independently of the Bot API itself. Such library updates might introduce helper functions for formatting, change parameter names (e.g., deprecating disable_web_page_preview for link_preview_options as seen in python-telegram-bot v22.0 ), or abstract away some aspects of API interaction. While these libraries simplify development, developers must consult both the official Bot API documentation for the underlying rules and their specific library's documentation for the correct implementation methods and potential abstractions related to message formatting and escaping. Client applications like Telegram Desktop also receive frequent updates , which primarily affect rendering and UI, not the API's parsing rules.   

Developers should always consult the official Bot API changelog  or the "Recent changes" section on the main API page  before starting new projects or deploying major updates to ensure they are using the latest specifications.   

8. Recommendations for Developers
Based on the analysis of the Telegram Bot API documentation and formatting capabilities, the following recommendations are provided for developers building Telegram bots in March 2025:

Utilize MarkdownV2: For all new bot development, prioritize using parse_mode: 'MarkdownV2' to access the full spectrum of formatting options, including underline, strikethrough, spoilers, blockquotes, and custom emoji.   
Implement Rigorous Escaping: Pay meticulous attention to escaping special characters (_, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., !) in MarkdownV2. Develop or utilize robust utility functions to handle escaping correctly, especially when incorporating dynamic content or user input. Remember the specific, stricter escaping rules required within code blocks/inline code and link/emoji URLs. Errors in escaping are a common source of formatting failures.   
Test Across Clients: While the API defines the parsing rules, subtle rendering differences might occur across various Telegram clients (Desktop, iOS, Android, Web). Thoroughly test formatted messages on multiple platforms to ensure consistent and expected visual output.   
Rely on Official Documentation: Use https://core.telegram.org/bots/api  and the official changelog  as the primary sources of truth for API behavior and formatting rules. Avoid relying solely on potentially outdated third-party tutorials or summaries.   
Consult Library Documentation: If using a helper library or SDK , carefully review its documentation regarding how it handles the parse_mode parameter and whether it provides specific functions or methods for applying formatting or managing escaping. Understand how the library interacts with the underlying API specification.   
Anticipate Plain Text Fallback: Be aware that if Telegram encounters errors in parsing the Markdown syntax (often due to incorrect escaping or invalid structure), it may default to sending the message as plain text without applying any formatting. Implement checks or logging if precise formatting is critical for the bot's functionality.
9. Conclusion
The Telegram Bot API offers powerful tools for formatting messages, significantly enhancing bot communication. As of March 2025, developers have access to the comprehensive MarkdownV2 mode and the simpler legacy Markdown mode, controlled via the parse_mode parameter. MarkdownV2 is the recommended standard, providing a rich set of features including bold, italics, underline, strikethrough, spoilers, links, mentions, code formatting, blockquotes, and custom emoji.   

Effective use of MarkdownV2 hinges on strict adherence to its syntax and, critically, its complex character escaping rules. Developers must meticulously escape special characters, paying close attention to the distinct requirements within code blocks and link definitions to avoid parsing errors. The specifications detailed in this report, based on the documentation associated with Bot API 8.3 (February 12, 2025) , represent the expected behavior for March 2025. By understanding these specifications, implementing careful escaping, and leveraging the official documentation, developers can create Telegram bots that communicate clearly and effectively through well-formatted messages.   


Sources used in the report

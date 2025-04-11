import logging
import google.generativeai as genai
import subprocess
import tempfile
import os
import json
from datetime import datetime
import pytz
import re
import io

logger = logging.getLogger(__name__)

# TODO: Replace with actual path to Mermaid CLI config if needed
MERMAID_CONFIG_PATH = "mermaid_config.json"
# TODO: Replace with actual path to theme CSS if needed
MERMAID_CSS_PATH = "mermaid_theme.css"

# Define headers for diagram titles in different languages
DIAGRAM_HEADERS = {
    "en": "VOICE MESSAGE DIAGRAM",
    "ru": "СХЕМА ГОЛОСОВОГО СООБЩЕНИЯ",
    "kk": "ДАУЫСТЫҚ ХАБАРЛАМА ДИАГРАММАСЫ"
}

# Moscow timezone for timestamps
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Fallback text for failed diagram rendering
DIAGRAM_FAILED_TEXT = {
    "en": "Failed to render diagram. Technical error occurred.",
    "ru": "Не удалось создать схему. Произошла техническая ошибка.",
    "kk": "Диаграмманы құру мүмкін болмады. Техникалық қате орын алды."
}

async def generate_diagram_data(transcript: str, language: str, author_name: str = None) -> dict | None:
    """
    Sends a prompt to Gemini to extract structured data for a diagram.

    Args:
        transcript: The voice message transcript.
        language: The target language (e.g., 'ru', 'en') for the output.
        author_name: The name of the user who sent the voice message.

    Returns:
        A dictionary representing the diagram structure or None on failure.
    """
    logger.info(f"Generating diagram data for transcript (length: {len(transcript)}) in language '{language}'")

    # Language-specific instructions for diagram content
    language_instructions = {
        'en': "ALL DIAGRAM CONTENT, NODE TEXTS, LABELS AND TITLES MUST BE IN ENGLISH ONLY.",
        'ru': "ВСЕ СОДЕРЖИМОЕ ДИАГРАММЫ, ТЕКСТЫ УЗЛОВ, МЕТКИ И ЗАГОЛОВКИ ДОЛЖНЫ БЫТЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.",
        'kk': "ДИАГРАММАНЫҢ БАРЛЫҚ МАЗМҰНЫ, ТОРАПТАР МӘТІНДЕРІ, БЕЛГІЛЕР ЖӘНЕ ТАҚЫРЫПТАР ТЕК ҚАЗАҚ ТІЛІНДЕ БОЛУЫ КЕРЕК."
    }.get(language, "ВСЕ СОДЕРЖИМОЕ ДИАГРАММЫ, ТЕКСТЫ УЗЛОВ, МЕТКИ И ЗАГОЛОВКИ ДОЛЖНЫ БЫТЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.")

    # Language-specific examples for diagram nodes
    node_examples = {
        'en': "Example nodes should be like: 'Feature Description', 'Main Topic', 'Key Point'",
        'ru': "Примеры узлов должны быть типа: 'Описание функции', 'Основная тема', 'Ключевой момент'",
        'kk': "Тораптар мысалдары мынадай болуы керек: 'Функция сипаттамасы', 'Негізгі тақырып', 'Негізгі момент'"
    }.get(language, "Примеры узлов должны быть типа: 'Описание функции', 'Основная тема', 'Ключевой момент'")

    # Create the prompt
    prompt = f"""
    Analyze the following transcript and create a visual diagram that effectively represents its CONTENT.

    {language_instructions}

    RESPONSE REQUIREMENTS:
    1. IMPORTANT: Your goal is to represent the ACTUAL CONTENT and KEY POINTS from the transcript while PRESERVING THE ORDER of information as it appears in the transcript.

    2. YOU MUST USE {language.upper()} LANGUAGE ONLY FOR ALL TEXT USERS SEES IN THE FINISHED DIAGRAM. BUT RESPECT THE LANGUAGE OF SYNTAX. This is critical.

    3. CAREFULLY CONSIDER WHICH DIAGRAM TYPE WOULD BEST REPRESENT THIS CONTENT:
       - Choose the diagram type that will most effectively visualize the content in a portrait orientation seen from mobile devices
       - Your options to consider (but the decision is yours):
         * mindmap: Great for ideas, concepts, features, or related topics
         * flowchart TD: Useful for processes, decisions, or hierarchical structures
         * sequenceDiagram: Good for interactions, step-by-step processes, or timelines
         * classDiagram: Helpful for showing relationships between components
       - Don't feel restricted - use your judgment to pick the most suitable type for this specific content

    4. CRITICAL MINDMAP RULES (if you choose mindmap):
        - A mindmap MUST have exactly ONE root node - no more!
        - All other nodes must connect to this single root or its children
        - Start with "mindmap TD" on its own line
        - The FIRST node after "mindmap TD" will be the root (no indentation)
        - Child nodes must be indented with spaces
        - Example correct structure:
          ```
          mindmap TD
            Root[Main Topic]
              Child1[Subtopic 1]
                GrandChild1[Detail 1]
                GrandChild2[Detail 2]
              Child2[Subtopic 2]
          ```

    5. IMPORTANT LAYOUT INSTRUCTIONS:
       - FOR ALL DIAGRAM TYPES: Use a VERTICAL ORIENTATION (top-to-bottom flow)
       - For mindmaps: Use "mindmap TD" to ensure top-down orientation, NOT left-to-right
       - For flowcharts: Use "flowchart TD" for top-down orientation
       - Information should flow from top to bottom whenever possible

    6. CRITICAL SYNTAX RULES TO FOLLOW:
       - NEVER use double quotes (") inside node labels - use single quotes (') instead
       - Keep node text concise - use at most 5-7 words per node
       - Node text MUST absolutely stay on a single line. NEVER include literal newline characters (`\n`) within the text content defined inside node brackets `[...]` or parentheses `(...)`. Keep text concise.
       - For flowcharts, use proper node format: A[Text without quotes] or A(Simple text)
       - Avoid special characters in node IDs - use only letters, numbers, and underscores
       - Ensure all node IDs are unique - don't reuse the same ID
       - Add proper semicolons at the end of each node/connection definition

    7. Your response must be a JSON object with this EXACT structure:
    {{
      "diagram_type": "the_chosen_diagram_type",
      "title": "A concise title that summarizes the MAIN TOPIC of the transcript",
      "mermaid_code": "The complete Mermaid syntax for the diagram"
    }}

    VERY IMPORTANT GUIDELINES:
    - EVERYTHING in the diagram must be in {language.upper()} language only. {node_examples}
    - DO NOT use any English terms in the diagram unless they are technical terms with no translation
    - PRESERVE THE LOGICAL ORDER of information as it appears in the transcript
    - Represent the main topic first, then branch into details in the same order they are mentioned
    - If the transcript describes a sequence of events or steps, use a sequenceDiagram
    - If the transcript describes a process with decisions, use a flowchart TD
    - Create a structure that reads naturally from top to bottom, NOT left to right
    - The diagram must be designed for a 9:16 aspect ratio (tall portrait orientation)
    - Provide COMPLETE, VALID Mermaid syntax that can be rendered directly

    Transcript:
    ---
    {transcript}
    ---
    """

    try:
        # Use Gemini 2.0 Flash model for better response quality
        model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")
        response = await model.generate_content_async(prompt)

        # Clean the response: Gemini might wrap JSON in ```json ... ```
        cleaned_response_text = response.text.strip()

        # Extract JSON from response if wrapped in markdown code blocks
        json_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', cleaned_response_text)
        if json_match:
            cleaned_response_text = json_match.group(1)

        # Try to parse the JSON response
        try:
            diagram_data = json.loads(cleaned_response_text)
        except json.JSONDecodeError:
            # If direct parsing fails, try to extract JSON object using regex
            pattern = r'{[\s\S]*}'
            match = re.search(pattern, cleaned_response_text)
            if match:
                try:
                    diagram_data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.error("Failed to extract valid JSON after regex attempt")
                    return None
            else:
                logger.error("No JSON-like structure found in response")
                return None

        # Basic validation
        if not isinstance(diagram_data, dict) or \
           "diagram_type" not in diagram_data or \
           "title" not in diagram_data or \
           "mermaid_code" not in diagram_data:
            logger.error(f"Invalid JSON structure received from Gemini: {diagram_data}")
            return None

        # Force diagram orientation to TD (top-down) in the code itself
        mermaid_code = diagram_data["mermaid_code"]
        diagram_type = diagram_data["diagram_type"]

        # Preprocess mermaid code to replace problematic quotes
        mermaid_code = mermaid_code.replace('"', "'")

        # Add 'TD' orientation to mindmap if not already specified
        if diagram_type == "mindmap" and "mindmap TD" not in mermaid_code:
            mermaid_code = mermaid_code.replace("mindmap", "mindmap TD")
            diagram_data["mermaid_code"] = mermaid_code

        # Add 'TD' orientation to flowchart if not already specified
        if diagram_type == "flowchart" and not any(x in mermaid_code for x in ["flowchart TD", "flowchart TB"]):
            mermaid_code = mermaid_code.replace("flowchart", "flowchart TD")
            diagram_data["mermaid_code"] = mermaid_code

        # Add author information if provided
        if author_name:
            diagram_data["author"] = author_name

        # Add timestamp
        moscow_time = datetime.now(MOSCOW_TZ)
        diagram_data["timestamp"] = moscow_time.strftime("%Y-%m-%d %H:%M")

        logger.info(f"Successfully generated diagram data: {diagram_data.get('title')}")
        return diagram_data

    except Exception as e:
        logger.error(f"Error calling Gemini for diagram data: {e}", exc_info=True)
        return None

def create_mermaid_syntax(diagram_data: dict, language: str = 'ru') -> str | None:
    """
    Extracts and cleans the raw Mermaid code from the diagram data generated by Gemini.
    It strips any leading diagram type declaration, as that will be added during rendering.

    Args:
        diagram_data: The dictionary received from generate_diagram_data.
        language: The language code (unused in this function currently).

    Returns:
        A string containing the cleaned Mermaid syntax (code only) or None on failure.
    """
    logger.info("Extracting Mermaid syntax code from diagram data")
    if not diagram_data:
        return None

    try:
        # Get the main diagram code from Gemini
        mermaid_code = diagram_data.get("mermaid_code", "").strip()
        diagram_type = diagram_data.get("diagram_type", "flowchart").strip()

        # Define known diagram type keywords and potential directions
        diagram_keywords = ["flowchart", "graph", "sequenceDiagram", "classDiagram",
                            "stateDiagram", "stateDiagram-v2", "erDiagram", "journey",
                            "gantt", "pie", "mindmap"]
        directions = ["TD", "LR", "RL", "BT", "TB"] # Common directions

        # Check if the code starts with any known diagram type declaration (case-insensitive)
        # and strip it reliably
        lines = mermaid_code.split('\n')
        first_line_stripped = lines[0].strip() if lines else ""
        first_line_lower = first_line_stripped.lower()

        stripped_declaration = False
        for keyword in diagram_keywords:
            keyword_lower = keyword.lower()
            # Check if the first line starts with the keyword
            if first_line_lower.startswith(keyword_lower):
                # Check if the rest of the line is just a direction or empty
                rest_of_line = first_line_stripped[len(keyword):].strip()
                if not rest_of_line or rest_of_line.upper() in directions:
                    # It's likely a declaration line, remove it
                    logger.debug(f"Stripping leading diagram declaration: {first_line_stripped}")
                    mermaid_code = '\n'.join(lines[1:]).strip()
                    stripped_declaration = True
                    break # Found and stripped, no need to check other keywords

        # Log if no declaration was stripped (for debugging)
        if not stripped_declaration:
            logger.debug("No leading diagram declaration found or stripped.")

        # Fix mindmap syntax to ensure exactly one root node
        if diagram_type.lower() == "mindmap":
            mermaid_code = fix_mindmap_structure(mermaid_code)

        # Fix quote-related syntax issues by escaping quotes inside node texts
        # This addresses the parse errors that occur with unescaped quotes in node texts
        if diagram_type in ["flowchart", "graph"]:
            # Fix node definitions with quotes in them A("text with "quotes"") → A("text with \"quotes\"")
            mermaid_code = re.sub(r'([A-Za-z0-9_]+)\("([^"]*)"([^"]*)"([^"]*)"\)', r'\1("\2\"\3\"\4")', mermaid_code)
            # Also handle single quotes and parentheses
            mermaid_code = re.sub(r"([A-Za-z0-9_]+)\(([^()]*)\"([^()]*)\"([^()]*)\)", r'\1(\2\"\3\"\4)', mermaid_code)

        # For any diagram type, replace problematic characters in all text
        # Replace quotes with escaped quotes throughout
        mermaid_code = mermaid_code.replace('"', '\\"')

        # If we're dealing with a flowchart, ensure proper node syntax
        if diagram_type in ["flowchart", "graph"]:
            # Ensure nodes have proper syntax: A["Text"] or A("Text")
            lines = mermaid_code.split('\n')
            fixed_lines = []
            for line in lines:
                # Skip empty lines and pure connection lines (A --> B)
                if not line.strip() or '-->' in line or '---' in line:
                    fixed_lines.append(line)
                    continue

                # Try to fix malformed nodes if we can identify them
                if re.search(r'[A-Za-z0-9_]+\([^)]*$', line):  # Unclosed parenthesis
                    line = line.rstrip(';') + ')'
                    if not line.endswith(';'):
                        line += ';'

                fixed_lines.append(line)

            mermaid_code = '\n'.join(fixed_lines)

        # Return the potentially cleaned code
        logger.debug(f"Cleaned Mermaid code body:\n{mermaid_code}")
        return mermaid_code

    except Exception as e:
        logger.error(f"Error cleaning Mermaid syntax: {e}", exc_info=True)
        return None

def fix_mindmap_structure(mermaid_code: str) -> str:
    """
    Fixes mindmap structure to ensure there is exactly one root node.

    Args:
        mermaid_code: The original mindmap code

    Returns:
        Fixed mindmap code with exactly one root node
    """
    logger.info("Validating and fixing mindmap structure")

    # If code is empty, return minimal valid mindmap
    if not mermaid_code.strip():
        logger.warning("Empty mindmap code, returning minimal valid structure")
        return "root[Diagram]"

    lines = mermaid_code.strip().split('\n')
    non_empty_lines = [l for l in lines if l.strip()]

    # If no content, return minimal valid mindmap
    if not non_empty_lines:
        logger.warning("No non-empty lines in mindmap, returning minimal valid structure")
        return "root[Diagram]"

    # Find root-level lines (those with no indentation)
    root_level_lines = []
    for line in non_empty_lines:
        if not line.startswith(' ') and not line.startswith('\t'):
            root_level_lines.append(line)

    # If there's exactly one root, the structure is already correct
    if len(root_level_lines) == 1:
        logger.info("Mindmap already has exactly one root node, no fixes needed")
        return mermaid_code

    # If there are no root-level lines, create one using the title
    if len(root_level_lines) == 0:
        logger.warning("No root lines found in mindmap, creating one from title")
        title = "Diagram"  # Default title

        # Try to extract a reasonable title
        for line in non_empty_lines:
            stripped = line.strip()
            if stripped and any(char in stripped for char in "[({"):
                # Extract title from first node with brackets
                match = re.search(r'[\[\(]["\']?([^"\'\]\)]+)["\']?[\]\)]', stripped)
                if match:
                    title = match.group(1)
                    break

        # Create a new root and indent all existing lines
        new_lines = [f"root[{title}]"]
        for line in non_empty_lines:
            new_lines.append("  " + line)

        logger.info(f"Created new root node '{title}' and indented all existing content")
        return '\n'.join(new_lines)

    # If there are multiple root-level lines, choose the first as the main root
    # and make all others children of it
    if len(root_level_lines) > 1:
        logger.warning(f"Multiple root nodes found ({len(root_level_lines)}), fixing structure")

        # Use the first root as the main root
        main_root = root_level_lines[0]

        # Process all lines to build the new structure
        new_lines = []
        in_first_root = False

        for line in non_empty_lines:
            if line == main_root and not in_first_root:
                new_lines.append(line)
                in_first_root = True
            elif not line.startswith(' ') and not line.startswith('\t'):
                # This is another root level line - indent it to make it a child
                new_lines.append("  " + line)
            else:
                # If it's already an indented line, keep its current indentation plus more
                if line in root_level_lines[1:]:
                    new_lines.append("  " + line)
                else:
                    # For lines that are already indented, add two more spaces
                    # to maintain their relative hierarchy
                    indentation = len(line) - len(line.lstrip())
                    if any(line.startswith(root) for root in root_level_lines[1:]):
                        new_lines.append("  " + " " * indentation + line.lstrip())
                    else:
                        new_lines.append(line)

        logger.info(f"Fixed mindmap structure, using '{main_root}' as the main root")
        return '\n'.join(new_lines)

    return mermaid_code

def render_mermaid_to_png(mermaid_code_body: str, diagram_data: dict, language: str = 'ru') -> bytes | None:
    """
    Renders Mermaid syntax to a PNG image using the Mermaid CLI (mmdc).
    Includes header with title, timestamp and author, and prepends the diagram type.

    Args:
        mermaid_code_body: The cleaned Mermaid syntax string (code only, no type declaration).
        diagram_data: The diagram data dictionary containing metadata (including diagram_type).
        language: The language code for localization.

    Returns:
        Bytes of the rendered PNG image, or None on failure.
    """
    logger.info("Rendering Mermaid code body to PNG")
    # mermaid_code_body might be None if create_mermaid_syntax failed
    if mermaid_code_body is None:
        logger.error("Received None for mermaid_code_body, cannot render.")
        return None
    if not diagram_data:
        logger.error("Received None for diagram_data, cannot render.")
        return None

    # Handle empty code body gracefully - perhaps return error or specific image?
    if not mermaid_code_body.strip():
        logger.warning("Mermaid code body is empty after cleaning.")
        # Optionally, create a placeholder image or return None
        # For now, return None as it's likely an error state
        return None

    # Get the Puppeteer config path from environment variable
    puppeteer_config_path = os.environ.get('PUPPETEER_CONFIG_PATH')
    if not puppeteer_config_path:
        logger.warning("PUPPETEER_CONFIG_PATH environment variable not set.")
    elif not os.path.exists(puppeteer_config_path):
        logger.warning(f"Puppeteer config file specified by PUPPETEER_CONFIG_PATH does not exist: {puppeteer_config_path}")
        puppeteer_config_path = None # Fallback to not using the config

    # Get the Chromium executable path from environment variable (set in Dockerfile)
    executable_path = os.environ.get('PUPPETEER_EXECUTABLE_PATH')
    if not executable_path or not os.path.exists(executable_path):
        logger.error(f"Chromium executable not found at path specified by PUPPETEER_EXECUTABLE_PATH: {executable_path}")
        # Fallback check in common locations (although should be set by env var)
        chrome_paths = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable"
        ]
        for path in chrome_paths:
            if os.path.exists(path):
                executable_path = path
                logger.info(f"Found fallback Chromium/Chrome at: {path}")
                break

    if not executable_path:
         logger.error("No Chrome or Chromium browser found.")
         return create_fallback_text_image(diagram_data, language)

    # Verify dependencies (optional but good for debugging)
    try:
        ldd_output = subprocess.run(
            ["ldd", executable_path],
            capture_output=True,
            text=True,
            check=False
        )
        if "libnss3.so" not in ldd_output.stdout:
            logger.warning(f"libnss3.so NOT found in dependencies for {executable_path}")
        else:
            logger.info(f"libnss3.so found in dependencies for {executable_path}")
    except Exception as ldd_err:
        logger.warning(f"Could not check dependencies for {executable_path}: {ldd_err}")

    infile_path = None
    outfile_path = None

    try:
        # Create temporary files manually to handle paths correctly
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False, encoding='utf-8') as infile:
            # Get diagram type and metadata
            diagram_type = diagram_data.get("diagram_type", "flowchart").strip()
            title = diagram_data.get("title", "")
            author = diagram_data.get("author", "")
            timestamp = diagram_data.get("timestamp", "")

            # Get header text in the appropriate language
            header_text = DIAGRAM_HEADERS.get(language, DIAGRAM_HEADERS['ru'])

            # Prepare the complete mermaid syntax with header AND diagram type
            header_comment = f"%% {header_text}\n%% {title}"
            if author:
                header_comment += f"\n%% Author: {author}"
            if timestamp:
                header_comment += f"\n%% Time: {timestamp} (MSK)"

            # Combine header, diagram type, and code body
            # Ensure diagram_type is followed by a newline before the code body
            complete_syntax = f"{header_comment}\n\n{diagram_type}\n{mermaid_code_body}"

            infile.write(complete_syntax)
            infile_path = infile.name

        # Create outfile path separately
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as outfile:
            outfile_path = outfile.name

        # Ensure files are closed before mmdc reads/writes
        # infile and outfile are already closed by the 'with' statement exit

        logger.debug(f"Mermaid input file: {infile_path}")
        logger.debug(f"Mermaid output file: {outfile_path}")
        logger.debug(f"Input file content:\n{complete_syntax}") # Log content being written

        # Construct the mmdc command with 9:16 aspect ratio
        # Double the dimensions for higher resolution (1800x3200 instead of 900x1600)
        command = [
            "mmdc",
            "-i", infile_path,
            "-o", outfile_path,
            "-b", "transparent",  # Set background transparent
            "-w", "1800",  # Width: doubled for higher resolution
            "-H", "3200",  # Height: doubled for higher resolution
            "--pdfFit",  # Ensure diagram fits in the output
        ]

        # Add Puppeteer config if the path was found and exists
        if puppeteer_config_path:
            logger.info(f"Using Puppeteer config: {puppeteer_config_path}")
            command.extend(["-p", puppeteer_config_path])
        else:
             logger.warning(f"Puppeteer config file path not found or file does not exist. Running mmdc without -p flag.")
             # Log potentially problematic relative path calculation results if needed
             # script_dir_debug = os.path.dirname(os.path.abspath(__file__))
             # relative_path_debug = os.path.join(script_dir_debug, "..", "puppeteerConfigFile.json")
             # abs_path_debug = os.path.abspath(relative_path_debug)
             # logger.debug(f"Debug paths: script_dir={script_dir_debug}, relative_path={relative_path_debug}, abs_path={abs_path_debug}")

        logger.info(f"Executing command: {' '.join(command)}")

        # Execute the command with environment variable check
        env = os.environ.copy()
        # PUPPETEER_EXECUTABLE_PATH is already set globally via ENV in Dockerfile
        # We log it to confirm it's picked up
        # env['PUPPETEER_EXECUTABLE_PATH'] = executable_path
        # env['PUPPETEER_SKIP_CHROMIUM_DOWNLOAD'] = 'true' # Already set via ENV

        logger.info(f"Environment variables for subprocess: PUPPETEER_EXECUTABLE_PATH={env.get('PUPPETEER_EXECUTABLE_PATH', 'Not set via os.environ')}, "
                    f"PUPPETEER_SKIP_CHROMIUM_DOWNLOAD={env.get('PUPPETEER_SKIP_CHROMIUM_DOWNLOAD', 'Not set via os.environ')}, "
                    f"PUPPETEER_CONFIG_PATH={env.get('PUPPETEER_CONFIG_PATH', 'Not set via os.environ')}")

        process = subprocess.run(
            command,
            capture_output=True,
            text=True, # Ensure output is decoded as text
            check=False,
            env=env,
            timeout=60,  # Increased timeout from 30 to 60 seconds to prevent timeouts
            encoding='utf-8', # Specify encoding
            errors='replace' # Handle potential encoding errors in output
        )

        # Log MMDC output regardless of return code for debugging
        logger.info(f"MMDC STDOUT:\n{process.stdout}")
        logger.error(f"MMDC STDERR:\n{process.stderr}") # Log stderr as error for visibility

        if process.returncode != 0:
            error_message = f"Mermaid CLI failed (Exit Code {process.returncode})"
            logger.error(error_message)
            # Log the code that caused the error
            logger.error(f"Code Attempted:\n{mermaid_code_body}")
            # Return fallback text image instead
            return create_fallback_text_image(diagram_data, language, error_message) # Pass error

        logger.info(f"Mermaid CLI executed successfully. Output PNG: {outfile_path}")

        # Check if the output file exists and has content
        if not os.path.exists(outfile_path) or os.path.getsize(outfile_path) == 0:
            logger.error(f"Mermaid CLI did not produce a valid PNG file at {outfile_path}")
            return create_fallback_text_image(diagram_data, language, "MMDC produced empty file") # Pass error

        # Read the generated PNG file
        with open(outfile_path, 'rb') as f:
            png_bytes = f.read()

        # Try to add a logo if available
        try:
            # Look for logo relative to the script directory
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voiciologo.png")
            if os.path.exists(logo_path):
                logger.info(f"Found logo at: {logo_path}")
                # Log directory contents for debugging
                # logger.info(f"Script directory contents: {os.listdir(script_dir)}")

                # Add logo using PIL
                from PIL import Image, ImageDraw

                # Open the diagram image
                diagram_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA") # Ensure RGBA

                # Open the logo image
                logo_img = Image.open(logo_path).convert("RGBA") # Ensure RGBA

                # Resize logo to reasonable size (e.g., 10% of the width)
                logo_width = diagram_img.width // 10
                # Maintain aspect ratio
                logo_ratio = logo_img.height / logo_img.width
                logo_height = int(logo_width * logo_ratio)
                logo_img = logo_img.resize((logo_width, logo_height))

                # Calculate position for bottom right corner with padding
                padding = 20
                position = (diagram_img.width - logo_width - padding,
                            diagram_img.height - logo_height - padding)

                # Paste the logo onto the diagram using its alpha channel as mask
                diagram_img.paste(logo_img, position, logo_img)

                # Save the watermarked image back to bytes
                img_byte_arr_watermarked = io.BytesIO()
                diagram_img.save(img_byte_arr_watermarked, format='PNG')
                watermarked_png = img_byte_arr_watermarked.getvalue()

                logger.info(f"Added logo to diagram.")
                return watermarked_png
            else:
                 logger.warning(f"Logo file not found at {logo_path}")

        except ImportError:
             logger.warning("Pillow library not installed. Cannot add logo.")
        except Exception as logo_err:
            logger.warning(f"Failed to add logo to diagram: {logo_err}")
            # Return the original PNG if logo addition fails

        return png_bytes

    except FileNotFoundError:
        logger.error("Mermaid CLI (mmdc) not found. Ensure it's installed and in the system PATH.")
        return create_fallback_text_image(diagram_data, language, "MMDC not found") # Pass error
    except subprocess.TimeoutExpired:
        logger.error("Mermaid CLI process timed out")
        return create_fallback_text_image(diagram_data, language, "MMDC timeout") # Pass error
    except Exception as e:
        logger.error(f"Error rendering Mermaid diagram: {e}", exc_info=True)
        return create_fallback_text_image(diagram_data, language, f"Rendering Error: {e}") # Pass error
    finally:
        # Ensure temporary files are always cleaned up
        if infile_path and os.path.exists(infile_path):
            try:
                os.remove(infile_path)
                logger.debug(f"Cleaned up temporary input file: {infile_path}")
            except OSError as e:
                logger.warning(f"Error cleaning up input file {infile_path}: {e}")
        if outfile_path and os.path.exists(outfile_path):
            try:
                os.remove(outfile_path)
                logger.debug(f"Cleaned up temporary output file: {outfile_path}")
            except OSError as e:
                logger.warning(f"Error cleaning up output file {outfile_path}: {e}")


def create_fallback_text_image(diagram_data: dict, language: str = 'ru', error_info: str = None) -> bytes | None:
    """
    Creates a simple text-based image with the diagram content as a fallback
    when Mermaid rendering fails. Includes error info if provided.

    Args:
        diagram_data: The diagram data dictionary containing metadata.
        language: The language code for localization.
        error_info: Optional string describing the rendering error.

    Returns:
        Bytes of the generated image or None on failure.
    """
    try:
        # Import PIL only when needed to avoid dependency issues
        from PIL import Image, ImageDraw, ImageFont
        import textwrap

        # Get diagram data
        title = diagram_data.get("title", "Diagram")
        mermaid_code = diagram_data.get("mermaid_code", "") # Keep for fallback content extraction

        # Create a blank image with doubled resolution
        width, height = 1800, 3200  # Doubled from 900x1600 to match new diagram resolution
        background_color = (255, 255, 255)  # White background
        text_color = (0, 0, 0)  # Black text
        error_color = (255, 0, 0) # Red for error message

        image = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(image)

        # Try to load a font, fall back to default if not available
        try:
            # Try to find a system font that supports the language
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            # Double the font sizes
            title_font = ImageFont.truetype(font_path, 80)  # Doubled from 40
            body_font = ImageFont.truetype(font_path, 48)   # Doubled from 24
            error_font = ImageFont.truetype(font_path, 40) # Font for error message
        except (IOError, ImportError, OSError):
            logger.warning("DejaVu fonts not found. Using PIL defaults.")
            # Fall back to default font
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()
            error_font = ImageFont.load_default()

        # Draw a header with the error message - positions doubled
        error_header = DIAGRAM_FAILED_TEXT.get(language, DIAGRAM_FAILED_TEXT['en'])
        draw.text((100, 100), error_header, fill=error_color, font=title_font) # Use error color

        # Draw the title - positions doubled
        draw.text((100, 260), title, fill=text_color, font=title_font)

        # Draw the specific error info if available
        current_y = 380
        if error_info:
             wrapped_error = textwrap.fill(f"Error Details: {error_info}", width=80)
             draw.text((100, current_y), wrapped_error, fill=error_color, font=error_font, spacing=10)
             # Estimate text height and add padding
             try:
                 # Simple estimation, might need refinement
                 bbox = draw.textbbox((100, current_y), wrapped_error, font=error_font, spacing=10)
                 current_y = bbox[3] + 40 # Add padding below error
             except AttributeError: # Handle older PIL versions
                 current_y += len(wrapped_error.split('\n')) * 50 + 40


        # Draw a line separator - positions and width doubled
        draw.line([(100, current_y), (width - 100, current_y)], fill=text_color, width=4)
        current_y += 40 # Add padding below line

        # Extract content from mermaid code - clean it up for display
        content = "No diagram content available." # Default text
        if mermaid_code:
            # Remove any mermaid syntax and extract just the text content
            content_lines = []
            for line in mermaid_code.split('\n'):
                # Skip syntax lines and keep content
                line = line.strip()
                if line and not line.startswith(('graph', 'flowchart', 'mindmap', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'journey', 'gantt', 'pie', '%%', '-->', '---', '-.->', '===', ':::')):
                    # Remove node ids and formatting symbols more aggressively
                    cleaned_line = re.sub(r'^[A-Za-z0-9_]+(?:\[|\(|\{|\<|\>|\/|\\)', '', line).strip() # Remove ID[({<>/\... start
                    cleaned_line = re.sub(r'[\]\)\}\>]', '', cleaned_line) # Remove closing brackets
                    cleaned_line = cleaned_line.replace(';', '').strip() # Remove semicolons
                    cleaned_line = cleaned_line.replace('\\"', '"') # Unescape quotes for display
                    if cleaned_line:
                        content_lines.append(cleaned_line)

            if content_lines:
                content = "\n• ".join(content_lines)
                content = "• " + content

        # Wrap text to fit the image width - adjusted for wider image
        wrapped_text = textwrap.fill(content, width=80)  # Adjusted from 40

        # Draw the content - position doubled
        draw.text((100, current_y), wrapped_text, fill=text_color, font=body_font, spacing=20)  # Spacing doubled

        # Save to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        logger.info("Fallback text image created successfully.")
        return img_byte_arr.getvalue()

    except ImportError:
         logger.error("Pillow library not installed. Cannot create fallback image.")
         return None
    except Exception as e:
        logger.error(f"Error creating fallback text image: {e}", exc_info=True)
        return None

import logging
import google.generativeai as genai # Keep for type hinting if generate_diagram_data is called from here
import subprocess
import tempfile
import os
import json
from datetime import datetime
import pytz
import re
import io

# Import Image, ImageDraw, ImageFont only if Pillow is installed
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None
    ImageDraw = None
    ImageFont = None

logger = logging.getLogger(__name__)

MERMAID_CONFIG_PATH = "mermaid_config.json"
PUPPETEER_CONFIG_PATH = "puppeteerConfigFile.json"

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

# Regex to find node definitions like A[Text] or A("Text") or A(Text)
NODE_DEF_REGEX = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*(\(|\{|\[|\"|\').*$", re.MULTILINE)

async def generate_diagram_data(transcript: str, language: str, author_name: str = None) -> dict | None:
    """
    Sends the prompt to Gemini to extract structured diagram data (title, type, mermaid code) from a transcript.

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

    # --- Improved Prompt Construction ---
    # This is where the prompt is defined and where you apply prompt engineering.
    prompt = f"""
    Analyze the following transcript and create a visual diagram (Mermaid syntax) that effectively represents its CONTENT.

    {language_instructions}

    RESPONSE REQUIREMENTS:
    1. IMPORTANT: Your goal is to represent the ACTUAL CONTENT and KEY POINTS from the transcript while PRESERVING THE ORDER of information as it appears in the transcript.

    2. YOU MUST USE {language.upper()} LANGUAGE ONLY FOR ALL TEXT IN THE DIAGRAM. This is critical.

    3. CAREFULLY CONSIDER WHICH DIAGRAM TYPE WOULD BEST REPRESENT THIS CONTENT:
       - Choose the diagram type that will most effectively visualize the content in a portrait orientation (9:16 aspect ratio) on mobile devices.
       - Your options: mindmap, flowchart TD, sequenceDiagram, classDiagram, entityRelationshipDiagram. Use your judgment.
       - Keep the overall diagram width compact. Avoid excessively long horizontal branches.

    4. CRITICAL MINDMAP RULES (if you choose mindmap):
        - A mindmap MUST have exactly ONE root node (no indentation).
        - All other nodes must connect to this single root or its children (must be indented).
        - Start the code ONLY with the root node definition (e.g., `Root[Main Topic]`). Do NOT include `mindmap TD` in the mermaid_code response field.

    5. IMPORTANT LAYOUT INSTRUCTIONS:
       - FOR ALL DIAGRAM TYPES: Use a VERTICAL ORIENTATION (top-to-bottom flow).
       - For flowcharts: Start the code ONLY with node/link definitions. Do NOT include `flowchart TD` in the mermaid_code response field.
       - Information should flow from top to bottom.

    6. CRITICAL SYNTAX RULES TO FOLLOW (VERY IMPORTANT FOR STABILITY):
       - Enclose ALL node text containing special characters (parentheses (), brackets [], braces {{}}, quotes "", '') in DOUBLE QUOTES, e.g., `A["Node text with (parentheses)"]` or `B["Text with 'quotes'"]`. Use `&quot;` for literal double quotes inside the text itself if needed.
       - Keep node text concise (max 5-7 words).
       - Use simple alphanumeric node IDs (e.g., `Node1`, `Action_A`). Ensure IDs are unique.
       - Add a semicolon `;` at the end of EVERY node definition and connection definition (e.g., `A[Text];`, `A --> B;`). THIS IS MANDATORY.
       - Ensure link syntax is correct (e.g., `A --> B;` for flowcharts).

    7. Your response must be ONLY a JSON object with this EXACT structure (no introductory text, no markdown fences):
    {{
      "diagram_type": "the_chosen_diagram_type (e.g., mindmap, flowchart)",
      "title": "A concise title in {language.upper()} summarizing the MAIN TOPIC",
      "mermaid_code": "The complete Mermaid syntax BODY for the diagram (excluding the 'mindmap TD' or 'flowchart TD' declaration line, start directly with nodes/links)."
    }}

    VERY IMPORTANT GUIDELINES:
    - EVERYTHING in the diagram must be in {language.upper()} language only. {node_examples}.
    - PRESERVE THE LOGICAL ORDER of information from the transcript.
    - Provide COMPLETE and **STRICTLY VALID** Mermaid syntax BODY according to the rules above. Double-check your syntax.

    Transcript:
    ---
    {transcript}
    ---
    """
    # --- End of Prompt ---

    try:
        # Use Gemini 2.0 Flash model
        model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")
        logger.debug("Sending diagram generation prompt to Gemini...")
        response = await model.generate_content_async(prompt)

        cleaned_response_text = response.text.strip()
        logger.debug(f"Raw Gemini response for diagram data:\n{cleaned_response_text}")

        # Extract JSON (handle potential markdown fences)
        json_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', cleaned_response_text, re.IGNORECASE)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'{[\s\S]*}', cleaned_response_text) # Fallback
            if json_match:
                json_str = json_match.group(0)
            else:
                logger.error(f"No JSON object found in Gemini response: {cleaned_response_text}")
                return None

        # Parse JSON
        try:
            diagram_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from Gemini response: {e}\nResponse text: {json_str}")
            return None

        # Validate JSON structure
        if not isinstance(diagram_data, dict) or \
           {"diagram_type", "title", "mermaid_code"} - diagram_data.keys(): # Check if all keys exist
            logger.error(f"Invalid JSON structure received from Gemini: {diagram_data}")
            return None

        # Add author and timestamp
        if author_name:
            diagram_data["author"] = author_name
        moscow_time = datetime.now(MOSCOW_TZ)
        diagram_data["timestamp"] = moscow_time.strftime("%Y-%m-%d %H:%M")

        logger.info(f"Successfully generated diagram data: {diagram_data.get('title')}")
        return diagram_data

    except Exception as e:
        logger.error(f"Error calling Gemini for diagram data: {e}", exc_info=True)
        return None


def _escape_mermaid_text(text: str) -> str:
    """Escapes characters problematic within Mermaid node text."""
    text = text.replace('\\', '\\\\')
    text = text.replace('"', '&quot;') # Use HTML entity for quotes
    return text

def create_mermaid_syntax(diagram_data: dict, language: str = 'ru') -> str | None:
    """
    Extracts, cleans, and attempts to validate/fix the Mermaid code body.

    Args:
        diagram_data: The dictionary containing diagram details.
        language: The language code (currently unused here).

    Returns:
        Cleaned Mermaid syntax body string or None on failure.
    """
    logger.info("Extracting and cleaning Mermaid syntax code body")
    if not diagram_data or "mermaid_code" not in diagram_data:
        logger.error("Invalid or missing diagram_data for syntax extraction.")
        return None

    mermaid_code_body = diagram_data.get("mermaid_code", "").strip()
    diagram_type = diagram_data.get("diagram_type", "flowchart").strip().lower()

    # Remove potential markdown code block fences (already handled in parsing ideally)
    mermaid_code_body = re.sub(r'^\s*```[a-z]*\n?', '', mermaid_code_body, flags=re.IGNORECASE | re.MULTILINE)
    mermaid_code_body = re.sub(r'\n?\s*```\s*$', '', mermaid_code_body)
    mermaid_code_body = mermaid_code_body.strip()

    # Remove potential header comments (lines starting with %%) - should not be generated by prompt
    lines = mermaid_code_body.split('\n')
    code_lines = [line for line in lines if not line.strip().startswith('%%')]
    mermaid_code_body = '\n'.join(code_lines).strip()

    # Fix mindmap structure if necessary (ensure single root)
    if diagram_type == "mindmap":
        mermaid_code_body = fix_mindmap_structure(mermaid_code_body)

    # --- Basic Syntax Sanity Checks/Fixes ---
    fixed_lines = []
    for line in mermaid_code_body.split('\n'):
        stripped = line.strip()
        if not stripped: # Skip empty lines
            continue

        # Attempt to fix potentially unquoted nodes with problematic chars like () or []
        # This matches lines like: `  NodeId Some text (with parens)`
        match_unquoted = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s+([^"\'\[({].*)$', line)
        if match_unquoted:
            indent, node_id, node_text = match_unquoted.groups()
            if any(c in node_text for c in '()[]{}'):
                fixed_text = _escape_mermaid_text(node_text.rstrip(';')) # Escape and remove trailing semicolon if any
                line = f'{indent}{node_id}["{fixed_text}"]' # Default to quoted string
                logger.debug(f"Attempting to fix node syntax: {stripped} -> {line.strip()}")

        # Ensure lines defining nodes or links end with a semicolon
        if stripped and not stripped.endswith(';') and ('-->' in stripped or '---' in stripped or NODE_DEF_REGEX.match(stripped)):
            line += ';'
            logger.debug(f"Added missing semicolon: {line.strip()}")

        fixed_lines.append(line)
    mermaid_code_body = '\n'.join(fixed_lines)

    # Final replacement of literal double quotes inside text with HTML entity as a safeguard
    # This helps if the prompt rule for quoting wasn't perfectly followed
    mermaid_code_body = mermaid_code_body.replace('"', '&quot;')

    logger.debug(f"Final cleaned Mermaid code body:\n{mermaid_code_body}")
    return mermaid_code_body if mermaid_code_body else None


def fix_mindmap_structure(mermaid_code_body: str) -> str:
    """
    Ensures the mindmap code body starts with exactly one root node.

    Args:
        mermaid_code_body: The mindmap code body.

    Returns:
        Fixed mindmap code body.
    """
    logger.info("Validating and fixing mindmap structure")
    lines = mermaid_code_body.strip().split('\n')
    non_empty_lines = [l for l in lines if l.strip()]

    if not non_empty_lines:
        logger.warning("Mindmap code body is empty, returning minimal structure.")
        return "root[Diagram];" # Added semicolon

    root_level_lines = []
    root_indices = []
    for i, line in enumerate(non_empty_lines):
        if not line.startswith(' ') and not line.startswith('\t'):
            root_level_lines.append(line)
            root_indices.append(i)

    if len(root_level_lines) == 1:
        logger.info("Mindmap already has exactly one root node.")
        return mermaid_code_body # Structure is likely correct

    new_lines = []
    if len(root_level_lines) == 0:
        logger.warning("No root node found. Creating one.")
        title = "Diagram"
        # Extract title from first node-like line if possible
        first_node_match = re.search(r'^\s*\w+\["?([^"\]]+)"?\]', non_empty_lines[0])
        if first_node_match:
            title = first_node_match.group(1)
        new_lines.append(f"root[{_escape_mermaid_text(title)}];") # Added semicolon
        # Indent all existing lines
        for line in non_empty_lines:
            new_lines.append("  " + line)
    else: # More than one root node
        logger.warning(f"Multiple root nodes ({len(root_level_lines)}) found. Consolidating.")
        new_lines.append(root_level_lines[0]) # Keep the first root
        # Indent all subsequent root lines and their children
        for i, line in enumerate(non_empty_lines):
            if i == root_indices[0]: # Skip the main root line itself
                continue
            # Indent all other lines by two spaces
            new_lines.append("  " + line)

    logger.info("Fixed mindmap structure.")
    # Ensure all lines end with semicolon after fixing
    final_fixed_lines = [l.rstrip() + ';' if not l.rstrip().endswith(';') else l.rstrip() for l in new_lines]
    return '\n'.join(final_fixed_lines)


def render_mermaid_to_png(mermaid_code_body: str, diagram_data: dict, language: str = 'ru') -> bytes | None:
    """
    Renders Mermaid syntax to a PNG image using the Mermaid CLI (mmdc).

    Args:
        mermaid_code_body: The cleaned Mermaid syntax string (code body only).
        diagram_data: The diagram data dictionary containing metadata.
        language: The language code for localization.

    Returns:
        Bytes of the rendered PNG image, or None on failure.
    """
    logger.info("Rendering Mermaid code body to PNG")
    if mermaid_code_body is None or not diagram_data:
        logger.error("Cannot render: Missing mermaid_code_body or diagram_data.")
        return None
    if not mermaid_code_body.strip():
        logger.warning("Mermaid code body is empty. Cannot render.")
        return create_fallback_text_image(diagram_data, language, "Error: Empty diagram content.")

    infile_path = None
    outfile_path = None

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False, encoding='utf-8') as infile, \
             tempfile.NamedTemporaryFile(suffix='.png', delete=False) as outfile:

            diagram_type = diagram_data.get("diagram_type", "flowchart").strip()
            title = diagram_data.get("title", "")
            author = diagram_data.get("author", "")
            timestamp = diagram_data.get("timestamp", "")

            header_text = DIAGRAM_HEADERS.get(language, DIAGRAM_HEADERS['ru'])
            header_comment = f"%% {header_text}\n%% {title}"
            if author: header_comment += f"\n%% Author: {author}"
            if timestamp: header_comment += f"\n%% Time: {timestamp} (MSK)"

            # Prepend diagram type declaration with enforced TD orientation where applicable
            type_declaration = diagram_type
            if diagram_type.lower() in ["flowchart", "mindmap", "graph"]:
                 type_declaration += " TD" # Enforce Top-Down

            complete_syntax = f"{header_comment}\n\n{type_declaration}\n{mermaid_code_body}"

            infile.write(complete_syntax)
            infile_path = infile.name
            outfile_path = outfile.name
            # Files are closed automatically upon exiting 'with' block

        logger.debug(f"Complete Mermaid syntax written to {infile_path}:\n{complete_syntax}")
        logger.debug(f"Output will be written to: {outfile_path}")

        command = [
            "mmdc", "-i", infile_path, "-o", outfile_path,
            "-b", "transparent", "-w", "1800", "-H", "3200", "--pdfFit",
        ]

        if os.path.exists(PUPPETEER_CONFIG_PATH):
            logger.info(f"Using Puppeteer config: {PUPPETEER_CONFIG_PATH}")
            command.extend(["-p", PUPPETEER_CONFIG_PATH])
        else:
            logger.info("Puppeteer config file not found, running mmdc without -p flag.")

        logger.info(f"Executing command: {' '.join(command)}")
        env = os.environ.copy()

        process = subprocess.run(
            command, capture_output=True, text=True, check=False, env=env, timeout=60
        )

        if process.returncode != 0:
            error_message = f"Mermaid CLI failed (Exit Code {process.returncode})"
            logger.error(error_message)
            if process.stdout: logger.error(f"MMDC STDOUT:\n{process.stdout}")
            if process.stderr: logger.error(f"MMDC STDERR:\n{process.stderr}")
            stderr_lines = process.stderr.strip().splitlines()
            specific_error = stderr_lines[-1] if stderr_lines else "No stderr details"
            # Include attempted code in error for fallback image
            error_message += f"\nDetails: {specific_error}\nCode Attempted:\n{mermaid_code_body[:500]}{'...' if len(mermaid_code_body)>500 else ''}"
            return create_fallback_text_image(diagram_data, language, error_message, mermaid_code_body) # Pass code

        logger.info("Mermaid CLI executed successfully.")

        if not os.path.exists(outfile_path) or os.path.getsize(outfile_path) == 0:
            logger.error("Mermaid CLI did not produce a valid PNG file.")
            return create_fallback_text_image(diagram_data, language, "Error: mmdc created an empty file.", mermaid_code_body)

        with open(outfile_path, 'rb') as f:
            png_bytes = f.read()
        logger.info(f"Successfully read {len(png_bytes)} bytes from {outfile_path}")

        # Add Logo (Optional)
        if PIL_AVAILABLE:
            try:
                logo_path = "voiciologo.png"
                if os.path.exists(logo_path):
                    logger.info(f"Attempting to add logo from: {logo_path}")
                    diagram_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                    logo_img = Image.open(logo_path).convert("RGBA")

                    logo_width = diagram_img.width // 10
                    aspect_ratio = logo_img.height / logo_img.width if logo_img.width > 0 else 1
                    logo_height = int(logo_width * aspect_ratio)
                    if logo_width > 0 and logo_height > 0: # Ensure valid dimensions
                        logo_img = logo_img.resize((logo_width, logo_height))

                        padding = 30
                        position = (diagram_img.width - logo_width - padding,
                                    diagram_img.height - logo_height - padding)

                        diagram_img.paste(logo_img, position, logo_img)

                        output_buffer = io.BytesIO()
                        diagram_img.save(output_buffer, format='PNG')
                        png_bytes = output_buffer.getvalue()
                        logger.info("Successfully added logo to the diagram.")
                    else:
                        logger.warning("Skipping logo paste due to zero dimension.")
                else:
                    logger.info("Logo file not found, skipping logo addition.")
            except Exception as logo_err:
                logger.warning(f"Failed to add logo to diagram: {logo_err}", exc_info=True)
        else:
            logger.warning("Pillow library not installed. Cannot add logo.")

        return png_bytes

    except FileNotFoundError:
        logger.error("Mermaid CLI (mmdc) not found.")
        return create_fallback_text_image(diagram_data, language, "Error: mmdc command not found.", mermaid_code_body)
    except subprocess.TimeoutExpired:
        logger.error("Mermaid CLI process timed out.")
        return create_fallback_text_image(diagram_data, language, "Error: Rendering timed out.", mermaid_code_body)
    except Exception as e:
        logger.error(f"Unexpected error during Mermaid rendering: {e}", exc_info=True)
        return create_fallback_text_image(diagram_data, language, f"Unexpected Error: {e}", mermaid_code_body)
    finally:
        # Ensure cleanup
        try:
            if infile_path and os.path.exists(infile_path): os.remove(infile_path)
            if outfile_path and os.path.exists(outfile_path): os.remove(outfile_path)
        except OSError as clean_e:
             logger.warning(f"Error during temporary file cleanup: {clean_e}")


def create_fallback_text_image(diagram_data: dict, language: str = 'ru', error_info: str = "Failed to render diagram.", mermaid_code: str | None = None) -> bytes | None:
    """
    Creates a simple text-based fallback image when Mermaid rendering fails.

    Args:
        diagram_data: The diagram data dictionary.
        language: The language code for localization.
        error_info: Specific error message to display.
        mermaid_code: The attempted mermaid code body (optional).


    Returns:
        Bytes of the generated PNG image or None on failure.
    """
    if not PIL_AVAILABLE:
        logger.error("Pillow library not found. Cannot create fallback image.")
        return None

    logger.warning(f"Creating fallback text image due to error: {error_info}")
    try:
        title = diagram_data.get("title", "Diagram")
        # Use passed mermaid_code if available, otherwise try to get from dict
        mermaid_code_body = mermaid_code if mermaid_code is not None else diagram_data.get("mermaid_code", "(Mermaid code not available)")

        width, height = 1800, 3200
        background_color = (255, 255, 255)
        text_color = (0, 0, 0)
        error_color = (211, 47, 47)
        code_color = (66, 66, 66)

        image = Image.new('RGB', (width, height), background_color)
        draw = ImageDraw.Draw(image)

        # Font loading
        try:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            mono_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
            title_font = ImageFont.truetype(font_path, 90)
            error_font = ImageFont.truetype(font_path, 60)
            code_font = ImageFont.truetype(mono_font_path, 36)
        except (IOError, OSError):
            logger.warning("DejaVu fonts not found, using default PIL font.")
            title_font = ImageFont.load_default()
            error_font = ImageFont.load_default()
            code_font = ImageFont.load_default()

        # --- Draw Content ---
        y = 100
        # Error Header
        error_header = DIAGRAM_FAILED_TEXT.get(language, DIAGRAM_FAILED_TEXT['en'])
        draw.text((100, y), error_header, fill=error_color, font=title_font)
        y += title_font.getbbox(error_header)[3] + 40 # Use bbox for better spacing

        # Specific Error Info
        wrapped_error = textwrap.fill(f"Details: {error_info}", width=70) # Adjusted width for font
        draw.text((100, y), wrapped_error, fill=error_color, font=error_font)
        y += draw.textbbox((0,0), wrapped_error, font=error_font)[3] + 60

        # Diagram Title
        draw.text((100, y), f"Intended Title: {title}", fill=text_color, font=error_font)
        y += error_font.getbbox(title)[3] + 60

        # Separator
        draw.line([(100, y), (width - 100, y)], fill=(189, 189, 189), width=4)
        y += 60

        # Attempted Mermaid Code
        draw.text((100, y), "Attempted Mermaid Code:", fill=text_color, font=error_font)
        y += error_font.getbbox("Test")[3] + 20 # Approx height + padding

        max_code_lines = 40
        code_lines = mermaid_code_body.strip().split('\n')[:max_code_lines]
        code_to_draw = "\n".join(code_lines)
        if len(mermaid_code_body.strip().split('\n')) > max_code_lines:
            code_to_draw += "\n..."

        # Draw code line by line to handle potential overflows better
        for code_line in code_to_draw.split('\n'):
             # Ensure y stays within image bounds
             if y + code_font.getbbox("Test")[3] > height - 100:
                 draw.text((120, y), "...", fill=code_color, font=code_font)
                 break
             draw.text((120, y), code_line, fill=code_color, font=code_font)
             y += code_font.getbbox(code_line)[3] + 10 # Line height + spacing

        # --- Save Image ---
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        logger.info("Fallback text image created successfully.")
        return img_byte_arr.getvalue()

    except Exception as e:
        logger.error(f"Error creating fallback text image: {e}", exc_info=True)
        return None
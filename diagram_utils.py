import logging
import google.generativeai as genai
import subprocess
import tempfile
import os
import json
from datetime import datetime
import pytz
import re

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
    
    # Choose appropriate language for instructions
    output_language_prompt = {
        'en': "Generate your response in English, including all diagram elements and code comments.",
        'ru': "Сгенерируйте ответ на русском языке, включая все элементы диаграммы и комментарии в коде.",
        'kk': "Барлық диаграмма элементтері мен код түсініктемелерін қоса, қазақ тілінде жауап жасаңыз."
    }.get(language, "Generate your response in Russian, including all diagram elements and code comments.")
    
    diagram_types_prompt = {
        'en': "Choose the most appropriate diagram type for the content (flowchart, sequence, mindmap, etc).",
        'ru': "Выберите наиболее подходящий тип диаграммы для содержимого (блок-схема, последовательность, интеллект-карта и т.д.).",
        'kk': "Мазмұн үшін ең қолайлы диаграмма түрін таңдаңыз (блок-схема, реттілік, ойлау картасы, т.б.)."
    }.get(language, "Choose the most appropriate diagram type for the content (flowchart, sequence, mindmap, etc).")
    
    # Create the prompt
    prompt = f"""
    Analyze the following transcript and create a visual diagram that effectively represents its content.
    {output_language_prompt}
    {diagram_types_prompt}
    
    RESPONSE REQUIREMENTS:
    1. First analyze the content and choose the MOST APPROPRIATE diagram type from the following Mermaid options:
       - flowchart
       - sequenceDiagram
       - classDiagram
       - stateDiagram
       - entityRelationshipDiagram
       - journey
       - gantt
       - pie
       - mindmap
    
    2. Your response must be a JSON object with this EXACT structure:
    {{
      "diagram_type": "the_chosen_diagram_type",
      "title": "A concise title for the diagram",
      "mermaid_code": "The complete Mermaid syntax for the diagram"
    }}
    
    VERY IMPORTANT GUIDELINES:
    - The diagram must be designed for a 9:16 aspect ratio (portrait orientation)
    - Choose a layout that maximizes readability in this tall/narrow format
    - For flowcharts, prefer TD (top-down) or LR (left-right) direction based on content needs
    - Use clear, concise labels that fit well in diagram components
    - If appropriate for content, organize hierarchically or sequentially
    - Optimize for quick visual understanding of the content
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
        
        # Define known diagram type keywords
        diagram_keywords = ["flowchart", "graph", "sequenceDiagram", "classDiagram", 
                            "stateDiagram", "stateDiagram-v2", "erDiagram", "journey", 
                            "gantt", "pie", "mindmap"]
        
        # Check if the code starts with any known diagram type declaration (case-insensitive)
        mermaid_code_lower = mermaid_code.lower()
        found_keyword = None
        for keyword in diagram_keywords:
            # Check for keyword followed by space or newline or TD/LR etc.
            if re.match(rf"^{re.escape(keyword)}(\s|\n|TD|LR|RL|BT)", mermaid_code_lower, re.IGNORECASE):
                 found_keyword = keyword
                 break
                 
        if found_keyword:
            # Strip the found declaration line (handle potential variations)
            # Find the first newline after the keyword
            first_line_end = mermaid_code.find('\n')
            if first_line_end != -1:
                 first_line = mermaid_code[:first_line_end].strip()
                 # Check if the first line primarily contains the keyword and direction
                 if found_keyword in first_line.lower(): # Basic check
                      mermaid_code = mermaid_code[first_line_end:].strip()
                      logger.debug(f"Stripped leading diagram declaration: {first_line}")
            else:
                 # If no newline, the whole code might be the declaration - unlikely but handle
                 if found_keyword in mermaid_code_lower: # Basic check
                      logger.warning("Mermaid code seems to only contain declaration. Returning empty string.")
                      mermaid_code = ""

        # Return the potentially cleaned code
        logger.debug(f"Cleaned Mermaid code body:\n{mermaid_code}")
        return mermaid_code
        
    except Exception as e:
        logger.error(f"Error cleaning Mermaid syntax: {e}", exc_info=True)
        return None

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

    try:
        # Check for Chromium/Chrome first
        chrome_exists = False
        chrome_paths = [
            "/usr/bin/chromium",  # Standard Linux path for Chromium
            "/usr/bin/chromium-browser",  # Alternate Linux chromium name
            "/usr/bin/google-chrome",  # Standard Linux Chrome path
            "/usr/bin/google-chrome-stable"  # Alternate Chrome path
        ]
        
        for path in chrome_paths:
            if os.path.exists(path):
                logger.info(f"Found Chrome/Chromium at: {path}")
                chrome_exists = True
                # Check for required libraries
                try:
                    ldd_output = subprocess.run(
                        ["ldd", path], 
                        capture_output=True, 
                        text=True,
                        check=False
                    )
                    if "libnss3.so" in ldd_output.stdout:
                        logger.info("libnss3.so found in dependencies")
                    else:
                        logger.warning("libnss3.so NOT found in dependencies")
                except Exception as ldd_err:
                    logger.warning(f"Could not check dependencies: {ldd_err}")
                break
        
        if not chrome_exists:
            logger.error("No Chrome or Chromium browser found in standard locations")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False) as infile, \
             tempfile.NamedTemporaryFile(suffix='.png', delete=False) as outfile:
            
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
            outfile_path = outfile.name
        
        # Ensure files are closed before mmdc reads/writes
        infile.close()
        outfile.close()
        
        logger.debug(f"Mermaid input file: {infile_path}")
        logger.debug(f"Mermaid output file: {outfile_path}")

        # Dump puppeteer config to log for inspection
        if os.path.exists("puppeteerConfigFile.json"):
            try:
                with open("puppeteerConfigFile.json", 'r') as config_file:
                    logger.info(f"Puppeteer config: {config_file.read()}")
            except Exception as config_err:
                logger.warning(f"Could not read puppeteerConfigFile.json: {config_err}")
        else:
            logger.warning("puppeteerConfigFile.json not found")

        # Construct the mmdc command with 9:16 aspect ratio
        # Setting width and height to achieve 9:16 ratio (e.g., 900x1600)
        # The exact dimensions can be adjusted as needed
        command = [
            "mmdc", 
            "-i", infile_path, 
            "-o", outfile_path,
            "-b", "transparent",  # Set background transparent
            "-w", "900",  # Width: adjust as needed
            "-H", "1600",  # Height: set for 9:16 ratio
            "--pdfFit",  # Ensure diagram fits in the output
        ]
        
        # Add Puppeteer config if available
        if os.path.exists("puppeteerConfigFile.json"):
            command.extend(["-p", "puppeteerConfigFile.json"])
            
        # Set verbose mode to get more debugging info
        command.extend(["-v"])

        logger.info(f"Executing command: {' '.join(command)}")
        
        # Execute the command with environment variable check
        env = os.environ.copy()
        logger.info(f"Environment variables: PUPPETEER_EXECUTABLE_PATH={env.get('PUPPETEER_EXECUTABLE_PATH', 'Not set')}, "
                    f"PUPPETEER_SKIP_CHROMIUM_DOWNLOAD={env.get('PUPPETEER_SKIP_CHROMIUM_DOWNLOAD', 'Not set')}")
                    
        process = subprocess.run(
            command, 
            capture_output=True, 
            text=True,
            check=False,
            env=env
        )

        if process.returncode != 0:
            logger.error(f"Mermaid CLI failed with exit code {process.returncode}")
            logger.error(f"stderr: {process.stderr}")
            logger.error(f"stdout: {process.stdout}")
            # Clean up temp files on error
            try:
                os.remove(infile_path)
                if os.path.exists(outfile_path) and os.path.getsize(outfile_path) == 0:
                    os.remove(outfile_path)
            except OSError as e:
                logger.warning(f"Error cleaning up temp files: {e}")
                
            # Return the failure text based on language    
            return None
        
        logger.info(f"Mermaid CLI executed successfully. Output PNG: {outfile_path}")
        
        # Read the generated PNG file
        with open(outfile_path, 'rb') as f:
            png_bytes = f.read()

        # Clean up temporary files
        try:
            os.remove(infile_path)
            os.remove(outfile_path)
            logger.debug("Cleaned up temporary Mermaid files.")
        except OSError as e:
            logger.warning(f"Error cleaning up temp files: {e}")

        return png_bytes

    except FileNotFoundError:
        logger.error("Mermaid CLI (mmdc) not found. Ensure it's installed and in the system PATH.")
        return None
    except Exception as e:
        logger.error(f"Error rendering Mermaid diagram: {e}", exc_info=True)
        # Attempt cleanup
        try:
            if 'infile_path' in locals() and os.path.exists(infile_path):
                os.remove(infile_path)
            if 'outfile_path' in locals() and os.path.exists(outfile_path):
                os.remove(outfile_path)
        except OSError as clean_e:
             logger.warning(f"Error during exception cleanup: {clean_e}")
        return None 
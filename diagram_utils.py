import logging
import google.generativeai as genai
import subprocess
import tempfile
import os
import json # Added for parsing Gemini response

logger = logging.getLogger(__name__)

# TODO: Replace with actual path to Mermaid CLI config if needed
MERMAID_CONFIG_PATH = "mermaid_config.json" 
# TODO: Replace with actual path to theme CSS if needed
MERMAID_CSS_PATH = "mermaid_theme.css" 

async def generate_diagram_data(transcript: str, language: str) -> dict | None:
    """
    Sends a prompt to Gemini to extract structured data for a diagram.
    
    Args:
        transcript: The voice message transcript.
        language: The target language (e.g., 'ru', 'en') - currently unused but for future.

    Returns:
        A dictionary representing the diagram structure (e.g., {'title': '...', 'nodes': [...]}) 
        or None on failure.
    """
    logger.info(f"Generating diagram data for transcript (length: {len(transcript)}) in language '{language}'")
    # --- Define the prompt for Gemini ---
    # TODO: Refine this prompt significantly
    prompt = f"""
    Analyze the following transcript and extract the key steps or points to create a vertical flowchart (top-down graph).
    Present the output as a JSON object with the following structure:
    {{
      "title": "A concise title for the diagram",
      "nodes": [
        {{"id": "node1", "text": "Step 1 description"}},
        {{"id": "node2", "text": "Step 2 description"}},
        ...
      ],
      "links": [
        {{"from": "node1", "to": "node2"}},
        {{"from": "node2", "to": "node3"}},
        ...
      ]
    }}
    
    Keep the text for each node concise. Ensure the links represent a logical flow based on the transcript.

    Transcript:
    ---
    {transcript}
    ---
    """

    try:
        # TODO: Use the appropriate Gemini model (Flash or Pro)
        model = genai.GenerativeModel(model_name="models/gemini-1.5-flash") 
        response = await model.generate_content_async(prompt)
        
        # Clean the response: Gemini might wrap JSON in ```json ... ```
        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-3]
        
        diagram_data = json.loads(cleaned_response_text)
        
        # Basic validation
        if not isinstance(diagram_data, dict) or \
           "title" not in diagram_data or \
           "nodes" not in diagram_data or \
           "links" not in diagram_data:
           logger.error(f"Invalid JSON structure received from Gemini: {diagram_data}")
           return None
           
        logger.info(f"Successfully generated diagram data: {diagram_data.get('title')}")
        return diagram_data
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response from Gemini: {e}")
        logger.debug(f"Raw Gemini response text: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Error calling Gemini for diagram data: {e}", exc_info=True)
        return None

def create_mermaid_syntax(diagram_data: dict) -> str | None:
    """
    Converts the structured diagram data into Mermaid syntax.

    Args:
        diagram_data: The dictionary received from generate_diagram_data.

    Returns:
        A string containing the Mermaid syntax (graph TD) or None on failure.
    """
    logger.info("Creating Mermaid syntax from diagram data")
    if not diagram_data:
        return None
        
    try:
        mermaid_lines = ["graph TD"] # Top-Down graph
        
        # Add title as a comment (optional, Mermaid doesn't render titles directly in graph)
        if diagram_data.get("title"):
             # Escape potential comment-ending characters in title
             safe_title = diagram_data["title"].replace("-->", "--\>")
             mermaid_lines.append(f"%% {safe_title}")

        # Define nodes
        for node in diagram_data.get("nodes", []):
            node_id = node.get("id")
            node_text = node.get("text", "")
            # Escape quotes within the text for Mermaid string definition
            escaped_text = node_text.replace('"', '#quot;')
            if node_id and escaped_text:
                # Use single quotes for the outer f-string definition
                mermaid_lines.append(f'    {node_id}["{escaped_text}"]')
                
        # Define links
        for link in diagram_data.get("links", []):
            from_node = link.get("from")
            to_node = link.get("to")
            if from_node and to_node:
                 mermaid_lines.append(f"    {from_node} --> {to_node}")

        syntax = "\n".join(mermaid_lines)
        logger.debug(f"Generated Mermaid syntax:\n{syntax}")
        return syntax
        
    except Exception as e:
        logger.error(f"Error creating Mermaid syntax: {e}", exc_info=True)
        return None


def render_mermaid_to_png(mermaid_syntax: str) -> bytes | None:
    """
    Renders Mermaid syntax to a PNG image using the Mermaid CLI (mmdc).

    Args:
        mermaid_syntax: The Mermaid syntax string.

    Returns:
        Bytes of the rendered PNG image, or None on failure.
    """
    logger.info("Rendering Mermaid syntax to PNG")
    if not mermaid_syntax:
        return None

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False) as infile, \
             tempfile.NamedTemporaryFile(suffix='.png', delete=False) as outfile:
             
            infile.write(mermaid_syntax)
            infile_path = infile.name
            outfile_path = outfile.name
        
        # Ensure files are closed before mmdc reads/writes
        infile.close()
        outfile.close()
        
        logger.debug(f"Mermaid input file: {infile_path}")
        logger.debug(f"Mermaid output file: {outfile_path}")

        # Construct the mmdc command
        # Basic command: mmdc -i input.mmd -o output.png
        # Added: -p puppeteerConfigFile.json (essential for Fly.io/Docker)
        # Added: -b transparent (transparent background)
        # Added: -w 800 (optional: set width)
        # Added: -C theme.css (optional: apply custom theme)
        # TODO: Adjust width, background, theme as needed
        command = [
            "mmdc", 
            "-i", infile_path, 
            "-o", outfile_path,
            "-b", "transparent", # Set background transparent
            # "-w", "1000", # Optional: Control width
            # Add Puppeteer config ONLY if the file exists
        ]
        if os.path.exists("puppeteerConfigFile.json"):
             command.extend(["-p", "puppeteerConfigFile.json"])
        # Add CSS theme ONLY if the file exists
        # if os.path.exists(MERMAID_CSS_PATH):
        #      command.extend(["-C", MERMAID_CSS_PATH])

        logger.debug(f"Executing command: {' '.join(command)}")
        
        # Execute the command
        process = subprocess.run(
            command, 
            capture_output=True, 
            text=True,
            check=False # Don't raise exception on non-zero exit, check manually
        )

        if process.returncode != 0:
            logger.error(f"Mermaid CLI failed with exit code {process.returncode}")
            logger.error(f"stderr: {process.stderr}")
            logger.error(f"stdout: {process.stdout}")
            # Clean up temp files on error before returning
            try:
                os.remove(infile_path)
                # Only remove outfile if it was created but empty/invalid
                if os.path.exists(outfile_path) and os.path.getsize(outfile_path) == 0:
                     os.remove(outfile_path)
            except OSError as e:
                logger.warning(f"Error cleaning up temp files: {e}")
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
        # Attempt cleanup even on other exceptions
        try:
            if 'infile_path' in locals() and os.path.exists(infile_path):
                 os.remove(infile_path)
            if 'outfile_path' in locals() and os.path.exists(outfile_path):
                 os.remove(outfile_path)
        except OSError as clean_e:
             logger.warning(f"Error during exception cleanup: {clean_e}")
        return None 
import asyncio
import logging
import os
import json
from datetime import datetime

import pytz
from diagram_utils import generate_diagram_data, create_mermaid_syntax, render_mermaid_to_png

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

logger = logging.getLogger(__name__)

TEST_TRANSCRIPT = """
This is a test transcript for a Telegram voice-to-text bot. The bot receives voice messages from users and processes them through several steps. First, the user sends a voice message to the bot. The bot then uses Google's Gemini API to transcribe the voice message to text. After transcription, the bot processes the text to create different summary formats based on the user's preferences: bullet points, diagrams, or other formats. Finally, the bot sends the formatted summary back to the user through Telegram. The user can choose their preferred output format using the provided inline keyboard buttons.
"""

async def test_diagram_generation():
    logger.info("Starting diagram generation test")
    
    # Step 1: Generate diagram data from transcript
    diagram_data = await generate_diagram_data(TEST_TRANSCRIPT, "en", "Test User")
    if not diagram_data:
        logger.error("Failed to generate diagram data")
        return
    
    logger.info(f"Generated diagram data: {json.dumps(diagram_data, indent=2)}")
    
    # Step 2: Extract Mermaid syntax
    mermaid_code = create_mermaid_syntax(diagram_data, "en")
    if not mermaid_code:
        logger.error("Failed to extract Mermaid syntax")
        return
    
    logger.info(f"Generated Mermaid code:\n{mermaid_code}")
    
    # Step 3: Render to PNG
    png_bytes = render_mermaid_to_png(mermaid_code, diagram_data, "en")
    if not png_bytes:
        logger.error("Failed to render diagram to PNG")
        return
    
    # Save the output for inspection
    output_path = f"test_diagram_{datetime.now(pytz.utc).strftime('%Y%m%d_%H%M%S')}.png"
    with open(output_path, "wb") as f:
        f.write(png_bytes)
        
    logger.info(f"Successfully generated diagram image: {output_path}")
    logger.info(f"Image size: {len(png_bytes)} bytes")

if __name__ == "__main__":
    asyncio.run(test_diagram_generation()) 
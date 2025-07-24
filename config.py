import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file in development
load_dotenv()

# Check if we're in development mode
IS_DEVELOPMENT = os.environ.get("ENVIRONMENT", "development").lower() == "development"

# Use TEST_BOT_TOKEN if in development and it exists, otherwise use BOT_TOKEN
if IS_DEVELOPMENT and os.environ.get("TEST_BOT_TOKEN"):
    BOT_TOKEN = os.environ.get("TEST_BOT_TOKEN")
    print("Using TEST_BOT_TOKEN for development")
else:
    BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable not set.", file=sys.stderr)
    print("Please create a .env file with your BOT_TOKEN or set it as an environment variable.")
    sys.exit(1) 
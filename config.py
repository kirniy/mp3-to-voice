import os
import sys

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable not set.", file=sys.stderr)
    # Optionally, you could fall back to a local file for testing:
    # print("Trying to read from a local .env file for development...")
    # try:
    #     from dotenv import load_dotenv
    #     load_dotenv()
    #     BOT_TOKEN = os.environ.get("BOT_TOKEN")
    #     if not BOT_TOKEN:
    #         raise ValueError
    # except (ImportError, ValueError):
    #     print("Error: BOT_TOKEN not found in environment or .env file.", file=sys.stderr)
    #     sys.exit(1) # Exit if token is absolutely required
    sys.exit(1) 
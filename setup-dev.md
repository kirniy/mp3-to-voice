# Development Setup for MP3 to Voice Bot

## Overview
This Telegram bot converts MP3 files to voice messages. It's currently hosted on Fly.io but can be run locally for development.

## Prerequisites
- Python 3.10+
- PostgreSQL
- ffmpeg
- Node.js and npm (for Mermaid diagram generation)
- Telegram Bot Token
- Google API Key (for Gemini integration)

## Local Development Setup

### 1. Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install ffmpeg (macOS)
brew install ffmpeg

# Install ffmpeg (Ubuntu/Debian)
sudo apt-get install ffmpeg

# Install Mermaid CLI
npm install -g @mermaid-js/mermaid-cli
```

### 2. Configure Environment

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:
- `BOT_TOKEN`: Get from @BotFather on Telegram
- `GEMINI_API_KEY`: Get from Google AI Studio (https://makersuite.google.com/app/apikey)
- `DATABASE_URL`: PostgreSQL connection string (e.g., `postgresql://user:password@localhost:5432/voicio_db`)
- `ADMIN_USER_ID`: Your Telegram user ID (optional, for admin features)

### 3. Run Locally

#### Option A: Using Docker Compose (Recommended)
```bash
docker-compose up
```

#### Option B: Run Directly
```bash
# Start PostgreSQL (if not using Docker)
# Set up your database connection in .env

# Run the bot
python bot.py
```

## Production Deployment (Fly.io)

The bot is configured to deploy on Fly.io:

```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Login to Fly
fly auth login

# Deploy (from project root)
fly deploy
```

## Configuration Files

- `fly.toml`: Fly.io deployment configuration
- `Dockerfile`: Container setup with all dependencies
- `requirements.txt`: Python dependencies
- `config.py`: Bot configuration loader
- `docker-compose.yml`: Local development with PostgreSQL

## Key Features
- MP3 to voice message conversion
- Multi-language support
- User statistics and diagrams
- Gemini AI integration for audio analysis
- PostgreSQL for data persistence
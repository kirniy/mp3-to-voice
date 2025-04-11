# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install build essentials, ffmpeg, Node.js, npm, and Puppeteer dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    nodejs \
    npm \
    # Puppeteer/Chromium dependencies:
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    wget \
    xdg-utils \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Mermaid CLI globally using npm
# Also consider installing puppeteer dependencies if needed, but mmdc might handle it
# RUN apt-get update && apt-get install -y wget gnupg ca-certificates procps libxss1 && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN npm install -g @mermaid-js/mermaid-cli

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 8080 available to the world outside this container (Fly needs *something* exposed, even if we don't use it)
EXPOSE 8080

# Run bot.py when the container launches
CMD ["python3", "bot.py"]
# CMD printenv && sleep infinity 
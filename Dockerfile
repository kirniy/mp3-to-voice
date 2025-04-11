# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install ffmpeg and Node.js (required for Mermaid CLI)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    # Add required dependencies for Chrome/Puppeteer
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
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
    # Explicitly install libnss3 and related packages
    libnss3 \
    libnss3-tools \
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
    # Debug utilities to verify installations
    procps \
    file \
    libc-bin \
    # Make sure these critical packages are explicitly installed
    chromium \
    chromium-common \
    # Ensure package lists are updated and removed to keep image small
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set environment variables for Puppeteer to use the installed Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# Install Mermaid CLI globally (required by diagram_utils.py)
RUN npm install -g @mermaid-js/mermaid-cli && \
    mmdc --version

# Verify Chrome and dependencies are accessible
RUN which chromium || which chromium-browser || echo "Chromium not found in PATH!" && \
    file /usr/bin/chromium || echo "Chromium binary not accessible!" && \
    ldd /usr/bin/chromium | grep nss || echo "NSS libraries not found in Chromium dependencies" && \
    ls -la /usr/lib/*/libnss* || echo "NSS libraries not found in expected location"

# Set the working directory in the container
WORKDIR /app

# Create puppeteer config file with correct settings directly in the app directory
RUN echo '{ \
  "executablePath": "/usr/bin/chromium", \
  "args": [ \
    "--no-sandbox", \
    "--disable-setuid-sandbox", \
    "--disable-dev-shm-usage", \
    "--disable-accelerated-2d-canvas", \
    "--no-first-run", \
    "--no-zygote", \
    "--single-process", \
    "--disable-gpu" \
  ] \
}' > /app/puppeteerConfigFile.json

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
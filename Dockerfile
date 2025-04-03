# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && apt-get clean && rm -rf /var/lib/apt/lists/*

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

# Define environment variable
ENV BOT_TOKEN=$BOT_TOKEN

# Run bot.py when the container launches
CMD ["python3", "bot.py"] 
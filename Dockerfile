FROM python:3.11-slim

WORKDIR /app

# Install required packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    openssl \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install the latest version of yt-dlp via pip
RUN pip install --no-cache-dir -U yt-dlp
RUN pip install --upgrade pip
RUN pip install yt-dlp --upgrade

# Verify the installation
RUN yt-dlp --version

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p db downloads

# Default environment variables (will be overridden by .env file)
ENV ADMIN_USERNAME=admin
ENV ADMIN_PASSWORD=ytdlp_admin_password
ENV DEBUG=false

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

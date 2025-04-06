# VibeTube

A web interface for managing YouTube downloads with yt-dlp, similar to Sonarr/Radarr but for YouTube content.

## Features

- Add individual videos, channels, or playlists for tracking
- Automatic discovery of new videos in channels and playlists
- Track downloaded content to avoid duplicate downloads
- Customizable download path and filename format
- Built with yt-dlp for maximum YouTube compatibility

## Quick Start

The easiest way to run VibeTube is with Docker:

```bash
# Clone the repository
git clone https://github.com/yourusername/ytdlp-webui.git
cd ytdlp-webui

# Start the container with Docker Compose
docker-compose up -d
```

Then open your browser and navigate to http://localhost:8000

## Manual Installation

If you prefer to run without Docker:

1. Make sure you have Python 3.9+ and yt-dlp installed
2. Clone the repository
3. Install dependencies: `pip install -r requirements.txt`
4. Run the application: `python -m app.main`

## Configuration

All settings can be configured through the web interface:

- **Download Path**: Where videos will be saved
- **Filename Format**: Format for downloaded files
- **Check Interval**: How often to check for new videos
- **Auto Download**: Whether to automatically download new videos

## Usage

### Adding Content

1. Go to the Sources tab
2. Select the type of content you want to add (video, channel, or playlist)
3. Enter the ID for the content
4. Click "Add Source"

### Downloading Videos

Videos can be downloaded:
- Individually from the Videos tab
- In bulk using the "Download All Pending" button
- Automatically if the "Auto Download" setting is enabled

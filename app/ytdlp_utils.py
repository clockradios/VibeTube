import json
import os
import subprocess
import datetime
import xml.dom.minidom
import xml.etree.ElementTree as ET
import time
import threading
import shutil
from typing import List, Dict, Any, Tuple, Optional
import logging
import certifi
import requests
from urllib.parse import quote

from app.models import get_db_session, Video, Source, Setting, Subfolder

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ytdlp_utils")

# Global variables for download queue
download_queue_running = False
download_queue_thread = None

# Add a global variable for library scanner thread
library_scan_running = False
library_scan_thread = None

# Global cookie file management
GLOBAL_COOKIE_FILE = None
COOKIE_TIMESTAMP = 0
COOKIE_LOCK = threading.Lock()

def get_setting(key: str) -> str:
    """Get a setting value from the database"""
    session = get_db_session()
    setting = session.query(Setting).filter_by(key=key).first()
    value = setting.value if setting else None
    session.close()
    return value

def get_cookie_file() -> Optional[str]:
    """Get the global cookie file path, creating it if needed
    Returns None if no cookies are set or if there was an error.
    """
    global GLOBAL_COOKIE_FILE, COOKIE_TIMESTAMP
    
    with COOKIE_LOCK:
        # Get YouTube cookies from settings
        youtube_cookies = get_setting("youtube_cookies")
        
        # If no cookies are set, return None
        if not youtube_cookies or not youtube_cookies.strip():
            if GLOBAL_COOKIE_FILE and os.path.exists(GLOBAL_COOKIE_FILE):
                try:
                    os.remove(GLOBAL_COOKIE_FILE)
                    logger.info(f"Removed global cookie file {GLOBAL_COOKIE_FILE}")
                    GLOBAL_COOKIE_FILE = None
                except Exception as e:
                    logger.error(f"Error removing global cookie file: {e}")
            return None
        
        # Create temp directory if it doesn't exist
        temp_dir = os.path.join(os.path.abspath("downloads"), ".temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        cookie_file_path = os.path.join(temp_dir, "youtube_cookies.txt")
        
        # Check if we need to recreate the cookie file
        # This happens if:
        # 1. The file doesn't exist yet
        # 2. The cookie setting has changed since we created the file
        setting_updated = False
        session = get_db_session()
        cookie_setting = session.query(Setting).filter_by(key="youtube_cookies").first()
        if cookie_setting and cookie_setting.id:
            try:
                # Use the ID to check when it was updated in SQLite
                conn = session.bind.raw_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT rowid FROM settings WHERE key = 'youtube_cookies'")
                row_id = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                
                if row_id > COOKIE_TIMESTAMP:
                    setting_updated = True
                    COOKIE_TIMESTAMP = row_id
            except Exception as e:
                logger.error(f"Error checking cookie timestamp: {e}")
        session.close()
        
        # Create or recreate the cookie file if needed
        if not GLOBAL_COOKIE_FILE or not os.path.exists(cookie_file_path) or setting_updated:
            try:
                with open(cookie_file_path, "w") as f:
                    f.write(youtube_cookies)
                GLOBAL_COOKIE_FILE = cookie_file_path
                logger.info(f"Created global cookie file at {cookie_file_path}")
            except Exception as e:
                logger.error(f"Error creating global cookie file: {e}")
                return None
        
        return GLOBAL_COOKIE_FILE

def get_video_info(video_id: str) -> Optional[Dict[str, Any]]:
    """Get video information using yt-dlp"""
    try:
        # Get cookie file if available
        cookie_file = get_cookie_file()
        
        cmd = [
            "yt-dlp", 
            f"https://www.youtube.com/watch?v={video_id}", 
            "--dump-json",
            "--no-playlist"
        ]
        
        # Add cookies if available
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error getting video info: {e}")
        logger.error(f"Stderr: {e.stderr}")
        return None
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from yt-dlp output")
        return None

def get_channel_videos(channel_id: str) -> List[Dict[str, Any]]:
    """Get all videos from a channel"""
    try:
        # Get cookie file if available
        cookie_file = get_cookie_file()
        
        cmd = [
            "yt-dlp", 
            f"https://www.youtube.com/channel/{channel_id}/videos", 
            "--dump-json",
            "--flat-playlist"
        ]
        
        # Add cookies if available
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        videos = []
        for line in result.stdout.splitlines():
            if line.strip():
                video_data = json.loads(line)
                # If we have a video ID, try to get more detailed info
                if "id" in video_data:
                    detailed_info = get_video_info(video_data["id"])
                    if detailed_info:
                        # Use detailed info but preserve any extra fields from list data
                        for key, value in video_data.items():
                            if key not in detailed_info:
                                detailed_info[key] = value
                        video_data = detailed_info
                videos.append(video_data)
        return videos
    except subprocess.CalledProcessError as e:
        logger.error(f"Error getting channel videos: {e}")
        logger.error(f"Stderr: {e.stderr}")
        return []
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from yt-dlp output")
        return []

def get_playlist_videos(playlist_id: str) -> List[Dict[str, Any]]:
    """Get all videos from a playlist"""
    try:
        # Get cookie file if available
        cookie_file = get_cookie_file()
        
        cmd = [
            "yt-dlp", 
            f"https://www.youtube.com/playlist?list={playlist_id}", 
            "--dump-json",
            "--flat-playlist"
        ]
        
        # Add cookies if available
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        videos = []
        for line in result.stdout.splitlines():
            if line.strip():
                video_data = json.loads(line)
                # If we have a video ID, try to get more detailed info
                if "id" in video_data:
                    detailed_info = get_video_info(video_data["id"])
                    if detailed_info:
                        # Use detailed info but preserve any extra fields from list data
                        for key, value in video_data.items():
                            if key not in detailed_info:
                                detailed_info[key] = value
                        video_data = detailed_info
                videos.append(video_data)
        return videos
    except subprocess.CalledProcessError as e:
        logger.error(f"Error getting playlist videos: {e}")
        logger.error(f"Stderr: {e.stderr}")
        return []
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from yt-dlp output")
        return []

def download_video(video_id: str) -> Tuple[bool, str]:
    """Download a YouTube video as MP4 using yt-dlp"""
    session = get_db_session()
    video = session.query(Video).filter_by(video_id=video_id).first()

    if not video:
        session.close()
        return False, "Video not found in database"

    if video.downloaded:
        session.close()
        return True, "Video already downloaded"
        
    # Reset any previous failed status when retrying
    if video.failed_download:
        video.failed_download = False
        video.error_message = None
        session.commit()

    # Get base download path
    download_path = get_setting("download_path")
    
    # Check if the video's source has a subfolder
    if video.source and video.source.subfolder_id:
        subfolder = session.query(Subfolder).filter_by(id=video.source.subfolder_id).first()
        if subfolder:
            download_path = subfolder.path
    
    # Create a safer filename by replacing any characters that might cause issues
    # Ensure title and channel_name are strings, even if None in DB
    title = video.title or ""
    channel_name = video.channel_name or ""
    
    safe_title = title.replace('/', '_').replace('\\', '_').replace(':', '_').replace('?', '_').replace('"', '_').replace('*', '_').replace('<', '_').replace('>', '_').replace('|', '_')
    safe_channel = channel_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('?', '_').replace('"', '_').replace('*', '_').replace('<', '_').replace('>', '_').replace('|', '_')
    
    # Create individual folder for this video
    # Use video_id if title is empty to avoid creating unnamed folders
    video_folder_name = safe_title if safe_title else video.video_id 
    video_folder_path = os.path.join(download_path, video_folder_name)
    
    # Ensure video directory exists
    os.makedirs(video_folder_path, exist_ok=True)
    
    # Create the video filename (use video_id if title is empty)
    video_filename = f"{video_folder_name}.mp4" 
    output_file = os.path.join(video_folder_path, video_filename)
    logger.info(f"Downloading to: {output_file}")

    if os.path.exists(output_file):
        os.remove(output_file)

    # Set environment variables for SSL handling
    os.environ['SSL_CERT_FILE'] = certifi.where()
    os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
    os.environ['PYTHONHTTPSVERIFY'] = '0'  # Only as a last resort fallback

    try:
        # Fetch detailed video info to create metadata files
        detailed_info = get_video_info(video_id)
        if not detailed_info:
            logger.warning(f"Could not get detailed info for {video_id}, will still try to download")
        
        # Get global cookie file if available
        cookie_file = get_cookie_file()
        
        # First attempt with best MP4 format
        logger.info("Attempting download with best MP4 format...")
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-check-certificate",  # Bypass SSL verification
            "--write-thumbnail",       # Download thumbnail
        ]
        
        # Add cookies if available
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            
        cmd.extend([
            "-o", output_file,
            f"https://www.youtube.com/watch?v={video_id}"
        ])

        logger.info(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        # If first attempt fails, try with simpler format selection
        if result.returncode != 0:
            logger.warning(f"First attempt failed: {result.stderr}")
            logger.info("Attempting download with simpler format...")
            
            cmd = [
                "yt-dlp",
                "-f", "best",
                "--no-check-certificate",
                "--write-thumbnail",  # Download thumbnail
            ]
            
            # Add cookies if available
            if cookie_file:
                cmd.extend(["--cookies", cookie_file])
                
            cmd.extend([
                "-o", output_file,
                f"https://www.youtube.com/watch?v={video_id}"
            ])
            
            logger.info(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"All download attempts failed: {result.stderr}")
            
            # Mark video as failed in the database
            error_message = result.stderr.strip()
            video.failed_download = True
            video.error_message = error_message[:500] if error_message else "Unknown error"
            session.commit()
            session.close()
            
            return False, f"yt-dlp error: {error_message}"

        if not os.path.exists(output_file) or os.path.getsize(output_file) < 10000:
            # Mark video as failed in the database if file is missing or too small
            error_message = "Download failed or file too small"
            video.failed_download = True
            video.error_message = error_message
            session.commit()
            session.close()
            
            return False, error_message
        
        # Find and handle the downloaded thumbnail
        thumbnail_path = None
        for ext in ['jpg', 'jpeg', 'png', 'webp']:
            potential_thumb = os.path.splitext(output_file)[0] + f".{ext}"
            if os.path.exists(potential_thumb):
                thumbnail_path = potential_thumb
                break
        
        # If thumbnail is found, update the database with the path
        if thumbnail_path:
            logger.info(f"Found thumbnail at {thumbnail_path}")
            # Make the path relative to serve it via the app
            rel_path = os.path.relpath(thumbnail_path, get_setting("download_path"))
            # Update the thumbnail URL in the database with proper URL encoding
            video.thumbnail_url = f"/downloads/{quote(rel_path)}"
        else:
            # If thumbnail wasn't downloaded, attempt to download it directly
            if detailed_info and "thumbnail" in detailed_info:
                try:
                    # Use the default thumbnail or the one from detailed info
                    thumbnail_url = detailed_info.get("thumbnail")
                    if thumbnail_url:
                        logger.info(f"Downloading thumbnail from {thumbnail_url}")
                        
                        # Determine filename based on video filename
                        thumb_filename = os.path.splitext(os.path.basename(output_file))[0] + ".jpg"
                        thumb_path = os.path.join(video_folder_path, thumb_filename)
                        
                        # Download the thumbnail
                        response = requests.get(thumbnail_url, stream=True, timeout=10)
                        if response.status_code == 200:
                            with open(thumb_path, 'wb') as f:
                                for chunk in response.iter_content(chunk_size=8192):
                                    f.write(chunk)
                            
                            # Update the thumbnail URL in the database with proper URL encoding
                            rel_path = os.path.relpath(thumb_path, get_setting("download_path"))
                            video.thumbnail_url = f"/downloads/{quote(rel_path)}"
                            logger.info(f"Downloaded thumbnail to {thumb_path}")
                        else:
                            logger.warning(f"Failed to download thumbnail: HTTP {response.status_code}")
                except Exception as e:
                    logger.error(f"Error downloading thumbnail: {e}")
        
        # Create Jellyfin NFO metadata file
        create_jellyfin_nfo(video, detailed_info, video_folder_path)
        
        # Create Plex metadata files
        create_plex_metadata(video, detailed_info, video_folder_path)

        # Mark as downloaded and clear any failed flag
        video.downloaded = True
        video.download_date = datetime.datetime.utcnow()
        video.download_path = output_file  # Store the actual file path
        video.failed_download = False
        video.error_message = None
        session.commit()
        session.close()

        return True, f"Downloaded successfully: {output_file}"

    except Exception as e:
        logger.error(f"Exception during download: {e}")
        
        # Mark video as failed in the database
        error_message = str(e)
        video.failed_download = True
        video.error_message = error_message[:500] if error_message else "Unknown error"
        session.commit()
        session.close()
        
        return False, f"Exception: {error_message}"

def create_jellyfin_nfo(video: Video, detailed_info: Dict[str, Any], output_dir: str) -> None:
    """Create a .nfo file for Jellyfin metadata"""
    try:
        # Ensure title is a string, use video_id if None/empty
        title = video.title or video.video_id
        safe_title = title.replace('/', '_').replace('\\', '_').replace(':', '_') \
                       .replace('?', '_').replace('"', '_').replace('*', '_') \
                       .replace('<', '_').replace('>', '_').replace('|', '_')
        
        nfo_filename = os.path.join(output_dir, f"{safe_title}.nfo")
        
        # Get upload date in YYYY-MM-DD format
        upload_date_str = ""
        if detailed_info and "upload_date" in detailed_info:
            upload_date_str = detailed_info["upload_date"][:10]
        
        # Create the XML structure
        movie = ET.Element("movie")
        
        # Basic info
        title_element = ET.SubElement(movie, "title")
        title_element.text = title
        
        originaltitle = ET.SubElement(movie, "originaltitle")
        originaltitle.text = title
        
        # YouTube specific ID
        id_element = ET.SubElement(movie, "id")
        id_element.text = video.video_id
        
        # YouTube URL
        youtube_url = ET.SubElement(movie, "youtube")
        youtube_url.text = f"https://www.youtube.com/watch?v={video.video_id}"
        
        # Additional metadata if available
        if detailed_info:
            if "upload_date" in detailed_info:
                # Format date as YYYY-MM-DD
                date_str = detailed_info.get("upload_date", "")
                if len(date_str) == 8:  # YYYYMMDD format
                    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    premiered = ET.SubElement(movie, "premiered")
                    premiered.text = formatted_date
                    
                    year = ET.SubElement(movie, "year")
                    year.text = date_str[:4]
            
            if "description" in detailed_info:
                plot = ET.SubElement(movie, "plot")
                plot.text = detailed_info["description"]
                
                outline = ET.SubElement(movie, "outline")
                outline.text = detailed_info["description"][:200] + "..." if len(detailed_info["description"]) > 200 else detailed_info["description"]
            
            if "duration" in detailed_info:
                runtime = ET.SubElement(movie, "runtime")
                # Convert seconds to minutes
                runtime.text = str(int(detailed_info["duration"] / 60))
            
            if "channel" in detailed_info:
                studio = ET.SubElement(movie, "studio")
                studio.text = detailed_info["channel"]
                
                director = ET.SubElement(movie, "director")
                director.text = detailed_info["channel"]
        
        # Default values if not in detailed_info
        if video.channel_name:
            studio = movie.find("studio")
            if studio is None:
                studio = ET.SubElement(movie, "studio")
                studio.text = video.channel_name
                
            director = movie.find("director")
            if director is None:
                director = ET.SubElement(movie, "director")
                director.text = video.channel_name
        
        if video.upload_date:
            year = movie.find("year")
            if year is None and len(video.upload_date) >= 4:
                year = ET.SubElement(movie, "year")
                year.text = video.upload_date[:4]
        
        # Add source tag for YouTube
        source = ET.SubElement(movie, "source")
        source.text = "YouTube"
        
        # Write formatted XML to the NFO file
        tree = ET.ElementTree(movie)
        xml_str = ET.tostring(movie, encoding='unicode')
        
        # Format the XML to be pretty and readable
        dom = xml.dom.minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ")
        
        # Save the NFO file with the same name as the video
        nfo_filename = f"{safe_title}.nfo"
        nfo_path = os.path.join(output_dir, nfo_filename)
        
        with open(nfo_path, 'w', encoding='utf-8') as f:
            f.write(pretty_xml)
            
        logger.info(f"Created Jellyfin NFO file: {nfo_path}")
        
    except Exception as e:
        logger.error(f"Error creating NFO file: {e}")

def create_plex_metadata(video: Video, detailed_info: Dict[str, Any], output_dir: str) -> None:
    """Create metadata files compatible with Plex Movie agent"""
    try:
        # Ensure title is a string, use video_id if None/empty
        title = video.title or video.video_id
        safe_title = title.replace('/', '_').replace('\\', '_').replace(':', '_') \
                       .replace('?', '_').replace('"', '_').replace('*', '_') \
                       .replace('<', '_').replace('>', '_').replace('|', '_')
        
        # Plex expects metadata files named after the video file
        video_filename_base = safe_title
        
        # Create summary file (.summary)
        summary_filename = os.path.join(output_dir, f"{video_filename_base}.summary")
        
        # Create the XML structure for Plex
        metadata = ET.Element("metadata")
        
        # Basic info
        title_element = ET.SubElement(metadata, "title")
        title_element.text = title
        
        # YouTube URL and ID
        youtube = ET.SubElement(metadata, "youtube")
        youtube.text = f"https://www.youtube.com/watch?v={video.video_id}"
        
        # Additional metadata if available
        if detailed_info:
            if "description" in detailed_info:
                summary = ET.SubElement(metadata, "summary")
                summary.text = detailed_info["description"]
            
            if "upload_date" in detailed_info:
                # Format date as YYYY-MM-DD
                date_str = detailed_info.get("upload_date", "")
                if len(date_str) == 8:  # YYYYMMDD format
                    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    year = ET.SubElement(metadata, "year")
                    year.text = date_str[:4]
                    
                    originally_available = ET.SubElement(metadata, "originally_available")
                    originally_available.text = formatted_date
            
            if "channel" in detailed_info:
                studio = ET.SubElement(metadata, "studio")
                studio.text = detailed_info["channel"]
                
                # Add channel as a director tag which Plex often uses
                director = ET.SubElement(metadata, "director")
                director.text = detailed_info["channel"]
        
        # Default values if not in detailed_info
        if video.channel_name:
            studio = metadata.find("studio")
            if studio is None:
                studio = ET.SubElement(metadata, "studio")
                studio.text = video.channel_name
                
            director = metadata.find("director")
            if director is None:
                director = ET.SubElement(metadata, "director")
                director.text = video.channel_name
        
        if video.upload_date:
            year = metadata.find("year")
            if year is None and len(video.upload_date) >= 4:
                year = ET.SubElement(metadata, "year")
                year.text = video.upload_date[:4]
        
        # Write formatted XML to the metadata file
        tree = ET.ElementTree(metadata)
        xml_str = ET.tostring(metadata, encoding='unicode')
        
        # Format the XML to be pretty and readable
        dom = xml.dom.minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ")
        
        # Save the XML file with the name that Plex prefers
        xml_filename = f"{safe_title}.xml"
        xml_path = os.path.join(output_dir, xml_filename)
        
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write(pretty_xml)
            
        logger.info(f"Created Plex metadata file: {xml_path}")
        
    except Exception as e:
        logger.error(f"Error creating Plex metadata file: {e}")

def process_download_queue():
    """
    Process the download queue, downloading videos at the rate specified in settings
    This function runs in its own thread
    """
    global download_queue_running
    
    logger.info("Starting download queue processor")
    
    while download_queue_running:
        try:
            # Check global auto_download setting first
            global_auto_download = get_setting("auto_download").lower() == "true"
            
            # Get the download delay setting
            try:
                download_delay = int(get_setting("download_delay"))
                # Set a minimum delay of 5 seconds to prevent hammering the servers
                if download_delay < 5:
                    download_delay = 5
                    logger.warning("Download delay set below minimum of 5 seconds, using 5 seconds instead")
            except (ValueError, TypeError):
                download_delay = 60  # Default to 60 seconds if setting is invalid
                logger.warning("Invalid download_delay setting, using default of 60 seconds")
            
            # If global auto_download is disabled, just wait and check again
            if not global_auto_download:
                logger.debug("Global auto-download is disabled, waiting...")
                time.sleep(10)
                continue
                
            # Get next undownloaded video that belongs to a source with auto_download enabled
            # or is a single video (which always gets auto-downloaded)
            session = get_db_session()
            
            # First, we build a query that includes videos where:
            # 1. The video is from a source with auto_download=True, OR
            # 2. The video is a standalone video (not part of a channel/playlist)
            # And the video is not downloaded, not deleted, not skipped, and not failed
            query = session.query(Video).filter_by(
                downloaded=False, 
                file_deleted=False, 
                skip=False,
                failed_download=False
            )
            
            # Get all videos from sources with auto_download=True
            auto_download_sources = session.query(Source.id).filter_by(auto_download=True).all()
            auto_download_source_ids = [s[0] for s in auto_download_sources]
            
            # Find videos that match our criteria
            if auto_download_sources:
                # Auto-downloading sources exist
                video = query.filter(
                    (Video.source_id.in_(auto_download_source_ids))
                ).first()
            else:
                # No auto-downloading sources, check for standalone videos
                standalone_videos = session.query(Video).join(Source).filter(
                    Source.source_type == 'video',
                    Video.downloaded == False,
                    Video.file_deleted == False,
                    Video.skip == False,
                    Video.failed_download == False
                ).first()
                video = standalone_videos
            
            if video:
                logger.info(f"Processing download queue: downloading video {video.video_id}")
                # Release the session first so the download function can use its own session
                session.close()
                
                # Download the video
                success, message = download_video(video.video_id)
                if success:
                    logger.info(f"Queue processor: Downloaded {video.video_id} successfully")
                else:
                    logger.error(f"Queue processor: Failed to download {video.video_id}: {message}")
                
                # Wait according to the download_delay setting before processing the next download
                logger.info(f"Waiting {download_delay} seconds before next download")
                for _ in range(download_delay):
                    if not download_queue_running:
                        break
                    time.sleep(1)
            else:
                session.close()
                # No videos to download, wait 10 seconds and check again
                for _ in range(10):
                    if not download_queue_running:
                        break
                    time.sleep(1)
        
        except Exception as e:
            logger.error(f"Error in download queue processor: {e}")
            # Wait a bit before retrying to avoid hammering in case of persistent errors
            time.sleep(10)
    
    logger.info("Download queue processor stopped")

def start_download_queue():
    """Start the download queue processor thread"""
    global download_queue_running, download_queue_thread
    
    if download_queue_running:
        return  # Already running
    
    download_queue_running = True
    download_queue_thread = threading.Thread(target=process_download_queue, daemon=True)
    download_queue_thread.start()
    logger.info("Download queue processor started")

def stop_download_queue():
    """Stop the download queue processor thread"""
    global download_queue_running
    
    if not download_queue_running:
        return  # Already stopped
    
    download_queue_running = False
    logger.info("Download queue processor stopping...")

def add_source(source_type: str, source_id: str, subfolder_id: Optional[int] = None, auto_download: bool = True) -> Tuple[bool, str]:
    """Add a source (video, channel, playlist) to the database"""
    session = get_db_session()
    
    # Check if source already exists
    existing = session.query(Source).filter_by(source_id=source_id).first()
    if existing:
        session.close()
        return False, f"{source_type.capitalize()} already exists in database"
    
    # Get source information
    name = None
    videos_info = []
    
    if source_type == "video":
        video_info = get_video_info(source_id)
        if video_info:
            name = video_info.get("title", "Unknown video")
            videos_info = [video_info]
        else:
            session.close()
            return False, "Failed to get video information"
    
    elif source_type == "channel":
        videos_info = get_channel_videos(source_id)
        if videos_info:
            # Use the first video's channel name as the source name
            # This is a bit of a simplification - might need to fetch channel details separately
            name = videos_info[0].get("channel", "Unknown channel")
        else:
            session.close()
            return False, "Failed to get channel information or no videos found"
    
    elif source_type == "playlist":
        videos_info = get_playlist_videos(source_id)
        if videos_info:
            # For playlists, we'd need to get the playlist name separately
            # For now, just use a placeholder
            name = f"Playlist {source_id}"
        else:
            session.close()
            return False, "Failed to get playlist information or no videos found"
    
    # If no subfolder was specified, get the default one
    if subfolder_id is None:
        default_subfolder = session.query(Subfolder).filter_by(is_default=True).first()
        if default_subfolder:
            subfolder_id = default_subfolder.id
    
    # Create the source
    new_source = Source(
        source_type=source_type,
        source_id=source_id,
        name=name,
        added_at=datetime.datetime.utcnow(),
        last_checked=datetime.datetime.utcnow(),
        subfolder_id=subfolder_id,
        auto_download=auto_download  # Set auto_download from parameter
    )
    session.add(new_source)
    session.flush()  # To get the new source ID
    
    # Add videos
    for video_data in videos_info:
        video_id = video_data.get("id", "")
        
        # Skip if video already exists
        if session.query(Video).filter_by(video_id=video_id).first():
            continue
            
        # Check if upload_date is in YYYYMMDD format, if not, convert it
        upload_date = video_data.get("upload_date", "")
        
        # Format might be ISO (YYYY-MM-DD), convert to YYYYMMDD if needed
        if upload_date and "-" in upload_date:
            upload_date = upload_date.replace("-", "")[:8]
            
        # Get full video info for single videos (for channels/playlists we can use the list data)
        if source_type == "video":
            video = Video(
                video_id=video_id,
                title=video_data.get("title", "Unknown"),
                channel_name=video_data.get("channel", "Unknown"),
                upload_date=upload_date,
                source_id=new_source.id,
                thumbnail_url=video_data.get("thumbnail", ""),
                duration=video_data.get("duration", 0)
            )
        else:
            video = Video(
                video_id=video_id,
                title=video_data.get("title", "Unknown"),
                channel_name=video_data.get("channel", "Unknown"),
                upload_date=upload_date,
                source_id=new_source.id,
                thumbnail_url=video_data.get("thumbnail", "")
            )
        
        session.add(video)
    
    session.commit()
    video_count = len(videos_info)
    session.close()
    
    return True, f"Added {source_type} with {video_count} videos"

def refresh_sources() -> Tuple[int, int]:
    """Check all sources for new videos"""
    session = get_db_session()
    sources = session.query(Source).all()
    new_videos_count = 0
    
    for source in sources:
        videos_info = []
        
        if source.source_type == "video":
            # For videos, we don't need to refresh as they're singular
            continue
        
        elif source.source_type == "channel":
            videos_info = get_channel_videos(source.source_id)
        
        elif source.source_type == "playlist":
            videos_info = get_playlist_videos(source.source_id)
        
        # Add new videos to database
        for video_data in videos_info:
            video_id = video_data.get("id", "")
            
            # Skip if video already exists
            if session.query(Video).filter_by(video_id=video_id).first():
                continue
            
            # Check if upload_date is in YYYYMMDD format, if not, convert it
            upload_date = video_data.get("upload_date", "")
            
            # Format might be ISO (YYYY-MM-DD), convert to YYYYMMDD if needed
            if upload_date and "-" in upload_date:
                upload_date = upload_date.replace("-", "")[:8]
            
            video = Video(
                video_id=video_id,
                title=video_data.get("title", "Unknown"),
                channel_name=video_data.get("channel", "Unknown"),
                upload_date=upload_date,
                source_id=source.id,
                thumbnail_url=video_data.get("thumbnail", ""),
                # If auto_download is disabled for this source, mark videos with skip=True
                # so they don't get automatically downloaded
                skip=not source.auto_download
            )
            
            session.add(video)
            new_videos_count += 1
        
        # Update last checked timestamp
        source.last_checked = datetime.datetime.utcnow()
    
    session.commit()
    source_count = len(sources)
    session.close()
    
    return source_count, new_videos_count

def delete_video_files(video_id: str) -> Tuple[bool, str]:
    """Delete a video's files from disk and mark as deleted in the database"""
    session = get_db_session()
    video = session.query(Video).filter_by(video_id=video_id).first()

    if not video:
        session.close()
        return False, "Video not found in database"

    if not video.downloaded:
        session.close()
        return False, "Video has not been downloaded"

    if not video.download_path or not os.path.exists(os.path.dirname(video.download_path)):
        # Video was downloaded but files are already gone
        video.file_deleted = True
        video.downloaded = False
        video.skip = True
        session.commit()
        session.close()
        return True, "Video marked as deleted (files were already removed)"

    try:
        # Get the folder containing the video
        video_folder = os.path.dirname(video.download_path)
        
        # Delete the entire folder
        if os.path.exists(video_folder):
            shutil.rmtree(video_folder)
            logger.info(f"Deleted video folder: {video_folder}")
        
        # Mark as deleted in the database
        video.file_deleted = True
        video.downloaded = False
        video.skip = True
        session.commit()
        session.close()
        
        return True, "Video files deleted successfully"
    except Exception as e:
        logger.error(f"Error deleting video files: {e}")
        session.close()
        return False, f"Error: {str(e)}"

def scan_library():
    """
    Scan the library to detect deleted files
    This function runs in its own thread
    """
    global library_scan_running
    
    logger.info("Starting library scanner")
    
    while library_scan_running:
        try:
            # Get scan interval from settings
            scan_interval = int(get_setting("scan_interval"))
            
            # Get all downloaded videos
            session = get_db_session()
            videos = session.query(Video).filter_by(downloaded=True).all()
            
            changed_count = 0
            for video in videos:
                # Check if the file exists
                if not video.download_path or not os.path.exists(video.download_path):
                    # File is missing, mark as not downloaded and skipped
                    video.downloaded = False
                    video.file_deleted = True
                    video.skip = True
                    changed_count += 1
                    logger.info(f"Library scan: Marked video {video.video_id} as deleted and skipped (file not found)")
            
            if changed_count > 0:
                session.commit()
                logger.info(f"Library scan: Found {changed_count} missing videos")
            
            session.close()
            
            # Sleep until next scan
            for _ in range(scan_interval):
                if not library_scan_running:
                    break
                time.sleep(1)
        
        except Exception as e:
            logger.error(f"Error in library scanner: {e}")
            time.sleep(60)  # Sleep for a minute if there's an error
    
    logger.info("Library scanner stopped")

def start_library_scanner():
    """Start the library scanner thread"""
    global library_scan_running, library_scan_thread
    
    if library_scan_running:
        return  # Already running
    
    library_scan_running = True
    library_scan_thread = threading.Thread(target=scan_library, daemon=True)
    library_scan_thread.start()
    logger.info("Library scanner started")

def stop_library_scanner():
    """Stop the library scanner thread"""
    global library_scan_running
    
    if not library_scan_running:
        return  # Already stopped
    
    library_scan_running = False
    logger.info("Library scanner stopping...") 
import os
import uvicorn
import logging
import threading
import time
import json
import subprocess
import datetime
import sqlite3
import shutil
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, Request, Form, Depends, HTTPException, BackgroundTasks, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from fastapi import Body

from app.models import get_db_session, Video, Source, Setting, Subfolder, initialize_db, initialize_settings, VibePlaylist, VibePlaylistItem
from app.ytdlp_utils import (
    add_source, 
    download_video, 
    refresh_sources, 
    get_setting, 
    start_download_queue, 
    stop_download_queue,
    delete_video_files,
    start_library_scanner,
    stop_library_scanner,
    get_cookie_file
)
from app.auth import (
    setup_auth, create_session, validate_session, get_session_username, 
    get_admin_user, ADMIN_USERNAME, ADMIN_PASSWORD
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ytdlp-webui")

# Initialize settings
initialize_settings()

app = FastAPI(title="VibeTube")

# Set up auth middleware
setup_auth(app)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount the downloads directory to serve downloaded files and thumbnails
downloads_path = os.path.abspath("downloads")
os.makedirs(downloads_path, exist_ok=True)
app.mount("/downloads", StaticFiles(directory=downloads_path), name="downloads")

# Setup templates
templates_dir = os.path.abspath("templates")
templates = Jinja2Templates(directory=templates_dir)

# Start the download queue processor
start_download_queue()

# Background task for checking sources periodically
def background_checker():
    while True:
        try:
            check_interval = int(get_setting("check_interval"))
            auto_download = get_setting("auto_download").lower() == "true"
            
            # Refresh sources
            sources_count, new_videos = refresh_sources()
            logger.info(f"Refreshed {sources_count} sources, found {new_videos} new videos")
            
            # Log auto_download status
            if not auto_download:
                logger.info("Auto-download is disabled in settings. Videos will be tracked but not automatically downloaded.")
            
            # Sleep until next check
            time.sleep(check_interval)
        except Exception as e:
            logger.error(f"Error in background checker: {e}")
            time.sleep(60)  # Sleep for a minute if there's an error

# Start background task
background_thread = threading.Thread(target=background_checker, daemon=True)
background_thread.start()

# Helper to get database session
def get_db():
    db = get_db_session()
    try:
        yield db
    finally:
        db.close()

# Shutdown event handler to stop the download queue
@app.on_event("shutdown")
async def shutdown_event():
    stop_download_queue()
    stop_library_scanner()
    logger.info("Application shutdown")

@app.on_event("startup")
def startup_event():
    initialize_db()
    initialize_settings()
    start_download_queue()
    start_library_scanner()
    
    # Initialize the global cookie file
    cookie_file = get_cookie_file()
    if cookie_file:
        logger.info(f"Initialized global cookie file at {cookie_file}")
    
    logger.info("Application started")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    """Render the home page"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get all settings
    settings_list = db.query(Setting).all()
    settings = {s.key: s.value for s in settings_list}
    
    # Get statistic counts
    video_count = db.query(Video).count()
    downloaded_count = db.query(Video).filter_by(downloaded=True).count()
    source_count = db.query(Source).count()
    queued_count = db.query(Video).filter_by(downloaded=False, file_deleted=False, skip=False).count()
    
    # Get last 5 downloaded videos
    recent_downloads = db.query(Video).filter_by(downloaded=True).order_by(Video.download_date.desc()).limit(5).all()
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "username": username,
            "title": "Dashboard - VibeTube",
            "video_count": video_count,
            "downloaded_count": downloaded_count,
            "source_count": source_count,
            "queued_count": queued_count,
            "recent_downloads": recent_downloads,
            "settings": settings
        }
    )

@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, db: Session = Depends(get_db)):
    """Display the sources page with a list of all sources and their videos"""
    # Get username for the template
    username = get_session_username(request)
    
    sources = db.query(Source).all()
    subfolders = db.query(Subfolder).all()
    
    # Get videos for each source
    source_data = []
    for source in sources:
        videos = db.query(Video).filter_by(source_id=source.id).all()
        downloaded_count = sum(1 for v in videos if v.downloaded)
        pending_count = sum(1 for v in videos if not v.downloaded and not v.file_deleted)
        deleted_count = sum(1 for v in videos if v.file_deleted)
        
        source_data.append({
            "source": source,
            "video_count": len(videos),
            "downloaded_count": downloaded_count,
            "pending_count": pending_count,
            "deleted_count": deleted_count
        })
    
    return templates.TemplateResponse(
        "sources.html",
        {
            "request": request,
            "username": username,
            "sources": source_data,
            "subfolders": subfolders,
            "title": "Sources - VibeTube"
        }
    )

@app.get("/videos", response_class=HTMLResponse)
async def videos(
    request: Request, 
    source_id: Optional[int] = None,
    downloaded: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Render the videos page with filtering options"""
    # Get username for the template
    username = get_session_username(request)
    
    # Build query with filters
    query = db.query(Video)
    active_filters = {}
    
    # Track the download status for template
    current_downloaded = None
    
    if source_id:
        query = query.filter(Video.source_id == source_id)
        active_filters["source_id"] = source_id
    
    if downloaded:
        if downloaded == "true":
            query = query.filter(Video.downloaded == True)
            current_downloaded = True
        elif downloaded == "false":
            query = query.filter(Video.downloaded == False, 
                                Video.file_deleted == False, 
                                Video.skip == False,
                                Video.failed_download == False)
            current_downloaded = False
        elif downloaded == "deleted":
            query = query.filter(Video.file_deleted == True)
            current_downloaded = "deleted"
        elif downloaded == "skipped":
            query = query.filter(Video.skip == True)
            current_downloaded = "skipped"
        elif downloaded == "failed":
            query = query.filter(Video.failed_download == True)
            current_downloaded = "failed"
    
    # Get videos with pagination
    videos = query.order_by(Video.id.desc()).all()
    
    # Get sources for the filter dropdown
    sources = db.query(Source).all()
    
    return templates.TemplateResponse(
        "videos.html",
        {
            "request": request,
            "username": username,
            "title": "Videos - VibeTube",
            "videos": videos,
            "sources": sources,
            "current_source_id": source_id,
            "current_downloaded": current_downloaded
        }
    )

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """Render the settings page"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get all settings
    settings = db.query(Setting).all()
    
    # Convert to dictionary for easier access in template
    settings_dict = {s.key: s.value for s in settings}
    
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "username": username,
            "title": "Settings - VibeTube",
            "settings": settings_dict
        }
    )

@app.get("/subfolders", response_class=HTMLResponse)
async def subfolders_page(request: Request, db: Session = Depends(get_db)):
    """Render the subfolders page"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get all subfolders
    subfolders = db.query(Subfolder).all()
    
    return templates.TemplateResponse(
        "subfolders.html",
        {
            "request": request,
            "username": username,
            "title": "Subfolders - VibeTube",
            "subfolders": subfolders
        }
    )

@app.post("/add_subfolder")
async def add_subfolder(
    name: str = Form(...),
    description: str = Form(""),
    is_default: bool = Form(False),
    db: Session = Depends(get_db)
):
    # Check if name already exists
    existing = db.query(Subfolder).filter_by(name=name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Subfolder with name '{name}' already exists")
    
    download_path = get_setting("download_path")
    subfolder_path = os.path.join(download_path, name.replace(" ", "_").lower())
    
    # If setting as default, unset current default
    if is_default:
        current_default = db.query(Subfolder).filter_by(is_default=True).first()
        if current_default:
            current_default.is_default = False
    
    # Create subfolder
    new_subfolder = Subfolder(
        name=name,
        path=subfolder_path,
        description=description,
        is_default=is_default
    )
    db.add(new_subfolder)
    
    # Create directory
    os.makedirs(subfolder_path, exist_ok=True)
    
    db.commit()
    return RedirectResponse(url="/subfolders", status_code=303)

@app.delete("/subfolder/{subfolder_id}")
async def delete_subfolder(subfolder_id: int, db: Session = Depends(get_db)):
    subfolder = db.query(Subfolder).filter_by(id=subfolder_id).first()
    
    if not subfolder:
        raise HTTPException(status_code=404, detail=f"Subfolder {subfolder_id} not found")
    
    # Don't allow deleting the default subfolder
    if subfolder.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default subfolder")
    
    # Update any sources using this subfolder to use the default subfolder
    default_subfolder = db.query(Subfolder).filter_by(is_default=True).first()
    if default_subfolder:
        sources = db.query(Source).filter_by(subfolder_id=subfolder_id).all()
        for source in sources:
            source.subfolder_id = default_subfolder.id
    
    db.delete(subfolder)
    db.commit()
    return {"message": f"Subfolder {subfolder_id} deleted"}

@app.post("/set_default_subfolder/{subfolder_id}")
async def set_default_subfolder(subfolder_id: int, db: Session = Depends(get_db)):
    subfolder = db.query(Subfolder).filter_by(id=subfolder_id).first()
    
    if not subfolder:
        raise HTTPException(status_code=404, detail=f"Subfolder {subfolder_id} not found")
    
    # Unset current default
    current_default = db.query(Subfolder).filter_by(is_default=True).first()
    if current_default:
        current_default.is_default = False
    
    # Set new default
    subfolder.is_default = True
    db.commit()
    
    return RedirectResponse(url="/subfolders", status_code=303)

@app.post("/add_source")
async def add_source_endpoint(
    source_type: str = Form(...),
    source_id: str = Form(...),
    db: Session = Depends(get_db),
    subfolder_id: Optional[int] = Form(None),
    auto_download: bool = Form(True),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    # If no subfolder specified, use the default
    if not subfolder_id:
        default_subfolder = db.query(Subfolder).filter_by(is_default=True).first()
        if default_subfolder:
            subfolder_id = default_subfolder.id
        else:
            # Handle case where no default subfolder exists if necessary
            # For now, we might proceed without a subfolder or raise an error
            # Let's log a warning and proceed without a specific subfolder_id for now
            logger.warning("No default subfolder found, adding source without a specific subfolder.")
            subfolder_id = None # Explicitly set to None

    # Add the potentially long-running task to the background
    background_tasks.add_task(add_source, source_type, source_id, subfolder_id, auto_download)

    # Return immediately, informing the user the process has started
    # Redirecting to the sources page might be best UX for now
    return RedirectResponse(url="/sources", status_code=303)

@app.post("/download_video/{video_id}")
async def download_video_endpoint(
    video_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    background_tasks.add_task(download_video, video_id)
    return {"message": f"Download for video {video_id} started"}

@app.post("/refresh_sources")
async def refresh_sources_endpoint():
    threading.Thread(target=refresh_sources, daemon=True).start()
    return {"message": "Refreshing sources in background"}

@app.post("/scan_library")
async def scan_library_endpoint():
    """Manually trigger a library scan to detect deleted files"""
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
            logger.info(f"Manual library scan: Marked video {video.video_id} as deleted and skipped (file not found)")
    
    if changed_count > 0:
        session.commit()
        logger.info(f"Manual library scan: Found {changed_count} missing videos")
    
    session.close()
    return {"message": f"Library scan complete. Found {changed_count} missing videos.", "changed_count": changed_count}

@app.post("/update_setting")
async def update_setting(
    key: str = Form(...),
    value: str = Form(...),
    db: Session = Depends(get_db)
):
    setting = db.query(Setting).filter_by(key=key).first()
    
    if setting:
        setting.value = value
        db.commit()
        return RedirectResponse(url="/settings", status_code=303)
    else:
        # For youtube_cookies specifically, create the setting if it doesn't exist
        if key == "youtube_cookies":
            new_setting = Setting(key=key, value=value)
            db.add(new_setting)
            db.commit()
            return RedirectResponse(url="/settings", status_code=303)
        else:
            raise HTTPException(status_code=404, detail=f"Setting {key} not found")

@app.delete("/source/{source_id}")
async def delete_source(
    source_id: int, 
    delete_videos: bool = False,
    db: Session = Depends(get_db)
):
    source = db.query(Source).filter_by(id=source_id).first()
    
    if not source:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    
    # Get all videos related to this source
    videos = db.query(Video).filter_by(source_id=source_id).all()
    video_count = len(videos)
    deleted_count = 0
    
    # If delete_videos flag is set, delete all related video files
    if delete_videos:
        for video in videos:
            if video.downloaded and video.download_path:
                # Use the existing delete_video_files function
                success, message = delete_video_files(video.video_id)
                if success:
                    deleted_count += 1
    
    # Delete the source (will cascade delete the related videos in the database)
    db.delete(source)
    db.commit()
    
    if delete_videos:
        return {"message": f"Source {source_id} deleted with {deleted_count}/{video_count} video files removed"}
    else:
        return {"message": f"Source {source_id} deleted (kept {video_count} video files)"}

@app.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request, db: Session = Depends(get_db)):
    # Get username for the template
    username = get_session_username(request)
    
    # Get list of videos in the queue (all undownloaded videos)
    queued_videos = db.query(Video).filter_by(downloaded=False).order_by(Video.id).all()
    
    # Get count of downloaded videos
    downloaded_count = db.query(Video).filter_by(downloaded=True).count()
    
    return templates.TemplateResponse(
        "queue.html",
        {
            "request": request,
            "username": username,
            "queued_videos": queued_videos,
            "queue_count": len(queued_videos),
            "downloaded_count": downloaded_count,
            "title": "Download Queue - VibeTube"
        }
    )

@app.post("/toggle_auto_download/{source_id}")
async def toggle_auto_download(source_id: int):
    """Toggle the auto_download flag for a source"""
    session = get_db_session()
    source = session.query(Source).filter_by(id=source_id).first()
    
    if not source:
        session.close()
        raise HTTPException(status_code=404, detail="Source not found")
    
    # Toggle the auto_download flag
    source.auto_download = not source.auto_download
    session.commit()
    
    result = {"success": True, "auto_download": source.auto_download}
    session.close()
    
    return JSONResponse(content=result)

@app.post("/delete_video/{video_id}")
async def delete_video(video_id: str):
    """Delete a video's files and mark it as deleted"""
    success, message = delete_video_files(video_id)
    
    return JSONResponse(content={"success": success, "message": message})

@app.post("/reset_deleted_video/{video_id}")
async def reset_deleted_video(video_id: str):
    """Reset a video's deleted status to make it available for download again"""
    session = get_db_session()
    video = session.query(Video).filter_by(video_id=video_id).first()
    
    if not video:
        session.close()
        raise HTTPException(status_code=404, detail="Video not found")
    
    if not video.file_deleted:
        session.close()
        return JSONResponse(content={"success": False, "message": "Video is not marked as deleted"})
    
    # Reset the file_deleted and skip flags
    video.file_deleted = False
    video.skip = False
    session.commit()
    session.close()
    
    return JSONResponse(content={"success": True, "message": "Video reset successfully"})

@app.post("/toggle_skip/{video_id}")
async def toggle_skip(video_id: str):
    """Toggle the skip flag for a video"""
    session = get_db_session()
    video = session.query(Video).filter_by(video_id=video_id).first()
    
    if not video:
        session.close()
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Toggle the skip flag
    video.skip = not video.skip
    session.commit()
    
    result = {"success": True, "skip": video.skip}
    session.close()
    
    return JSONResponse(content=result)

@app.get("/download/{video_id}")
async def download_video_file(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Download a video file"""
    # Get the video
    video = db.query(Video).filter_by(video_id=video_id).first()
    
    if not video or not video.downloaded or not video.download_path:
        raise HTTPException(status_code=404, detail="Video not found or not downloaded")
    
    if not os.path.exists(video.download_path):
        # File is missing, mark as not downloaded
        video.downloaded = False
        video.file_deleted = True
        video.skip = True
        db.commit()
        raise HTTPException(status_code=404, detail="Video file not found")
    
    # Get file details
    file_size = os.path.getsize(video.download_path)
    content_type = "video/mp4"  # Assume most are mp4
    
    # Determine the proper content type from file extension
    if video.download_path.lower().endswith(".webm"):
        content_type = "video/webm"
    elif video.download_path.lower().endswith(".mkv"):
        content_type = "video/x-matroska"
    
    # Handle Range header for better seeking
    range_header = request.headers.get("Range", "").lower()
    
    # No Range header, return full file
    if not range_header:
        headers = {
            "Content-Disposition": f'inline; filename="{os.path.basename(video.download_path)}"',
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "max-age=86400"  # Cache for 24 hours
        }
        return FileResponse(
            video.download_path,
            media_type=content_type,
            headers=headers
        )
    
    # Parse Range header
    # Format: "bytes=start-end"
    try:
        start_bytes, end_bytes = range_header.replace("bytes=", "").split("-")
        start = int(start_bytes) if start_bytes else 0
        end = int(end_bytes) if end_bytes else file_size - 1
        
        # Ensure start and end are within file bounds
        if start < 0:
            start = 0
        if end >= file_size:
            end = file_size - 1
            
        # Calculate content length
        content_length = end - start + 1
        
        # Create custom response with proper headers for range requests
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'inline; filename="{os.path.basename(video.download_path)}"',
            "Cache-Control": "max-age=86400"  # Cache for 24 hours
        }
        
        # Open file and seek to position
        with open(video.download_path, "rb") as f:
            f.seek(start)
            file_data = f.read(content_length)
        
        # Return partial content response
        return Response(
            content=file_data,
            status_code=206,  # Partial Content
            headers=headers,
            media_type=content_type
        )
        
    except (ValueError, IOError):
        # If range parsing fails, return the whole file
        headers = {
            "Content-Disposition": f'inline; filename="{os.path.basename(video.download_path)}"',
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "max-age=86400"  # Cache for 24 hours
        }
        return FileResponse(
            video.download_path,
            media_type=content_type,
            headers=headers
        )

# Authentication routes
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Render the login page"""
    # If already logged in, redirect to home
    username = get_session_username(request)
    if username:
        return RedirectResponse(url="/", status_code=302)
    
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Login - VibeTube",
            "error": error
        }
    )

@app.post("/login", response_class=RedirectResponse)
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...)
):
    """Process login form submission"""
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # Create a session
        session_id = create_session(username)
        
        # Set cookie and redirect
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            max_age=3600 * 24,  # 24 hours
            path="/"
        )
        return response
    else:
        # Return to login page with error
        return RedirectResponse(
            url=f"/login?error=Invalid+username+or+password",
            status_code=302
        )

@app.get("/logout")
async def logout(
    response: Response,
    session: str = Cookie(None)
):
    """Log the user out by clearing the session cookie"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key="session", path="/")
    return response

@app.post("/clear_youtube_cookies")
async def clear_youtube_cookies(db: Session = Depends(get_db)):
    """Clear the YouTube cookies setting"""
    setting = db.query(Setting).filter_by(key="youtube_cookies").first()
    
    if setting:
        setting.value = ""
        db.commit()
        
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/reset_failed_video/{video_id}")
async def reset_failed_video(video_id: str):
    """Reset a video's failed download status to make it available for download again"""
    session = get_db_session()
    video = session.query(Video).filter_by(video_id=video_id).first()
    
    if not video:
        session.close()
        raise HTTPException(status_code=404, detail="Video not found")
    
    if not video.failed_download:
        session.close()
        return JSONResponse(content={"success": False, "message": "Video is not marked as failed"})
    
    # Reset the failed_download flag and error message
    video.failed_download = False
    video.error_message = None
    session.commit()
    session.close()
    
    return JSONResponse(content={"success": True, "message": "Failed status reset successfully"})

@app.get("/play/{video_id}", response_class=HTMLResponse)
async def player_page(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Render the video player page for a specific video"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get video from database
    video = db.query(Video).filter_by(video_id=video_id).first()
    
    if not video or not video.downloaded or not video.download_path:
        raise HTTPException(status_code=404, detail="Video not found or not downloaded")
    
    if not os.path.exists(video.download_path):
        # File is missing, mark as not downloaded
        video.downloaded = False
        video.file_deleted = True
        video.skip = True
        db.commit()
        raise HTTPException(status_code=404, detail="Video file not found")
    
    return templates.TemplateResponse(
        "player.html",
        {
            "request": request,
            "username": username,
            "title": f"Playing: {video.title}",
            "video": video
        }
    )

# Update the base.html navbar to include playlists
@app.get("/base.html", response_class=HTMLResponse)
async def get_base():
    return templates.TemplateResponse("base.html", {})

# Playlist management routes
@app.get("/playlists", response_class=HTMLResponse)
async def playlists_page(
    request: Request,
    db: Session = Depends(get_db)
):
    """Render the playlists management page"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get all playlists with their items count
    playlists = db.query(VibePlaylist).options(
        selectinload(VibePlaylist.items)
    ).all()
    
    return templates.TemplateResponse(
        "playlists.html",
        {
            "request": request,
            "username": username,
            "playlists": playlists
        }
    )

@app.get("/playlist/{playlist_id}", response_class=HTMLResponse)
async def playlist_editor_page(
    playlist_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Render the playlist editor page"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get the playlist with its items
    playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get playlist items ordered by position
    playlist_items = db.query(VibePlaylistItem).filter_by(
        playlist_id=playlist_id
    ).order_by(VibePlaylistItem.position).all()
    
    # Get all downloaded videos for the library section
    library_videos = db.query(Video).filter_by(downloaded=True).all()
    
    return templates.TemplateResponse(
        "playlist_player.html",
        {
            "request": request,
            "username": username,
            "playlist": playlist,
            "playlist_items": playlist_items,
            "library_videos": library_videos,
            "current_video": None
        }
    )

@app.get("/playlist/{playlist_id}/play", response_class=HTMLResponse)
async def playlist_play_page(
    playlist_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Play the first video in a playlist"""
    # Find the first video in the playlist
    playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    first_item = db.query(VibePlaylistItem).filter_by(
        playlist_id=playlist_id
    ).order_by(VibePlaylistItem.position).first()
    
    if first_item:
        # Redirect to play this video using index 0 (first video)
        return RedirectResponse(url=f"/playlist/{playlist_id}/play/index/0")
    else:
        # Redirect to the editor if no videos
        return RedirectResponse(url=f"/playlist/{playlist_id}")

@app.get("/playlist/{playlist_id}/play/index/{index}", response_class=HTMLResponse)
async def playlist_play_index_page(
    playlist_id: int,
    index: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Play a specific video by index in a playlist"""
    # Get username for the template
    username = get_session_username(request)
    
    # Get the playlist
    playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get all playlist items ordered by position
    playlist_items = db.query(VibePlaylistItem).filter_by(
        playlist_id=playlist_id
    ).order_by(VibePlaylistItem.position).all()
    
    # Check if index is valid
    if not playlist_items or index < 0 or index >= len(playlist_items):
        if playlist_items:
            # If index is invalid but we have items, redirect to first item
            return RedirectResponse(url=f"/playlist/{playlist_id}/play/index/0")
        else:
            # Redirect to the editor if no videos
            return RedirectResponse(url=f"/playlist/{playlist_id}")
    
    # Get the video at the specified index
    item = playlist_items[index]
    video = db.query(Video).filter_by(video_id=item.video_id).first()
    
    if not video or not video.downloaded:
        # If video not found or not downloaded, remove it from playlist
        db.delete(item)
        db.commit()
        # Redirect to the same index (will get next video or redirect appropriately)
        return RedirectResponse(url=f"/playlist/{playlist_id}/play/index/{index}")
    
    # Get all downloaded videos for the library
    library_videos = db.query(Video).filter_by(downloaded=True).all()
    
    # Pass the current index to the template
    return templates.TemplateResponse(
        "playlist_player.html",
        {
            "request": request,
            "username": username,
            "playlist": playlist,
            "playlist_items": playlist_items,
            "library_videos": library_videos,
            "current_video": video,
            "current_index": index
        }
    )

# Keep the video_id based route for backward compatibility
@app.get("/playlist/{playlist_id}/play/{video_id}", response_class=HTMLResponse)
async def playlist_play_video_page(
    playlist_id: int,
    video_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Play a specific video in a playlist (legacy route - redirects to index-based route)"""
    # Get the playlist
    playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get all playlist items
    playlist_items = db.query(VibePlaylistItem).filter_by(
        playlist_id=playlist_id
    ).order_by(VibePlaylistItem.position).all()
    
    # Find index of the video in the playlist
    index = -1
    for i, item in enumerate(playlist_items):
        if item.video_id == video_id:
            index = i
            break
    
    if index == -1:
        # If video not found in playlist, redirect to the first video
        if playlist_items:
            return RedirectResponse(url=f"/playlist/{playlist_id}/play/index/0")
        else:
            return RedirectResponse(url=f"/playlist/{playlist_id}")
    
    # Redirect to the index-based route
    return RedirectResponse(url=f"/playlist/{playlist_id}/play/index/{index}")

# API routes for playlist management
@app.post("/api/playlist/create", response_class=JSONResponse)
async def create_playlist(
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Create a new playlist"""
    try:
        name = data.get("name", "").strip()
        description = data.get("description", "").strip()
        
        if not name:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Playlist name is required"}
            )
        
        # Create new playlist
        playlist = VibePlaylist(
            name=name,
            description=description
        )
        
        db.add(playlist)
        db.commit()
        db.refresh(playlist)
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Playlist created successfully",
                "playlist_id": playlist.id
            }
        )
    except Exception as e:
        logger.error(f"Error creating playlist: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Server error creating playlist"}
        )

@app.post("/api/playlist/{playlist_id}/update", response_class=JSONResponse)
async def update_playlist(
    playlist_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Update playlist items"""
    try:
        # Check if playlist exists
        playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
        if not playlist:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Playlist not found"}
            )
        
        # Handle removed items
        if "removed" in data and data["removed"]:
            for item_id in data["removed"]:
                # Find and delete the item
                item = db.query(VibePlaylistItem).filter_by(id=item_id).first()
                if item:
                    db.delete(item)
        
        # Handle position updates
        if "positions" in data and data["positions"]:
            for item_id, position in data["positions"].items():
                # Update the position
                db.query(VibePlaylistItem).filter_by(id=int(item_id)).update(
                    {"position": position}
                )
        
        # Handle added items
        new_items = []
        if "added" in data and data["added"]:
            for item in data["added"]:
                video_id = item["video_id"]
                position = item["position"]
                temp_id = item.get("temp_id")
                
                # Create new playlist item
                new_item = VibePlaylistItem(
                    playlist_id=playlist_id,
                    video_id=video_id,
                    position=position
                )
                
                db.add(new_item)
                # Flush to get the ID assigned
                db.flush()
                
                # Store for response
                new_items.append({
                    "id": new_item.id,
                    "video_id": video_id,
                    "position": position,
                    "temp_id": temp_id
                })
        
        # Update the playlist's updated_at timestamp
        playlist.updated_at = datetime.utcnow()
        
        db.commit()
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Playlist updated successfully",
                "newItems": new_items
            }
        )
    except Exception as e:
        logger.error(f"Error updating playlist: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Server error updating playlist"}
        )

@app.post("/api/playlist/{playlist_id}/rename", response_class=JSONResponse)
async def rename_playlist(
    playlist_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Rename a playlist"""
    try:
        name = data.get("name", "").strip()
        description = data.get("description", "").strip()
        
        if not name:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Playlist name is required"}
            )
        
        # Find and update the playlist
        playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
        if not playlist:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Playlist not found"}
            )
        
        playlist.name = name
        playlist.description = description
        playlist.updated_at = datetime.utcnow()
        
        db.commit()
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Playlist renamed successfully"
            }
        )
    except Exception as e:
        logger.error(f"Error renaming playlist: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Server error renaming playlist"}
        )

@app.post("/api/playlist/{playlist_id}/delete", response_class=JSONResponse)
async def delete_playlist(
    playlist_id: int,
    db: Session = Depends(get_db)
):
    """Delete a playlist"""
    try:
        # Find the playlist
        playlist = db.query(VibePlaylist).filter_by(id=playlist_id).first()
        if not playlist:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Playlist not found"}
            )
        
        # Delete the playlist (cascade will delete items)
        db.delete(playlist)
        db.commit()
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Playlist deleted successfully"
            }
        )
    except Exception as e:
        logger.error(f"Error deleting playlist: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Server error deleting playlist"}
        )

@app.get("/api/video/{video_id}", response_class=JSONResponse)
async def get_video_details(video_id: str, db: Session = Depends(get_db)):
    """Get video details for API usage"""
    try:
        video = db.query(Video).filter(Video.video_id == video_id).first()
        if not video:
            return JSONResponse(content={"success": False, "message": "Video not found"}, status_code=404)
        
        # Handle upload_date correctly
        upload_date = video.upload_date
        if upload_date and not isinstance(upload_date, str):
            upload_date = upload_date.isoformat()
        
        # Return serialized video data
        return {
            "success": True,
            "video": {
                "id": video.id,
                "video_id": video.video_id,
                "title": video.title,
                "duration": video.duration,
                "thumbnail_url": video.thumbnail_url,
                "downloaded": video.downloaded,
                "download_path": video.download_path,
                "source": video.source,
                "upload_date": upload_date
            }
        }
    except Exception as e:
        logging.error(f"Error fetching video details: {e}")
        return JSONResponse(content={"success": False, "message": str(e)}, status_code=500)

if __name__ == "__main__":
    # Make sure directories exist
    os.makedirs("db", exist_ok=True)
    os.makedirs("downloads", exist_ok=True)
    
    # Run the app
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True) 
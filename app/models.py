from sqlalchemy import Boolean, Column, DateTime, Integer, String, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import os
import datetime

Base = declarative_base()

class Subfolder(Base):
    __tablename__ = "subfolders"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    path = Column(String, nullable=False)
    description = Column(String)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    sources = relationship("Source", back_populates="subfolder")

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    source_type = Column(String, nullable=False)  # video, channel, playlist
    source_id = Column(String, nullable=False, unique=True)
    name = Column(String)  # Channel name, playlist name, or video title
    added_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_checked = Column(DateTime)
    subfolder_id = Column(Integer, ForeignKey("subfolders.id"))
    auto_download = Column(Boolean, default=True)  # Whether to auto-download new videos
    
    videos = relationship("Video", back_populates="source", cascade="all, delete-orphan")
    subfolder = relationship("Subfolder", back_populates="sources")

class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True)
    video_id = Column(String, nullable=False, unique=True)
    title = Column(String)
    channel_name = Column(String)
    upload_date = Column(String)
    source_id = Column(Integer, ForeignKey("sources.id"))
    downloaded = Column(Boolean, default=False)
    download_path = Column(String)
    download_date = Column(DateTime)
    thumbnail_url = Column(String)
    duration = Column(Integer)  # Duration in seconds
    file_deleted = Column(Boolean, default=False)  # Whether the file has been deleted
    skip = Column(Boolean, default=False)  # Whether to skip this video from downloading
    failed_download = Column(Boolean, default=False)  # Whether download attempts have failed
    error_message = Column(String)  # Error message from failed download
    
    source = relationship("Source", back_populates="videos")

class Setting(Base):
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    value = Column(String)

class VibePlaylist(Base):
    __tablename__ = "vibe_playlists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationship to items
    items = relationship("VibePlaylistItem", back_populates="playlist", cascade="all, delete-orphan")

class VibePlaylistItem(Base):
    __tablename__ = "vibe_playlist_items"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("vibe_playlists.id", ondelete="CASCADE"))
    video_id = Column(String, ForeignKey("videos.video_id", ondelete="CASCADE"))
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    playlist = relationship("VibePlaylist", back_populates="items")
    video = relationship("Video")

def get_db_session():
    db_path = os.path.join("db", "ytdlp.db")
    db_uri = f"sqlite:///{db_path}"
    engine = create_engine(db_uri)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

def initialize_db():
    """Initialize the database by creating all tables"""
    db_path = os.path.join("db", "ytdlp.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    db_uri = f"sqlite:///{db_path}"
    engine = create_engine(db_uri)
    Base.metadata.create_all(engine)
    
    import logging
    logger = logging.getLogger("models")
    logger.info(f"Initialized database at {db_path}")

def initialize_settings():
    session = get_db_session()
    
    # Ensure downloads folder exists and get absolute path
    downloads_path = os.path.abspath("downloads")
    os.makedirs(downloads_path, exist_ok=True)
    
    default_settings = [
        {"key": "download_path", "value": downloads_path},
        {"key": "filename_format", "value": "{video_id} - {title}.{ext}"},
        {"key": "check_interval", "value": "3600"},  # Default 1 hour in seconds
        {"key": "auto_download", "value": "true"},
        {"key": "scan_interval", "value": "86400"},  # Default 24 hours in seconds for library scan
        {"key": "download_delay", "value": "60"}  # Default 1 minute in seconds between downloads
    ]
    
    for setting in default_settings:
        existing = session.query(Setting).filter_by(key=setting["key"]).first()
        if not existing:
            session.add(Setting(**setting))
        elif setting["key"] == "download_path" and not os.path.isabs(existing.value):
            # Update download path to absolute path if it's not already
            existing.value = os.path.abspath(existing.value)
    
    session.commit()
    
    # Create default subfolder if none exists
    default_subfolder = session.query(Subfolder).filter_by(is_default=True).first()
    if not default_subfolder:
        default_subfolder = Subfolder(
            name="Default",
            path=os.path.join(downloads_path, "default"),
            description="Default download location",
            is_default=True
        )
        session.add(default_subfolder)
        os.makedirs(default_subfolder.path, exist_ok=True)
        session.commit()
    
    # Log current settings
    import logging
    logger = logging.getLogger("models")
    settings = {s.key: s.value for s in session.query(Setting).all()}
    logger.info(f"Current settings: {settings}")
    
    session.close() 
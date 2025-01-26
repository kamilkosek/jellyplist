import os
import sys

import app

class Config:
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    SECRET_KEY = os.getenv('SECRET_KEY')  
    JELLYFIN_SERVER_URL = os.getenv('JELLYFIN_SERVER_URL')  
    JELLYFIN_ADMIN_USER = os.getenv('JELLYFIN_ADMIN_USER')
    JELLYFIN_ADMIN_PASSWORD = os.getenv('JELLYFIN_ADMIN_PASSWORD')
    JELLYFIN_REQUEST_TIMEOUT = int(os.getenv('JELLYFIN_REQUEST_TIMEOUT','10'))
    SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
    SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
    SPOTIFY_COOKIE_FILE = os.getenv('SPOTIFY_COOKIE_FILE')
    JELLYPLIST_DB_HOST = os.getenv('JELLYPLIST_DB_HOST')
    JELLYPLIST_DB_PORT = int(os.getenv('JELLYPLIST_DB_PORT','5432'))
    JELLYPLIST_DB_USER = os.getenv('JELLYPLIST_DB_USER')
    JELLYPLIST_DB_PASSWORD = os.getenv('JELLYPLIST_DB_PASSWORD')
    START_DOWNLOAD_AFTER_PLAYLIST_ADD  = os.getenv('START_DOWNLOAD_AFTER_PLAYLIST_ADD',"false").lower() == 'true' # If a new Playlist is added, the Download Task will be scheduled immediately
    REFRESH_LIBRARIES_AFTER_DOWNLOAD_TASK  = os.getenv('REFRESH_LIBRARIES_AFTER_DOWNLOAD_TASK',"false").lower() == 'true' 
    DISPLAY_EXTENDED_AUDIO_DATA = os.getenv('DISPLAY_EXTENDED_AUDIO_DATA',"false").lower() == 'true' 
    CACHE_TYPE = 'redis'
    CACHE_REDIS_PORT = 6379
    CACHE_REDIS_HOST = os.getenv('CACHE_REDIS_HOST','redis')
    CACHE_REDIS_DB = 0
    CACHE_DEFAULT_TIMEOUT = 3600
    REDIS_URL = os.getenv('REDIS_URL','redis://redis:6379/0')
    SEARCH_JELLYFIN_BEFORE_DOWNLOAD = os.getenv('SEARCH_JELLYFIN_BEFORE_DOWNLOAD',"true").lower() == 'true'
    FIND_BEST_MATCH_USE_FFPROBE = os.getenv('FIND_BEST_MATCH_USE_FFPROBE','false').lower() == 'true'
    SPOTIFY_COUNTRY_CODE = os.getenv('SPOTIFY_COUNTRY_CODE','DE')
    LIDARR_API_KEY = os.getenv('LIDARR_API_KEY','') 
    LIDARR_URL = os.getenv('LIDARR_URL','')
    LIDARR_MONITOR_ARTISTS = os.getenv('LIDARR_MONITOR_ARTISTS','false').lower() == 'true'
    MUSIC_STORAGE_BASE_PATH = os.getenv('MUSIC_STORAGE_BASE_PATH')
    CHECK_FOR_UPDATES = os.getenv('CHECK_FOR_UPDATES','true').lower() == 'true'
    SPOTDL_PROXY = os.getenv('SPOTDL_PROXY',None)
    SPOTDL_OUTPUT_FORMAT = os.getenv('SPOTDL_OUTPUT_FORMAT','__jellyplist/{artist}-{title}.mp3')
    QUALITY_SCORE_THRESHOLD = float(os.getenv('QUALITY_SCORE_THRESHOLD',1000.0))
    # SpotDL specific configuration
    SPOTDL_CONFIG = {
        'threads': 12
    }
    # combine the path provided in MUSIC_STORAGE_BASE_PATH with the SPOTDL_OUTPUT_FORMAT to get the value for output      
    if os.getenv('MUSIC_STORAGE_BASE_PATH'):
        # Ensure MUSIC_STORAGE_BASE_PATH ends with "__jellyplist"
        if not MUSIC_STORAGE_BASE_PATH.endswith("__jellyplist"):
            MUSIC_STORAGE_BASE_PATH += "__jellyplist"

        # Ensure SPOTDL_OUTPUT_FORMAT does not start with "/"
        normalized_spotdl_output_format = SPOTDL_OUTPUT_FORMAT.lstrip("/").replace(" ", "_")

        # Join the paths
        output_path = os.path.join(MUSIC_STORAGE_BASE_PATH, normalized_spotdl_output_format)

        SPOTDL_CONFIG.update({'output': output_path})
        
    if SPOTIFY_COOKIE_FILE:
        SPOTDL_CONFIG.update({'cookie_file': SPOTIFY_COOKIE_FILE})
    
    @classmethod
    def validate_env_vars(cls):
        required_vars = {
            'SECRET_KEY': cls.SECRET_KEY,
            'JELLYFIN_SERVER_URL': cls.JELLYFIN_SERVER_URL,
            'JELLYFIN_ADMIN_USER': cls.JELLYFIN_ADMIN_USER,
            'JELLYFIN_ADMIN_PASSWORD': cls.JELLYFIN_ADMIN_PASSWORD,
            
            'SPOTIFY_CLIENT_ID': cls.SPOTIFY_CLIENT_ID,
            'SPOTIFY_CLIENT_SECRET': cls.SPOTIFY_CLIENT_SECRET,
            'JELLYPLIST_DB_HOST' : cls.JELLYPLIST_DB_HOST,
            'JELLYPLIST_DB_USER' : cls.JELLYPLIST_DB_USER,
            'JELLYPLIST_DB_PASSWORD' : cls.JELLYPLIST_DB_PASSWORD,
            'REDIS_URL': cls.REDIS_URL,
            'MUSIC_STORAGE_BASE_PATH': cls.MUSIC_STORAGE_BASE_PATH
        }

        missing_vars = [var for var, value in required_vars.items() if not value]

        if missing_vars:
            missing = ', '.join(missing_vars)
            sys.stderr.write(f"Error: The following environment variables are not set: {missing}\n")
            sys.exit(1)

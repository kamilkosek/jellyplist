import os
import sys


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')  
    JELLYFIN_SERVER_URL = os.getenv('JELLYFIN_SERVER_URL')  
    JELLYFIN_ADMIN_USER = os.getenv('JELLYFIN_ADMIN_USER')
    JELLYFIN_ADMIN_PASSWORD = os.getenv('JELLYFIN_ADMIN_PASSWORD')
    SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
    SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
    JELLYPLIST_DB_HOST = os.getenv('JELLYPLIST_DB_HOST')
    JELLYPLIST_DB_USER = os.getenv('JELLYPLIST_DB_USER')
    JELLYPLIST_DB_PASSWORD = os.getenv('JELLYPLIST_DB_PASSWORD')
    START_DOWNLOAD_AFTER_PLAYLIST_ADD  = os.getenv('START_DOWNLOAD_AFTER_PLAYLIST_ADD',"true").lower() == 'true' # If a new Playlist is added, the Download Task will be scheduled immediately
    CACHE_TYPE = 'redis'
    CACHE_REDIS_PORT = 6379
    CACHE_REDIS_HOST = 'redis'
    CACHE_REDIS_DB = 0
    CACHE_DEFAULT_TIMEOUT = 3600
    REDIS_URL = os.getenv('REDIS_URL','redis://redis:6379/0')
    SEARCH_JELLYFIN_BEFORE_DOWNLOAD = os.getenv('SEARCH_JELLYFIN_BEFORE_DOWNLOAD',"true").lower() == 'true'
    
    
    SEARCH_JELLYFIN_BEFORE_DOWNLOAD = True
    # SpotDL specific configuration
    SPOTDL_CONFIG = {
        'cookie_file': '/jellyplist/cookies.txt',
        'output': '/jellyplist_downloads/__jellyplist/{track-id}',
        'threads': 12
    }
    @classmethod
    def validate_env_vars(cls):
        required_vars = {
            'SECRET_KEY': cls.SECRET_KEY,
            'JELLYFIN_SERVER_URL': cls.JELLYFIN_SERVER_URL,
            'JELLYFIN_ADMIN_USER': cls.JELLYFIN_ADMIN_USER,
            'JELLYFIN_ADMIN_PASSWORD': cls.JELLYFIN_ADMIN_PASSWORD,
            'SPOTIPY_CLIENT_ID': cls.SPOTIPY_CLIENT_ID,
            'SPOTIPY_CLIENT_SECRET': cls.SPOTIPY_CLIENT_SECRET,
            'JELLYPLIST_DB_HOST' : cls.JELLYPLIST_DB_HOST,
            'JELLYPLIST_DB_USER' : cls.JELLYPLIST_DB_USER,
            'JELLYPLIST_DB_PASSWORD' : cls.JELLYPLIST_DB_PASSWORD,
            'REDIS_URL': cls.REDIS_URL
        }

        missing_vars = [var for var, value in required_vars.items() if not value]

        if missing_vars:
            missing = ', '.join(missing_vars)
            sys.stderr.write(f"Error: The following environment variables are not set: {missing}\n")
            sys.exit(1)
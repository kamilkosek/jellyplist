from logging.handlers import RotatingFileHandler
import os
import threading
import time
import yaml
from flask_socketio import SocketIO

import sys
from flask import Flask, has_request_context
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from psycopg2 import OperationalError
import redis
import spotipy
from spotipy.oauth2 import  SpotifyClientCredentials
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import create_engine
from config import Config
from jellyfin.client import JellyfinClient
import logging
from spotdl.utils.config import DEFAULT_CONFIG
from flask_caching import Cache
from .version import __version__



def check_db_connection(db_uri, retries=5, delay=5):
    """
    Check if the database is reachable.

    Args:
        db_uri (str): The database URI.
        retries (int): Number of retry attempts.
        delay (int): Delay between retries in seconds.

    Raises:
        SystemExit: If the database is not reachable after retries.
    """
    for attempt in range(1, retries + 1):
        try:
            engine = create_engine(db_uri)
            connection = engine.connect()
            connection.close()
            app.logger.info("Successfully connected to the database.")
            return
        except OperationalError as e:
            app.logger.error(f"Database connection failed on attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                app.logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                app.logger.critical("Could not connect to the database. Exiting application.")
                sys.exit(1)

# Celery setup
def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['result_backend'],  
        broker=app.config['CELERY_BROKER_URL'],
        include=['app.tasks']  
    )
    celery.conf.update(app.config)
    # Configure Celery Beat schedule
    celery.conf.beat_schedule = {
        'download-missing-tracks-schedule': {
            'task': 'app.tasks.download_missing_tracks',
            'schedule': crontab(minute='30'),  
        },
        'check-playlist-updates-schedule': {
            'task': 'app.tasks.check_for_playlist_updates',
            'schedule': crontab(minute='25'),  
        },
        'update_all_playlists_track_status-schedule': {
            'task': 'app.tasks.update_all_playlists_track_status',
            'schedule': crontab(minute='*/5'),  
            
        },
        'update_jellyfin_id_for_downloaded_tracks-schedule': {
            'task': 'app.tasks.update_jellyfin_id_for_downloaded_tracks',
            'schedule': crontab(minute='*/10'),  
        }
    }
    if app.config['LIDARR_API_KEY']:
        celery.conf.beat_schedule['request-lidarr-schedule'] = {
            'task': 'app.tasks.request_lidarr',
            'schedule': crontab(minute='50')
        }
    
    celery.conf.timezone = 'UTC'
    return celery

# Why this ? Because we are using the same admin login for web, worker and beat we need to distinguish the device_id´s
device_id = f'JellyPlist_{'_'.join(sys.argv)}'

# Initialize Flask app
app = Flask(__name__, template_folder="../templates", static_folder='../static')


app.config.from_object(Config)
app.config['runtime_settings'] = {}
yaml_file = 'settings.yaml'
def load_yaml_settings():
    with open(yaml_file, 'r') as f:
        app.config['runtime_settings'] =  yaml.safe_load(f)
def save_yaml_settings():
    with open(yaml_file, 'w') as f:
        yaml.dump(app.config['runtime_settings'], f)


for handler in app.logger.handlers:
    app.logger.removeHandler(handler)


log_level = getattr(logging, app.config['LOG_LEVEL'], logging.INFO)  # Default to DEBUG if invalid
app.logger.setLevel(log_level)

FORMAT = "[%(asctime)s][%(filename)18s:%(lineno)4s - %(funcName)36s() ] %(levelname)7s - %(message)s" 
logging.basicConfig(format=FORMAT)

# Add RotatingFileHandler to log to a file
# if worker is in sys.argv, we are running a celery worker, so we log to a different file
if 'worker' in sys.argv:
    log_file = os.path.join("/var/log/", 'jellyplist_worker.log')
elif 'beat' in sys.argv:
    log_file = os.path.join("/var/log/", 'jellyplist_beat.log')
else:
    log_file = os.path.join("/var/log/", 'jellyplist.log')
file_handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=10)
file_handler.setFormatter(logging.Formatter(FORMAT))
app.logger.addHandler(file_handler)

Config.validate_env_vars()
cache = Cache(app)
redis_client = redis.StrictRedis(host=app.config['CACHE_REDIS_HOST'], port=app.config['CACHE_REDIS_PORT'], db=0, decode_responses=True)


# Spotify, Jellyfin, and Spotdl setup
app.logger.info(f"setting up spotipy")
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=app.config['SPOTIFY_CLIENT_ID'],
    client_secret=app.config['SPOTIFY_CLIENT_SECRET']
))

app.logger.info(f"setting up jellyfin client, BaseUrl = {app.config['JELLYFIN_SERVER_URL']}, timeout = {app.config['JELLYFIN_REQUEST_TIMEOUT']}")

jellyfin = JellyfinClient(app.config['JELLYFIN_SERVER_URL'], app.config['JELLYFIN_REQUEST_TIMEOUT'])
jellyfin_admin_token, jellyfin_admin_id, jellyfin_admin_name, jellyfin_admin_is_admin = jellyfin.login_with_password(
    app.config['JELLYFIN_ADMIN_USER'],
    app.config['JELLYFIN_ADMIN_PASSWORD'], device_id= device_id
)

# SQLAlchemy and Migrate setup
app.logger.info(f"connecting to db: {app.config['JELLYPLIST_DB_HOST']}:{app.config['JELLYPLIST_DB_PORT']}")
db_uri = f'postgresql://{app.config["JELLYPLIST_DB_USER"]}:{app.config["JELLYPLIST_DB_PASSWORD"]}@{app.config["JELLYPLIST_DB_HOST"]}:{app.config['JELLYPLIST_DB_PORT']}/jellyplist'
check_db_connection(db_uri=db_uri,retries=5,delay=2)
app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.logger.info(f"applying db migrations")
migrate = Migrate(app, db)

# Celery Configuration (Updated)
app.config.update(
    CELERY_BROKER_URL=app.config['REDIS_URL'],     
    result_backend=app.config['REDIS_URL'] 
)

def read_dev_build_file(file_path="/jellyplist/DEV_BUILD"):
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            content = file.read().strip()
            return f"-{content}"
    else:
        return ''
app.logger.info(f"initializing celery")
celery = make_celery(app)
socketio = SocketIO(app, message_queue=app.config['REDIS_URL'], async_mode='eventlet')
celery.set_default()

app.logger.info(f'Jellyplist {__version__}{read_dev_build_file()} started')
app.logger.debug(f"Debug logging active")

from app.routes import pl_bp, routes, jellyfin_routes
app.register_blueprint(pl_bp)

from app import filters  # Import the filters dictionary

# Register all filters
for name, func in filters.filters.items():
    app.jinja_env.filters[name] = func
    
    
from .providers import SpotifyClient
if app.config['SPOTIFY_COOKIE_FILE']:
    if os.path.exists(app.config['SPOTIFY_COOKIE_FILE']):
        spotify_client = SpotifyClient(app.config['SPOTIFY_COOKIE_FILE'])
    else:
        app.logger.error(f"Cookie file {app.config['SPOTIFY_COOKIE_FILE']} does not exist. Exiting.")
        sys.exit(1)
else:
    spotify_client = SpotifyClient()
    
spotify_client.authenticate()
app.logger.info('spotify auth successful')
from .registry import MusicProviderRegistry
MusicProviderRegistry.register_provider(spotify_client)

if app.config['ENABLE_DEEZER']:
    from .providers import DeezerClient
    deezer_client = DeezerClient()
    deezer_client.authenticate()
    MusicProviderRegistry.register_provider(deezer_client)

if app.config['LIDARR_API_KEY'] and app.config['LIDARR_URL']:
    app.logger.info(f'Creating Lidarr Client with URL: {app.config["LIDARR_URL"]}')
    from lidarr.client import LidarrClient
    lidarr_client = LidarrClient(app.config['LIDARR_URL'], app.config['LIDARR_API_KEY'])



        

if os.path.exists(yaml_file):
    app.logger.info('Loading runtime settings from settings.yaml')
    load_yaml_settings()
    # def watch_yaml_file(yaml_file, interval=30):
    #     last_mtime = os.path.getmtime(yaml_file)
    #     while True:
    #         time.sleep(interval)
    #         current_mtime = os.path.getmtime(yaml_file)
    #         if current_mtime != last_mtime:
    #             last_mtime = current_mtime
    #             yaml_settings = load_yaml_settings(yaml_file)
    #             app.config.update(yaml_settings)
    #             app.logger.info(f"Reloaded YAML settings from {yaml_file}")

    # watcher_thread = threading.Thread(
    #     target=watch_yaml_file,
    #     args=('settings.yaml',),
    #     daemon=True
    # )
    # watcher_thread.start()
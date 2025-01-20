from datetime import datetime,timezone
import logging
import subprocess
from typing import List

from sqlalchemy import insert
from app import celery, app, db, functions, sp, jellyfin, jellyfin_admin_token, jellyfin_admin_id, redis_client

from app.classes import AudioProfile
from app.models import JellyfinUser,Playlist,Track, user_playlists, playlist_tracks
import os
import redis
from celery import current_task,signals
from celery.result import AsyncResult

from app.providers import base
from app.registry.music_provider_registry import MusicProviderRegistry
from lidarr.classes import Artist



@signals.celeryd_init.connect
def setup_log_format(sender, conf, **kwargs):
    FORMAT = "[%(asctime)s][%(filename)18s:%(lineno)4s - %(funcName)42s() ] %(levelname)7s - %(message)s"  
    
    conf.worker_log_format = FORMAT.strip().format(sender)
    conf.worker_task_log_format = FORMAT.format(sender)

@celery.task(bind=True)
def update_all_playlists_track_status(self):
    lock_key = "update_all_playlists_track_status_lock"
    
    if task_manager.acquire_lock(lock_key, expiration=600):  
        try:
            with app.app_context():
                playlists = Playlist.query.all()
                total_playlists = len(playlists)
                if not playlists:
                    app.logger.info("No playlists found.")
                    return {'status': 'No playlists found'}  

                app.logger.info(f"Found {total_playlists} playlists to update.")
                processed_playlists = 0

                for playlist in playlists:
                    total_tracks = 0
                    available_tracks = 0
                    app.logger.info(f"Current Playlist: {playlist.name} [{playlist.id}:{playlist.provider_playlist_id}]" )
                    for track in playlist.tracks:
                        total_tracks += 1
                        app.logger.debug(f"Processing track: {track.name}  [{track.provider_track_id}]")
                        app.logger.debug(f"\tPath = {track.filesystem_path}")
                        if track.filesystem_path:
                            app.logger.debug(f"\tPath exists = {os.path.exists(track.filesystem_path)}")
                        app.logger.debug(f"\tJellyfinID = {track.jellyfin_id}")
                        if track.filesystem_path and os.path.exists(track.filesystem_path):
                            app.logger.info(f"Track {track.name} is already downloaded at {track.filesystem_path}.")
                            available_tracks += 1
                            track.downloaded = True
                            db.session.commit()
                        #If not found in filesystem, but a jellyfin_id is set, query the jellyfin server for the track and populate the filesystem_path from the response with the path
                        elif track.jellyfin_id:
                            jellyfin_track = jellyfin.get_item(jellyfin_admin_token, track.jellyfin_id)
                            app.logger.debug(f"\tJellyfin Path: {jellyfin_track['Path']}")
                            app.logger.debug(f"\tJellyfin Path exists: {os.path.exists(jellyfin_track['Path'])}")
                            if jellyfin_track and os.path.exists(jellyfin_track['Path']):
                                app.logger.info(f"Track {track.name} found in Jellyfin at {jellyfin_track['Path']}.")
                                track.filesystem_path = jellyfin_track['Path']
                                track.downloaded = True
                                db.session.commit()
                                available_tracks += 1
                            else:
                                track.downloaded = False
                                track.filesystem_path = None
                                db.session.commit()
                                
                        
                        
                            
                        else:
                            track.downloaded = False
                            track.filesystem_path = None
                            db.session.commit()

                    playlist.track_count = total_tracks
                    playlist.tracks_available = available_tracks
                    db.session.commit()

                    processed_playlists += 1
                    progress = (processed_playlists / total_playlists) * 100
                    self.update_state(state='PROGRESS', meta={'current': processed_playlists, 'total': total_playlists, 'percent': progress})
                    if processed_playlists % 10 == 0 or processed_playlists == total_playlists:
                        app.logger.info(f"Processed {processed_playlists}/{total_playlists} playlists.")

                app.logger.info("All playlists' track statuses updated.")
                return {'status': 'All playlists updated', 'total': total_playlists, 'processed': processed_playlists}
        except Exception as e:
            app.logger.error(f"Error downloading tracks: {str(e)}", exc_info=True)
            return {'status': 'Error downloading tracks'}
        finally:
            task_manager.release_lock(lock_key)
    else:
        app.logger.info("Skipping task. Another instance is already running.")
        return {'status': 'Task skipped, another instance is running'}


@celery.task(bind=True)
def download_missing_tracks(self):
    lock_key = "download_missing_tracks_lock"

    if task_manager.acquire_lock(lock_key, expiration=1800): 
        try:
            app.logger.info("Starting track download job...")

            with app.app_context():
                spotdl_config = app.config['SPOTDL_CONFIG']
                cookie_file = spotdl_config['cookie_file']
                output_dir = spotdl_config['output']
                client_id = app.config['SPOTIFY_CLIENT_ID']
                client_secret = app.config['SPOTIFY_CLIENT_SECRET']
                search_before_download = app.config['SEARCH_JELLYFIN_BEFORE_DOWNLOAD']

                # Downloading using SpotDL only works for Spotify tracks
                undownloaded_tracks : List[Track] = Track.query.filter_by(downloaded=False,provider_id = "Spotify").all()
                total_tracks = len(undownloaded_tracks)
                if not undownloaded_tracks:
                    app.logger.info("No undownloaded tracks found.")
                    return {'status': 'No undownloaded tracks found'}

                app.logger.info(f"Found {total_tracks} tracks to download.")
                app.logger.debug(f"output_dir: {output_dir}")
                processed_tracks = 0
                failed_downloads = 0
                for track in undownloaded_tracks:
                    app.logger.info(f"Processing track: {track.name} [{track.provider_track_id}]")
                    self.update_state(state=f'[{processed_tracks}/{total_tracks}] {track.name} [{track.provider_track_id}]', meta={
                        'current': processed_tracks,
                        'total': total_tracks,
                        'percent':  (processed_tracks / total_tracks) * 100 if processed_tracks > 0 else 0,
                        'failed': failed_downloads
                    })
                    # Check if the track already exists in the output directory
                    if os.getenv('SPOTDL_OUTPUT_FORMAT') == '__jellyplist/{track-id}':
                        file_path = f"{output_dir.replace('{track-id}', track.provider_track_id)}"
                    else:
                        # if the output format is other than the default, we need to fetch the track first! 
                        spotify_track = functions.get_cached_provider_track(track.provider_track_id, provider_id="Spotify")
                        # spotify_track has name, artists, album and id
                        # name needs to be mapped to {title}
                        # artist[0] needs to be mapped to {artist}
                        # artists needs to be mapped to {artists}
                        # album needs to be mapped to {album} , but needs to be checked if it is set or not, because it is Optional
                        # id needs to be mapped to {track-id}
                        # the output format is then used to create the file path
                        if spotify_track:
                            
                            file_path = output_dir.replace("{title}",spotify_track.name)
                            file_path = file_path.replace("{artist}",spotify_track.artists[0].name)
                            file_path = file_path.replace("{artists}",",".join([artist.name for artist in spotify_track.artists]))
                            file_path = file_path.replace("{album}",spotify_track.album.name if spotify_track.album else "")
                            file_path = file_path.replace("{track-id}",spotify_track.id)
                            app.logger.debug(f"File path: {file_path}")
                    
                    if not file_path:
                        app.logger.error(f"Error creating file path for track {track.name}.")
                        failed_downloads += 1
                        track.download_status = "Error creating file path"
                        db.session.commit()
                        continue
                    
                                           
                                            
                    # region search before download
                    if search_before_download:
                        app.logger.info(f"Searching for track in Jellyfin: {track.name}")
                        # at first try to find the track without fingerprinting it
                        best_match = find_best_match_from_jellyfin(track)
                        if best_match:
                            track.downloaded = True
                            if track.jellyfin_id != best_match['Id']:
                                track.jellyfin_id = best_match['Id']
                                app.logger.info(f"Updated Jellyfin ID for track: {track.name} ({track.provider_track_id})")
                            if track.filesystem_path != best_match['Path']:
                                track.filesystem_path = best_match['Path']
                            db.session.commit()
                            processed_tracks+=1
                            continue                
                        
                        # region search with fingerprinting   
                        # as long as there is no endpoint found providing a preview url, we can't use this feature
                        # if spotify_track:                     
                        #     preview_url = spotify_track.get('preview_url')
                        #     if not preview_url:
                        #         app.logger.error(f"Preview URL not found for track {track.name}.")
                        #         # Decide whether to skip or proceed to download
                        #         # For now, we'll proceed to download
                        #     else:
                        #         # Get the list of Spotify artist names
                        #         spotify_artists = [artist['name'] for artist in spotify_track['artists']]

                        #         # Perform the search in Jellyfin
                        #         match_found, jellyfin_file_path = jellyfin.search_track_in_jellyfin(
                        #             session_token=jellyfin_admin_token,
                        #             preview_url=preview_url,
                        #             song_name=track.name,
                        #             artist_names=spotify_artists
                        #         )
                        #         if match_found:
                        #             app.logger.info(f"Match found in Jellyfin for track {track.name}. Skipping download.")
                        #             track.downloaded = True
                        #             track.filesystem_path = jellyfin_file_path
                        #             db.session.commit()
                        #             continue
                        #         else:
                        #             app.logger.info(f"No match found in Jellyfin for track {track.name}. Proceeding to download.")
                        # else:
                        #     app.logger.warning(f"spotify_track not set, see previous log messages")
                        #endregion
                                
                    #endregion 
                    if file_path:
                        if os.path.exists(file_path):
                            app.logger.info(f"Track {track.name} is already downloaded at {file_path}. Marking as downloaded.")
                            track.downloaded = True
                            track.filesystem_path = file_path
                            db.session.commit()
                            continue

                    

                    # Attempt to download the track using spotdl
                    try:
                        app.logger.info(f"Trying to download track: {track.name} ({track.provider_track_id}), spotdl timeout = 90")
                        s_url = f"https://open.spotify.com/track/{track.provider_track_id}"
                        
                        command = [
                            "spotdl", "download", s_url,
                            "--output", output_dir,
                            "--client-id", client_id,
                            "--client-secret", client_secret
                        ]
                        if os.path.exists(cookie_file):
                            app.logger.debug(f"Found {cookie_file}, using it for spotDL")
                            command.append("--cookie-file")
                            command.append(cookie_file)
                        if app.config['SPOTDL_PROXY']:
                            app.logger.debug(f"Using proxy: {app.config['SPOTDL_PROXY']}")
                            command.append("--proxy")
                            command.append(app.config['SPOTDL_PROXY'])

                        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
                        if result.returncode == 0:
                            track.downloaded = True
                            if file_path:
                                track.filesystem_path = file_path
                                app.logger.info(f"Track {track.name} downloaded successfully to {file_path}.")
                        else:
                            app.logger.error(f"Download failed for track {track.name}.")
                            if result.stdout:
                                app.logger.error(f"\t stdout: {result.stdout}")
                            if result.stderr:
                                app.logger.error(f"\t stderr: {result.stderr} ")
                            failed_downloads += 1
                            track.download_status = result.stdout[:2048]
                    except Exception as e:
                        app.logger.error(f"Error downloading track {track.name}: {str(e)}")
                        failed_downloads += 1
                        track.download_status = str(e)[:2048]

                    processed_tracks += 1
                    progress = (processed_tracks / total_tracks) * 100
                    db.session.commit()

                    self.update_state(state=f'[{processed_tracks}/{total_tracks}] {track.name} [{track.provider_track_id}]', meta={
                        'current': processed_tracks,
                        'total': total_tracks,
                        'percent': progress,
                        'failed': failed_downloads
                    })

                app.logger.info("Track download job finished.")
                return {
                    'status': 'download_missing_tracks finished',
                    'total': total_tracks,
                    'processed': processed_tracks,
                    'failed': failed_downloads
                }
        except Exception as e:
            app.logger.error(f"Error downloading tracks: {str(e)}", exc_info=True)
            return {'status': 'Error downloading tracks'}
        finally:
            task_manager.release_lock(lock_key)
            if app.config['REFRESH_LIBRARIES_AFTER_DOWNLOAD_TASK']:
                libraries = jellyfin.get_libraries(jellyfin_admin_token)
                for lib in libraries:
                    if lib['CollectionType'] == 'music':
                        jellyfin.refresh_library(jellyfin_admin_token, lib['ItemId'])
    else:
        app.logger.info("Skipping task. Another instance is already running.")
        return {'status': 'Task skipped, another instance is running'}
    
@celery.task(bind=True)
def check_for_playlist_updates(self):
    lock_key = "check_for_playlist_updates_lock"
    
    if task_manager.acquire_lock(lock_key, expiration=600):  
        try:
            app.logger.info('Starting playlist update check...')
            with app.app_context():
                playlists: List[Playlist]     = Playlist.query.all()
                total_playlists = len(playlists)
                if not playlists:
                    app.logger.info("No playlists found.")
                    return {'status': 'No playlists found'}

                app.logger.info(f"Found {total_playlists} playlists to check for updates.")
                processed_playlists = 0

                for playlist in playlists:
                    playlist.last_updated = datetime.now( timezone.utc)
                    # get the correct MusicProvider from the registry 
                    provider = MusicProviderRegistry.get_provider(playlist.provider_id)
                    provider_playlist = provider.get_playlist(playlist.provider_playlist_id)
                    provider_tracks = provider_playlist.tracks
                    full_update = True
                    app.logger.info(f'Checking updates for playlist: {playlist.name}')
                    db.session.commit()
                    
                    try:
                        #region Check for updates
                        if full_update:
                            existing_tracks = {track.provider_track_id: track for track in playlist.tracks}

                            # Determine tracks to add and remove
                            tracks_to_add = []
                            for idx, track_info in enumerate(provider_tracks):
                                if track_info:
                                    track_id = track_info.track.id
                                    if track_id not in existing_tracks:
                                        track = Track.query.filter_by(provider_track_id=track_id,provider_id = playlist.provider_id).first()
                                        if not track:
                                            track = Track(name=track_info.track.name, provider_track_id=track_id, provider_uri=track_info.track.uri, downloaded=False,provider_id = playlist.provider_id)
                                            db.session.add(track)
                                            db.session.commit()
                                            app.logger.info(f'Added new track: {track.name}')
                                        tracks_to_add.append((track, idx))

                            tracks_to_remove = [
                                existing_tracks[track_id] 
                                for track_id in existing_tracks 
                                if track_id not in {track.track.id for track in provider_tracks if track}
                            ]

                            if tracks_to_add or tracks_to_remove:
                                playlist.last_changed = datetime.now( timezone.utc)

                            # Add and remove tracks while maintaining order
                            
                            if tracks_to_add:
                                
                                for track, track_order in tracks_to_add:
                                    stmt = insert(playlist_tracks).values(
                                        playlist_id=playlist.id,
                                        track_id=track.id,
                                        track_order=track_order
                                    )
                                    db.session.execute(stmt)
                                db.session.commit()
                                app.logger.info(f'Added {len(tracks_to_add)} tracks to playlist: {playlist.name}')

                            if tracks_to_remove:
                                for track in tracks_to_remove:
                                    playlist.tracks.remove(track)
                                db.session.commit()
                                app.logger.info(f'Removed {len(tracks_to_remove)} tracks from playlist: {playlist.name}')
                            #endregion
                        
                        #region Update Playlist Items and Metadata
                        functions.update_playlist_metadata(playlist, provider_playlist)
                        ordered_tracks = db.session.execute(
                            db.select(Track, playlist_tracks.c.track_order)
                            .join(playlist_tracks, playlist_tracks.c.track_id == Track.id)
                            .where(playlist_tracks.c.playlist_id == playlist.id)
                            .order_by(playlist_tracks.c.track_order)
                        ).all()

                        tracks = []
                        size = 0
                        for track, idx in ordered_tracks:
                            if track.jellyfin_id is not None:
                                tracks.append(track.jellyfin_id)
                                size = size + 1
                            if size >= 100:
                                jellyfin.add_songs_to_playlist(session_token=jellyfin_admin_token, user_id=jellyfin_admin_id, playlist_id=playlist.jellyfin_id, song_ids=tracks)
                                tracks = []
                                size = 0
                        if size > 0:
                            jellyfin.add_songs_to_playlist(session_token=jellyfin_admin_token, user_id=jellyfin_admin_id, playlist_id=playlist.jellyfin_id, song_ids=tracks)

                        #endregion
                    except Exception as e:
                        app.logger.error(f"Error updating playlist {playlist.name}: {str(e)}")

                    processed_playlists += 1
                    progress = (processed_playlists / total_playlists) * 100

                    # Update progress
                    # self.update_state(state='PROGRESS', meta={'current': processed_playlists, 'total': total_playlists, 'percent': progress})

                    if processed_playlists % 10 == 0 or processed_playlists == total_playlists:
                        app.logger.info(f"Processed {processed_playlists}/{total_playlists} playlists.")

                return {'status': 'Playlist update check completed', 'total': total_playlists, 'processed': processed_playlists}
        except Exception as e:
            app.logger.error(f"Error downloading tracks: {str(e)}", exc_info=True)
            return {'status': 'Error downloading tracks'}
        finally:
            task_manager.release_lock(lock_key)
    else:
        app.logger.info("Skipping task. Another instance is already running.")
        return {'status': 'Task skipped, another instance is running'}

@celery.task(bind=True)
def update_jellyfin_id_for_downloaded_tracks(self):
    lock_key = "update_jellyfin_id_for_downloaded_tracks_lock"
    full_update_key = 'full_update_jellyfin_ids_lock'
    if task_manager.acquire_lock(lock_key, expiration=600):  # Lock for 10 minutes
        try:
            app.logger.info("Starting Jellyfin ID update for tracks...")

            with app.app_context():
                
                downloaded_tracks = Track.query.filter(
                    Track.downloaded == True,
                    Track.jellyfin_id == None,
                    (Track.quality_score < app.config['QUALITY_SCORE_THRESHOLD']) | (Track.quality_score == None)
                ).all()
                if task_manager.acquire_lock(full_update_key, expiration=60*60*24):
                    app.logger.info(f"performing full update on jellyfin track ids. (Update tracks and playlists if better quality will be found)")
                    app.logger.info(f"\tQUALITY_SCORE_THRESHOLD = {app.config['QUALITY_SCORE_THRESHOLD']}")
                    downloaded_tracks = Track.query.filter(
                        (Track.quality_score < app.config['QUALITY_SCORE_THRESHOLD']) | (Track.quality_score == None)
                    ).all()
                else:
                    app.logger.debug(f"doing update on tracks with downloaded = True and jellyfin_id = None")
                total_tracks = len(downloaded_tracks)
                if not downloaded_tracks:
                    app.logger.info("No downloaded tracks without Jellyfin ID found.")
                    return {'status': 'No tracks to update'}

                app.logger.info(f"Found {total_tracks} tracks to update ")
                processed_tracks = 0

                for track in downloaded_tracks:
                    try:
                        best_match = find_best_match_from_jellyfin(track)
                        
                        if best_match:
                            track.downloaded = True
                            if track.jellyfin_id != best_match['Id']:
                                track.jellyfin_id = best_match['Id']
                                app.logger.info(f"Updated Jellyfin ID for track: {track.name} ({track.provider_track_id})")
                            if track.filesystem_path != best_match['Path']:
                                track.filesystem_path = best_match['Path']
                                app.logger.info(f"Updated filesystem_path for track: {track.name} ({track.provider_track_id})")
                                
                            track.quality_score = best_match['quality_score']    
                            
                            db.session.commit()
                        else:
                            
                            app.logger.warning(f"No matching track found in Jellyfin for {track.name}.")
                        
                        spotify_track = None
                        
                    except Exception as e:
                        app.logger.error(f"Error searching Jellyfin for track {track.name}: {str(e)}")

                    processed_tracks += 1
                    progress = (processed_tracks / total_tracks) * 100
                    
                    self.update_state(state=f'{processed_tracks}/{total_tracks}: {track.name}', meta={'current': processed_tracks, 'total': total_tracks, 'percent': progress})

                app.logger.info("Finished updating Jellyfin IDs for all tracks.")
                return {'status': 'All tracks updated', 'total': total_tracks, 'processed': processed_tracks}
        except Exception as e:
            app.logger.error(f"Error updating jellyfin ids: {str(e)}", exc_info=True)
            return {'status': 'Error updating jellyfin ids '}
        finally:
            task_manager.release_lock(lock_key)
    else:
        app.logger.info("Skipping task. Another instance is already running.")
        return {'status': 'Task skipped, another instance is running'}

@celery.task(bind=True)
def request_lidarr(self):
    lock_key = "request_lidarr_lock"
    
    if task_manager.acquire_lock(lock_key, expiration=600):  
        with app.app_context():
            if app.config['LIDARR_API_KEY'] and app.config['LIDARR_URL']:
                from app import lidarr_client
                try:
                    app.logger.info('Submitting request to Lidarr...')
                    # get all tracks from db
                    tracks = Track.query.filter_by(lidarr_processed=False).all()
                    total_items = len(tracks)
                    processed_items = 0
                    for track in tracks:
                        tfp = functions.get_cached_provider_track(track.provider_track_id, provider_id=track.provider_id)
                        if tfp:                            
                            if app.config['LIDARR_MONITOR_ARTISTS']:
                                app.logger.debug("Monitoring artists instead of albums")
                                # get all artists from all tracks_from_provider and unique them
                                artists : dict[str,base.Artist] = {}
                                
                                for artist in tfp.artists:
                                    artists[artist.name] = artist
                                app.logger.debug(f"Found {len(artists)} artists to monitor")
                                #pylint: disable=consider-using-dict-items
                                for artist in artists:
                                    artist_from_lidarr = None
                                    search_result = lidarr_client.search(artists[artist].name)
                                    for url in artists[artist].external_urls:
                                        artist_from_lidarr : Artist = lidarr_client.get_object_by_external_url(search_result, url.url)
                                        if artist_from_lidarr:
                                            app.logger.debug(f"Found artist {artist_from_lidarr.artistName} by external url {url.url}")
                                            functions.apply_default_profile_and_root_folder(artist_from_lidarr)
                                            try:
                                                lidarr_client.monitor_artist(artist_from_lidarr)
                                                track.lidarr_processed = True
                                                db.session.commit()
                                            except Exception as e:
                                                app.logger.error(f"Error monitoring artist {artist_from_lidarr.artistName}: {str(e)}")
                                                
                                    if not artist_from_lidarr:
                                        # if the artist isnt found by the external url, search by name
                                        artist_from_lidarr = lidarr_client.get_artists_by_name(search_result, artists[artist].name)
                                        for artist2 in artist_from_lidarr:
                                            functions.apply_default_profile_and_root_folder(artist2)
                                            try:
                                                lidarr_client.monitor_artist(artist2)
                                                track.lidarr_processed = True
                                                db.session.commit()
                                            except Exception as e:
                                                app.logger.error(f"Error monitoring artist {artist2.artistName}: {str(e)}")
                                                
                                    processed_items += 1
                                    self.update_state(state=f'{processed_items}/{total_items}: {artist}', meta={'current': processed_items, 'total': total_items, 'percent': (processed_items / total_items) * 100})

                            else:
                                if tfp.album:
                                    album_from_lidarr = None
                                    search_result = lidarr_client.search(tfp.album.name)
                                    # if the album isnt found by the external url, search by name
                                    album_from_lidarr = lidarr_client.get_albums_by_name(search_result, tfp.album.name)
                                    for album2 in album_from_lidarr:
                                        functions.apply_default_profile_and_root_folder(album2.artist)
                                        try:
                                            lidarr_client.monitor_album(album2)
                                            track.lidarr_processed = True
                                            db.session.commit()
                                        except Exception as e:
                                            app.logger.error(f"Error monitoring album {album2.title}: {str(e)}")
                                    processed_items += 1
                                    self.update_state(state=f'{processed_items}/{total_items}: {tfp.album.name}', meta={'current': processed_items, 'total': total_items, 'percent': (processed_items / total_items) * 100})


                    app.logger.info(f'Requests sent to Lidarr. Total items: {total_items}')
                    return {'status': 'Request sent to Lidarr'}
                except Exception as e:
                    app.logger.error(f"Error downloading tracks: {str(e)}", exc_info=True)
                    return {'status': 'Error downloading tracks'}
                finally:
                    task_manager.release_lock(lock_key)
    
            else:
                app.logger.info('Lidarr API key or URL not set. Skipping request.')
                task_manager.release_lock(lock_key)
            
                
    else:
        app.logger.info("Skipping task. Another instance is already running.")
        return {'status': 'Task skipped, another instance is running'}

def find_best_match_from_jellyfin(track: Track):
    app.logger.debug(f"Trying to find best match from Jellyfin server for track: {track.name}")
    search_results = jellyfin.search_music_tracks(jellyfin_admin_token, functions.get_longest_substring(track.name))
    provider_track = None
    try:
        best_match = None
        best_quality_score = -1  # Initialize with the lowest possible score
        for result in search_results:
            app.logger.debug(f"Processing search result: {result['Id']}, Path = {result['Path']}")
            quality_score = compute_quality_score(result, app.config['FIND_BEST_MATCH_USE_FFPROBE'])
            try:
                provider_track = functions.get_cached_provider_track(track.provider_track_id, provider_id=track.provider_id)
                provider_track_name = provider_track.name.lower()
                provider_artists = [artist.name.lower() for artist in provider_track.artists]
            except Exception as e:
                app.logger.error(f"\tError fetching track details from Spotify for {track.name}: {str(e)}")
                continue
            jellyfin_track_name = result.get('Name', '').lower()
            if len(result.get('Artists', [])) == 1:
                jellyfin_artists = [a.lower() for a in result.get('Artists', [])[0].split('/')]
            else:
                jellyfin_artists = [artist.lower() for artist in result.get('Artists', [])]
            jellyfin_album_artists = [artist['Name'].lower() for artist in result.get('AlbumArtists', [])]
            
            if provider_track and jellyfin_track_name and jellyfin_artists and provider_artists:
                app.logger.debug("\tTrack details to compare: ")
                app.logger.debug(f"\t\tJellyfin-Trackname : {jellyfin_track_name}")
                app.logger.debug(f"\t\t Spotify-Trackname : {provider_track_name}")
                app.logger.debug(f"\t\t  Jellyfin-Artists : {jellyfin_artists}")
                app.logger.debug(f"\t\t   Spotify-Artists : {provider_artists}")
                app.logger.debug(f"\t\t  Jellyfin-Alb.Art.: {jellyfin_album_artists}")
                if len(search_results) == 1:
                    app.logger.debug(f"\tOnly 1 search_result: {result['Id']} ({app.config['JELLYFIN_SERVER_URL']}/web/#/details?id={result['Id']})")
                    
                    if (provider_track_name.lower() == jellyfin_track_name and
                        (set(artist.lower() for artist in provider_artists) == set(jellyfin_artists) or set(jellyfin_album_artists) == set(artist.lower() for artist in provider_artists))):
                        
                        app.logger.debug(f"\tQuality score for track {result['Name']}: {quality_score} [{result['Path']}]")
                        best_match = result
                        best_quality_score = quality_score
                        break
                
                
                if (provider_track_name.lower() == jellyfin_track_name and
                    (set(artist.lower() for artist in provider_artists) == set(jellyfin_artists) or set(jellyfin_album_artists) == set(artist.lower() for artist in provider_artists))):
                    app.logger.debug(f"\tQuality score for track {result['Name']}: {quality_score} [{result['Path']}]")
                    
                    if quality_score > best_quality_score:
                        best_match = result
                        best_quality_score = quality_score
        # attach the quality_score to the best_match
        if best_match:
            best_match['quality_score'] = best_quality_score
        return best_match
    except Exception as e:
        app.logger.error(f"Error searching Jellyfin for track {track.name}: {str(e)}")
        return None

def compute_quality_score(result, use_ffprobe=False) -> float:
    """
    Compute a quality score for a track based on its metadata or detailed analysis using ffprobe.
    """
    score = 0
    container = result.get('Container', '').lower()
    if container == 'flac':
        score += 100
    elif container == 'wav':
        score += 50
    elif container == 'mp3':
        score += 10
    elif container == 'aac':
        score += 5
    
    if result.get('HasLyrics'):
        score += 10
    
    #runtime_ticks = result.get('RunTimeTicks', 0)
    #score += runtime_ticks / 1e6

    if use_ffprobe:
        path = result.get('Path')
        if path:
            profile = AudioProfile.analyze_audio_quality_with_ffprobe(path)
            ffprobe_score = profile.compute_quality_score()
            score += ffprobe_score
        else:
            app.logger.warning(f"No valid file path for track {result.get('Name')} - Skipping ffprobe analysis.")
    
    return score



class TaskManager:
    def __init__(self):
        self.tasks = {
            'update_all_playlists_track_status': None,
            'download_missing_tracks': None,
            'check_for_playlist_updates': None,
            'update_jellyfin_id_for_downloaded_tracks': None
        }
        if app.config['LIDARR_API_KEY']:
            self.tasks['request_lidarr'] = None

    def start_task(self, task_name, *args, **kwargs):
        if task_name not in self.tasks:
            raise ValueError(f"Task {task_name} is not defined.")
        task = globals()[task_name].delay(*args, **kwargs)
        self.tasks[task_name] = task.id
        return task.id,'STARTED'

    def get_task_status(self, task_name):
        if task_name not in self.tasks:
            raise ValueError(f"Task {task_name} is not defined.")
        task_id = self.tasks[task_name]
        if not task_id:
            return {'state': 'NOT STARTED', 'info': {}, 'lock_status': False}
        result = AsyncResult(task_id)
        lock_status = True if self.get_lock(f"{task_name}_lock") else False
        return {'state': result.state, 'info': result.info if result.info else {}, 'lock_status': lock_status}

    def acquire_lock(self, lock_name, expiration=60):
        return redis_client.set(lock_name, "locked", ex=expiration, nx=True)

    def release_lock(self, lock_name):
        redis_client.delete(lock_name)

    def get_lock(self, lock_name):
        return redis_client.get(lock_name)

    def prepare_logger(self):
        FORMAT = "[%(asctime)s][%(filename)18s:%(lineno)4s - %(funcName)20s() ]  %(message)s"
        logging.basicConfig(format=FORMAT)

task_manager = TaskManager()

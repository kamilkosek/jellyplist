import os
import re
import subprocess
import tempfile
import numpy as np
import requests
import base64
import acoustid
import chromaprint
import logging
from jellyfin.objects import PlaylistMetadata

def _clean_query(query):
    # Regex to match any word containing problematic characters: ', `, or ´
    pattern = re.compile(r"[`´'’]")
    
    # Split the query into words and filter out words with problematic characters
    cleaned_words = [word for word in query.split() if not pattern.search(word)]
    
    # Join the cleaned words back into a query string
    cleaned_query = " ".join(cleaned_words)
    return cleaned_query

class JellyfinClient:
    def __init__(self, base_url, timeout = 10):
        """
        Initialize the Jellyfin client with the base URL of the server.
        :param base_url: The base URL of the Jellyfin server (e.g., 'http://localhost:8096')
        """
        self.base_url = base_url
        self.timeout = timeout
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(os.getenv('LOG_LEVEL', 'INFO').upper())
        FORMAT = "[%(asctime)s][%(filename)18s:%(lineno)4s - %(funcName)23s() ] %(levelname)7s - %(message)s"  
        logging.basicConfig(format=FORMAT)
        self.logger.debug(f"Initialized Jellyfin API Client. Base = '{self.base_url}', timeout = {timeout}")

    def _get_headers(self, session_token: str):
        """
        Get the authentication headers for requests.
        :return: A dictionary of headers
        """
        return {
            'X-Emby-Token': session_token,
        }

    def login_with_password(self, username: str, password: str, device_id = 'JellyPlist'):
        """
        Log in to Jellyfin using a username and password.
        :param username: The username of the user.
        :param password: The password of the user.
        :return: Access token and user ID
        """
        url = f'{self.base_url}/Users/AuthenticateByName'
        headers = {
            'Content-Type': 'application/json',
            'X-Emby-Authorization': f'MediaBrowser Client="JellyPlist", Device="Web", DeviceId="{device_id}", Version="1.0"'
        }
        data = {
            'Username': username,
            'Pw': password
        }
        self.logger.debug(f"Url={url}")
        response = requests.post(url, json=data, headers=headers)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            return result['AccessToken'], result['User']['Id'], result['User']['Name'],result['User']['Policy']['IsAdministrator']
        else:
            raise Exception(f"Login failed: {response.content}")

    def create_music_playlist(self, session_token: str, name: str, song_ids, user_id : str):
        """
        Create a new music playlist.
        :param access_token: The access token of the authenticated user.
        :param user_id: The user ID.
        :param name: The name of the playlist.
        :param song_ids: A list of song IDs to include in the playlist.
        :return: The newly created playlist object
        """
        url = f'{self.base_url}/Playlists'
        data = {
            'Name': name,
            'UserId': user_id,
            'MediaType': 'Audio',
            'Ids': ','.join(song_ids),  # Join song IDs with commas
            'IsPublic' : False
        }
        self.logger.debug(f"Url={url}")

        response = requests.post(url, json=data, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to create playlist: {response.content}")

    def update_music_playlist(self, session_token: str, playlist_id: str, song_ids):
        """
        Update an existing music playlist by adding or removing songs.
        :param playlist_id: The ID of the playlist to update.
        :param song_ids: A list of song IDs to include in the playlist.
        :return: The updated playlist object
        """
        url = f'{self.base_url}/Playlists/{playlist_id}/Items'
        data = {
            'Ids': ','.join(song_ids)  # Join song IDs with commas
        }
        self.logger.debug(f"Url={url}")

        response = requests.post(url, json=data, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")

        if response.status_code == 204:  # 204 No Content indicates success for updating
            return {"status": "success", "message": "Playlist updated successfully"}
        else:
            raise Exception(f"Failed to update playlist: {response.content}")

    def get_playlist_metadata(self, session_token: str, user_id: str,playlist_id: str) -> PlaylistMetadata:
        url = f'{self.base_url}/Items/{playlist_id}'
        params = {
            'UserId' : user_id
        }
        self.logger.debug(f"Url={url}")
        
        response = requests.get(url, headers=self._get_headers(session_token=session_token), timeout = self.timeout, params = params)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code != 200:
            raise Exception(f"Failed to fetch playlist metadata: {response.content}")
        
        return PlaylistMetadata( response.json())

    def update_playlist_metadata(self, session_token: str, user_id: str, playlist_id: str, updates: PlaylistMetadata):
        """
        Update the metadata of an existing playlist using a PlaylistMetadata object.
        
        :param session: The user's session containing the Jellyfin access token.
        :param user_id: the user id, since we are updating the playlist using an api key, we do it with the user id of the first logged in admin
        :param playlist_id: The ID of the playlist to update.
        :param updates: A PlaylistMetadata object containing the metadata to update.
        :return: Response data indicating the result of the update operation.
        """
        # Fetch the existing metadata for the playlist
        params = {
            'UserId' : user_id
        }
       
        # Initialize PlaylistMetadata with current data and apply updates
        metadata_obj = self.get_playlist_metadata(session_token=session_token, user_id= user_id, playlist_id= playlist_id)
        
        # Update only the provided fields in the updates object
        for key, value in updates.to_dict().items():
            if value is not None:
                setattr(metadata_obj, key, value)
        
        # Send the updated metadata to Jellyfin
        url = f'{self.base_url}/Items/{playlist_id}'
        self.logger.debug(f"Url={url}")
        
        response = requests.post(url, json=metadata_obj.to_dict(), headers=self._get_headers(session_token= session_token), timeout = self.timeout, params = params)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code == 204:
            return {"status": "success", "message": "Playlist metadata updated successfully"}
        else:
            raise Exception(f"Failed to update playlist metadata: {response.content} \nReason: {response.reason}")


    def get_playlists(self, session_token: str):
        """
        Get all music playlists for the currently authenticated user.
        :return: A list of the user's music playlists
        """
        url = f'{self.base_url}/Items'
        params = {
            'IncludeItemTypes': 'Playlist',  # Retrieve only playlists
            'Recursive': 'true',             # Include nested playlists
            'Fields': 'OpenAccess'              # Fields we want
        }

        self.logger.debug(f"Url={url}")
        
        response = requests.get(url, headers=self._get_headers(session_token=session_token), params=params , timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code == 200:
            return response.json()['Items']
        else:
            raise Exception(f"Failed to get playlists: {response.content}")

    def get_libraries(self, session_token: str):
        url = f'{self.base_url}/Library/VirtualFolders'
        params = {
            
        }
        self.logger.debug(f"Url={url}")
        
        response = requests.get(url, headers=self._get_headers(session_token=session_token), params=params , timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get playlists: {response.content}")
        
    def refresh_library(self, session_token: str, library_id: str) -> bool:
        url = f'{self.base_url}/Items/{library_id}/Refresh'
        
        params = {
            "Recursive": "true",
            "ImageRefreshMode": "Default",
            "MetadataRefreshMode": "Default",
            "ReplaceAllImages": "false",
            "RegenerateTrickplay": "false",
            "ReplaceAllMetadata": "false"
        }
        self.logger.debug(f"Url={url}")
        
        response = requests.post(url, headers=self._get_headers(session_token=session_token), params=params , timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        if response.status_code == 204:
            return True
        else:
            raise Exception(f"Failed to update library: {response.content}")



    def search_music_tracks(self, session_token: str, search_query: str):
        """
        Search for music tracks by title, song name, and optionally Spotify-ID.
        :param search_query: The search term (title or song name).
        :return: A list of matching songs.
        """
        url = f'{self.base_url}/Items'
        params = {
            'SearchTerm': search_query.replace('\'',"´").replace('’','´'),
            
            'IncludeItemTypes': 'Audio',  # Search only for audio items
            'Recursive': 'true',          # Search within all folders
            'Fields': 'Name,Id,Album,Artists,Path'           # Retrieve the name and ID of the song
        }
        self.logger.debug(f"Url={url}")
        

        response = requests.get(url, headers=self._get_headers(session_token=session_token), params=params, timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code == 200:
            return response.json()['Items']
        else:
            raise Exception(f"Failed to search music tracks: {response.content}")

    def add_songs_to_playlist(self, session_token: str, user_id: str, playlist_id: str, song_ids: list[str]):
        """
        Add songs to an existing playlist.
        :param playlist_id: The ID of the playlist to update.
        :param song_ids: A list of song IDs to add.
        :return: A success message.
        """
        # Construct the API URL with query parameters
        url = f'{self.base_url}/Playlists/{playlist_id}/Items'
        params = {
            'ids': ','.join(song_ids),  # Comma-separated song IDs
            'userId': user_id
        }

        self.logger.debug(f"Url={url}")
        
        # Send the request to Jellyfin API with query parameters
        response = requests.post(url, headers=self._get_headers(session_token=session_token), params=params, timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")

        # Check for success
        if response.status_code == 204:  # 204 No Content indicates success
            return {"status": "success", "message": "Songs added to playlist successfully"}
        else:
            raise Exception(f"Failed to add songs to playlist: {response.status_code} - {response.content}")

    def remove_songs_from_playlist(self, session_token: str, playlist_id: str, song_ids):
        """
        Remove songs from an existing playlist.
        :param playlist_id: The ID of the playlist to update.
        :param song_ids: A list of song IDs to remove.
        :return: A success message.
        """
        url = f'{self.base_url}/Playlists/{playlist_id}/Items'
        params = {
            'EntryIds': ','.join(song_ids)  # Join song IDs with commas
        }
        self.logger.debug(f"Url={url}")
        
        response = requests.delete(url, headers=self._get_headers(session_token=session_token), params=params, timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")

        if response.status_code == 204:  # 204 No Content indicates success for updating
            return {"status": "success", "message": "Songs removed from playlist successfully"}
        else:
            raise Exception(f"Failed to remove songs from playlist: {response.content}")

    def remove_item(self, session_token: str, playlist_id: str):  
        """
        Remove an existing playlist by its ID.
        :param playlist_id: The ID of the playlist to remove.
        :return: A success message upon successful deletion.
        """
        url = f'{self.base_url}/Items/{playlist_id}'
        self.logger.debug(f"Url={url}")
        
        response = requests.delete(url, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        logging.getLogger('requests').setLevel(logging.WARNING)

        if response.status_code == 204:  # 204 No Content indicates successful deletion
            return {"status": "success", "message": "Playlist removed successfully"}
        else:
            raise Exception(f"Failed to remove playlist: {response.content}")
    
    def get_item(self, session_token: str, item_id: str):
        url = f'{self.base_url}/Items/{item_id}'
        logging.getLogger('requests').setLevel(logging.WARNING)
        response = requests.get(url, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get item: {response.content}")
        
    def remove_user_from_playlist(self, session_token: str, playlist_id: str, user_id: str):
        """
        Remove a user from a playlist.
        
        :param session: The user's session containing the Jellyfin access token.
        :param playlist_id: The ID of the playlist from which to remove the user.
        :param user_id: The ID of the user to be removed from the playlist.
        :return: Success message or raises an exception on failure.
        """
        # Construct the API endpoint URL
        url = f'{self.base_url}/Playlists/{playlist_id}/Users/{user_id}'
        self.logger.debug(f"Url={url}")
        
        # Send the DELETE request to remove the user from the playlist
        response = requests.delete(url, headers=self._get_headers(session_token= session_token), timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        
        if response.status_code == 204:
            # 204 No Content indicates the user was successfully removed
            return {"status": "success", "message": f"User {user_id} removed from playlist {playlist_id}"}
        else:
            # Raise an exception if the request failed
            raise Exception(f"Failed to remove user from playlist: {response.content}")
    

    def set_playlist_cover_image(self, session_token: str, playlist_id: str, provider_image_url: str):
        """
        Set the cover image of a playlist in Jellyfin using an image URL from Spotify.
        
        :param session: The user's session containing the Jellyfin access token.
        :param playlist_id: The ID of the playlist in Jellyfin.
        :param spotify_image_url: The URL of the image from Spotify.
        :return: Success message or raises an exception on failure.
        """
        # Step 1: Download the image from the Spotify URL
        response = requests.get(provider_image_url, timeout = self.timeout)
        
        if response.status_code != 200:
            raise Exception(f"Failed to download image from Spotify: {response.content}")
        
        # Step 2: Check the image content type (assume it's JPEG or PNG based on the content type from the response)
        content_type = response.headers.get('Content-Type').lower()
        if content_type not in ['image/jpeg', 'image/png', 'image/webp', 'application/octet-stream']:
            raise Exception(f"Unsupported image format: {content_type}")
        # Todo: 
        if content_type == 'application/octet-stream':
            content_type = 'image/jpeg'
        # Step 3: Encode the image content as Base64
        image_base64 = base64.b64encode(response.content).decode('utf-8')

        # Step 4: Prepare the headers for the Jellyfin API request
        headers = self._get_headers(session_token= session_token)
        headers['Content-Type'] = content_type  # Set to the correct image type
        headers['Accept'] = '*/*'

        # url 5: Upload the Base64-encoded image to Jellyfin as a plain string in the request body
        url = f'{self.base_url}/Items/{playlist_id}/Images/Primary'
        self.logger.debug(f"Url={url}")
        
        # Send the Base64-encoded image data
        upload_response = requests.post(url, headers=headers, data=image_base64, timeout = self.timeout)
        self.logger.debug(f"Response = {response.status_code}")
        
        if upload_response.status_code == 204:  # 204 No Content indicates success
            return {"status": "success", "message": "Playlist cover image updated successfully"}
        else:
            raise Exception(f"Failed to upload image to Jellyfin: {upload_response.status_code} - {upload_response.content}")


    def add_users_to_playlist(self, session_token: str,user_id: str, playlist_id: str, user_ids: list[str], can_edit: bool = False):
        """
        Add users to a Jellyfin playlist with no editing rights by default.
        
        :param session: The user's session containing the Jellyfin access token.
        :param playlist_id: The ID of the playlist in Jellyfin.
        :param user_ids: List of user IDs to add to the playlist.
        :param can_edit: Set to True if users should have editing rights (default is False).
        :return: Success message or raises an exception on failure.
        """
        
        # FOr some reason when updating the users, all metadata gets wiped
        metadata = self.get_playlist_metadata(session_token= session_token, user_id= user_id, playlist_id= playlist_id)
        
        # Construct the API URL
        url = f'{self.base_url}/Playlists/{playlist_id}'
        users_data = [{'UserId': user_id, 'CanEdit': can_edit} for user_id in user_ids]
        # get current users: 
        current_users = self.get_playlist_users(session_token=session_token, playlist_id= playlist_id)
        for cu in current_users:
            users_data.append({'UserId': cu['UserId'], 'CanEdit': cu['CanEdit']})
        data = {
            'Users' : users_data
        }
        # Prepare the headers
        headers = self._get_headers(session_token=session_token)
        
        # Send the request to Jellyfin API
        response = requests.post(url, headers=headers, json=data,timeout = self.timeout)

        # Check for success
        if response.status_code == 204:
            self.update_playlist_metadata(session_token= session_token, user_id= user_id, playlist_id= playlist_id , updates= metadata)
            return {"status": "success", "message": f"Users added to playlist {playlist_id}."}
        else:
            raise Exception(f"Failed to add users to playlist: {response.status_code} - {response.content}")
        
    def get_me(self, session_token: str):
        """
        
        """
        me_url = f'{self.base_url}/Users/Me'
        response = requests.get(me_url, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get playlists: {response.content}")
        
    def get_playlist_users(self, session_token: str, playlist_id: str):
        url = f'{self.base_url}/Playlists/{playlist_id}/Users'
        
        response = requests.get(url, headers=self._get_headers(session_token=session_token), timeout = self.timeout)
        
        if response.status_code != 200:
            raise Exception(f"Failed to fetch playlist metadata: {response.content}")
    
        return response.json()
    
    def search_track_in_jellyfin(self, session_token: str, preview_url: str, song_name: str, artist_names: list):
        """
        Search for a track in Jellyfin by comparing the preview audio to tracks in the library.
        :param session_token: The session token for Jellyfin API access.
        :param preview_url: The URL to the Spotify preview audio.
        :param song_name: The name of the song to search for.
        :param artist_names: A list of artist names.
        :return: Tuple (match_found: bool, jellyfin_file_path: Optional[str])
        """
        try:
            # Download the Spotify preview audio
            

            self.logger.debug(f"Downloading preview  {preview_url} to tmp file")
            tmp = self.download_preview_to_tempfile(preview_url=preview_url)
            if tmp is None:
                self.logger.error(f"Downloading preview  {preview_url} to tmp file failed, not continuing")
                return False, None

            # Convert the preview file to a normalized WAV file
            self.logger.debug(f"Converting preview to WAV file")
            
            tmp_wav = self.convert_to_wav(tmp)
            if tmp_wav is None:
                self.logger.error(f"Converting preview to WAV failed, not continuing")
                os.remove(tmp)
                return False, None

            # Fingerprint the normalized preview WAV file
            self.logger.debug(f"Performing fingerprinting on preview {tmp_wav}")

            _, tmp_fp = acoustid.fingerprint_file(tmp_wav)
            tmp_fp_dec, version = chromaprint.decode_fingerprint(tmp_fp)
            tmp_fp_dec = np.array(tmp_fp_dec, dtype=np.uint32)
            self.logger.debug(f"decoded fingerprint for preview: {tmp_fp_dec[:5]}")

            # Search for matching tracks in Jellyfin using only the song name
            search_query = song_name  # Only use the song name in the search query
            jellyfin_results = self.search_music_tracks(session_token, search_query)

            matches = []

            # Prepare the list of Spotify artists in lowercase
            spotify_artists = [artist.lower() for artist in artist_names]

            for result in jellyfin_results:
                jellyfin_artists = [artist.lower() for artist in result.get("Artists", [])]

                # Check for matching artists u
                artist_match = any(artist in spotify_artists for artist in jellyfin_artists)
                if not artist_match:
                    continue  # Skip if no artist matches

                jellyfin_file_path = result.get("Path")
                if not jellyfin_file_path:
                    continue

                # Convert the full Jellyfin track to a normalized WAV file
                jellyfin_wav = self.convert_to_wav(jellyfin_file_path)
                if jellyfin_wav is None:
                    continue

                # Fingerprint the normalized Jellyfin WAV file
                _, full_fp = acoustid.fingerprint_file(jellyfin_wav)
                full_fp_dec, version2 = chromaprint.decode_fingerprint(full_fp)
                full_fp_dec = np.array(full_fp_dec, dtype=np.uint32)

                # Compare fingerprints using the sliding similarity function
                sim, best_offset = self.sliding_fingerprint_similarity(full_fp_dec, tmp_fp_dec)

                # Clean up temporary files
                os.remove(jellyfin_wav)

                # Store the match data
                matches.append({
                    'jellyfin_file_path': jellyfin_file_path,
                    'similarity': sim,
                    'best_offset': best_offset,
                    'track_name': result.get('Name'),
                    'artists': jellyfin_artists,
                })

            # Clean up the preview files
            os.remove(tmp_wav)
            os.remove(tmp)

            # After processing all tracks, select the best match
            if matches:
                best_match = max(matches, key=lambda x: x['similarity'])
                if best_match['similarity'] > 60:  # Adjust the threshold as needed
                    return True, best_match['jellyfin_file_path']
                else:
                    return False, None
            else:
                return False, None

        except Exception as e:
            # Log the error (assuming you have a logging mechanism)
            print(f"Error in search_track_in_jellyfin: {str(e)}")
            return False, None

    # Helper methods used in search_track_in_jellyfin
    def download_preview_to_tempfile(self, preview_url):
        try:
            response = requests.get(preview_url, timeout = self.timeout)
            if response.status_code != 200:
                return None

            # Save to a temporary file
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            tmp_file.write(response.content)
            tmp_file.close()
            return tmp_file.name
        except Exception as e:
            print(f"Error downloading preview: {str(e)}")
            return None

    def convert_to_wav(self, input_file_path):
        try:
            output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            output_file.close()

            # Use ffmpeg to convert to WAV and normalize audio
            command = [
                "ffmpeg", "-y", "-i", input_file_path,
                "-acodec", "pcm_s16le", "-ar", "44100",
                "-ac", "2", output_file.name
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.error(f"Error converting to WAV, subprocess exitcode: {result.returncode} , input_file_path = {input_file_path}")
                self.logger.error(f"\tprocess stdout: {result.stdout}")
                self.logger.error(f"\tprocess stderr: {result.stderr}")
                
                os.remove(output_file.name)
                return None

            return output_file.name
        except Exception as e:
            self.logger.error(f"Error converting to WAV: {str(e)}")
            return None

    def sliding_fingerprint_similarity(self, full_fp, preview_fp):
        len_full = len(full_fp)
        len_preview = len(preview_fp)

        best_score = float('inf')
        best_offset = 0

        max_offset = len_full - len_preview

        if max_offset < 0:
            return 0, 0

        total_bits = len_preview * 32  # Total bits in the preview fingerprint

        for offset in range(max_offset + 1):
            segment = full_fp[offset:offset + len_preview]
            xored = np.bitwise_xor(segment, preview_fp)
            diff_bits = np.unpackbits(xored.view(np.uint8)).sum()
            score = diff_bits / total_bits  # Lower score is better

            if score < best_score:
                best_score = score
                best_offset = offset

        similarity = (1 - best_score) * 100  # Convert to percentage

        return similarity, best_offset

import os
from app.providers.base import AccountAttributes, Album, Artist, BrowseCard, BrowseSection, Image, MusicProviderClient, Owner, Playlist, PlaylistTrack, Profile, Track, ExternalUrl, Category
import base64

import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import List, Dict, Optional
from http.cookiejar import MozillaCookieJar
import logging

from typing import Callable, Tuple
import hmac
import time
import hashlib

l = logging.getLogger(__name__)

_TOTP_SECRET = bytearray([53,53,48,55,49,52,53,56,53,51,52,56,55,52,57,57,53,57,50,50,52,56,54,51,48,51,50,57,51,52,55])

def generate_totp(
    secret: bytes = _TOTP_SECRET,
    algorithm: Callable[[], object] = hashlib.sha1,
    digits: int = 6,
    counter_factory: Callable[[], int] = lambda: int(time.time()) // 30,
) -> Tuple[str, int]:
    counter = counter_factory()
    hmac_result = hmac.new(
        secret, counter.to_bytes(8, byteorder="big"), algorithm  # type: ignore
    ).digest()

    offset = hmac_result[-1] & 15
    truncated_value = (
        (hmac_result[offset] & 127) << 24
        | (hmac_result[offset + 1] & 255) << 16
        | (hmac_result[offset + 2] & 255) << 8
        | (hmac_result[offset + 3] & 255)
    )
    return (
        str(truncated_value % (10**digits)).zfill(digits),
        counter * 30_000,
    )  # (30 * 1000)

class SpotifyClient(MusicProviderClient):
    """
    Spotify implementation of the MusicProviderClient.
    """
    @property
    def _identifier(self) -> str:
        return "Spotify"
    
    def __init__(self, cookie_file: Optional[str] = None):
        self.base_url = "https://api-partner.spotify.com"
        self.app_server_config_data = None
        self.client_token = None
        self.access_token = None
        self.client_id = None
        self.cookies = None
        if cookie_file:
            self._load_cookies(cookie_file)
            
    def _load_cookies(self, cookie_file: str) -> None:
        """
        Load cookies from a file.

        :param cookie_file: Path to the cookie file.
        """
        if not os.path.exists(cookie_file):
            l.error(f"Cookie file not found: {cookie_file}")
            raise FileNotFoundError(f"Cookie file not found: {cookie_file}")

        cookie_jar = MozillaCookieJar(cookie_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        self.cookies = requests.utils.dict_from_cookiejar(cookie_jar)

    def authenticate(self, credentials: Optional[dict] = None) -> None:
        """
        Authenticate with Spotify using cookies if available, or fetch session and config data.

        :param credentials: Optional dictionary of credentials.
        """
        if self.cookies:
            l.debug("Authenticating using cookies.")
            self.app_server_config_data = self._fetch_app_server_config_data()
        else:
            l.debug("Authenticating without cookies.")
            self.app_server_config_data = self._fetch_app_server_config_data(fetch_with_cookies=False)

        self._get_access_token_and_client_id()
        self.client_token = self._fetch_client_token()

    def _fetch_app_server_config_data(self, fetch_with_cookies: bool = True):
        """
        Fetch app_server_config data from Spotify.

        We will use the correlationId from app_server_config_data for a successful _fetch_client_token call

        :param fetch_with_cookies: Whether to include cookies in the request.
        :return: Tuple containing session and config data.
        """
        url = 'https://open.spotify.com/'
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
        cookies = self.cookies if fetch_with_cookies else None
        response = requests.get(url, headers=headers, cookies=cookies)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        app_server_config_script = soup.find('script', {'id': 'appServerConfig'}).text # decode JWT to obtain correlation required for obtain Bearer Token
        decoded_app_server_config_script = base64.b64decode(app_server_config_script) # base64 decode
        decoded_app_server_config_script = decoded_app_server_config_script.decode().strip("b'") # decode from byte object to string object
        if decoded_app_server_config_script:
            l.debug("fetched app_server_config_script scripts")
            return json.loads(decoded_app_server_config_script)
        else:
            raise ValueError("Failed to fetch app_server_config data.")

    def _fetch_client_token(self):
        """
        Fetch the client token using session data and cookies.

        :return: The client token as a string.
        """
        url = f'https://clienttoken.spotify.com/v1/clienttoken'
        headers = {
            'accept': 'application/json',
            'content-type': 'application/json',
            'origin': 'https://open.spotify.com',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
        payload = {
            "client_data": {
                "client_version": "1.2.52.404.gcb99a997",
                "client_id": self.client_id,
                "js_sdk_data": {
                    "device_brand": "unknown",
                    "device_model": "unknown",
                    "os": "windows",
                    "os_version": "NT 10.0",
                    "device_id": self.app_server_config_data.get("correlationId", ""),
                    "device_type": "computer"
                }
            }
        }
        response = requests.post(url, headers=headers, json=payload, cookies=self.cookies)
        response.raise_for_status()
        l.debug("fetched client_token (granted_token)")
        return response.json().get("granted_token", "")

    def _get_access_token_and_client_id(self, fetch_with_cookies: bool = True):
        """
        Fetch the Access Token and Client ID by making GET call to open.spotify.com/get_access_token
        """
        url = f'https://open.spotify.com/get_access_token'
        headers = {
            'accept': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }

        totp, timestamp = generate_totp()

        query_params = {
            "reason": "init",
            "productType": "web-player",
            "totp": totp,
            "totpVer": 5,
            "ts": timestamp,
        }
        cookies = self.cookies if fetch_with_cookies else None

        response = requests.get(url, params=query_params, headers=headers, cookies=self.cookies)
        response.raise_for_status()
        self.client_id = response.json().get("clientId", "")
        self.access_token = response.json().get("accessToken", "")
        l.debug("fetched access_token and client_id")

    def _make_request(self, endpoint: str, params: dict = None) -> dict:
        """
        Helper method to make authenticated requests to Spotify APIs.
        """
        headers = {
            'accept': 'application/json',
            'app-platform': 'WebPlayer',
            'authorization': f'Bearer {self.access_token}',
            'client-token': self.client_token.get('token','')
        }
        l.debug(f"starting request: {self.base_url}/{endpoint}")
        response = requests.get(f"{self.base_url}/{endpoint}", headers=headers, params=params, cookies=self.cookies)
        # if the response is unauthorized, we need to reauthenticate
        if response.status_code == 401:
            l.debug("reauthenticating")
            self.authenticate()
            headers['authorization'] = f'Bearer {self.access_token}'
            headers['client-token'] = self.client_token.get('token','')
            response = requests.get(f"{self.base_url}/{endpoint}", headers=headers, params=params, cookies=self.cookies)
        
        response.raise_for_status()
        return response.json()
    
    #region utility functions to help parsing objects
    def _parse_external_urls(self, uri: str, entity_type: str) -> List[ExternalUrl]:
        """
        Create ExternalUrl instances for an entity.
        
        :param uri: The URI of the entity.
        :param entity_type: The type of entity ('track', 'album', 'artist', 'playlist', etc.).
        :return: A list of ExternalUrl instances.
        """
        return [ExternalUrl(url=f"https://open.spotify.com/{entity_type}/{uri.split(':')[-1]}")]


    def _parse_images(self, image_data: List[Dict]) -> List[Image]:
        """
        Parse images from the API response.
        
        :param image_data: List of dictionaries containing image data.
        :return: A list of Image objects.
        """
        images = []
        for img in image_data:
            # Extract the first source if available
            sources = img.get("sources", [])
            if sources:
                source = sources[0]  # Take the first source as the default
                images.append(Image(
                    url=source.get("url"),
                    height=source.get("height"),
                    width=source.get("width")
                ))
        return images


    def _parse_artist(self, artist_data: Dict) -> Artist:
        """
        Parse an artist object from API data.
        
        :param artist_data: Dictionary representing an artist.
        :return: An Artist instance.
        """
        return Artist(
            id=artist_data["uri"].split(":")[-1],
            name=artist_data["profile"]["name"],
            uri=artist_data["uri"],
            external_urls=self._parse_external_urls(artist_data["uri"], "artist")
        )


    def _parse_album(self, album_data: Dict) -> Album:
        """
        Parse an album object from API data.
        
        :param album_data: Dictionary representing an album.
        :return: An Album instance.
        """
        artists = []
        if album_data.get("artists"):
            artists = [self._parse_artist(artist) for artist in album_data.get("artists").get('items', [])]
        return Album(
            id=album_data["uri"].split(":")[-1],
            name=album_data["name"],
            uri=album_data["uri"],
            external_urls=self._parse_external_urls(album_data["uri"], "album"),
            artists=artists,
            images=self._parse_images(album_data["coverArt"]["sources"])
        )


    def _parse_track(self, track_data: Dict) -> Track:
        """
        Parse a track object from API data.
        
        :param track_data: Dictionary representing a track.
        :return: A Track instance.
        """
        duration_ms = 0
        aritsts = []
        if track_data.get("duration"):
            duration_ms = int(track_data.get("duration", 0).get("totalMilliseconds", 0))
        elif track_data.get("trackDuration"):
            duration_ms = track_data["trackDuration"]["totalMilliseconds"] 
        
        if track_data.get("firstArtist"):
            for artist in track_data.get("firstArtist").get('items', []):
                aritsts.append(self._parse_artist(artist))
        elif track_data.get("artists"):
            for artist in track_data.get("artists").get('items', []):
                aritsts.append(self._parse_artist(artist))
        
        if track_data.get("albumOfTrack"):
            album = self._parse_album(track_data["albumOfTrack"])
        
            
        return Track(
            id=track_data["uri"].split(":")[-1],
            name=track_data["name"],
            uri=track_data["uri"],
            external_urls=self._parse_external_urls(track_data["uri"], "track"),
            duration_ms=duration_ms,
            explicit=track_data.get("explicit", False),
            album=self._parse_album(track_data["albumOfTrack"]),
            artists=aritsts
        )
    def _parse_owner(self, owner_data: Dict) -> Optional[Owner]:
        """
        Parse an owner object from API data.

        :param owner_data: Dictionary representing an owner.
        :return: An Owner instance or None if the owner data is empty.
        """
        if not owner_data:
            return None

        return Owner(
            id=owner_data.get("uri", "").split(":")[-1],
            name=owner_data.get("name", ""),
            uri=owner_data.get("uri", ""),
            external_urls=self._parse_external_urls(owner_data.get("uri", ""), "user")
        )
    def _parse_card_artwork(self, sources: List[Dict]) -> List[Image]:
        """
        Parse artwork for a browse card.

        :param sources: List of artwork source dictionaries.
        :return: A list of CardArtwork instances.
        """
        return [Image(url=source["url"], height=source.get("height"), width=source.get("width")) for source in sources]


    def _parse_browse_card(self, card_data: Dict) -> BrowseCard:
        """
        Parse a single browse card.

        :param card_data: Dictionary containing card data.
        :return: A BrowseCard instance.
        """
        card_content = card_data["content"]["data"]["data"]["cardRepresentation"]
        artwork_sources = card_content["artwork"]["sources"]

        return BrowseCard(
            title=card_content["title"]["transformedLabel"],
            uri=card_data["uri"],
            background_color=card_content["backgroundColor"]["hex"],
            artwork=self._parse_card_artwork(artwork_sources)
        )

    def _parse_playlist(self, playlist_data: Dict) -> Playlist:
        """
        Parse a playlist object from API response data.

        :param playlist_data: Dictionary containing playlist data.
        :return: A Playlist object.
        """
        images = self._parse_images(playlist_data.get("images", {}).get("items", []))

        owner_data = playlist_data.get("ownerV2", {}).get("data", {})
        owner = self._parse_owner(owner_data)

        
        valid_tracks = []
        for item in playlist_data.get("content", {}).get("items", []):
            data = item.get("itemV2", {}).get("data", {})
            uri = data.get("uri", "")
            if uri.startswith("spotify:track"):
                valid_tracks.append(self._parse_track(data))
        tracks = valid_tracks

        return Playlist(
            id=playlist_data.get("uri", "").split(":")[-1],
            name=playlist_data.get("name", ""),
            uri=playlist_data.get("uri", ""),
            external_urls=self._parse_external_urls(playlist_data.get("uri", "").split(":")[-1], "playlist"),
            description=playlist_data.get("description", ""),
            public=playlist_data.get("public", None),
            collaborative=playlist_data.get("collaborative", None),
            followers=playlist_data.get("followers", 0),
            images=images,
            owner=owner,
            tracks=[
                PlaylistTrack(
                    added_at=item.get("addedAt", {}).get("isoString", ""),
                    added_by=None,
                    is_local=False,
                    track=track
                )
                for item, track in zip(
                    playlist_data.get("content", {}).get("items", []),
                    tracks
                )
            ]
        )
    def _parse_browse_section(self, section_data: Dict) -> BrowseSection:
        """
        Parse a single browse section.

        :param section_data: Dictionary containing section data.
        :return: A BrowseSection instance.
        """
        section_title = section_data["data"]["title"]["transformedLabel"]
        section_items = [
            item for item in section_data["sectionItems"]["items"]
            if not item["uri"].startswith("spotify:xlink")
        ]
        return BrowseSection(
            title=section_title,
            items=[self._parse_browse_card(item) for item in section_items],
            uri=section_data["uri"]
        )
    
    #endregion

    def get_playlist(self, playlist_id: str) -> Playlist:
        """
        Fetch a playlist by ID with all tracks.
        """
        limit = 50
        offset = 0
        all_items = []

        while True:
            query_parameters = {
                "operationName": "fetchPlaylist",
                "variables": json.dumps({
                    "uri": f"spotify:playlist:{playlist_id}",
                    "offset": offset,
                    "limit": limit
                }),
                "extensions": json.dumps({
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "19ff1327c29e99c208c86d7a9d8f1929cfdf3d3202a0ff4253c821f1901aa94d"
                    }
                })
            }
            encoded_query = urlencode(query_parameters)
            data = self._make_request(f"pathfinder/v1/query?{encoded_query}")
            playlist_data = data.get('data', {}).get('playlistV2', {})
            content = playlist_data.get('content', {})
            items = content.get('items', [])
            all_items.extend(items)

            if len(all_items) >= content.get('totalCount', 0):
                break

            offset += limit

        playlist_data["content"]["items"] = all_items
        return self._parse_playlist(playlist_data)
    
    def extract_playlist_id(self, uri: str) -> str:
        """
        Extract the playlist ID from a Spotify URI.
        """
        # check whether the uri is a full url with https or just a uri
        if uri.startswith("https://open.spotify.com/"):
            #if it starts with https, we need to extract the playlist id from the url
            return uri.split('/')[-1]
        elif uri.startswith("spotify:playlist:"):
            return uri.split(':')[-1]
        else :
            raise ValueError("Invalid Spotify URI.")

    def search_playlist(self, query: str, limit: int = 50) -> List[Playlist]:
        """
        Searches for playlists on Spotify.
        :param query: Search query.
        :param limit: Maximum number of results.
        :return: A list of Playlist objects.
        """
        query_parameters = {
            "operationName": "searchDesktop",
            "variables": json.dumps({
            "searchTerm": query,
            "offset": 0,
            "limit": limit,
            "numberOfTopResults": 5,
            "includeAudiobooks": False,
            "includeArtistHasConcertsField": False,
            "includePreReleases": False,
            "includeLocalConcertsField": False
            }),
            "extensions": json.dumps({
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "f1f1c151cd392433ef4d2683a10deb9adeefd660f29692d8539ce450d2dfdb96"
            }
            })
        }
        encoded_query = urlencode(query_parameters)
        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            response = self._make_request(url)
            search_data = response.get("data", {}).get("searchV2", {})
            playlists_data = search_data.get("playlists", {}).get("items", [])

            playlists = [self._parse_playlist(item["data"]) for item in playlists_data]
            return playlists

        except Exception as e:
            print(f"An error occurred while searching for playlists: {e}")
            return []
        

    def get_track(self, track_id: str) -> Track:
        """
        Fetches details for a specific track.
        :param track_id: The ID of the track.
        :return: A Track object.
        """
        query_parameters = {
            "operationName": "getTrack",
            "variables": json.dumps({
                "uri": f"spotify:track:{track_id}"
            }),
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "5c5ec8c973a0ac2d5b38d7064056c45103c5a062ee12b62ce683ab397b5fbe7d"
                }
            })
        }
        encoded_query = urlencode(query_parameters)
        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            response = self._make_request(url)
            track_data = response.get("data", {}).get("trackUnion", {})
            return self._parse_track(track_data)

        except Exception as e:
            print(f"An error occurred while fetching the track: {e}")
            return None

    
    # non generic method implementations: 
    def get_profile(self) -> Optional[Profile]:
        """
        Fetch the profile attributes of the authenticated Spotify user.

        :return: A Profile object containing the user's profile information or None if an error occurs.
        """
        query_parameters = {
            "operationName": "profileAttributes",
            "variables": json.dumps({}),  
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "53bcb064f6cd18c23f752bc324a791194d20df612d8e1239c735144ab0399ced"
                }
            })
        }

        encoded_query = urlencode(query_parameters)

        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            response = self._make_request(url)
            profile_data = response.get('data', {}).get('me', {}).get('profile', {})
            if not profile_data:
                raise ValueError("Invalid profile data received.")
            return Profile(
                avatar=profile_data.get("avatar"),
                avatar_background_color=profile_data.get("avatarBackgroundColor"),
                name=profile_data.get("name", ""),
                uri=profile_data.get("uri", ""),
                username=profile_data.get("username", "")
            )

        except Exception as e:
            print(f"An error occurred while fetching profile attributes: {e}")
            return None
    def get_account_attributes(self) -> Optional[AccountAttributes]:
        """
        Fetch the account attributes of the authenticated Spotify user.

        :return: An AccountAttributes object containing the user's account information or None if an error occurs.
        """
        # Define the query parameters
        query_parameters = {
            "operationName": "accountAttributes",
            "variables": json.dumps({}),  # Empty variables for this query
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "4fbd57be3c6ec2157adcc5b8573ec571f61412de23bbb798d8f6a156b7d34cdf"
                }
            })
        }

        # Encode the query parameters
        encoded_query = urlencode(query_parameters)

        # API endpoint
        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            # Perform the request
            response = self._make_request(url)

            # Extract and validate the account data
            account_data = response.get('data', {}).get('me', {}).get('account', {})
            attributes = account_data.get("attributes", {})
            if not attributes or not account_data.get("country") or not account_data.get("product"):
                raise ValueError("Invalid account data received.")

            # Map the response to the AccountAttributes class
            return AccountAttributes(
                catalogue=attributes.get("catalogue", ""),
                dsa_mode_available=attributes.get("dsaModeAvailable", False),
                dsa_mode_enabled=attributes.get("dsaModeEnabled", False),
                multi_user_plan_current_size=attributes.get("multiUserPlanCurrentSize"),
                multi_user_plan_member_type=attributes.get("multiUserPlanMemberType"),
                on_demand=attributes.get("onDemand", False),
                opt_in_trial_premium_only_market=attributes.get("optInTrialPremiumOnlyMarket", False),
                country=account_data.get("country", ""),
                product=account_data.get("product", "")
            )

        except Exception as e:
            print(f"An error occurred while fetching account attributes: {e}")
            return None
    def browse(self, **kwargs) -> List[BrowseSection]:
        """
        Fetch all browse sections with cards.

        :param kwargs: Keyword arguments. Supported:
            - page_limit: Maximum number of pages to fetch (default: 50)
            - section_limit: Maximum number of sections per page (default: 99)
        :return: A list of BrowseSection objects.
        """
        page_limit = kwargs.get('page_limit', 50)
        section_limit = kwargs.get('section_limit', 99)
        query_parameters = {
            "operationName": "browseAll",
            "variables": json.dumps({
                "pagePagination": {"offset": 0, "limit": page_limit},
                "sectionPagination": {"offset": 0, "limit": section_limit}
            }),
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "cd6fcd0ce9d1849477645646601a6d444597013355467e24066dad2c1dc9b740"
                }
            })
        }
        encoded_query = urlencode(query_parameters)
        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            response = self._make_request(url)
            browse_data = response.get("data", {}).get("browseStart", {}).get("sections", {})
            sections = browse_data.get("items", [])

            return [self._parse_browse_section(section) for section in sections]

        except Exception as e:
            print(f"An error occurred while fetching browse sections: {e}")
            return []
        
    def browse_page(self, uri: str) -> List[Playlist]:
        """
        Fetch the content of a browse page using the URI.

        :param uri: Should start with 'spotify:page'.
        :return: A list of Playlist objects from the browse page.
        """
        
        if not uri or not uri.startswith("spotify:page"):
            raise ValueError("The 'uri' parameter must be provided and start with 'spotify:page'.")

        query_parameters = {
            "operationName": "browsePage",
            "variables": json.dumps({
                "pagePagination": {"offset": 0, "limit": 10},
                "sectionPagination": {"offset": 0, "limit": 10},
                "uri": uri
            }),
            "extensions": json.dumps({
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "d8346883162a16a62a5b69e73e70c66a68c27b14265091cd9e1517f48334bbb3"
                }
            })
        }
        encoded_query = urlencode(query_parameters)
        url = f"pathfinder/v1/query?{encoded_query}"

        try:
            response = self._make_request(url)
            browse_data = response.get("data", {}).get("browse", {})
            sections = browse_data.get("sections", {}).get("items", [])

            playlists = []
            for section in sections:
                section_items = section.get("sectionItems", {}).get("items", [])
                for item in section_items:
                    content = item.get("content", {}).get("data", {})
                    if content.get("__typename") == "Playlist":
                        playlists.append(self._parse_playlist(content))

            return playlists

        except Exception as e:
            print(f"An error occurred while fetching the browse page: {e}")
            return []
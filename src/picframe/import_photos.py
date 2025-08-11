"""
ImportPhotos

Handles importing photos from third-party services to the local filesystem.
Integrates with configured import sources (e.g. Nixplay) and maintains imported_playlists database table.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import ntplib
import pytz
import requests
import urllib3
from requests.exceptions import HTTPError

from picframe.schema import create_schema
from picframe.process_images import ProcessImages

def extract_filename_and_ext(url_or_path):
    """
    Extracts the base filename and extension from a URL or local file path.

        Returns:
        tuple: (base, ext)
            base (str): filename without extension
            ext (str): extension without dot, lowercase
    """
    if not url_or_path:
        return None, None
    
    # Remove query parameters if URL
    filename = url_or_path.split('/')[-1].split('?')[0]
    base, ext = os.path.splitext(filename)
    ext = ext.lstrip('.').lower()
    return base, ext

def unix_to_utc_string(timestamp):
    """
    Converts a UNIX timestamp (int/str) or ISO 8601 string to UTC ISO format,
    auto-detecting millisecond/microsecond inputs.
    """
    if isinstance(timestamp, str):
        try:
            # Try ISO 8601 parsing
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.isoformat()
        except ValueError:
            timestamp = int(timestamp)

    elif isinstance(timestamp, (float, int)):
        timestamp = int(timestamp)
    else:
        raise ValueError(f"Unsupported timestamp type: {timestamp}")

    # Adjust if timestamp is in ms or us
    if timestamp > 1e14:
        timestamp = timestamp / 1e6
    elif timestamp > 1e11:
        timestamp = timestamp / 1e3

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()

class LoginError(Exception):
    pass

class GetPlaylistsError(Exception):
    pass

class FolderCreationError(Exception):
    pass

class GetMediaError(Exception):
    pass

class ImportPhotos:
    """Class to import photos from third-party services to local filesystem."""
    def __init__(self, model):
        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
        self.__logger = logging.getLogger(__name__)
        self.__model = model
        self.__sources = self.__model.get_aspect_config()["sources"]
        if not self.__sources:
            raise Exception("No import sources configured! Aborting creation of ImportPhotos instance.")   
        model_config = self.__model.get_model_config()
        self.__db_file = os.path.expanduser(model_config['db_file'])
        self.__import_dir = self.__model.get_aspect_config()["import_dir"]
        self._importing = False
        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=5.0)
        # Use WAL mode for better concurrency, DELETE for compatibility with DB Browser for SQLite
        self.__db.execute("PRAGMA journal_mode=DELETE")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        create_schema(self.__db)

    async def check_for_updates(self) -> None:
        self._importing = True
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._check_for_updates_blocking)
                # === Process images after importing ===
            processor = ProcessImages(self.__model)
            await processor.process_images()  
        finally:
            self._importing = False

    def get_source_playlists(self, source) -> list:
        """Retrieves playlist names that match identifier and last_updated_date from external sources."""      
        login_url = self.__sources[source]['login_url']
        acct_id  = self.__sources[source]['acct_id']
        acct_pwd  = self.__sources[source]['acct_pwd']
        playlist_url = self.__sources[source]['playlist_url']
        identifier = self.__sources[source]['identifier']

        if source == 'nixplay':                                             # Designing for multiple sources, but currently only Nixplay is implemented
            try:
                session = self.create_nixplay_authorized_client(acct_id, acct_pwd, login_url)
                if session.cookies.get("prod.session.id") is None:
                    raise LoginError("Bad Credentials")
            except LoginError as e:
                self.__logger.info(f"Login failed: {e}")
                self.__logger.info("Exiting")
                sys.exit()
            except Exception as e:
                self.__logger.info(f"An error occurred: {e}")
            self.__logger.info("logged in")
            playlists = []
            try:
                playlists = self.get_playlist_names(session, source, playlist_url, identifier)
            except GetPlaylistsError as e:
                self.__logger.info(f"Playlist Request failed: {e}")
            except Exception as e:
                self.__logger.info(f"An error occurred: {e}")
            self.__logger.info("got playlists")
            return playlists

    def update_imported_playlists_db(self, source, playlists):
        """Update the DB to match current playlists for a source."""
        try:
            with self.__db:  # auto-commit
                # Get existing playlists from DB for this source
                cur = self.__db.execute("SELECT playlist_id FROM imported_playlists WHERE source = ?", (source,))
                existing_ids = set(row[0] for row in cur.fetchall())

                current_ids = set()
                for plist in playlists:
                    pid = plist["id"]
                    pname = plist["playlist_name"]
                    picture_count = plist["picture_count"]
                    last_modified = plist["last_modified"]
                    last_imported = 0  # 0 will force all media to be checked
                    current_ids.add(pid)
                    self.__logger.info(f"playlist: {pname}")
                    # Insert or replace if updated
                    self.__db.execute("""
                        INSERT INTO imported_playlists (source, playlist_name, playlist_id, picture_count, last_modified, last_imported)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, playlist_id) DO UPDATE SET
                            playlist_name = excluded.playlist_name,
                            last_modified = excluded.last_modified
                    """, (source, pname, pid, picture_count, unix_to_utc_string(last_modified), last_imported))

                # Delete any playlists no longer present in source
                stale_ids = existing_ids - current_ids
                for pid in stale_ids:
                    self.__db.execute("DELETE FROM imported_playlists WHERE source = ? AND playlist_id = ?", (source, pid))
        except Exception as e:
            self.__logger.warning(f"Error updating imported playlists: {e}")

    def _check_for_updates_blocking(self):
        if not any(self.__sources[source]['enable'] for source in self.__sources):
            self.__logger.info("No enabled import sources")
            return
        
        for source in self.__sources:                                       # Designing for multiple sources, but currently only Nixplay is implemented
            if not self.__sources[source]['enable']:
                continue

            playlists = self.get_source_playlists(source)
            if playlists:
                self.update_imported_playlists_db(source, playlists)
                try:
                    with self.__db:  # auto-commit
                        cur = self.__db.execute("""
                            SELECT playlist_id, playlist_name FROM imported_playlists
                            WHERE source = ? AND last_imported = 0
                        """, (source,))
                        to_import = cur.fetchall()
                except Exception as e:
                    self.__logger.warning(f"Error querying unimported playlists: {e}")
                    to_import = []

                if not to_import:
                    self.__logger.info(f"No unimported playlists for source {source}")
                    continue
                
                session = self.create_nixplay_authorized_client(            # Reuse one session per source if allowed
                    self.__sources[source]['acct_id'],
                    self.__sources[source]['acct_pwd'],
                    self.__sources[source]['login_url']
                )

                for playlist_id, playlist_name in to_import:
                    self.__logger.info(f"Importing media for unimported playlist: {playlist_name}")

                    item_path = "slides"                                    # Adjust as needed

                    media_items = self.get_nixplay_media(
                        session,
                        self.__sources[source]['playlist_url'],
                        item_path,
                        [(playlist_id, playlist_name, self.__import_dir)]
                    )

                    self.save_downloaded_media(source, playlist_id, media_items)
                    
                    try:
                        with self.__db:  # auto-commit
                            self.__db.execute("""
                                UPDATE imported_playlists SET last_imported = ?
                                WHERE source = ? AND playlist_id = ?
                            """, (unix_to_utc_string(int(time.time())), source, playlist_id))
                    except Exception as e:
                        self.__logger.warning(f"Error updating last_imported timestamp: {e}")

    def create_nixplay_authorized_client(self, acct_id: str, acct_pwd: str, login_url: str):
        """Submits login form and returns valid session for Nixplay."""    
        data = {
            'email': acct_id,
            'password': acct_pwd
        }
        with requests.Session() as session:
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = session.post(login_url, headers=headers, data=data)
        return session

    def get_playlist_names(self, session, source, playlist_url, identifier):
        """Retrieves playlist names that match identifier and last_updated_date from nixplay cloud."""
        json = session.get(playlist_url).json()
        playlists = []
        for plist in json:
            if re.search(identifier + "$", plist["playlist_name"]):
                data = {
                    "source": source,
                    "id": plist["id"],
                    "playlist_name": plist["playlist_name"],
                    "last_modified": plist["last_updated_date"],
                    "picture_count": plist["picture_count"]
                }
                self.__logger.info(f"{plist['playlist_name']}, {plist['id']}")
                playlists.append(data)
        return playlists

    def get_nixplay_media(self, session, playlist_url, item_path, playlists_to_update):
        """Retrieves individual media item metadata from nixplay cloud/"""
        media_items = []
        for item_id in playlists_to_update:
            url = playlist_url + '/' + str(item_id[0]) + '/' + item_path
            response = session.get(url).json()
            self.__logger.debug(f"get_nixplay_media url: {url}")
            # filename = f"/home/pi/nixplay_playlist_{str(item_id[0])}.json"
            # with open(filename, "w") as f:
            #     json.dump(response, f, indent=2)

        for slide in response.get(item_path, []):
            data = {
                "mediaItemId": slide.get("mediaItemId"),
                "caption": slide.get("caption", ""),
                "mediaType": slide.get("mediaType"),
                "originalUrl": slide.get("originalUrl"),
                "timestamp": slide.get("timestamp"),
                "filename": slide.get("filename", None)  # returns None if 'filename' not present
            }
            media_items.append(data)
        return media_items


# for each playlist (source_playlistname)
# check if in DB
# check modified time
# check each file modified time
# update as needed
# delete removed playlists & files
# item_path = "slides"                                            # NIXPLAY   url is: playlist_url + list_id + '/' + item_path

    def get_timer_task(self):
        return self.check_for_updates

    def get_ntp_time(self):
        """Gets the current time from an NTP server."""
        try:
            client = ntplib.NTPClient()
            response = client.request('pool.ntp.org', version=3)
            return datetime.fromtimestamp(response.tx_time, tz=timezone.utc)
        except Exception as e:
            self.__logger.error(f"Error getting NTP time: {e}")
            return None
        
    def get_local_time(self):
        """Gets the current local time."""
        return datetime.now(tz=timezone.utc)  

    def wait_for_directory(self, path, timeout=10):
        """Waits for a directory to be created, timeout: The maximum time to wait in seconds (default: 30)."""
        start_time = time.time()
        while not os.path.exists(path):
            time.sleep(1)
            if time.time() - start_time > timeout:
                return False
        return True

    def create_valid_folder_name(self, string) -> str:
        """Converts a string to a valid folder name."""
        string = re.sub(r'[\\/:*?"<>|]', '_', string)                       # Replace invalid characters with underscores
        string = string.strip()                                             # Remove leading/trailing whitespace
        return string

    def compare_modified_times(self, subdirectory, date):
        """Checks if nixplay playlist modified is > local directory (always use UTC)"""
        local_mtime = os.path.getmtime(subdirectory)
        local_mtime = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
        nix_mtime = datetime.fromisoformat(date)
        nix_mtime = nix_mtime.replace(tzinfo=pytz.utc)
        diff = local_mtime - nix_mtime                                      # if diff is negative, nixcloud playlist has changed - we must check local contents for adds/changes/deletes
        return diff


    def save_downloaded_media(self, source, playlist_id, media_items):
        """
        Downloads media items and inserts their metadata into imported_files table.
        
        Args:
            source (str): The source name (e.g. 'nixplay').
            playlist_id (str): The ID of the playlist these media items belong to.
            media_items (list): List of dicts with keys including 'mediaItemId', 'originalUrl'.
        """
        self.__logger.info(f"Storing {len(media_items)} media items for playlist {playlist_id}")
        
        import_dir_path = Path(os.path.expanduser(self.__import_dir))
        import_dir_path.mkdir(parents=True, exist_ok=True)

        try:
            with self.__db:  # auto-commit
                for item in media_items:
                    media_id = item.get("mediaItemId")
                    url = item.get("originalUrl")
                    nix_caption = item.get("caption")
                    timestamp = item.get("timestamp")
                    orig_filename = item.get("filename", None)

                    if not url:
                        self.__logger.warning(f"No URL for mediaItemId {media_id}, skipping.")
                        continue

                    basename, extension = extract_filename_and_ext(orig_filename or url)

                    basename = f"{source}_{playlist_id}_{basename}"
                    full_name = f"{basename}.{extension}"
                    local_path = import_dir_path / full_name

                    # Download file
                    try:
                        response = requests.get(url, stream=True, timeout=30)
                        response.raise_for_status()
                        with open(local_path, 'wb') as f:
                            shutil.copyfileobj(response.raw, f)
                        self.__logger.info(f"Downloaded {full_name}")
                    except Exception as e:
                        self.__logger.error(f"Failed to download {url}: {e}")
                        continue

                    # Insert into database
                    self.__logger.info(f"Inserting into database: source: {source}, playlist_id: {playlist_id}, media_item_id: {media_id}, original_url: {url}, basename: {basename}, extension: {extension}, nix_caption: {nix_caption}, orig_extension: {extension}, processed: 0, orig_timestamp: {timestamp}, last_modified: {unix_to_utc_string(int(os.path.getmtime(local_path)))}")
                    self.__db.execute("""
                        INSERT INTO imported_files (source, playlist_id, media_item_id, original_url, basename, extension,nix_caption, orig_extension, processed, orig_timestamp, last_modified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        source,
                        playlist_id,
                        media_id,
                        url,
                        basename,
                        extension,  # may be changed to .jpg in later processing
                        nix_caption,
                        extension,  # original extension same as stored
                        0,
                        unix_to_utc_string(timestamp),
                        unix_to_utc_string(int(os.path.getmtime(local_path)))
                    ))
        except Exception as e:
            self.__logger.warning(f"Error saving downloaded media: {e}")

# CHECK OR CREATE SUBDIRECTORIES
#     playlists_to_update = []
#     flag = 0
#     for playlist in playlists:
#         folder_name = create_valid_folder_name(playlist["playlist_name"])   # Normalize name just in case (edge case of overwrites)
#         subdirectory = os.path.expanduser(local_pictures_path + folder_name + "/")

#         if os.path.isdir(subdirectory):
#             diff = compare_modified_times(subdirectory, playlist["last_updated_date"])
#             if diff < timedelta(0):
#                 playlists_to_update.append((playlist["id"], playlist["playlist_name"], subdirectory))
#                 flag = 1
#         else:
#             try:                                                            # Create new directory
#                 os.makedirs(subdirectory, mode=0o700, exist_ok=False)
#                 if wait_for_directory(subdirectory, timeout=10):
#                     playlists_to_update.append((playlist["id"], playlist["playlist_name"], subdirectory))
#                 else:
#                     raise Exception("Creating new playlist directory timed out")
#             except FolderCreationError as e:
#                 print(f"Folder creation failed: {e}")
#             except Exception as e:
#                 print(f"An error occurred: {e}")
#             flag = 1
#             print("created new directories")
#     if flag == 0:
#         print("Nothing to update")    

# #   GET MEDIA TO ADD / CHANGE / DELETE
#     media_items = []
#     media_to_add = []
#     media_to_delete = []

#     try:
#         media_items = self.get_nixplay_media(session, playlist_url, item_path, playlists_to_update)
#     except GetMediaError as e:
#         print(f"Error getting media item names: {e}")
#     except Exception as e:
#         print(f"An error occurred: {e}")

#     print(media_items)

# NOTES:
#   when copying media, item should be named with the original filename, followed by a separator token and the unique nix mediaItemId
#   ** in playlist / slides some photos do not have a filename key!
#   individual media items can be modified on nixplay - rotated, captioned, favorited, ?
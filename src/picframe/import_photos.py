import os, sys, time, logging, warnings, json, re, pytz, ntplib, urllib3, requests, sqlite3, threading, asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from requests.exceptions import HTTPError   

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
        self.__picture_dir = model_config['pic_dir']
        self.__db_file = model_config['db_file']
        self.__import_dir = self.__model.get_aspect_config()["import_dir"]
        self.__session = None
        self.__modified_folders = []
        self.__modified_files = []
        self.__cached_file_stats = []                                       # collection shared between threads
        self.__source_playlists = []
        self._importing = False
        self.__db = self.__create_open_db(self.__db_file)
        self.__db_write_lock = threading.Lock()                             # lock to serialize db writes between threads
 
    async def check_for_updates(self):
        self._importing = True
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._check_for_updates_blocking)
        finally:
            self._importing = False

    def get_source_playlists(self, source):
        """Retrieves playlist names that match identifier and last_updated_date from external sources."""      
        source_prefix = f"{source}_"
        login_url = self.__sources[source]['login_url']
        acct_id  = self.__sources[source]['acct_id']
        acct_pwd  = self.__sources[source]['acct_pwd']
        playlist_url = self.__sources[source]['playlist_url']
        identifier = self.__sources[source]['identifier']

        if source == 'nixplay':                                 # Designing for multiple sources, but currently only Nixplay is implemented
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
        with self.__db_write_lock:
            cur = self.__db.cursor()
            
            # Get existing playlists from DB for this source
            cur.execute("SELECT playlist_id FROM imported_playlists WHERE source = ?", (source,))
            existing_ids = set(row[0] for row in cur.fetchall())

            current_ids = set()
            for plist in playlists:
                pid = plist["id"]
                pname = plist["playlist_name"]
                updated = plist["last_updated_date"]
                current_ids.add(pid)

                # Insert or replace if updated
                cur.execute("""
                    INSERT INTO imported_playlists (source, playlist_id, playlist_name, last_updated_date)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source, playlist_id) DO UPDATE SET
                        playlist_name = excluded.playlist_name,
                        last_updated_date = excluded.last_updated_date
                """, (source, pid, pname, updated))

            # Delete any playlists no longer present in source
            stale_ids = existing_ids - current_ids
            for pid in stale_ids:
                cur.execute("DELETE FROM imported_playlists WHERE source = ? AND playlist_id = ?", (source, pid))

            self.__db.commit()


    def _check_for_updates_blocking(self):
        if not any(self.__sources[source]['enable'] for source in self.__sources):
            self.__logger.info("No enabled import sources")
            return
        
        for source in self.__sources:                                       # Designing for multiple sources, but currently only Nixplay is implemented
            if self.__sources[source]['enable']:
                playlists = self.get_source_playlists(source)
                if playlists:
                    self.update_imported_playlists_db(source, playlists)
      

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
        """Retrieves playlist names that match key and last_updated_date from nixplay cloud."""
        json = session.get(playlist_url).json()
        playlists = []
        for plist in json:
            if re.search(identifier + "$", plist["playlist_name"]):
                data = {
                    "source": source,
                    "id": plist["id"],
                    "playlist_name": plist["playlist_name"],
                    "last_updated_date": plist["last_updated_date"]
                }
                playlists.append(data)
        return playlists

    def get_nixplay_media(self, session, playlist_url, item_path, playlists_to_update):
        """Retrieves individual media item metadata from nixplay cloud/"""
        media_items = []
        for item_id in playlists_to_update:
            url = playlist_url + str(item_id[0]) + '/' + item_path
            json = session.get(url).json()
            for slide in json[item_path]:
                data = {
                        "mediaItemId": slide["mediaItemId"],
                        "originalUrl": slide["originalUrl"]
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

    def __loop(self):
        pass

    def get_timer_task(self):
        return self.check_for_updates

    def __create_open_db(self, db_file):
        db_file = os.path.expanduser(db_file)
        db = sqlite3.connect(db_file, check_same_thread=False)
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS imported_playlists (
                    source TEXT NOT NULL,
                    playlist_id TEXT NOT NULL,
                    playlist_name TEXT NOT NULL,
                    last_updated_date TEXT NOT NULL,
                    PRIMARY KEY (source, playlist_id)
                )
            """)
        return db

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

    def create_valid_folder_name(self, string):
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
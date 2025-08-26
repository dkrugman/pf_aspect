"""
ImportPhotos

Handles importing photos from third-party services to the local filesystem.
Integrates with configured import sources (e.g. Nixplay) and maintains imported_playlists database table.
"""

import os, sys, re, time, warnings, sqlite3, asyncio, json, logging, shutil, requests, urllib3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from requests.exceptions import HTTPError
from picframe.process_images import ProcessImages
from picframe.file_utils import extract_filename_and_ext, unix_to_utc_string, wait_for_directory, create_valid_folder_name

class LoginError(Exception):
    pass

class GetPlaylistsError(Exception):
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
        self.local_pictures_path = self.__model.get_aspect_config()["import_dir"]
        
        # Ensure import directory exists
        import_path = Path(os.path.expanduser(self.__import_dir))
        import_path.mkdir(parents=True, exist_ok=True)
        self.__logger.debug(f"Import directory ensured: {import_path}")
        
        self.to_import = []
        self._importing = False
        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=5.0)
        # Use WAL mode for better concurrency, DELETE for compatibility with DB Browser for SQLite
        self.__db.execute("PRAGMA journal_mode=DELETE")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__logger.info(f"DB connection established")

    def get_timer_task(self):
        return self.check_for_updates

    def cleanup(self):
        """Clean up database connections and resources."""
        try:
            if hasattr(self, '__db') and self.__db:
                self.__db.close()
                self.__logger.info("ImportPhotos database connection closed")
        except Exception as e:
            self.__logger.warning(f"Error closing ImportPhotos database: {e}")

    async def check_for_updates(self) -> None:
        # Quick check if already importing
        if self._importing:
            self.__logger.info("Import already in progress, skipping this timer cycle")
            return
            
        self._importing = True
        try:
            start_time = time.time()
            self.__logger.info("Starting import check...")
            
            # Get media items to download (this part runs in thread pool)
            loop = asyncio.get_running_loop()
            media_items_by_source = await loop.run_in_executor(None, self._prepare_media_for_download)
            
            prep_time = time.time() - start_time
            self.__logger.info(f"Preparation took {prep_time:.2f} seconds")
            
            # Start downloads as background tasks (don't await - let them run in background)
            download_tasks = []
            for source, media_items in media_items_by_source.items():
                if media_items:
                    task = asyncio.create_task(self._download_and_update_async(source, media_items))
                    download_tasks.append(task)
                    self.__logger.info(f"Started background download for {len(media_items)} items from {source}")
            
            # Store tasks for potential cleanup/monitoring
            if download_tasks:
                self._active_download_tasks = getattr(self, '_active_download_tasks', [])
                self._active_download_tasks.extend(download_tasks)
                # Clean up completed tasks
                self._active_download_tasks = [t for t in self._active_download_tasks if not t.done()]
                self.__logger.info(f"Total active download tasks: {len(self._active_download_tasks)}")
            
            total_time = time.time() - start_time
            self.__logger.info(f"Import check completed in {total_time:.2f} seconds, downloads running in background")
            
            # === Process images as background task too ===
            if download_tasks:
                # Only process images if we have downloads
                process_task = asyncio.create_task(self._process_images_async())
                self.__logger.info("Started background image processing task")  
        finally:
            self._importing = False

    def _prepare_media_for_download(self):
        """Prepare media items for download. Returns dict of {source: media_items}"""
        start_time = time.time()
        
        if not any(self.__sources[source]['enable'] for source in self.__sources):
            self.__logger.info("No enabled import sources")
            return {}
        
        media_items_by_source = {}
        self.playlists_to_update = {}  # Store for timestamp updates later
        
        for source in self.__sources:                                       
            if not self.__sources[source]['enable']:
                continue

            source_start = time.time()
            self.__logger.info(f"Processing source: {source}")
            
            step_start = time.time()
            playlists = self.get_source_playlists(source)
            self.__logger.info(f"get_source_playlists took {time.time() - step_start:.2f}s")
            
            item_path = "slides"                                            # TODO: use config for item_path
            if playlists:
                step_start = time.time()
                self.update_imported_playlists_db(source, playlists)        # Update existing playlists in DB, delete any that are no longer present
                self.__logger.info(f"update_imported_playlists_db took {time.time() - step_start:.2f}s")
                
                # Get only new playlists from self.to_import
                if not self.to_import:
                    self.__logger.info(f"No playlists to import or update for source {source}")
                    continue
                
                # Store playlists for later timestamp update
                self.playlists_to_update[source] = self.to_import.copy()
                
                step_start = time.time()
                session = self.create_nixplay_authorized_client(            # Reuse one session per source if allowed
                    self.__sources[source]['acct_id'],
                    self.__sources[source]['acct_pwd'],
                    self.__sources[source]['login_url']
                )
                self.__logger.info(f"create_nixplay_authorized_client took {time.time() - step_start:.2f}s")
                
                step_start = time.time()
                media_items = self.get_playlist_media(session)
                self.__logger.info(f"get_playlist_media took {time.time() - step_start:.2f}s")
                
                media_items_by_source[source] = media_items
                
            source_time = time.time() - source_start
            self.__logger.info(f"Source {source} processing took {source_time:.2f}s total")
                
        total_time = time.time() - start_time
        self.__logger.info(f"_prepare_media_for_download completed in {total_time:.2f}s")
        return media_items_by_source

    def _update_import_timestamps(self):
        """Update last_imported timestamps for processed playlists"""
        if not hasattr(self, 'playlists_to_update'):
            return
            
        for source, playlists in self.playlists_to_update.items():
            try:
                with self.__db:  # auto-commit
                    for playlist_id, playlist_name, status in playlists:
                        self.__db.execute("""
                            UPDATE imported_playlists SET last_imported = ?
                            WHERE source = ? AND playlist_id = ?
                        """, (unix_to_utc_string(int(time.time())), source, playlist_id))
                        self.__logger.info(f"Updated last_imported timestamp for playlist {playlist_name}")
            except Exception as e:
                self.__logger.warning(f"Error updating last_imported timestamp: {e}")

    async def _download_and_update_async(self, source, media_items):
        """Download media items and update timestamps for a single source"""
        try:
            # Download files
            await self.save_downloaded_media(source, media_items)
            
            # Update timestamps for this source
            if hasattr(self, 'playlists_to_update') and source in self.playlists_to_update:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._update_single_source_timestamps, source)
                
            self.__logger.info(f"Completed download and update for source {source}")
        except Exception as e:
            self.__logger.error(f"Error in background download for source {source}: {e}")

    def _update_single_source_timestamps(self, source):
        """Update last_imported timestamps for a single source"""
        if not hasattr(self, 'playlists_to_update') or source not in self.playlists_to_update:
            return
            
        playlists = self.playlists_to_update[source]
        try:
            with self.__db:  # auto-commit
                for playlist_id, playlist_name, status in playlists:
                    self.__db.execute("""
                        UPDATE imported_playlists SET last_imported = ?
                        WHERE source = ? AND playlist_id = ?
                    """, (unix_to_utc_string(int(time.time())), source, playlist_id))
                    self.__logger.info(f"Updated last_imported timestamp for playlist {playlist_name}")
        except Exception as e:
            self.__logger.warning(f"Error updating last_imported timestamp for source {source}: {e}")

    async def _process_images_async(self):
        """Process images as a background task"""
        try:
            processor = ProcessImages(self.__model)
            await processor.process_images()
            self.__logger.info("Background image processing completed")
        except Exception as e:
            self.__logger.error(f"Error in background image processing: {e}")

    def get_source_playlists(self, source) -> list:
        """Retrieves playlist names external source."""      
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
                raise GetPlaylistsError(f"Playlist Request failed: {e}")
                return []
            except Exception as e:
                self.__logger.info(f"An error occurred: {e}")
                raise GetPlaylistsError(f"An error occurred: {e}")
                return []
            self.__logger.info("got playlists")
            return playlists

    def get_playlist_names(self, session, source, playlist_url, identifier):
        """Retrieves playlist names that match identifier and last_updated_date from nixplay cloud."""
        self.__logger.info(f"Getting playlist names from {playlist_url}")
        json = session.get(playlist_url).json()
        playlists = []
        for plist in json:
            #self.__logger.info(f"playlist: {plist}")
            if re.search(identifier + "$", plist["playlist_name"]):
                data = {
                    "id": plist["id"],
                    "playlist_name": plist["playlist_name"],
                    "last_updated_date": plist["last_updated_date"],
                    "picture_count": plist["picture_count"]
                }
                self.__logger.info(f"{plist['playlist_name']}, {plist['id']}")
                playlists.append(data)
        return playlists

    def update_imported_playlists_db(self, source, playlists):
        """Update the DB to match current playlists for a source."""
        try:
            with self.__db:  # auto-commit
                # Get existing playlists from DB for this source
                cur = self.__db.execute("SELECT playlist_id, playlist_name FROM imported_playlists WHERE source = ?", (source,))
                existing_playlists = {row[0]: row[1] for row in cur.fetchall()}
                existing_ids = set(existing_playlists.keys())

                # Initialize self.to_import as a set
                self.to_import = set()
                current_ids = set()
                
                for plist in playlists:
                    #self.__logger.info(f"playlist: {plist}")
                    playlist_id = plist["id"]
                    playlist_name = plist["playlist_name"]
                    picture_count = plist["picture_count"]
                    last_modified = plist["last_updated_date"] # This is confusing - it's the last time nixplay updated the playlist, not the last time we changed it
                    last_imported = 0  # 0 will force all media to be checked
                    current_ids.add(playlist_id)
                    self.__logger.info(f"playlist: {playlist_name}")
                    
                    # Add to self.to_import - 'update' if exists in DB, 'new' if not
                    if playlist_id in existing_ids:
                        self.to_import.add((playlist_id, playlist_name, 'update'))
                    else:
                        self.to_import.add((playlist_id, playlist_name, 'new'))
                    
                    # Insert or replace if updated
                    self.__db.execute("""
                        INSERT INTO imported_playlists (source, playlist_name, playlist_id, picture_count, last_modified, last_imported)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, playlist_id) DO UPDATE SET
                            playlist_name = excluded.playlist_name,
                            last_modified = excluded.last_modified
                    """, (source, playlist_name, int(playlist_id), picture_count, unix_to_utc_string(last_modified), last_imported))

                # Delete any playlists no longer present in source
                stale_ids = existing_ids - current_ids
                for playlist_id in stale_ids:
                    stale_playlist_name = existing_playlists.get(playlist_id, "Unknown")
                    self.__db.execute("DELETE FROM imported_playlists WHERE source = ? AND playlist_id = ?", (source, int(playlist_id)))
                    self.__logger.info(f"Deleted playlist {playlist_id} ({stale_playlist_name}) from imported_playlists")
                    cursor = self.__db.execute("DELETE FROM files WHERE source = ? AND playlist = ?", (source, playlist_id))
                    deleted_files_count = cursor.rowcount
                    self.__logger.info(f"Deleted {deleted_files_count} files for playlist {playlist_id}")
        except Exception as e:
            self.__logger.warning(f"Error updating imported playlists: {e}")

    def get_playlist_media(self, session):
        """Retrieves individual media item metadata from nixplay cloud for multiple playlists"""
        media_items = []
        
        for playlist_id, playlist_name, status in self.to_import:
            slides = self.get_single_playlist_media(session, playlist_id, playlist_name, status)
            media_items.extend(slides)
        return media_items

    def get_single_playlist_media(self, session, playlist_id, playlist_name, status):
        """Retrieves individual media item metadata from nixplay cloud for a single playlist"""
        # Get playlist URL and item path from source configuration
        source = "nixplay"  # TODO: make this configurable
        playlist_url = self.__sources[source]['playlist_url']
        item_path = "slides"  # TODO: make this configurable
        
        url = playlist_url + '/' + str(playlist_id) + '/' + item_path + '/'
                
        try:
            json = session.get(url).json()
            nix_lastVersion = json.get("slideshowItemsVersion")
            if status == 'new':
                src_version = -1
            elif status == 'update':
                try:
                    cur = self.__db.execute("SELECT src_version FROM imported_playlists WHERE source = ? AND playlist_id = ?", 
                                (source, int(playlist_id)))
                    result = cur.fetchone()
                    if result:
                        src_version = result[0]
                    if src_version == None:
                        self.__logger.info(f"src_version is None, updating to {nix_lastVersion}")
                        try:
                            cur = self.__db.execute("UPDATE imported_playlists SET src_version = ? WHERE source = ? AND playlist_id = ?", 
                                (nix_lastVersion, source, int(playlist_id)))
                            result = cur.fetchone()
                        except Exception as e:
                            self.__logger.warning(f"Error updating missing src_version: {e}")

                except Exception as e:
                    self.__logger.warning(f"Error querying database for src_version: {e}")
                    src_version = -1 
            
            if src_version == nix_lastVersion:   
                self.__logger.info(f"Playlist {playlist_name} is up to date")
                return []

            # Get existing media_item_id values from database for update operations
            existing_media_ids = set()
            if src_version != -1:
                try:
                    cur = self.__db.execute("SELECT media_item_id FROM imported_files WHERE source = ? AND playlist_id = ?", 
                                (source, int(playlist_id)))
                    existing_media_ids = set(row[0] for row in cur.fetchall() if row[0])
                    self.__logger.info(f"existing_media_ids: {existing_media_ids}")
                    self.__logger.info(f"Found {len(existing_media_ids)} existing media items in database for playlist {playlist_name}")
                except Exception as e:
                    self.__logger.warning(f"Error querying database for existing media: {e}")
                    existing_media_ids = set()  # fallback to empty set
            
            slides = []
            total_slides = 0
            skipped_slides = 0
            
            # Handle different JSON response structures
            media_list = json if isinstance(json, list) else json.get(item_path, [])
            
            for slide in media_list:
                if isinstance(slide, dict) and "mediaItemId" in slide:
                    total_slides += 1
                    media_id = slide["mediaItemId"]
                    
                    # Only add if not already in database
                    if src_version == -1 or media_id not in existing_media_ids:
                        data = {
                            "mediaItemId": media_id,
                            "mediaType": slide.get("mediaType", ""),
                            "originalUrl": slide.get("originalUrl", ""),
                            "caption": slide.get("caption", ""),
                            "timestamp": slide.get("timestamp", ""),
                            "filename": slide.get("filename", ""),
                            "playlist_id": playlist_id,
                            "playlist_name": playlist_name
                        }
                        slides.append(data)
                    else:
                        skipped_slides += 1
            
            self.__logger.info(f"Playlist {playlist_name}: {total_slides} total, {len(slides)} new, {skipped_slides} already exist")
            return slides
                
        except Exception as e:
            self.__logger.error(f"Error fetching media for playlist {playlist_name}: {e}")
            return []

    async def save_downloaded_media(self, source, media_items):
        """
        Downloads media items asynchronously and inserts their metadata into imported_files table.
        
        Args:
            source (str): The source name (e.g. 'nixplay').
            media_items (list): List of dicts with keys including 'mediaItemId', 'originalUrl'.
        """
        self.__logger.info(f"Storing {len(media_items)} media items.")
        
        import_dir_path = Path(os.path.expanduser(self.__import_dir))
        import_dir_path.mkdir(parents=True, exist_ok=True)

        # Create download tasks for all media items
        download_tasks = []
        for item in media_items:
            if item.get("originalUrl"):
                task = self._download_single_item_async(source, item, import_dir_path)
                download_tasks.append(task)

        # Execute all downloads concurrently
        if download_tasks:
            await asyncio.gather(*download_tasks, return_exceptions=True)

    async def _download_single_item_async(self, source, item, import_dir_path):
        """Download a single media item asynchronously."""
        media_id = item.get("mediaItemId")
        media_type = item.get("mediaType")
        url = item.get("originalUrl")
        nix_caption = item.get("caption")
        timestamp = item.get("timestamp")
        orig_filename = item.get("filename", None)
        playlist_id = item.get("playlist_id")

        if not url:
            self.__logger.warning(f"No URL for mediaItemId {media_id}, skipping.")
            return

        basename, extension = extract_filename_and_ext(orig_filename or url)
        basename = f"{source}_{playlist_id}_{basename}"
        full_name = f"{basename}.{extension}"
        local_path = import_dir_path / full_name

        try:
            # Download file in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._download_file_sync, url, local_path)
            self.__logger.info(f"Downloaded {full_name}")

            # Insert into database
            self.__logger.debug(f"Inserting into database: source: {source}, playlist_id: {playlist_id}, media_item_id: {media_id}")
            
            # Run database operation in thread pool too
            await loop.run_in_executor(None, self._insert_file_record, source, playlist_id, media_id, url, basename, extension, nix_caption, timestamp, local_path)
            
        except Exception as e:
            self.__logger.error(f"Failed to download {url}: {e}")

    def _download_file_sync(self, url, local_path):
        """Synchronous file download method to run in thread pool."""
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(local_path, 'wb') as f:
            shutil.copyfileobj(response.raw, f)

    def _insert_file_record(self, source, playlist_id, media_id, url, basename, extension, nix_caption, timestamp, local_path):
        """Synchronous database insert to run in thread pool."""
        # Create a separate connection for this thread to avoid transaction conflicts
        db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=10.0)
        try:
            with db:  # auto-commit
                db.execute("""
                    INSERT INTO imported_files (source, playlist_id, media_item_id, original_url, basename, extension, nix_caption, orig_extension, processed, orig_timestamp, last_modified)
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
            self.__logger.warning(f"Error inserting file record for {basename}: {e}")
        finally:
            db.close()

    async def _process_images_async(self):
        """Process imported images in background using ProcessImages."""
        try:
            processor = ProcessImages(self.__config)
            await processor.process_images()
        except Exception as e:
            self.__logger.error(f"Error in background image processing: {e}")

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

    def cleanup(self):
        """Clean up database connections and resources."""
        try:
            if hasattr(self, '__db') and self.__db:
                self.__logger.info("Closing database connection...")
                self.__db.close()
                self.__logger.info("Database connection closed successfully")
        except Exception as e:
            self.__logger.error(f"Error closing database: {e}")

if __name__ == '__main__':
    print("starting")
    print("Error: import_photos.py requires a Model instance to run.")
    print("This script is designed to be used as a module, not run directly.")
    print("Use run_import_photos.py (or nix.py) instead for standalone Nixplay import functionality.")
    sys.exit(1) 
    
# LOGIN
    try:
        session = importer.create_authorized_client(importer.username, importer.password, importer.login_url)
        if session.cookies.get("prod.session.id") is None:
            raise LoginError("Bad Credentials")
    except LoginError as e:
        print(f"Login failed: {e}")
        print("Exiting")
        sys.exit()
    except Exception as e:
        print(f"An error occurred: {e}")
    print("logged in")

# GET PLAYLIST NAMES 
    playlists = []
    try:
        playlists = importer.get_playlist_names(session, importer.playlist_url, importer.frame_key)

    except Exception as e:
        print(f"An error occurred: {e}")
    print("got playlists")
# CHECK OR CREATE SUBDIRECTORIES
    print("checking for playlist updates")
    playlists_to_update = []
    for playlist in playlists:
        folder_name = create_valid_folder_name(str(playlist["id"]))
        subdirectory = os.path.expanduser(importer.local_pictures_path + '/imports/' + folder_name + "/")
        
        if os.path.isdir(subdirectory):  # Directory exists - add to update list
            playlists_to_update.append((playlist["id"], playlist["playlist_name"], subdirectory))
        else:
            try:                         # Create new directory - no need to check version since it's new
                os.makedirs(subdirectory, mode=0o700, exist_ok=False)
                if wait_for_directory(subdirectory, timeout=10):
                    playlists_to_update.append((playlist["id"], playlist["playlist_name"], subdirectory))
                    print("created new directories")
                else:
                    print("Creating new playlist directory timed out")
            except Exception as e:
                print(f"Directory creation failed: {e}")
    
    if not playlists_to_update:
        print("Nothing to update - exiting early")
        sys.exit(0)      

    try:
        print("playlists_to_update", playlists_to_update)
        media_items, last_version = importer.get_playlist_media(session, importer.playlist_url, importer.item_path, playlists_to_update, db)
        print(f"Retrieved {len(media_items)} new media items to process")
        # TODO: Update version after import is complete
    except Exception as e:
        print(f"An error occurred: {e}")
    
    # Close database connection
    if 'db' in locals():
        db.close()
        print("Database connection closed")

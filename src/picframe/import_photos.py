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
from .process_images import ProcessImages
from .file_utils import extract_filename_and_ext, unix_to_utc_string, wait_for_directory, create_valid_folder_name
from .config import DB_JRNL_MODE
from threading import Lock

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
        
        # Throttling configuration - get from config or use defaults
        aspect_config = self.__model.get_aspect_config()
        self.__max_concurrent_downloads = aspect_config.get("max_concurrent_downloads", 3)
        self.__max_concurrent_db_operations = aspect_config.get("max_concurrent_db_operations", 1)
        self.__download_batch_size = aspect_config.get("download_batch_size", 5)
        
        self.__logger.info(f"Import throttling: max_downloads={self.__max_concurrent_downloads}, max_db_ops={self.__max_concurrent_db_operations}, batch_size={self.__download_batch_size}")
        
        # Ensure import directory exists
        import_path = Path(os.path.expanduser(self.__import_dir))
        import_path.mkdir(parents=True, exist_ok=True)
        self.__logger.debug(f"Import directory ensured: {import_path}")
        
        self.to_import = []
        self._importing = False
        self._stop_requested = False
        # Database connection pool for thread-safe operations
        self.__db_lock = Lock()
        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=30.0)
        
        # Configure main connection
        self.__db.execute(f"PRAGMA journal_mode={DB_JRNL_MODE}")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__db.execute("PRAGMA busy_timeout=30000")
        self.__db.execute("PRAGMA temp_store=MEMORY")
        self.__db.execute("PRAGMA mmap_size=30000000000")
        self.__db.execute("PRAGMA cache_size=10000")
        self.__logger.debug(f"DB connection established")
        
        # Additional database configuration for better concurrency
        self.__db.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.__db.execute("PRAGMA cache_size=-64000")  # 64MB cache
        self.__logger.debug(f"DB configured for better concurrency")

    def get_timer_task(self):
        return self.check_for_updates

    def _get_db_connection(self):
        """Get a new database connection for thread-safe operations."""
        try:
            db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=60.0)
            # Configure connection for better concurrency
            db.execute(f"PRAGMA journal_mode={DB_JRNL_MODE}")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=60000")  # 60 second timeout
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA cache_size=10000")
            return db
        except Exception as e:
            self.__logger.error(f"Failed to create database connection: {e}")
            raise

    def _execute_db_operation(self, operation_func, *args, **kwargs):
        """Execute a database operation with proper connection management."""
        db = None
        try:
            db = self._get_db_connection()
            result = operation_func(db, *args, **kwargs)
            return result
        except Exception as e:
            self.__logger.error(f"Database operation failed: {e}")
            raise
        finally:
            if db:
                try:
                    db.close()
                except Exception as e:
                    self.__logger.warning(f"Error closing database connection: {e}")

    def cleanup(self):
        """Clean up database connections and resources."""
        self._stop_requested = True
        
        try:
            if hasattr(self, '_ImportPhotos__db') and self._ImportPhotos__db:
                self._ImportPhotos__db.close()
                self.__logger.debug("ImportPhotos database connection closed")
            else:
                self.__logger.debug("ImportPhotos database was already closed or not initialized")
        except Exception as e:
            self.__logger.warning(f"Error closing ImportPhotos database: {e}")

    async def check_for_updates(self) -> None:
        # Quick check if already importing or stop requested
        if self._importing:
            self.__logger.debug("Import already in progress, skipping this timer cycle")
            return
            
        if self._stop_requested:
            self.__logger.debug("Import check skipped - stop requested")
            return
            
        self._importing = True
        try:
            start_time = time.time()
            self.__logger.info("Starting import check...")

            # Get media items to download (this part runs in thread pool)
            # Check for cancellation before submitting to thread pool
            if self._stop_requested:
                self.__logger.debug("Import check cancelled before thread pool submission - stop requested")
                return
                
            loop = asyncio.get_running_loop()
            media_items_by_source = await loop.run_in_executor(None, self._prepare_media_for_download)
            
            # Check for stop signal after preparation
            if self._stop_requested:
                self.__logger.debug("Import check cancelled after preparation - stop requested")
                return
            
            prep_time = time.time() - start_time
            self.__logger.debug(f"Preparation took {prep_time:.2f} seconds")
            
            # Start downloads as background tasks (don't await - let them run in background)
            download_tasks = []
            for source, media_items in media_items_by_source.items():
                if media_items:
                    self.__logger.info(f"Starting background download for {len(media_items)} items from {source} with throttling (max_downloads={self.__max_concurrent_downloads}, max_db_ops={self.__max_concurrent_db_operations}, batch_size={self.__download_batch_size})")
                    task = asyncio.create_task(self._download_and_update_async(source, media_items))
                    download_tasks.append(task)
            
            # Store tasks for potential cleanup/monitoring
            if download_tasks:
                self._active_download_tasks = getattr(self, '_active_download_tasks', [])
                self._active_download_tasks.extend(download_tasks)
                # Clean up completed tasks
                self._active_download_tasks = [t for t in self._active_download_tasks if not t.done()]
                self.__logger.debug(f"Total active download tasks: {len(self._active_download_tasks)}")
            
            total_time = time.time() - start_time
            self.__logger.debug(f"Import check completed in {total_time:.2f} seconds, downloads running in background")
            
            # === Process images as background task too ===
            # Always process images to handle any existing imported files
            # Note: New downloads will start image processing immediately in parallel
            process_task = asyncio.create_task(self._process_images_async())
            self.__logger.info("Started background image processing task for existing files")
            
        finally:
            pass
            
        #finally:
        #     self.__logger.info("Finally... set importing false.")
        #     self._importing = False
            
        #     # Cancel any active download tasks
        #     if hasattr(self, '_active_download_tasks'):
        #         for task in self._active_download_tasks:
        #             if not task.done():
        #                 task.cancel()
        #                 self.__logger.info("Cancelled active download task")
        #         self._active_download_tasks.clear()

    def _prepare_media_for_download(self):
        """Prepare media items for download. Returns dict of {source: media_items}"""
        start_time = time.time()
        
        # Check for stop signal before starting (this method runs in thread pool)
        if self._stop_requested:
            self.__logger.debug("Import preparation cancelled - stop requested")
            return {}
        
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
            self.__logger.debug(f"get_source_playlists took {time.time() - step_start:.2f}s")
            
            item_path = "slides"                                            # TODO: use config for item_path
            if playlists:
                # Check for stop signal before database operations
                if self._stop_requested:
                    self.__logger.debug("Import preparation cancelled before database update - stop requested")
                    return {}
                    
                step_start = time.time()
                self.update_imported_playlists_db(source, playlists)        # Update existing playlists in DB, delete any that are no longer present
                self.__logger.debug(f"update_imported_playlists_db took {time.time() - step_start:.2f}s")
                
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
                self.__logger.debug(f"create_nixplay_authorized_client took {time.time() - step_start:.2f}s")
                
                step_start = time.time()
                media_items = self.get_playlist_media(session)
                self.__logger.debug(f"get_playlist_media took {time.time() - step_start:.2f}s")
                
                media_items_by_source[source] = media_items
                
            source_time = time.time() - source_start
            self.__logger.debug(f"Source {source} processing took {source_time:.2f}s total")
                
        total_time = time.time() - start_time
        self.__logger.debug(f"_prepare_media_for_download completed in {total_time:.2f}s")
        return media_items_by_source

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
        
        def update_timestamps(db):
            with db:  # auto-commit
                for playlist_id, playlist_name, status in playlists:
                    db.execute("""
                        UPDATE imported_playlists SET last_imported = ?
                        WHERE source = ? AND playlist_id = ?
                    """, (unix_to_utc_string(int(time.time())), source, playlist_id))
                    self.__logger.debug(f"Updated last_imported timestamp for playlist {playlist_name}")
        
        try:
            self._execute_db_operation(update_timestamps)
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
                self.__logger.error(f"Login failed: {e}")
                self.__logger.info("Exiting")
                sys.exit()
            except Exception as e:
                self.__logger.error(f"An error occurred: {e}")
            self.__logger.info("logged in")
            playlists = []
            try:
                playlists = self.get_playlist_names(session, source, playlist_url, identifier)
            except GetPlaylistsError as e:
                self.__logger.error(f"Playlist Request failed: {e}")
                raise GetPlaylistsError(f"Playlist Request failed: {e}")
                return []
            except Exception as e:
                self.__logger.error(f"An error occurred: {e}")
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
        
        def update_playlists(db):
            with db:  # auto-commit
                # Get existing playlists from DB for this source
                cur = db.execute("SELECT playlist_id, playlist_name FROM imported_playlists WHERE source = ?", (source,))
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
                    db.execute("""
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
                    
                    # Delete files from disk first
                    self._delete_stale_files_from_disk(source, playlist_id)
                    
                    # Then delete from database
                    self._delete_stale_files_from_db(source, playlist_id, stale_playlist_name)
        
        try:
            self._execute_db_operation(update_playlists)
        except Exception as e:
            self.__logger.warning(f"Error updating imported playlists: {e}")

    def _delete_stale_files_from_disk(self, source, playlist_id):
        """Delete files from disk for a stale playlist."""
        import_dir_path = Path(os.path.expanduser(self.__import_dir))
        pattern = f"{source}_{playlist_id}_*"
        
        deleted_count = 0
        for file_path in import_dir_path.glob(pattern):
            try:
                file_path.unlink()
                deleted_count += 1
                self.__logger.debug(f"Deleted stale file: {file_path.name}")
            except Exception as e:
                self.__logger.warning(f"Failed to delete {file_path}: {e}")
                
        self.__logger.info(f"Deleted {deleted_count} stale files for playlist {playlist_id}")

    def _delete_stale_files_from_db(self, source, playlist_id, playlist_name):
        """Delete files from database for a stale playlist."""
        
        def delete_playlist_files(db):
            with db:  # auto-commit
                # Delete from imported_playlists table
                db.execute("DELETE FROM imported_playlists WHERE source = ? AND playlist_id = ?", (source, int(playlist_id)))
                self.__logger.debug(f"Deleted playlist {playlist_id} ({playlist_name}) from imported_playlists")
                
                # Delete from file table
                cursor = db.execute("DELETE FROM file WHERE source = ? AND playlist = ?", (source, playlist_id))
                deleted_files_count = cursor.rowcount
                self.__logger.info(f"Deleted {deleted_files_count} file records for playlist {playlist_id}")
        
        try:
            self._execute_db_operation(delete_playlist_files)
        except Exception as e:
            self.__logger.warning(f"Error deleting stale files from database for playlist {playlist_id}: {e}")

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
                    def get_src_version(db):
                        cur = db.execute("SELECT src_version FROM imported_playlists WHERE source = ? AND playlist_id = ?", 
                                    (source, int(playlist_id)))
                        result = cur.fetchone()
                        if result:
                            return result[0]
                        return None
                    
                    src_version = self._execute_db_operation(get_src_version)
                    
                    if src_version == None:
                        self.__logger.info(f"src_version is None, updating to {nix_lastVersion}")
                        try:
                            def update_src_version(db):
                                db.execute("UPDATE imported_playlists SET src_version = ? WHERE source = ? AND playlist_id = ?", 
                                    (nix_lastVersion, source, int(playlist_id)))
                            
                            self._execute_db_operation(update_src_version)
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
                    def get_existing_media(db):
                        cur = db.execute("SELECT media_item_id FROM imported_files WHERE source = ? AND playlist_id = ?", 
                                    (source, int(playlist_id)))
                        return set(row[0] for row in cur.fetchall() if row[0])
                    
                    existing_media_ids = self._execute_db_operation(get_existing_media)
                    #self.__logger.info(f"existing_media_ids: {existing_media_ids}")
                    self.__logger.info(f"Found {len(existing_media_ids)} existing media items in database for playlist {playlist_name}")
                except Exception as e:
                    self.__logger.warning(f"Error querying database for existing media: {e}")
            
            slides = []
            matched_slides = unmatched_slides = duplicates = 0
            # Handle different JSON response structures
            media_list = json if isinstance(json, list) else json.get(item_path, [])
            total_in_playlist = len(media_list)
            
            # Use seen_ids - a set() - to remove duplicates
            seen_ids = set()       
            
            for slide in media_list:
                if isinstance(slide, dict) and "mediaItemId" in slide:
                    media_id = slide["mediaItemId"]
                    if media_id not in seen_ids:
                        seen_ids.add(media_id)
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
                            unmatched_slides += 1
                        else:
                            matched_slides += 1
                    else:
                        duplicates += 1
            
            self.__logger.info(f"Playlist {playlist_name}: {total_in_playlist} total, {unmatched_slides} new, {matched_slides} already exist, {duplicates} are duplicates")
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
        self.__logger.info(f"Storing {len(media_items)} media items with throttling.")
        
        import_dir_path = Path(os.path.expanduser(self.__import_dir))
        import_dir_path.mkdir(parents=True, exist_ok=True)

        # Create semaphores for throttling
        download_semaphore = asyncio.Semaphore(self.__max_concurrent_downloads)
        db_semaphore = asyncio.Semaphore(self.__max_concurrent_db_operations)
        
        # Process media items in batches to avoid overwhelming the system
        total_items = len(media_items)
        processed = 0
        
        for i in range(0, total_items, self.__download_batch_size):
            batch = media_items[i:i + self.__download_batch_size]
            batch_num = (i // self.__download_batch_size) + 1
            total_batches = (total_items + self.__download_batch_size - 1) // self.__download_batch_size
            
            self.__logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} items)")
            
            # Create download tasks for this batch
            download_tasks = []
            for item in batch:
                if item.get("originalUrl"):
                    task = self._download_single_item_async_throttled(source, item, import_dir_path, download_semaphore, db_semaphore)
                    download_tasks.append(task)

            # Execute batch downloads with throttling
            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)
                
                # Log batch results
                success_count = sum(1 for r in results if r is True)
                error_count = len(results) - success_count
                processed += len(batch)
                
                self.__logger.info(f"Batch {batch_num}/{total_batches} complete: {success_count} successful, {error_count} failed. Total progress: {processed}/{total_items}")
                
                            # Longer delay between batches to prevent overwhelming the system and give DB recovery time
            if i + self.__download_batch_size < total_items:
                await asyncio.sleep(1.0)
        
        # Wait for all image processing tasks to complete
        await self._wait_for_image_processing_completion()

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

        orig_extension = extension
        processed = 0
        orig_timestamp = timestamp

        try:
            # Download file in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._download_file_sync, url, local_path)
            self.__logger.info(f"Downloaded {full_name}")

            # Insert into database
            self.__logger.debug(f"Inserting into database: source: {source}, playlist_id: {playlist_id}, media_item_id: {media_id}")
            
            # Run database operation in thread pool too
            await loop.run_in_executor(None, self._insert_file_record, source, playlist_id, media_id, url, basename, extension, nix_caption, orig_extension, processed, orig_timestamp, local_path) 
            
        except Exception as e:
            self.__logger.error(f"Failed to download {url}: {e}")

    async def _download_single_item_async_throttled(self, source, item, import_dir_path, download_semaphore, db_semaphore):
        """Download a single media item asynchronously with throttling."""
        async with download_semaphore:  # Limit concurrent downloads
            media_id = item.get("mediaItemId")
            media_type = item.get("mediaType")
            url = item.get("originalUrl")
            nix_caption = item.get("caption")
            timestamp = item.get("timestamp")
            orig_filename = item.get("filename", None)
            playlist_id = item.get("playlist_id")

            if not url:
                self.__logger.warning(f"No URL for mediaItemId {media_id}, skipping.")
                return False

            basename, extension = extract_filename_and_ext(orig_filename or url)
            basename = f"{source}_{playlist_id}_{basename}"
            full_name = f"{basename}.{extension}"
            local_path = import_dir_path / full_name

            orig_extension = extension
            processed = 0
            orig_timestamp = timestamp

            try:
                # Download file in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._download_file_sync, url, local_path)
                self.__logger.debug(f"Downloaded {full_name}")

                # Insert into database with throttling
                async with db_semaphore:  # Limit concurrent database operations
                    self.__logger.debug(f"Inserting into database: source: {source}, playlist_id: {playlist_id}, media_item_id: {media_id}")
                    
                    # Run database operation in thread pool too
                    await loop.run_in_executor(None, self._insert_file_record, source, playlist_id, media_id, url, basename, extension, nix_caption, orig_extension, processed, orig_timestamp, local_path) 
                    
                    # Small delay to prevent database contention
                    await asyncio.sleep(0.1)
                
                # Start image processing immediately after successful download and database insertion
                await self._start_image_processing_async(local_path)
                
                return True
                
            except Exception as e:
                self.__logger.error(f"Failed to download {url}: {e}")
                return False

    def _download_file_sync(self, url, local_path):
        """Synchronous file download method to run in thread pool."""
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(local_path, 'wb') as f:
            shutil.copyfileobj(response.raw, f)

    def _insert_file_record(self, source, playlist_id, media_id, url, basename, extension, nix_caption, orig_extension, processed, orig_timestamp, local_path):
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
                    orig_extension,  # original extension same as stored
                    processed,
                    orig_timestamp,
                    unix_to_utc_string(int(os.path.getmtime(local_path)))
                ))
        except Exception as e:
            self.__logger.warning(f"Error inserting file record for {basename}: {e}")
        finally:
            db.close()

    async def _start_image_processing_async(self, file_path):
        """Start image processing for a downloaded file."""
        try:
            # Create background task and store for monitoring
            if not hasattr(self, '_image_processing_tasks'):
                self._image_processing_tasks = []
            
            task = asyncio.create_task(self._process_single_image_async(file_path))
            self._image_processing_tasks.append(task)
            
            # Clean up completed tasks
            self._image_processing_tasks = [t for t in self._image_processing_tasks if not t.done()]
            
            self.__logger.debug(f"Started image processing for {file_path.name}")
            
        except Exception as e:
            self.__logger.error(f"Failed to start image processing for {file_path.name}: {e}")

    async def _process_single_image_async(self, file_path):
        """Process a single image asynchronously."""
        try:
            from .process_images import ProcessImages
            
            processor = ProcessImages(self.__model)
            await processor.process_single_image_async(file_path)
            
            self.__logger.info(f"Completed image processing for {file_path.name}")
            
        except Exception as e:
            self.__logger.error(f"Error processing image {file_path.name}: {e}")

    async def _wait_for_image_processing_completion(self):
        """Wait for all image processing tasks to complete."""
        if not hasattr(self, '_image_processing_tasks') or not self._image_processing_tasks:
            self.__logger.info("No image processing tasks to wait for")
            return
        
        self.__logger.info(f"Waiting for {len(self._image_processing_tasks)} image processing tasks to complete...")
        
        # Wait for all tasks to complete
        await asyncio.gather(*self._image_processing_tasks, return_exceptions=True)
        
        # Log completion status
        completed = sum(1 for t in self._image_processing_tasks if t.done() and not t.exception())
        failed = len(self._image_processing_tasks) - completed
        
        self.__logger.info(f"Image processing completed: {completed} successful, {failed} failed")
        self._image_processing_tasks.clear()

    def get_image_processing_status(self):
        """Get current status of image processing tasks."""
        if not hasattr(self, '_image_processing_tasks'):
            return {'total': 0, 'completed': 0, 'running': 0, 'failed': 0}
        
        tasks = self._image_processing_tasks
        total = len(tasks)
        completed = sum(1 for t in tasks if t.done() and not t.exception())
        failed = sum(1 for t in tasks if t.done() and t.exception())
        running = total - completed - failed
        
        return {'total': total, 'completed': completed, 'running': running, 'failed': failed}

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

if __name__ == '__main__':
    print("starting")
    print("Error: import_photos.py requires a Model instance to run.")
    print("This script is designed to be used as a module, not run directly.")
    print("Use run_import_photos.py (or nix.py) instead for standalone Nixplay import functionality.")
    sys.exit(1) 
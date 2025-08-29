"""
ImportPhotos - Async Version

Handles importing photos from third-party services to the local filesystem.
Integrates with configured import sources (e.g. Nixplay) and maintains imported_playlists database table.
"""

import asyncio
import aiohttp
import aiofiles
import json
import logging
import os
import re
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from concurrent.futures import ThreadPoolExecutor

import ntplib
import pytz
import urllib3

from .schema import create_schema
from .process_images import ProcessImages


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
    """Convert Unix timestamp to UTC string."""
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
        return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


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
        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=30.0)
        self.__db.row_factory = sqlite3.Row
        # Use WAL mode for better concurrency, DELETE for compatibility with DB Browser for SQLite
        self.__db.execute("PRAGMA journal_mode=WAL")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__db.execute("PRAGMA busy_timeout=30000")
        self.__db.execute("PRAGMA temp_store=MEMORY")
        self.__db.execute("PRAGMA mmap_size=30000000000")
        self.__db.execute("PRAGMA cache_size=10000")
        create_schema(self.__db)
        
        # Thread pool for CPU-bound operations
        self._thread_pool = ThreadPoolExecutor(max_workers=4)

    async def check_for_updates(self) -> None:
        """Main async method to check for updates and import photos."""
        if self._importing:
            self.__logger.debug("Import already in progress, skipping.")
            return
            
        self._importing = True
        try:
            self.__logger.info("Starting async photo import process...")
            
            # Process each source
            for source_config in self.__sources:
                source_name = source_config.get('name', 'unknown')
                self.__logger.info(f"Processing source: {source_name}")
                
                try:
                    await self._process_source_async(source_config)
                except Exception as e:
                    self.__logger.error(f"Error processing source {source_name}: {e}")
                    continue
            
            # Process images after importing
            self.__logger.info("Starting image processing...")
            processor = ProcessImages(self.__model)
            await processor.process_images()
            
        except Exception as e:
            self.__logger.error(f"Error during import: {e}")
        finally:
            self._importing = False
            self.__logger.info("Photo import process completed.")

    async def _process_source_async(self, source_config):
        """Process a single import source asynchronously."""
        source_name = source_config.get('name')
        
        # Get playlists (this might involve API calls)
        playlists = await self._get_playlists_async(source_config)
        
        # Update database with playlist info
        await self._update_playlists_db_async(source_name, playlists)
        
        # Process each playlist
        for playlist in playlists:
            playlist_id = playlist.get('id')
            self.__logger.info(f"Processing playlist: {playlist_id}")
            
            try:
                # Get media items for this playlist
                media_items = await self._get_media_items_async(source_config, playlist_id)
                
                # Download and save media items
                await self._save_media_async(source_name, playlist_id, media_items)
                
            except Exception as e:
                self.__logger.error(f"Error processing playlist {playlist_id}: {e}")
                continue

    async def _get_playlists_async(self, source_config):
        """Get playlists from source API asynchronously."""
        # This would be implemented based on your specific API
        # For now, return empty list as placeholder
        return []

    async def _get_media_items_async(self, source_config, playlist_id):
        """Get media items for a playlist asynchronously."""
        # This would be implemented based on your specific API
        # For now, return empty list as placeholder
        return []

    async def _update_playlists_db_async(self, source, playlists):
        """Update playlist database asynchronously."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._thread_pool, 
            self._update_playlists_db_blocking, 
            source, 
            playlists
        )

    def _update_playlists_db_blocking(self, source, playlists):
        """Blocking database update for playlists."""
        try:
            with self.__db:
                for playlist in playlists:
                    self.__db.execute("""
                        INSERT OR REPLACE INTO imported_playlists 
                        (source, playlist_id, name, last_updated) 
                        VALUES (?, ?, ?, ?)
                    """, (
                        source,
                        playlist.get('id'),
                        playlist.get('name'),
                        unix_to_utc_string(time.time())
                    ))
        except Exception as e:
            self.__logger.error(f"Error updating playlists database: {e}")

    async def _save_media_async(self, source, playlist_id, media_items):
        """Download and save media items asynchronously."""
        if not media_items:
            return
            
        self.__logger.info(f"Downloading {len(media_items)} media items for playlist {playlist_id}")
        
        import_dir_path = Path(os.path.expanduser(self.__import_dir))
        import_dir_path.mkdir(parents=True, exist_ok=True)
        
        # Create semaphore to limit concurrent downloads
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent downloads
        
        # Create tasks for all downloads
        tasks = []
        for item in media_items:
            task = self._download_item_async(semaphore, source, playlist_id, item, import_dir_path)
            tasks.append(task)
        
        # Execute downloads concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log results
        success_count = sum(1 for r in results if r is True)
        error_count = len(results) - success_count
        self.__logger.info(f"Download complete: {success_count} successful, {error_count} failed")

    async def _download_item_async(self, semaphore, source, playlist_id, item, import_dir_path):
        """Download a single media item asynchronously."""
        async with semaphore:  # Limit concurrent downloads
            try:
                media_id = item.get("mediaItemId")
                url = item.get("originalUrl")
                nix_caption = item.get("caption")
                timestamp = item.get("timestamp")
                orig_filename = item.get("filename", None)

                if not url:
                    self.__logger.warning(f"No URL for mediaItemId {media_id}, skipping.")
                    return False

                basename, extension = extract_filename_and_ext(orig_filename or url)
                basename = f"{source}_{playlist_id}_{basename}"
                full_name = f"{basename}.{extension}"
                local_path = import_dir_path / full_name

                # Check if file already exists
                if local_path.exists():
                    self.__logger.debug(f"File already exists: {full_name}")
                    return True

                # Download file asynchronously
                success = await self._download_file_async(url, local_path)
                if not success:
                    return False

                # Insert into database (run in thread pool)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._thread_pool,
                    self._insert_media_db_blocking,
                    source, playlist_id, media_id, url, basename, extension,
                    nix_caption, timestamp, str(local_path)
                )

                self.__logger.info(f"Successfully imported: {full_name}")
                return True

            except Exception as e:
                self.__logger.error(f"Error downloading item {item.get('mediaItemId', 'unknown')}: {e}")
                return False

    async def _download_file_async(self, url, local_path):
        """Download a file asynchronously using aiohttp."""
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    
                    # Write file asynchronously
                    async with aiofiles.open(local_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                    
                    return True
                    
        except Exception as e:
            self.__logger.error(f"Failed to download {url}: {e}")
            return False

    def _insert_media_db_blocking(self, source, playlist_id, media_id, url, basename, extension, 
                                 nix_caption, timestamp, local_path):
        """Insert media item into database (blocking operation)."""
        try:
            last_modified = unix_to_utc_string(int(os.path.getmtime(local_path)))
            
            with self.__db:
                self.__db.execute("""
                    INSERT INTO imported_files 
                    (source, playlist_id, media_item_id, original_url, basename, extension,
                     nix_caption, orig_extension, processed, orig_timestamp, last_modified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    source, playlist_id, media_id, url, basename, extension,
                    nix_caption, extension, 0, timestamp, last_modified
                ))
                
        except Exception as e:
            self.__logger.error(f"Error inserting media into database: {e}")

    def is_importing(self):
        """Check if import is currently in progress."""
        return self._importing

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=True)
        if hasattr(self, '__db'):
            self.__db.close()

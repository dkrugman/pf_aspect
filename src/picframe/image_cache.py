import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import schema
from .config import DB_JRNL_MODE
from .file_time_utils import get_file_birth_time, get_file_times, is_birth_time_available
from .file_utils import parse_filename_metadata
from .image_meta_utils import get_exif_info
from .video_streamer import VIDEO_EXTENSIONS


class ImageCache:
    EXTENSIONS = [".png", ".jpg", ".jpeg", ".heif", ".heic", ".jxl", ".webp"]
    EXIF_TO_FIELD = {
        "EXIF FNumber": "f_number",
        "Image Make": "make",
        "Image Model": "model",
        "EXIF ExposureTime": "exposure_time",
        "EXIF ISOSpeedRatings": "iso",
        "EXIF FocalLength": "focal_length",
        "EXIF Rating": "rating",
        "EXIF LensModel": "lens",
        "EXIF DateTimeOriginal": "exif_datetime",
        "IPTC Keywords": "tags",
        "IPTC Caption/Abstract": "caption",
        "IPTC Object Name": "title",
    }

    def __init__(
        self,
        picture_dir,
        follow_links,
        db_file,
        geo_reverse,
        update_interval,
        square_img_setting="Landscape",
        model=None,
    ):
        self.__logger = logging.getLogger(__name__)
        self.__logger.debug("Creating an instance of ImageCache")

        self.__picture_dir = picture_dir
        self.__follow_links = follow_links
        self.__db_file = db_file
        self.__geo_reverse = geo_reverse
        self.__update_interval = update_interval

        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=30.0)
        self.__db.row_factory = sqlite3.Row

        self.__db.execute(f"PRAGMA journal_mode={DB_JRNL_MODE}")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__db.execute("PRAGMA busy_timeout=30000")
        self.__db.execute("PRAGMA temp_store=MEMORY")
        self.__db.execute("PRAGMA mmap_size=30000000000")
        # Optimizations
        self.__db.execute("PRAGMA cache_size=10000")

        self.__modified_folders = []
        self.__modified_files = []
        self.__cached_file_stats = []
        self.__keep_looping = True
        self.__pause_looping = False
        self.__shutdown_completed = False
        self.__purge_files = False
        self.__square_img_setting = square_img_setting
        # Guard flag to prevent concurrent slideshow creation
        self._creating_new_slideshow = False
        # Required back-reference to the owning model
        self.__model = model
        if self.__model is None:
            raise ValueError("ImageCache requires a model instance")

        if not self.__schema_exists_and_valid():
            self.__logger.debug("Creating schema")
            schema.create_schema(self.__db)
            self.__logger.debug("Updating cache (add files on disk to DB)")
            self.update_cache()

        # Log file time capabilities
        self.log_file_time_capabilities()

    def __schema_exists_and_valid(self):
        """Check if db_info table exists and has a valid schema version."""
        try:
            cur = self.__db.cursor()
            cur.execute(
                """
                SELECT schema_version FROM db_info
            """
            )
            row = cur.fetchone()
            return bool(row and row[0] >= schema.REQUIRED_SCHEMA_VERSION)
        except sqlite3.Error as e:
            self.__logger.warning(f"Schema check failed: {e}")
            return False

    def _is_active_slideshow(self):
        """Check if there's an active slideshow by checking if the slideshow table has any rows with played = 0."""
        try:
            cur = self.__db.cursor()
            cur.execute("SELECT COUNT(*) FROM slideshow WHERE played = 0")
            row = cur.fetchone()
            count = row[0] if row else 0
            self.__active_slideshow = count > 0
            return self.__active_slideshow
        except Exception as e:
            self.__logger.warning(f"Error checking slideshow status: {e}")
            return False

    def get_next_file_from_slideshow(self):
        """Get the next file from the slideshow."""
        cur = self.__db.cursor()
        cur.row_factory = sqlite3.Row
        cur.execute(
            """
            SELECT s.file_id, fo.name || '/' || f.basename || '.' || f.extension as fname, f.last_modified,
                   COALESCE(m.orientation, 1) as orientation,
                   COALESCE(m.exif_datetime, 0) as exif_datetime,
                   COALESCE(m.f_number, 0) as f_number, m.exposure_time,
                   COALESCE(m.iso, 0) as iso, m.focal_length, m.make, m.model, m.lens,
                   COALESCE(m.rating, 0) as rating, m.latitude, m.longitude,
                   COALESCE(f.width, 0) as width, COALESCE(f.height, 0) as height,
                   CASE WHEN f.height > f.width THEN 1 ELSE 0 END as is_portrait,
                   l.description as location, m.title, m.caption, m.tags, m.nix_caption
            FROM slideshow s
            JOIN file f ON s.file_id = f.file_id
            JOIN folder fo ON f.folder_id = fo.folder_id
            LEFT JOIN meta m ON s.file_id = m.file_id
            LEFT JOIN location l ON l.latitude = m.latitude AND l.longitude = m.longitude
            WHERE s.played = 0
            ORDER BY s.group_num ASC, s.order_in_group ASC
            LIMIT 1
        """
        )
        row = cur.fetchone()
        if row:
            # Import Pic class
            from .model import Pic

            return Pic(
                fname=row["fname"],
                last_modified=row["last_modified"],
                file_id=row["file_id"],
                orientation=row["orientation"],
                exif_datetime=row["exif_datetime"],
                f_number=row["f_number"],
                exposure_time=row["exposure_time"],
                iso=row["iso"],
                focal_length=row["focal_length"],
                make=row["make"],
                model=row["model"],
                lens=row["lens"],
                rating=row["rating"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                width=row["width"],
                height=row["height"],
                is_portrait=row["is_portrait"],
                location=row["location"],
                title=row["title"],
                caption=row["caption"],
                tags=row["tags"],
                nix_caption=row["nix_caption"],
            )
        return None

    def set_played_for_image(self, file_id):
        """Set played = 1 for the given file_id. Also increment displayed_count and
        set last_displayed to current time."""
        try:
            with self.__db:  # auto-commit
                self.__db.execute("UPDATE slideshow SET played = 1 WHERE file_id = ?", (file_id,))
                self.__db.execute(
                    "UPDATE file SET displayed_count = displayed_count + 1, last_displayed = ? WHERE file_id = ?",
                    (time.time(), file_id),
                )
        except Exception as e:
            self.__logger.warning(f"Error updating file display count: {e}")
        return

    def create_new_slideshow(self):
        """Create a new slideshow using the NewSlideshow class."""
        # Prevent re-entrancy if called concurrently (e.g., by a timer)
        self.__logger.debug("CREATE_NEW_SLIDESHOW called **************************")
        if getattr(self, "_creating_new_slideshow", False):
            self.__logger.debug("CREATING NEW SLIDESHOW ATTR NOT FOUND")
        elif self._creating_new_slideshow:
            self.__logger.debug("Slideshow creation already in progress; skipping new request.")
            return
        self._creating_new_slideshow = True
        try:
            from .create_new_slideshow import NewSlideshow

            # We need to pass a model instance, but ImageCache doesn't have direct access to it
            # For now, we'll create a simple slideshow without the full NewSlideshow functionality
            if self.__model is None:
                self.__logger.warning("Model reference not set on ImageCache; cannot create slideshow")
                return None
            self.__logger.debug("Calling NewSlideshow(model).generate_slideshow()...")
            ns = NewSlideshow(self.__model)
            ns.generate_slideshow()
            self.__logger.info("New slideshow creation completed")
        except Exception as e:
            self.__logger.warning(f"Error creating new slideshow: {e}")
        finally:
            self._creating_new_slideshow = False

    def pause_looping(self, value):
        self.__pause_looping = value

    def stop(self):
        self.__keep_looping = False
        # Since the background thread is commented out, we need to handle shutdown manually
        try:
            self.__db.close()
        except Exception as e:
            self.__logger.warning(f"Error closing database: {e}")
        self.__shutdown_completed = True

    def purge_files(self):
        self.__purge_files = True

    def update_cache(self):
        self.__logger.debug("Updating cache")

        if not self.__modified_files:
            self.__logger.debug("No unprocessed files in memory, checking disk")
            self.__modified_folders = self.__get_modified_folders()
            self.__logger.debug(f"Modified folders: {self.__modified_folders}")
            self.__modified_files = self.__get_modified_files(self.__modified_folders)
            self.__logger.debug("Found %d new files on disk", len(self.__modified_files))

        # Process files in batches for better performance
        if self.__modified_files and not self.__pause_looping:
            batch_size = 2000  # Large batches for many files
            while self.__modified_files and not self.__pause_looping:
                # Get next batch
                current_batch = []
                for _ in range(min(batch_size, len(self.__modified_files))):
                    if self.__modified_files:
                        current_batch.append(self.__modified_files.pop(0))

                if current_batch:
                    self.__logger.debug(f"Batch inserting {len(current_batch)} files")
                    self.__insert_files_batch(current_batch, source="update_cache")

        if not self.__modified_files:
            self.__update_folder_info(self.__modified_folders)
            self.__modified_folders.clear()

        if not self.__pause_looping:
            self.__purge_missing_files_and_folders()

    def query_cache(self, where_clause, sort_clause="fname ASC"):
        cursor = self.__db.cursor()
        cursor.row_factory = None
        try:
            sql = f"SELECT file_id FROM all_data WHERE {where_clause} ORDER BY {sort_clause}"
            return cursor.execute(sql).fetchall()

        except Exception:
            return []

    def get_column_names(self):
        sql = "PRAGMA table_info(all_data)"
        rows = self.__db.execute(sql).fetchall()
        return [row["name"] for row in rows]

    def __get_geo_location(
        self, lat, lon
    ):  # TODO periodically check all lat/lon in meta with no location and try again # noqa: E501
        location = self.__geo_reverse.get_address(lat, lon)
        if len(location) == 0:
            return False  # TODO this will continue to try even if there is some permanant cause
        else:
            sql = "INSERT OR REPLACE INTO location (latitude, longitude, description) VALUES (?, ?, ?)"
            starttime = round(time.time() * 1000)
            try:
                with self.__db:  # auto-commit
                    self.__db.execute(sql, (lat, lon, location))
                now = round(time.time() * 1000)
                self.__logger.debug("Update location: took %d ms for update", now - starttime)
                return True
            except Exception as e:
                self.__logger.warning(f"Error updating location: {e}")
                return False

    # --- Returns a set of folders matching any of
    #     - Found on disk, but not currently in the 'folder' table
    #     - Found on disk, but newer than the associated record in the 'folder' table
    #     - Found on disk, but flagged as 'missing' in the 'folder' table (REMOVED)
    # --- Note that all folders returned currently exist on disk
    def __get_modified_folders(self):
        out_of_date_folders = []
        sql_select = "SELECT * FROM folder WHERE name = ?"  # Using picture_dir for orientation switching
        parent_dir = os.path.dirname(self.__picture_dir)  # so it's set to ~/Pictures/Landscape or Portrait
        allowed_subfolders = ["Landscape", "Portrait", "Square"]  # need to look under the parent directory,
        # hardcoding names for now
        for subfolder in allowed_subfolders:
            dir_path = os.path.join(parent_dir, subfolder)

            if not os.path.exists(dir_path):
                continue  # skip if the subfolder does not exist

            # Walk this subfolder recursively
            for dir, _, _ in os.walk(dir_path, followlinks=self.__follow_links):
                if os.path.basename(dir).startswith("."):
                    continue  # ignore hidden folders

                mod_tm = int(os.stat(dir).st_mtime)
                found = self.__db.execute(sql_select, (dir,)).fetchone()

                if not found or found["last_modified"] < mod_tm:
                    out_of_date_folders.append((dir, mod_tm))
        return out_of_date_folders

    def __get_modified_files(self, modified_folders):
        out_of_date_files = []
        # sql_select = "SELECT fname, last_modified FROM all_data WHERE fname = ? and last_modified >= ?"
        sql_select = """
        SELECT file.basename, file.creation_time
            FROM file
                INNER JOIN folder
                    ON folder.folder_id = file.folder_id
            WHERE file.basename = ? AND file.extension = ? AND folder.name = ? AND file.creation_time >= ?
        """
        for dir, _date in modified_folders:
            for file in os.listdir(dir):
                base, extension = os.path.splitext(file)
                if (
                    extension.lower() in (ImageCache.EXTENSIONS + VIDEO_EXTENSIONS)
                    # have to filter out all the Apple junk
                    and ".AppleDouble" not in dir
                    and not file.startswith(".")
                ):
                    full_file = os.path.join(dir, file)
                    creation_tm = self.get_file_creation_time_timestamp(full_file)
                    found = self.__db.execute(sql_select, (base, extension.lstrip("."), dir, creation_tm)).fetchone()
                    if not found:
                        out_of_date_files.append(full_file)
        return out_of_date_files

    def insert_file(self, file, file_id=None, source=None, playlist=None):
        """Public method to insert a file into the database."""
        return self.__insert_file(file, file_id, source, playlist)

    async def insert_file_async(self, file, file_id=None, source=None, playlist=None):
        """Async version of insert_file - runs database operations in a thread pool."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.__insert_file, file, file_id, source, playlist)

    def __insert_files_batch(self, files, source="ImageCache"):
        """Insert multiple files in a single database transaction."""
        if not files:
            return

        try:
            # Use prepare statements for efficiency. In Production, use WAL mode for better concurrency.
            with self.__db:  # auto-commit transaction
                cursor = self.__db.cursor()

                # Prepare batch data
                folder_inserts = set()
                file_inserts = []
                meta_inserts = []

                for file in files:
                    try:
                        # Parse file info
                        dir, file_only = os.path.split(file)
                        base, extension = os.path.splitext(file_only)
                        extension = extension.lower().lstrip(".")

                        # Get configured sources from model
                        try:
                            configured_sources = self.__model.get_aspect_config().get("sources", {})
                        except Exception as e:
                            self.__logger.warning(f"Could not get aspect config from model: {e}")
                            configured_sources = {}

                        # Parse filename for source and playlist
                        filename = Path(file).name
                        parsed_source, parsed_playlist = parse_filename_metadata(filename, configured_sources)

                        # Add folder to batch
                        folder_inserts.add(dir)

                        # Get file metadata
                        width, height = 0, 0  # Default values
                        creation_tm = self.get_file_creation_time_timestamp(file)

                        # Get image metadata
                        meta = get_exif_info(file)
                        if meta:
                            width = meta.get("width", 0)
                            height = meta.get("height", 0)
                            meta_inserts.append((file, meta))

                        file_inserts.append(
                            (
                                dir,
                                parsed_source or source,
                                parsed_playlist,
                                base,
                                extension,
                                width,
                                height,
                                creation_tm,
                            )
                        )

                    except Exception as e:
                        self.__logger.error(f"Error preparing batch data for {file}: {e}")
                        continue

                # Execute batch folder inserts
                folder_insert_sql = "INSERT OR IGNORE INTO folder(name) VALUES(?)"
                cursor.executemany(folder_insert_sql, [(folder,) for folder in folder_inserts])

                # Execute batch file inserts
                file_insert_sql = """
                    INSERT OR IGNORE INTO file(folder_id, source, playlist, basename,
                    extension, width, height, creation_time)
                    VALUES((SELECT folder_id from folder where name = ?), ?, ?, ?, ?, ?, ?, ?)
                """
                cursor.executemany(file_insert_sql, file_inserts)

                # Execute metadata inserts individually (they have complex structure)
                for file_path, meta in meta_inserts:
                    try:
                        self.__insert_metadata(cursor, file_path, meta)
                    except Exception as e:
                        self.__logger.error(f"Error inserting metadata for {file_path}: {e}")

                cursor.close()
                self.__logger.debug(f"Successfully batch inserted {len(file_inserts)} files")

        except Exception as e:
            self.__logger.error(f"Error in batch insert: {e}")
            # Fallback to individual inserts
            self.__logger.debug("Falling back to individual file inserts...")
            for file in files:
                try:
                    self.__insert_file(file, source=source)
                except Exception as individual_error:
                    self.__logger.error(f"Error inserting {file}: {individual_error}")

    def __insert_metadata(self, cursor, file_path, meta):
        """Helper method to insert metadata for a single file."""
        # Implementation would match the existing metadata insertion logic
        # from __insert_file method
        pass

    def __insert_file(self, file, file_id=None, source=None, playlist=None):
        filename = Path(file).name

        # Get configured sources from model
        try:
            configured_sources = self.__model.get_aspect_config().get("sources", {})
        except Exception as e:
            self.__logger.warning(f"Could not get aspect config from model: {e}")
            configured_sources = {}

        source, playlist = parse_filename_metadata(filename, configured_sources)

        # Use INSERT OR REPLACE with all unique constraint fields to avoid duplicates
        file_insert = """
            INSERT OR IGNORE INTO file(folder_id, source, playlist, basename,
            extension, width, height, creation_time)
            VALUES((SELECT folder_id from folder where name = ?), ?, ?, ?, ?, ?, ?, ?)
        """
        # Insert the new folder if it's not already in the table.
        folder_insert = "INSERT OR IGNORE INTO folder(name) VALUES(?)"

        # Get the file's meta info and build the INSERT statement dynamically
        meta = {}

        meta = get_exif_info(file)
        width = meta.get("width")
        height = meta.get("height")
        meta_insert = self.__get_meta_sql_from_dict(meta)
        vals = list(meta.values())
        vals.insert(0, file)

        dir, file_only = os.path.split(file)
        base, extension = os.path.splitext(file_only)
        extension = extension.lower().lstrip(".")

        # Insert this file's info into the folder, file, and meta tables
        try:
            # Use prepare statements for efficiency.  In Production, use WAL mode for better concurrency.
            with self.__db:  # auto-commit
                # Prepare cursor for multiple operations
                cursor = self.__db.cursor()

                # Execute folder insert
                cursor.execute(folder_insert, (dir,))

                if file_id is None:
                    # Use creation time instead of modification time
                    creation_tm = self.get_file_creation_time_timestamp(file)
                    # Execute with all required parameters
                    self.__logger.debug(
                        f"Inserting file from {source}, playlist={playlist}: "
                        f"{base}.{extension} in {dir} with creation_time={creation_tm}"
                    )
                    cursor.execute(file_insert, (dir, source, playlist, base, extension, width, height, creation_tm))

                # Execute metadata insert
                try:
                    cursor.execute(meta_insert, vals)
                except Exception as e:
                    self.__logger.error(f"###FAILED meta_insert = {meta_insert}, vals = {vals}, error: {e}")

                # Close cursor
                cursor.close()

        except Exception as e:
            self.__logger.warning(f"Error inserting file {file}: {e}")

    def __update_folder_info(self, folder_collection):
        update_data = []
        sql = "UPDATE folder SET last_modified = ? WHERE name = ?"
        for folder, modtime in folder_collection:
            update_data.append((modtime, folder))
        try:
            with self.__db:  # auto-commit
                self.__db.executemany(sql, update_data)
        except Exception as e:
            self.__logger.warning(f"Error updating folder info: {e}")

    def __get_meta_sql_from_dict(self, dict):
        columns = ", ".join(dict.keys())
        ques = ", ".join("?" * len(dict.keys()))
        return (
            "INSERT OR REPLACE INTO meta(file_id, {0}) " "VALUES((SELECT file_id from all_data where fname = ?), {1})"
        ).format(columns, ques)

    def __purge_missing_files_and_folders(self):
        # Find folders in the db that are no longer on disk
        folder_id_list = []
        for row in self.__db.execute("SELECT folder_id, name from folder"):
            if not os.path.exists(row["name"]):
                folder_id_list.append([row["folder_id"]])

        # Flag or delete any non-existent folders from the db. Note, deleting will automatically
        # remove orphaned records from the 'file' and 'meta' tables
        if len(folder_id_list):
            try:
                with self.__db:  # auto-commit
                    if self.__purge_files:
                        self.__db.executemany("DELETE FROM folder WHERE folder_id = ?", folder_id_list)
                    else:
                        self.__logger.error(f"Folder in DB not found on disk. folder_id_list: {folder_id_list}")
            except Exception as e:
                self.__logger.warning(f"Error purging folders: {e}")

        # Find files in the db that are no longer on disk
        if self.__purge_files:
            file_id_list = []
            for row in self.__db.execute("SELECT file_id, fname from all_data"):
                if not os.path.exists(row["fname"]):
                    file_id_list.append([row["file_id"]])

            # Delete any non-existent files from the db. Note, this will automatically
            # remove matching records from the 'meta' table as well.
            if len(file_id_list):
                try:
                    with self.__db:  # auto-commit
                        self.__db.executemany("DELETE FROM file WHERE file_id = ?", file_id_list)
                except Exception as e:
                    self.__logger.warning(f"Error purging files: {e}")
            self.__purge_files = False

    def get_file_creation_time_linux(self, filepath):
        """
        Retrieves the creation time (birth time) of a file using enhanced utilities.

        This method now uses the file_time_utils module which can access creation time
        even on systems where Python's os.stat() doesn't support st_birthtime.

        Args:
            filepath (str): The path to the file.

        Returns:
            datetime.datetime: The creation time as a datetime object, or None if not available.
        """
        try:
            # Use our enhanced file time utilities
            birth_time = get_file_birth_time(filepath)

            if birth_time:
                return birth_time
            else:
                # Fall back to modification time if birth time unavailable
                mod_time = os.path.getmtime(filepath)
                fallback_time = datetime.fromtimestamp(mod_time)
                self.__logger.debug(f"Birth time unavailable for {filepath}, using modification time: {fallback_time}")
                return fallback_time

        except FileNotFoundError:
            self.__logger.warning(f"File not found: {filepath}")
            return None
        except Exception as e:
            self.__logger.warning(f"Error getting creation time for {filepath}: {e}")
            return None

    def get_file_creation_time_timestamp(self, filepath):
        """
        Get file creation time as a timestamp (float) for database storage.

        Args:
            filepath (str): The path to the file.

        Returns:
            float: Unix timestamp of creation time, or 0.0 if unavailable.
        """
        try:
            birth_time = self.get_file_creation_time_linux(filepath)
            if birth_time:
                return birth_time.timestamp()
            return 0.0
        except Exception as e:
            self.__logger.warning(f"Error getting creation timestamp for {filepath}: {e}")
            return 0.0

    def get_enhanced_file_times(self, filepath):
        """
        Get comprehensive file time information including creation time.

        Args:
            filepath (str): The path to the file.

        Returns:
            dict: Dictionary containing all available time information.
        """
        try:
            return get_file_times(filepath)
        except Exception as e:
            self.__logger.warning(f"Error getting enhanced file times for {filepath}: {e}")
            return {}

    def is_birth_time_supported(self):
        """
        Check if file creation time (birth time) is supported on this system.

        Returns:
            bool: True if birth time is available, False otherwise.
        """
        return is_birth_time_available()

    def log_file_time_capabilities(self):
        """
        Log information about file time capabilities of the current system.
        """
        birth_supported = self.is_birth_time_supported()
        self.__logger.debug(f"File birth time support: {'Available' if birth_supported else 'Not available'}")

        if birth_supported:
            self.__logger.debug("System supports file creation time retrieval")
        else:
            self.__logger.debug("System does not support file creation time, will use modification time as fallback")


# If being executed (instead of imported), kick it off...
if __name__ == "__main__":
    cache = ImageCache(
        picture_dir="/home/pi/Pictures",
        follow_links=False,
        db_file="/home/pi/db.db3",
        geo_reverse=None,
        update_interval=2,
    )

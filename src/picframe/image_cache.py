import sqlite3
import os
import time
import logging
import threading
from picframe.get_image_meta import GetImageMeta
from picframe.video_streamer import VIDEO_EXTENSIONS
from picframe.image_meta_utils import get_exif_info
from picframe.video_meta_utils import get_video_metadata
import picframe.schema as schema

class ImageCache:

    EXTENSIONS = ['.png', '.jpg', '.jpeg', '.heif', '.heic', '.jxl', '.webp']
    EXIF_TO_FIELD = {'EXIF FNumber': 'f_number',
                     'Image Make': 'make',
                     'Image Model': 'model',
                     'EXIF ExposureTime': 'exposure_time',
                     'EXIF ISOSpeedRatings': 'iso',
                     'EXIF FocalLength': 'focal_length',
                     'EXIF Rating': 'rating',
                     'EXIF LensModel': 'lens',
                     'EXIF DateTimeOriginal': 'exif_datetime',
                     'IPTC Keywords': 'tags',
                     'IPTC Caption/Abstract': 'caption',
                     'IPTC Object Name': 'title'}

    def __init__(self, picture_dir, follow_links, db_file, geo_reverse, update_interval, square_img_setting='Landscape'):
        self.__logger = logging.getLogger(__name__)
        self.__logger.debug('Creating an instance of ImageCache')

        self.__picture_dir = picture_dir
        self.__follow_links = follow_links
        self.__db_file = db_file
        self.__geo_reverse = geo_reverse
        self.__update_interval = update_interval

        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False, timeout=5.0)
        self.__db.row_factory = sqlite3.Row
        # Use WAL mode for better concurrency, DELETE for compatibility with DB Browser for SQLite
        self.__db.execute("PRAGMA journal_mode=DELETE")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")

        self.__modified_folders = []
        self.__modified_files = []
        self.__cached_file_stats = []
        self.__keep_looping = True
        self.__pause_looping = False
        self.__shutdown_completed = False
        self.__purge_files = False
        self.__square_img_setting = square_img_setting

        if not self.__schema_exists_and_valid():
            self.__logger.debug("Creating schema")
            schema.create_schema(self.__db)
            self.__logger.debug("Updating cache (add files on disk to DB)")
            self.update_cache()

    def __schema_exists_and_valid(self):
        """Check if db_info table exists and has a valid schema version."""
        try:
            cur = self.__db.cursor()
            cur.execute("""
                SELECT schema_version FROM db_info
            """)
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
        cur.execute("SELECT file_id FROM slideshow WHERE played = 0 ORDER BY group_num ASC, order_in_group ASC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None
        
    def set_played_for_image(self, file_id):
        """Set played = 1 for the given file_id."""
        cur = self.__db.cursor()
        cur.execute("UPDATE slideshow SET played = 1 WHERE file_id = ?", (file_id,))
        self.__db.commit()

    def create_new_slideshow(self):
        """Create a new slideshow using the NewSlideshow class."""
        try:
            from picframe.create_new_slideshow import NewSlideshow
            # We need to pass a model instance, but ImageCache doesn't have direct access to it
            # For now, we'll create a simple slideshow without the full NewSlideshow functionality
            self.__logger.info("Creating new slideshow...")
            
            # Get all available file IDs with their metadata
            cur = self.__db.cursor()
            cur.execute("""
                SELECT f.file_id, f.basename, f.extension, m.orientation 
                FROM file f 
                LEFT JOIN meta m ON f.file_id = m.file_id 
                ORDER BY RANDOM() LIMIT 50
            """)
            rows = cur.fetchall()
            
            if not rows:
                self.__logger.warning("No files available for slideshow")
                return
            
            # Clear existing slideshow
            cur.execute("DELETE FROM slideshow")
            
            # Insert new slideshow entries with proper schema
            for i, row in enumerate(rows, 1):
                orientation_text = 'portrait' if row['orientation'] == 2 else 'landscape'
                cur.execute("""
                    INSERT INTO slideshow (group_num, order_in_group, file_id, basename, extension, orientation, played) 
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                """, (1, i, row['file_id'], row['basename'], row['extension'], orientation_text))
            
            self.__db.commit()
            self.__logger.info(f"Created new slideshow with {len(rows)} images")
            
        except Exception as e:
            self.__logger.warning(f"Error creating new slideshow: {e}")

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
        self.__logger.debug('Updating cache')

        if not self.__modified_files:
            self.__logger.debug('No unprocessed files in memory, checking disk')
            self.__modified_folders = self.__get_modified_folders()
            self.__logger.debug(f"Modified folders: {self.__modified_folders}")
            self.__modified_files = self.__get_modified_files(self.__modified_folders)
            self.__logger.debug('Found %d new files on disk', len(self.__modified_files))

        while self.__modified_files and not self.__pause_looping:
            file = self.__modified_files.pop(0)
            self.__logger.debug('Inserting: %s', file)
            self.__insert_file(file)

        if not self.__modified_files:
            self.__update_folder_info(self.__modified_folders)
            self.__modified_folders.clear()

        if not self.__pause_looping:
            self.__purge_missing_files_and_folders()



    def query_cache(self, where_clause, sort_clause='fname ASC'):
        cursor = self.__db.cursor()
        cursor.row_factory = None
        try:
            sql = f"SELECT file_id FROM all_data WHERE {where_clause} ORDER BY {sort_clause}"
            return cursor.execute(sql).fetchall()

        except Exception:
            return []

    def get_file_info(self, file_id):
        if not file_id:
            return None
        sql = f"SELECT * FROM all_data where file_id = {file_id}"
        row = self.__db.execute(sql).fetchone()
        try:
            if row is not None and row['last_modified'] != os.path.getmtime(row['fname']):
                self.__logger.debug('Cache miss: File %s changed on disk', row['fname'])
                self.__insert_file(row['fname'], file_id)
                row = self.__db.execute(sql).fetchone()
        except OSError:
            self.__logger.warning("Image '%s' does not exist or is inaccessible", row['fname'])
        if row and row['latitude'] and row['longitude'] and row['location'] is None:
            if self.__get_geo_location(row['latitude'], row['longitude']):
                row = self.__db.execute(sql).fetchone()
        try:
            with self.__db:  # auto-commit
                self.__db.execute(
                    "UPDATE file SET displayed_count = displayed_count + 1, last_displayed = ? WHERE file_id = ?",
                    (time.time(), file_id)
                )
        except Exception as e:
            self.__logger.warning(f"Error updating file display count: {e}")
        return row

    def get_column_names(self):
        sql = "PRAGMA table_info(all_data)"
        rows = self.__db.execute(sql).fetchall()
        return [row['name'] for row in rows]

    def __get_geo_location(self, lat, lon):  # TODO periodically check all lat/lon in meta with no location and try again # noqa: E501
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
                self.__logger.debug(
                    'Update location: took %d ms for update',
                    now - starttime)
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
        sql_select = "SELECT * FROM folder WHERE name = ?"                      # Using picture_dir for orientation switching
        parent_dir = os.path.dirname(self.__picture_dir)                        # so it's set to ~/Pictures/Landscape or Portrait
        allowed_subfolders = ["Landscape", "Portrait", "Square"]                # need to look under the parent directory,
                                                                                # hardcoding names for now
        for subfolder in allowed_subfolders:
            dir_path = os.path.join(parent_dir, subfolder)
        
            if not os.path.exists(dir_path):
                continue  # skip if the subfolder does not exist

            # Walk this subfolder recursively
            for dir, _, _ in os.walk(dir_path, followlinks=self.__follow_links):
                if os.path.basename(dir).startswith('.'):
                    continue  # ignore hidden folders

                mod_tm = int(os.stat(dir).st_mtime)
                found = self.__db.execute(sql_select, (dir,)).fetchone()

                if not found or found['last_modified'] < mod_tm:
                    out_of_date_folders.append((dir, mod_tm))
        return out_of_date_folders

    def __get_modified_files(self, modified_folders):
        out_of_date_files = []
        # sql_select = "SELECT fname, last_modified FROM all_data WHERE fname = ? and last_modified >= ?"
        sql_select = """
        SELECT file.basename, file.last_modified
            FROM file
                INNER JOIN folder
                    ON folder.folder_id = file.folder_id
            WHERE file.basename = ? AND file.extension = ? AND folder.name = ? AND file.last_modified >= ?
        """
        for dir, _date in modified_folders:
            for file in os.listdir(dir):
                base, extension = os.path.splitext(file)
                if (extension.lower() in (ImageCache.EXTENSIONS + VIDEO_EXTENSIONS)
                        # have to filter out all the Apple junk
                        and '.AppleDouble' not in dir and not file.startswith('.')):
                    full_file = os.path.join(dir, file)
                    mod_tm = os.path.getmtime(full_file)
                    found = self.__db.execute(sql_select, (base, extension.lstrip("."), dir, mod_tm)).fetchone()
                    if not found:
                        out_of_date_files.append(full_file)
        return out_of_date_files

    def insert_file(self, file, file_id=None):
        """Public method to insert a file into the database."""
        return self.__insert_file(file, file_id)

    def __insert_file(self, file, file_id=None):
        file_insert = "INSERT OR REPLACE INTO file(folder_id, basename, extension, width, height, last_modified, source) VALUES((SELECT folder_id from folder where name = ?), ?, ?, ?, ?, ?, ?)"  # noqa: E501
        # file_update = "UPDATE file SET folder_id = (SELECT folder_id from folder where name = ?), basename = ?, extension = ?, last_modified = ?, source = ? WHERE file_id = ?"  # noqa: E501
        # Insert the new folder if it's not already in the table.
        folder_insert = "INSERT OR IGNORE INTO folder(name) VALUES(?)"

          # Get the file's meta info and build the INSERT statement dynamically
        meta = {}
  
        meta = get_exif_info(file)
        width = meta.get('width')
        height = meta.get('height')
        meta_insert = self.__get_meta_sql_from_dict(meta)
        vals = list(meta.values())
        vals.insert(0, file)

        mod_tm = os.path.getmtime(file)
        dir, file_only = os.path.split(file)
        base, extension = os.path.splitext(file_only)
        extension = extension.lower()
        source = "ImageCache"

        # Insert this file's info into the folder, file, and meta tables
        try:
            with self.__db:  # auto-commit
                self.__db.execute(folder_insert, (dir,))
                if file_id is None:
                    self.__db.execute(file_insert, (dir, base, extension.lstrip("."), width, height, mod_tm, source))
                try:
                    self.__db.execute(meta_insert, vals)
                except Exception as e:
                    self.__logger.error(f"###FAILED meta_insert = {meta_insert}, vals = {vals}, error: {e}")
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
        columns = ', '.join(dict.keys())
        ques = ', '.join('?' * len(dict.keys()))
        return 'INSERT OR REPLACE INTO meta(file_id, {0}) VALUES((SELECT file_id from all_data where fname = ?), {1})'.format(columns, ques)  # noqa: E501

    def __purge_missing_files_and_folders(self):
        # Find folders in the db that are no longer on disk
        folder_id_list = []
        for row in self.__db.execute('SELECT folder_id, name from folder'):
            if not os.path.exists(row['name']):
                folder_id_list.append([row['folder_id']])

        # Flag or delete any non-existent folders from the db. Note, deleting will automatically
        # remove orphaned records from the 'file' and 'meta' tables
        if len(folder_id_list):
            try:
                with self.__db:  # auto-commit
                    if self.__purge_files:
                        self.__db.executemany('DELETE FROM folder WHERE folder_id = ?', folder_id_list)
                    else:
                        self.__logger.error(f"Folder in DB not found on disk. folder_id_list: {folder_id_list}")
            except Exception as e:
                self.__logger.warning(f"Error purging folders: {e}")

        # Find files in the db that are no longer on disk
        if self.__purge_files:
            file_id_list = []
            for row in self.__db.execute('SELECT file_id, fname from all_data'):
                if not os.path.exists(row['fname']):
                    file_id_list.append([row['file_id']])

            # Delete any non-existent files from the db. Note, this will automatically
            # remove matching records from the 'meta' table as well.
            if len(file_id_list):
                try:
                    with self.__db:  # auto-commit
                        self.__db.executemany('DELETE FROM file WHERE file_id = ?', file_id_list)
                except Exception as e:
                    self.__logger.warning(f"Error purging files: {e}")
            self.__purge_files = False

# If being executed (instead of imported), kick it off...
if __name__ == "__main__":
    cache = ImageCache(picture_dir='/home/pi/Pictures', follow_links=False, db_file='/home/pi/db.db3', geo_reverse=None, update_interval=2)
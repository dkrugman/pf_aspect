import sqlite3
import os
import time
import logging
import threading
from picframe import get_image_meta
from picframe.video_streamer import VIDEO_EXTENSIONS, get_video_info
from picframe.image_meta_utils import get_exif_info
import picframe.schema as schema  # adjust import path as needed

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

    def __init__(self, picture_dir, follow_links, db_file, geo_reverse, update_interval):
        self.__logger = logging.getLogger(__name__)
        self.__logger.debug('Creating an instance of ImageCache')

        self.__picture_dir = picture_dir
        self.__follow_links = follow_links
        self.__db_file = db_file
        self.__geo_reverse = geo_reverse
        self.__update_interval = update_interval

        self.__db = sqlite3.connect(self.__db_file, check_same_thread=False)
        self.__db.row_factory = sqlite3.Row

        if not self.__schema_exists_and_valid():
            schema.create_schema(self.__db)

        self.__db_write_lock = threading.Lock()

        self.__modified_folders = []
        self.__modified_files = []
        self.__cached_file_stats = []
        self.__keep_looping = True
        self.__pause_looping = False
        self.__shutdown_completed = False
        self.__purge_files = False

        t = threading.Thread(target=self.__loop)
        t.start()

    def __schema_exists_and_valid(self):
        """Check if db_info table exists and has a valid schema version."""
        try:
            cur = self.__db.cursor()
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='db_info'
            """)
            if cur.fetchone() is None:
                return False  # db_info table does not exist

            cur.execute("SELECT schema_version FROM db_info")
            row = cur.fetchone()
            if row and row[0] >= schema.REQUIRED_SCHEMA_VERSION:
                return True
            else:
                return False  # outdated or missing version

        except sqlite3.Error as e:
            self.__logger.warning(f"Schema check failed: {e}")
            return False

    def __loop(self):
        while self.__keep_looping:
            if not self.__pause_looping:
                self.update_cache()
                time.sleep(self.__update_interval)
            time.sleep(0.01)
        with self.__db_write_lock:
            self.__db.commit()
            self.__db.close()
        self.__shutdown_completed = True

    def pause_looping(self, value):
        self.__pause_looping = value

    def stop(self):
        self.__keep_looping = False
        while not self.__shutdown_completed:
            time.sleep(0.05)

    def purge_files(self):
        self.__purge_files = True

    def update_cache(self):
        self.__logger.debug('Updating cache')

        if not self.__modified_files:
            self.__logger.debug('No unprocessed files in memory, checking disk')
            self.__modified_folders = self.__get_modified_folders()
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

        with self.__db_write_lock:
            self.__db.commit()

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
        with self.__db_write_lock:
            self.__db.execute(
                "UPDATE file SET displayed_count = displayed_count + 1, last_displayed = ? WHERE file_id = ?",
                (time.time(), file_id)
            )
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
            self.__db_write_lock.acquire()
            waittime = round(time.time() * 1000)
            self.__db.execute(sql, (lat, lon, location))
            self.__db_write_lock.release()
            now = round(time.time() * 1000)
            self.__logger.debug(
                'Update location: Wait for db %d ms and need %d ms for update ',
                waittime - starttime, now - waittime)
            return True

    # --- Returns a set of folders matching any of
    #     - Found on disk, but not currently in the 'folder' table
    #     - Found on disk, but newer than the associated record in the 'folder' table
    #     - Found on disk, but flagged as 'missing' in the 'folder' table
    # --- Note that all folders returned currently exist on disk
    def __get_modified_folders(self):
        out_of_date_folders = []
        sql_select = "SELECT * FROM folder WHERE name = ?"
        for dir in [d[0] for d in os.walk(self.__picture_dir, followlinks=self.__follow_links)]:
            if os.path.basename(dir):
                if os.path.basename(dir)[0] == '.':
                    continue  # ignore hidden folders
            mod_tm = int(os.stat(dir).st_mtime)
            found = self.__db.execute(sql_select, (dir,)).fetchone()
            if not found or found['last_modified'] < mod_tm or found['missing'] == 1:
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

    def __insert_file(self, file, file_id=None):
        file_insert = "INSERT OR REPLACE INTO file(folder_id, basename, extension, last_modified) VALUES((SELECT folder_id from folder where name = ?), ?, ?, ?)"  # noqa: E501
        file_update = "UPDATE file SET folder_id = (SELECT folder_id from folder where name = ?), basename = ?, extension = ?, last_modified = ? WHERE file_id = ?"  # noqa: E501
        # Insert the new folder if it's not already in the table. Update the missing field separately.
        folder_insert = "INSERT OR IGNORE INTO folder(name) VALUES(?)"
        folder_update = "UPDATE folder SET missing = 0 where name = ?"

        mod_tm = os.path.getmtime(file)
        dir, file_only = os.path.split(file)
        base, extension = os.path.splitext(file_only)

        # Get the file's meta info and build the INSERT statement dynamically
        meta = {}
        ext = os.path.splitext(file)[1].lower()
        if ext in VIDEO_EXTENSIONS: # no exif info available
            meta = self.__get_video_info(file)
        else:
            meta = self.__get_exif_info(file)
        meta_insert = self.__get_meta_sql_from_dict(meta)
        vals = list(meta.values())
        vals.insert(0, file)

        # Insert this file's info into the folder, file, and meta tables
        self.__db_write_lock.acquire()
        self.__db.execute(folder_insert, (dir,))
        self.__db.execute(folder_update, (dir,))
        if file_id is None:
            self.__db.execute(file_insert, (dir, base, extension.lstrip("."), mod_tm))
        else:
            self.__db.execute(file_update, (dir, base, extension.lstrip("."), mod_tm, file_id))
        try:
            self.__db.execute(meta_insert, vals)
        except:
            self.__logger.error(f"###FAILED meta_insert = {meta_insert}, vals = {vals}")
        self.__db_write_lock.release()

    def __update_folder_info(self, folder_collection):
        update_data = []
        sql = "UPDATE folder SET last_modified = ?, missing = 0 WHERE name = ?"
        for folder, modtime in folder_collection:
            update_data.append((modtime, folder))
        self.__db_write_lock.acquire()
        self.__db.executemany(sql, update_data)
        self.__db_write_lock.release()

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
            self.__db_write_lock.acquire()
            if self.__purge_files:
                self.__db.executemany('DELETE FROM folder WHERE folder_id = ?', folder_id_list)
            else:
                self.__db.executemany('UPDATE folder SET missing = 1 WHERE folder_id = ?', folder_id_list)
            self.__db_write_lock.release()

        # Find files in the db that are no longer on disk
        if self.__purge_files:
            file_id_list = []
            for row in self.__db.execute('SELECT file_id, fname from all_data'):
                if not os.path.exists(row['fname']):
                    file_id_list.append([row['file_id']])

            # Delete any non-existent files from the db. Note, this will automatically
            # remove matching records from the 'meta' table as well.
            if len(file_id_list):
                self.__db_write_lock.acquire()
                self.__db.executemany('DELETE FROM file WHERE file_id = ?', file_id_list)
                self.__db_write_lock.release()
            self.__purge_files = False

    def __get_video_info(self, file_path_name: str) -> dict:
        """
        Extracts metadata information from a video file.

        This method retrieves video metadata using the `get_video_info` function and 
        organizes it into a dictionary. The metadata includes dimensions, orientation, 
        and other optional EXIF and IPTC data if available.

        Args:
            file_path_name (str): The full path to the video file.

        Returns:
            dict: A dictionary containing the meta keys.
            Note, the 'key' must match a field in the 'meta' table
        """
        meta = get_video_info(file_path_name)

        # Dict to store interesting EXIF data
        # Note, the 'key' must match a field in the 'meta' table
        e: dict = {}

        # Orientation is set to 1 by default, as video files rarely have this info.
        e['orientation'] = 1

        width, height = meta.dimensions
        e['width'] = width
        e['height'] = height

        # Attempt to retrieve additional metadata if available in meta
        e['f_number'] = getattr(meta, 'f_number', None)
        e['make'] = getattr(meta, 'make', None)
        e['model'] = getattr(meta, 'model', None)
        e['exposure_time'] = getattr(meta, 'exposure_time', None)
        e['iso'] = getattr(meta, 'iso', None)
        e['focal_length'] = getattr(meta, 'focal_length', None)
        e['rating'] = getattr(meta, 'rating', None)
        e['lens'] = getattr(meta, 'lens', None)
        e['exif_datetime'] = meta.exif_datetime if not None else os.path.getmtime(file_path_name)

        if meta.gps_coords is not None:
            lat, lon = meta.gps_coords
        else:
            lat, lon = None, None
        e['latitude'] = round(lat, 4) if lat is not None else lat  # TODO sqlite requires (None,) to insert NULL
        e['longitude'] = round(lon, 4) if lon is not None else lon

        # IPTC
        e['tags'] = getattr(meta, 'tags', None)
        e['title'] = getattr(meta, 'title', None)
        e['caption'] = getattr(meta, 'caption', None)

        return e

# If being executed (instead of imported), kick it off...
if __name__ == "__main__":
    cache = ImageCache(picture_dir='/home/pi/Pictures', follow_links=False, db_file='/home/pi/db.db3', geo_reverse=None, update_interval=2)
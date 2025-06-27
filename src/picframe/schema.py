"""
schema.py

Defines the database schema for picframe and provides schema creation utilities.
"""

REQUIRED_SCHEMA_VERSION = 3

def create_schema(db):
    """Creates or upgrades the database schema to REQUIRED_SCHEMA_VERSION."""

    sql_folder_table = """
        CREATE TABLE IF NOT EXISTS folder (
            folder_id INTEGER NOT NULL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            last_modified REAL DEFAULT 0 NOT NULL
        )"""

    sql_file_table = """
        CREATE TABLE IF NOT EXISTS file (
            file_id INTEGER NOT NULL PRIMARY KEY,
            folder_id INTEGER NOT NULL,
            basename  TEXT NOT NULL,
            extension TEXT NOT NULL,
            last_modified REAL DEFAULT 0 NOT NULL,
            displayed_count INTEGER DEFAULT 0 NOT NULL,
            last_displayed REAL DEFAULT 0 NOT NULL,
            UNIQUE(folder_id, basename, extension)
        )"""

    sql_meta_table = """
        CREATE TABLE IF NOT EXISTS meta (
            file_id INTEGER NOT NULL PRIMARY KEY,
            orientation INTEGER DEFAULT 1 NOT NULL,
            exif_datetime REAL DEFAULT 0 NOT NULL,
            f_number REAL DEFAULT 0 NOT NULL,
            exposure_time TEXT,
            iso REAL DEFAULT 0 NOT NULL,
            focal_length TEXT,
            make TEXT,
            model TEXT,
            lens TEXT,
            rating INTEGER,
            latitude REAL,
            longitude REAL,
            width INTEGER DEFAULT 0 NOT NULL,
            height INTEGER DEFAULT 0 NOT NULL,
            title TEXT,
            caption TEXT,
            tags TEXT
        )"""

    sql_meta_index = """
        CREATE INDEX IF NOT EXISTS exif_datetime ON meta (exif_datetime)"""

    sql_location_table = """
        CREATE TABLE IF NOT EXISTS location (
            id INTEGER NOT NULL PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            description TEXT,
            UNIQUE (latitude, longitude)
        )"""

    sql_db_info_table = """
        CREATE TABLE IF NOT EXISTS db_info (
            schema_version INTEGER NOT NULL
        )"""

    sql_all_data_view = """
        CREATE VIEW IF NOT EXISTS all_data
        AS
        SELECT
            folder.name || "/" || file.basename || "." || file.extension AS fname,
            file.last_modified,
            meta.*,
            meta.height > meta.width as is_portrait,
            location.description as location
        FROM file
            INNER JOIN folder
                ON folder.folder_id = file.folder_id
            LEFT JOIN meta
                ON file.file_id = meta.file_id
            LEFT JOIN location
                ON location.latitude = meta.latitude AND location.longitude = meta.longitude
    """

    sql_clean_file_trigger = """
        CREATE TRIGGER IF NOT EXISTS Clean_File_Trigger
        AFTER DELETE ON folder
        FOR EACH ROW
        BEGIN
            DELETE FROM file WHERE folder_id = OLD.folder_id;
        END"""

    sql_clean_meta_trigger = """
        CREATE TRIGGER IF NOT EXISTS Clean_Meta_Trigger
        AFTER DELETE ON file
        FOR EACH ROW
        BEGIN
            DELETE FROM meta WHERE file_id = OLD.file_id;
        END"""

    schema_statements = [
        sql_folder_table,
        sql_file_table,
        sql_meta_table,
        sql_location_table,
        sql_meta_index,
        sql_all_data_view,
        sql_db_info_table,
        sql_clean_file_trigger,
        sql_clean_meta_trigger,
        "DELETE FROM db_info",
        f"INSERT INTO db_info VALUES({REQUIRED_SCHEMA_VERSION})"
    ]

    cur = db.cursor()
    for stmt in schema_statements:
        cur.execute(stmt)
    db.commit()

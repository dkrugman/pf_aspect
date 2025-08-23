import os
import time
import sqlite3
import math
import random
import logging
import requests

class NewSlideshow:
    """
    Creates a new slideshow table in the database, grouping images by orientation
    and using Random.org for shuffling if enabled in configuration.
    """

    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.model = model

        aspect_conf = model.get_aspect_config()
        random_org_conf = aspect_conf['services']['random_org']

        self.frame_id = aspect_conf.get('frame_id', 'ASPECT_001')
        self.picture_dir = os.path.expanduser(model.get_model_config()['pic_dir'])
        self.db_file = os.path.expanduser(model.get_model_config()['db_file'])

        self.api_url = random_org_conf.get("api_url")
        self.api_key = random_org_conf.get("api_key1")  # choose key rotation logic if needed
        self.daily_limit = random_org_conf.get("daily_limit", 1000)
        self.rate_limit = random_org_conf.get("rate_limit", 10)

        self.target_set_size = aspect_conf.get("target_set_size", 10)
        self.min_set_size = aspect_conf.get("min_set_size", 3)
        self.shuffle = model.get_model_config().get("shuffle", True)

        if not self.api_key:
            raise ValueError("API Key is required for Random.org integration.")
        if not os.path.isfile(self.db_file):
            raise ValueError(f"Database file '{self.db_file}' does not exist.")

    def fetch_file_ids(self):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("SELECT file_id, folder_id FROM file ORDER BY file_id")
                data = c.fetchall()    
                if not data:
                    self.__logger.warning("No files available for slideshow")
                    return None
                return data
        except Exception as e:
            self.__logger.warning(f"Error fetching file IDs: {e}")
            return []

    def fetch_random_sequence_fallback(self, n_total):
        indices = list(range(1, n_total + 1))
        random.shuffle(indices)
        return indices

    def fetch_random_sequence_large(self, n_total):
        all_randomized = []
        used_values = set()
        BATCH_LIMIT = 10000

        while len(all_randomized) < n_total:
            current_batch = min(BATCH_LIMIT, n_total - len(all_randomized))
            payload = {
                "jsonrpc": "2.0",
                "method": "generateIntegers",
                "params": {
                    "apiKey": self.api_key,
                    "n": current_batch,
                    "min": 1,
                    "max": n_total,
                    "replacement": False
                },
                "id": f"{self.frame_id}-{int(time.time()*1000)}"
            }

            try:
                self.__logger.info(f"Fetching {current_batch} random indices from Random.org...")
                res = requests.post(self.api_url, json=payload, timeout=10)
                res.raise_for_status()
                result = res.json()

                if 'error' in result:
                    raise RuntimeError(result['error'])

                new_vals = [v for v in result['result']['random']['data'] if v not in used_values]
                used_values.update(new_vals)
                all_randomized.extend(new_vals)

            except Exception as e:
                self.__logger.warning(f"Random.org failed: {e}. Falling back to local shuffle.")
                return self.fetch_random_sequence_fallback(n_total)

        return all_randomized[:n_total]

    def build_groups_dynamic(self, file_id_list, folder_map):
        portrait_ids = [fid for fid in file_id_list if folder_map[fid] == 2]
        landscape_ids = [fid for fid in file_id_list if folder_map[fid] == 1]

        dominant_ids = portrait_ids if len(portrait_ids) >= len(landscape_ids) else landscape_ids
        minority_ids = landscape_ids if dominant_ids == portrait_ids else portrait_ids
        dominant_type = 'portrait' if dominant_ids == portrait_ids else 'landscape'
        minority_type = 'landscape' if dominant_type == 'portrait' else 'portrait'

        total_images = len(file_id_list)
        num_groups = math.ceil(total_images / self.target_set_size)

        minority_groups = num_groups // 2
        dominant_groups = num_groups - minority_groups

        if minority_groups * self.min_set_size > len(minority_ids):
            minority_groups = len(minority_ids) // self.min_set_size
            dominant_groups = num_groups - minority_groups

        if minority_groups < 0 or dominant_groups < 0:
            raise ValueError("Too few images to distribute into valid groups.")

        def split_ids(ids, count):
            sizes = []
            rem = len(ids)
            for i in range(count):
                size = max(self.min_set_size, round(rem / (count - i)))
                sizes.append(size)
                rem -= size
            return sizes

        dominant_sizes = split_ids(dominant_ids, dominant_groups)
        minority_sizes = split_ids(minority_ids, minority_groups)

        groups = []
        d_idx = m_idx = 0

        for i in range(num_groups):
            if i % 2 == 0 or not minority_sizes:
                size = dominant_sizes.pop(0)
                groups.append((dominant_type, dominant_ids[d_idx:d_idx+size]))
                d_idx += size
            else:
                size = minority_sizes.pop(0)
                groups.append((minority_type, minority_ids[m_idx:m_idx+size]))
                m_idx += size

        return groups

    def save_to_slideshow(self, groups):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()

                # Drop and recreate the slideshow table
                c.execute("DROP TABLE IF EXISTS slideshow")
                c.execute("""
                    CREATE TABLE IF NOT EXISTS slideshow (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_num      INTEGER NOT NULL,
                        order_in_group INTEGER NOT NULL,
                        file_id        INTEGER NOT NULL,
                        basename       TEXT NOT NULL,
                        extension      TEXT NOT NULL,
                        orientation    TEXT NOT NULL,
                        created        REAL DEFAULT 0 NOT NULL,
                        played         INTEGER DEFAULT 0 NOT NULL
                    )
                """)

                # Get file metadata for all file IDs
                all_file_ids = []
                for g_type, ids in groups:
                    all_file_ids.extend(ids)
                
                # Fetch file metadata using row factory
                c.execute("""
                    SELECT f.file_id, f.basename, f.extension, f.width, f.height 
                    FROM file f 
                    WHERE f.file_id IN ({})
                """.format(','.join('?' * len(all_file_ids))), all_file_ids)
                
                file_metadata = {row['file_id']: (row['basename'], row['extension'], row['width'], row['height']) for row in c.fetchall()}

                insert_data = []
                for g_num, (g_type, ids) in enumerate(groups, start=1):
                    for order, file_id in enumerate(ids, start=1):
                        if file_id in file_metadata:
                            basename, extension, width, height = file_metadata[file_id]
                            orientation_text = 'portrait' if height > width else 'landscape'
                            insert_data.append((g_num, order, file_id, basename, extension, orientation_text, 0))

                c.executemany("""
                    INSERT INTO slideshow (group_num, order_in_group, file_id, basename, extension, orientation, played) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, insert_data)
                # Auto-commit when exiting the with block
        except Exception as e:
            self.__logger.warning(f"Error saving slideshow: {e}")

    def generate_slideshow(self):
        self.__logger.info("Loading image list from database...")
        
        file_data = self.fetch_file_ids()
        if not file_data:
            self.__logger.warning("Returning None")
            return None
        file_ids = [row['file_id'] for row in file_data]
        folder_map = {row['file_id']: row['folder_id'] for row in file_data}

        if self.shuffle:
            self.__logger.info("Shuffling file order using Random.org...")
            random_positions = self.fetch_random_sequence_large(len(file_ids))
            file_ids = [file_ids[i - 1] for i in random_positions]
        else:
            self.__logger.info("Shuffle disabled. Using original order.")

        self.__logger.info("Building alternating groups...")
        groups = self.build_groups_dynamic(file_ids, folder_map)

        self.__logger.info("Writing slideshow table...")
        self.save_to_slideshow(groups)

        self.__logger.info(f"Done. Created {len(groups)} groups.")

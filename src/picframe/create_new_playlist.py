import os, time, threading, sqlite3, requests, math, random, logging

Class NewPlaylist:

DB_PATH = "picframe_data.db3"
API_URL = "https://api.random.org/json-rpc/4/invoke"
API_KEY = "6ce1241d-4e32-4e54-8c7a-02654e36f6fc"
TARGET_GROUP_SIZE = 10
MIN_GROUP_SIZE = 3
BATCH_LIMIT = 10000
SHUFFLE = 1  # set to 0 to disable shuffling

def __init__(self, picture_dir, db_file):
    self.__logger = logging.getLogger("create_new_playlist.NewPlaylist")
    self.__logger.debug('Creating an instance of NewPlaylist')
    self.picture_dir = picture_dir
    self.follow_links = follow_links
    self.db_file = db_file
    self.geo_reverse = geo_reverse
    self.update_interval = update_interval

    if not os.path.exists(self.picture_dir):
        raise ValueError(f"Picture directory '{self.picture_dir}' does not exist.")

    if not os.path.isfile(self.db_file):
        raise ValueError(f"Database file '{self.db_file}' does not exist.")


def fetch_file_ids():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT file_id, folder_id FROM file ORDER BY file_id")
    data = c.fetchall()
    conn.close()
    return data

def fetch_random_sequence_fallback(n_total):
    indices = list(range(1, n_total + 1))
    random.shuffle(indices)
    return indices

def fetch_random_sequence_large(n_total):
    all_randomized = []
    used_values = set()

    while len(all_randomized) < n_total:
        current_batch = min(BATCH_LIMIT, n_total - len(all_randomized))
        payload = {
            "jsonrpc": "2.0",
            "method": "generateIntegers",
            "params": {
                "apiKey": API_KEY,
                "n": current_batch,
                "min": 1,
                "max": n_total,
                "replacement": False
            },
            "id": 12345 + len(all_randomized)
        }

        try:
            print(f"🔗 Fetching {current_batch} random indices from Random.org...")
            res = requests.post(API_URL, json=payload, timeout=10)
            res.raise_for_status()
            result = res.json()

            if 'error' in result:
                raise RuntimeError(result['error'])

            new_vals = [v for v in result['result']['random']['data'] if v not in used_values]
            used_values.update(new_vals)
            all_randomized.extend(new_vals)

        except Exception as e:
            print(f"⚠️ Random.org failed: {e}. Falling back to local shuffle.")
            return fetch_random_sequence_fallback(n_total)

    return all_randomized[:n_total]

def build_groups_dynamic(file_id_list, folder_map):
    portrait_ids = [fid for fid in file_id_list if folder_map[fid] == 2]
    landscape_ids = [fid for fid in file_id_list if folder_map[fid] == 1]

    dominant_ids = portrait_ids if len(portrait_ids) >= len(landscape_ids) else landscape_ids
    minority_ids = landscape_ids if dominant_ids == portrait_ids else portrait_ids
    dominant_type = 'portrait' if dominant_ids == portrait_ids else 'landscape'
    minority_type = 'landscape' if dominant_type == 'portrait' else 'portrait'

    total_images = len(file_id_list)
    num_groups = math.ceil(total_images / TARGET_GROUP_SIZE)

    minority_groups = num_groups // 2
    dominant_groups = num_groups - minority_groups

    if minority_groups * MIN_GROUP_SIZE > len(minority_ids):
        minority_groups = len(minority_ids) // MIN_GROUP_SIZE
        dominant_groups = num_groups - minority_groups

    if minority_groups < 0 or dominant_groups < 0:
        raise ValueError("Too few images to distribute into valid groups.")

    def split_ids(ids, count):
        sizes = []
        rem = len(ids)
        for i in range(count):
            size = max(MIN_GROUP_SIZE, round(rem / (count - i)))
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

def save_to_playlist(groups):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("DROP TABLE IF EXISTS playlist")
    c.execute("""
        CREATE TABLE playlist (
            group_num INTEGER,
            group_type TEXT,
            order_in_group INTEGER,
            file_id INTEGER
        )
    """)

    insert_data = []
    for g_num, (g_type, ids) in enumerate(groups, start=1):
        for order, file_id in enumerate(ids, start=1):
            insert_data.append((g_num, g_type, order, file_id))

    c.executemany("INSERT INTO playlist (group_num, group_type, order_in_group, file_id) VALUES (?, ?, ?, ?)", insert_data)
    conn.commit()
    conn.close()

def main():
    print("📂 Loading image list from database...")
    file_data = fetch_file_ids()
    file_ids = [fid for fid, _ in file_data]
    folder_map = {fid: folder_id for fid, folder_id in file_data}

    if SHUFFLE:
        print("🎲 Shuffling file order...")
        random_positions = fetch_random_sequence_large(len(file_ids))
        file_ids = [file_ids[i - 1] for i in random_positions]
    else:
        print("🚫 Shuffle disabled. Using original order.")

    print("🧠 Building alternating groups...")
    groups = build_groups_dynamic(file_ids, folder_map)

    print("💾 Writing playlist table...")
    save_to_playlist(groups)

    print(f"✅ Done. Created {len(groups)} groups.")

if __name__ == "__main__":
    main()

import os, sys, re, time, warnings, sqlite3, logging, urllib3
import requests

class LoginError(Exception):
    pass

class ImportPhotos:
    """Class to import photos from third-party services to local filesystem."""
    def __init__(self):
        self.username = ""
        self.password = ""
        self.login_url = "https://api.nixplay.com/www-login/"
        self.playlist_url = "https://api.nixplay.com/v3/playlists/"
        self.item_path = "slides"  # url is: playlist_url + list_id + '/' + item_path
        self.frame_key = "FR001"
        self.local_pictures_path = "~/Pictures/"

    def create_authorized_client(self, username: str, password: str, login_url: str):
        """Submits login form and returns valid session."""    
        data = {
            'email': username,
            'password': password
        }
        with requests.Session() as session:
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = session.post(login_url, headers=headers, data=data)
        return session

    def get_playlist_names(self, session, playlist_url, frame_key):
        """Retrieves playlist names that match key and last_updated_date from nixplay cloud."""
        print("Fetching playlist data from API")
        json = session.get(playlist_url).json()
        playlists = []
        for plist in json:
            if re.search(frame_key + "$", plist["playlist_name"]):
                data = {
                    "id": plist["id"],
                    "playlist_name": plist["playlist_name"],
                    "last_updated_date": plist["last_updated_date"]
                }
                playlists.append(data)
                print("playlists:", playlists)
        return playlists

    def get_single_playlist_media(self, session, playlist_url, item_path, playlist_id, playlist_name, db=None):
        """Retrieves individual media item metadata from nixplay cloud for a single playlist"""
        url = playlist_url + str(playlist_id) + '/' + item_path + '/'
        print(f"Fetching from URL: {url}")
        
        try:
            json = session.get(url).json()
            nix_lastVersion = json.get("slideshowItemsVersion")
            print("nix_lastVersion", nix_lastVersion)
            src_version = None
            if db:
                try:
                    cur = db.execute("SELECT src_version FROM imported_playlists WHERE source = ? AND playlist_id = ?", 
                                   ("nixplay", str(playlist_id)))
                    result = cur.fetchone()
                    if result:
                        src_version = result[0]
                except Exception as e:
                    print(f"Error querying database: {e}")
            
            if src_version == nix_lastVersion:
                print(f"Playlist {playlist_name} is up to date")
                return [], nix_lastVersion
            
            # Get existing media_item_id values from database
            existing_media_ids = set()
            if db:
                try:
                    cur = db.execute("SELECT media_item_id FROM imported_files WHERE source = ? AND playlist_id = ?", 
                                ("nixplay", str(playlist_id)))
                    existing_media_ids = set(row[0] for row in cur.fetchall() if row[0])
                    print(f"Found {len(existing_media_ids)} existing media items in database for playlist {playlist_name}")
                except Exception as e:
                    print(f"Error querying database: {e}")
            
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
                    if media_id not in existing_media_ids:
                        data = {
                            "mediaItemId": media_id,
                            "mediaType": slide.get("mediaType", ""),
                            "originalUrl": slide.get("originalUrl", ""),
                            "playlist_id": playlist_id,
                            "playlist_name": playlist_name
                        }
                        slides.append(data)
                    else:
                        skipped_slides += 1
            
            print(f"Playlist {playlist_name}: {total_slides} total, {len(slides)} new, {skipped_slides} already exist")
            return slides, nix_lastVersion
                
        except Exception as e:
            print(f"Error fetching media for playlist {playlist_name}: {e}")
            return [], None

    def get_playlist_media(self, session, playlist_url, item_path, playlists_to_update, db=None):
        """Retrieves individual media item metadata from nixplay cloud for multiple playlists"""
        media_items = []
        last_version = None
        
        for playlist_id, playlist_name, subdirectory in playlists_to_update:
            print(f"Fetching media for playlist: {playlist_name} (ID: {playlist_id})")
            slides, nix_lastVersion = self.get_single_playlist_media(session, playlist_url, item_path, playlist_id, playlist_name, db)
            media_items.extend(slides)
            if nix_lastVersion:
                last_version = nix_lastVersion
                
        return media_items, last_version

def wait_for_directory(path, timeout=10):
    """Waits for a directory to be created, timeout: The maximum time to wait in seconds (default: 30)."""
    start_time = time.time()
    while not os.path.exists(path):
        time.sleep(1)
        if time.time() - start_time > timeout:
            return False
    return True

def create_valid_folder_name(string):
    """Converts a string to a valid folder name."""
    string = re.sub(r'[\\/:*?"<>|]', '_', string)    # Replace invalid characters with underscores
    string = string.strip()                          # Remove leading/trailing whitespace
    return string

if __name__ == '__main__':
    print("starting")
    
    importer = ImportPhotos()                           # Instantiate ImportPhotos class with configuration 
    
    # Setup database connection
    db_file = "~/picframe_data/data/pictureframe.db3" 
    db = sqlite3.connect(os.path.expanduser(db_file), check_same_thread=False, timeout=5.0)
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    print("Database connection established")

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

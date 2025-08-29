"""
File utility functions for picframe.

Contains shared functionality for parsing filenames and extracting metadata.
"""
import os
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

def parse_filename_metadata(filename, configured_sources=None):
    """
    Parse filename to extract source and playlist information.
    
    Args:
        filename (str): The filename to parse
        configured_sources (dict, optional): Dictionary of configured sources to validate against
    
    Returns:
        tuple: (source, playlist) where both are strings or None if invalid
    """
    logger = logging.getLogger(__name__)
    
    # Parse filename to extract source and playlist
    # Format: source_playlist_...
    if isinstance(filename, Path):
        filename = filename.name
    else:
        # Ensure we only use the basename, not full path
        filename = os.path.basename(str(filename))
    
    parts = filename.split('_')
    
    if len(parts) >= 2:
        source = parts[0]
        playlist_str = parts[1]
        
        # Validate that source is in the configured sources if provided
        if configured_sources and source not in configured_sources:
            logger.warning(f"Source '{source}' not found in configured sources. Available sources: {list(configured_sources.keys())}")
            source = 'unknown'
        
        # Validate that playlist is numeric
        if playlist_str.isdigit():
            playlist = playlist_str
        else:
            playlist = None
            logger.warning(f"Playlist part '{playlist_str}' is not numeric in filename: {filename}")
    else:
        source = 'unknown'
        playlist = None
        logger.warning(f"Filename '{filename}' does not follow expected format: source-playlist-...")
    
    return source, playlist

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
    """
    Converts a UNIX timestamp (int/str) or ISO 8601 string to UTC ISO format,
    auto-detecting millisecond/microsecond inputs.
    """
    if isinstance(timestamp, str):
        try:
            # Try ISO 8601 parsing
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.isoformat()
        except ValueError:
            timestamp = int(timestamp)

    elif isinstance(timestamp, (float, int)):
        timestamp = int(timestamp)
    else:
        raise ValueError(f"Unsupported timestamp type: {timestamp}")

    # Adjust if timestamp is in ms or us
    if timestamp > 1e14:
        timestamp = timestamp / 1e6
    elif timestamp > 1e11:
        timestamp = timestamp / 1e3

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()

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
"""
video_meta_utils.py

Provides utility to extract video metadata for ImageCache ingestion.
"""

import os
from .video_streamer import get_video_info

def get_video_metadata(file_path_name: str) -> dict:
    """
    Extracts metadata information from a video file for database insertion.

    Args:
        file_path_name (str): Full path to the video file.

    Returns:
        dict: Dictionary containing meta keys matching 'meta' table fields.
    """
    meta = get_video_info(file_path_name)

    e: dict = {}
    e['orientation'] = 1  # default for video

    width, height = meta.dimensions
    e['width'] = width
    e['height'] = height

    e['f_number'] = getattr(meta, 'f_number', None)
    e['make'] = getattr(meta, 'make', None)
    e['model'] = getattr(meta, 'model', None)
    e['exposure_time'] = getattr(meta, 'exposure_time', None)
    e['iso'] = getattr(meta, 'iso', None)
    e['focal_length'] = getattr(meta, 'focal_length', None)
    e['rating'] = getattr(meta, 'rating', None)
    e['lens'] = getattr(meta, 'lens', None)
    e['exif_datetime'] = getattr(meta, 'exif_datetime', None) or os.path.getmtime(file_path_name)

    if meta.gps_coords is not None:
        lat, lon = meta.gps_coords
    else:
        lat, lon = None, None

    e['latitude'] = round(lat, 4) if lat is not None else None
    e['longitude'] = round(lon, 4) if lon is not None else None

    e['tags'] = getattr(meta, 'tags', None)
    e['title'] = getattr(meta, 'title', None)
    e['caption'] = getattr(meta, 'caption', None)

    return e

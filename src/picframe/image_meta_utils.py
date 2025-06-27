import os
import re
from datetime import datetime
from picframe import get_image_meta

def get_exif_info(file_path_name: str) -> dict:
    meta = get_image_meta(file_path_name)
    
    # Dict to store interesting EXIF data
    # Note, the 'key' must match a field in the 'meta' table
    e: dict = {}

    # Orientation is required, default = 1
    e['orientation'] = int(meta.get('EXIF Orientation', 1))

    # Dimensions
    e['width'] = int(meta.get('EXIF ExifImageWidth', 0))
    e['height'] = int(meta.get('EXIF ExifImageHeight', 0))

    # EXIF Data
    e['f_number'] = float(meta.get('EXIF FNumber', 0))
    e['make'] = meta.get('Image Make', None)
    e['model'] = meta.get('Image Model', None)
    e['exposure_time'] = meta.get('EXIF ExposureTime', None)
    e['iso'] = float(meta.get('EXIF ISOSpeedRatings', 0))
    e['focal_length'] = meta.get('EXIF FocalLength', None)
    e['rating'] = int(meta.get('EXIF Rating', 0))
    e['lens'] = meta.get('EXIF LensModel', None)
    
    if 'EXIF DateTimeOriginal' in meta:
        try:
            dt_str = meta['EXIF DateTimeOriginal']
            e['exif_datetime'] = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S').timestamp()
        except Exception:
            e['exif_datetime'] = os.path.getmtime(file_path_name)
    else:
        e['exif_datetime'] = os.path.getmtime(file_path_name)

    # GPS
    if meta.get('GPS GPSLatitude') and meta.get('GPS GPSLongitude'):
        try:
            lat_str = meta['GPS GPSLatitude']
            lon_str = meta['GPS GPSLongitude']
            e['latitude'] = float(lat_str.split()[0])
            e['longitude'] = float(lon_str.split()[0])
        except Exception:
            e['latitude'], e['longitude'] = None, None
    else:
        e['latitude'], e['longitude'] = None, None

    # IPTC
    e['tags'] = meta.get('IPTC Keywords', None)
    e['title'] = meta.get('IPTC Object Name', None)
    e['caption'] = meta.get('IPTC Caption/Abstract', None)

    return e
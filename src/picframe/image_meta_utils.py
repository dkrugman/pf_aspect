from picframe.get_image_meta import GetImageMeta
import os
from datetime import datetime

def get_exif_info(file_path_name: str) -> dict:
    meta_obj = GetImageMeta(file_path_name)
    
    e: dict = {}
    e['orientation'] = meta_obj.get_exif('EXIF Orientation') or 1

    # Dimensions
    e['width'], e['height'] = meta_obj.size

    # EXIF Data
    e['f_number'] = meta_obj.get_exif('EXIF FNumber') or 0
    e['make'] = meta_obj.get_exif('Image Make')
    e['model'] = meta_obj.get_exif('Image Model')
    e['exposure_time'] = meta_obj.get_exif('EXIF ExposureTime')
    e['iso'] = meta_obj.get_exif('EXIF ISOSpeedRatings') or 0
    e['focal_length'] = meta_obj.get_exif('EXIF FocalLength')
    e['rating'] = meta_obj.get_exif('EXIF Rating') or 0
    e['lens'] = meta_obj.get_exif('EXIF LensModel')
    
    dt_str = meta_obj.get_exif('EXIF DateTimeOriginal')
    if dt_str:
        try:
            e['exif_datetime'] = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S').timestamp()
        except Exception:
            e['exif_datetime'] = os.path.getmtime(file_path_name)
    else:
        e['exif_datetime'] = os.path.getmtime(file_path_name)

    # GPS
    loc = meta_obj.get_location()
    e['latitude'] = loc['latitude']
    e['longitude'] = loc['longitude']

    # IPTC
    e['tags'] = meta_obj.get_exif('IPTC Keywords')
    e['title'] = meta_obj.get_exif('IPTC Object Name')
    e['caption'] = meta_obj.get_exif('IPTC Caption/Abstract')

    return e
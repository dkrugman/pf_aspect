import logging
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from fractions import Fraction

try:
    import iptcinfo3
    from iptcinfo3 import IPTCInfo
    logging.getLogger("iptcinfo3").setLevel(logging.ERROR)                  # turn off useless log infos
except ImportError:
    iptcinfo3 = None

try:
    from pi_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    register_heif_opener = None

class GetImageMeta:
    """
    Extracts image metadata (EXIF, GPS, IPTC, XMP) and dimensions from supported formats.
    Provides access to parsed tags and image size for database insertion and display logic.
    """
    def __init__(self, filename):
        self.__logger = logging.getLogger(__name__)
        self.__tags = {}
        self.__filename = filename                                          # in case no exif data in which case needed for size
        self.__image_width: int = 0
        self.__image_height: int = 0
        image = self.get_image_object(filename)
        if image:
            self.__image_width, self.__image_height = image.size
            exif = image.getexif()
            self.__do_image_tags(exif)
            self.__do_exif_tags(exif)
            self.__do_geo_tags(exif)
            if iptcinfo3:
                self.__do_iptc_keywords()
            try:
                xmp = image.getxmp()
                if len(xmp) > 0:
                    self.__do_xmp_keywords(xmp)
            except Exception as e:
                xmp = {}
                self.__logger.warning("PILL getxmp() failed: %s -> %s", filename, e)
 
    @property
    def size(self) -> tuple[int, int]:
        """Returns the (width, height) tuple of the image."""
        return self.__image_width, self.__image_height

    def __do_image_tags(self, exif):
        tags = {
            "Image " + str(TAGS.get(key, key)): value
            for key, value in exif.items()
        }
        self.__tags.update(tags)

    def __do_exif_tags(self, exif):
        for key, value in TAGS.items():
            if value == "ExifOffset":
                break
        info = exif.get_ifd(key)
        tags = {
            "EXIF " + str(TAGS.get(key, key)): value
            for key, value in info.items()
        }
        self.__tags.update(tags)

    def __do_geo_tags(self, exif):
        for key, value in TAGS.items():
            if value == "GPSInfo":
                break
        gps_info = exif.get_ifd(key)
        tags = {
            "GPS " + str(GPSTAGS.get(key, key)): value
            for key, value in gps_info.items()
        }
        self.__tags.update(tags)

    def __find_xmp_key(self, key, dic):
        for k, v in dic.items():
            if key == k:
                return v
            elif isinstance(v, dict):
                val = self.__find_xmp_key(key, v)
                if val:
                    return val
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        val = self.__find_xmp_key(key, x)
                        if val:
                            return val
        return None

    def __do_xmp_keywords(self, xmp):
        try:
            val = self.__find_xmp_key('Headline', xmp)                      # title
            if val and isinstance(val, str) and len(val) > 0:
                self.__tags['IPTC Object Name'] = val
            try:                                                            # caption
                val = self.__find_xmp_key('description', xmp)
                if val:
                    val = val['Alt']['li']['text']
                    if val and isinstance(val, str) and len(val) > 0:
                        self.__tags['IPTC Caption/Abstract'] = val
            except KeyError:
                pass
            try:
                val = self.__find_xmp_key('subject', xmp)                   # tags
                if val:
                    val = val['Bag']['li']
                    if val and isinstance(val, list) and len(val) > 0:
                        self.__tags['IPTC Keywords'] = ",".join(val)
            except KeyError:
                pass
        except Exception as e:
            self.__logger.warning("xmp loading has failed: %s -> %s", self.__filename, e)

    def __do_iptc_keywords(self):
        # Skip .jxl files as iptcinfo3 doesn't support them
        if self.__filename.lower().endswith('.jxl'):
            self.__logger.debug("Skipping IPTC processing for .jxl file: %s", self.__filename)
            return
            
        try:
            with open(self.__filename, 'rb') as fh:
                iptc = IPTCInfo(fh, force=True, out_charset='utf-8')        # TODO put IPTC read in separate function
                val = iptc['keywords']                                      # tags
                if val is not None and len(val) > 0:
                    keywords = ''
                    for key in iptc['keywords']:
                        keywords += key.decode('utf-8') + ','               # decode binary strings
                    self.__tags['IPTC Keywords'] = keywords
                val = iptc['caption/abstract']                              # caption
                if val is not None and len(val) > 0:                        # title
                    self.__tags['IPTC Caption/Abstract'] = iptc['caption/abstract'].decode('utf8')
                val = iptc['object name']                                   
                if val is not None and len(val) > 0:
                    self.__tags['IPTC Object Name'] = iptc['object name'].decode('utf-8')
        except Exception as e:
            self.__logger.warning("IPTC loading has failed - if you want to use this you will need to install iptcinfo3 %s -> %s",  # noqa: E501
                                  self.__filename, e)

    def has_exif(self):
        return bool(self.__tags)

    def __get_if_exist(self, key):
        if key in self.__tags:
            return self.__tags[key]
        return None

    def __convert_to_degrees(self, value):
        try:
            deg, min, sec = value
            return deg + (min / 60.0) + (sec / 3600.0)
        except Exception as e:
                self.__logger.warning("Failed to convert GPS value: %s -> %s", value, e)
                return None

    def get_location(self):
        gps = {"latitude": None, "longitude": None}
        lat = None
        lon = None

        gps_latitude = self.__get_if_exist('GPS GPSLatitude')
        gps_latitude_ref = self.__get_if_exist('GPS GPSLatitudeRef')
        gps_longitude = self.__get_if_exist('GPS GPSLongitude')
        gps_longitude_ref = self.__get_if_exist('GPS GPSLongitudeRef')

        try:
            if gps_latitude and gps_latitude_ref and gps_longitude and gps_longitude_ref:
                lat = self.__convert_to_degrees(gps_latitude)
                if len(gps_latitude_ref) > 0 and gps_latitude_ref[0] == 'S':
                    # assume zero length string means N
                    lat = 0 - lat
                gps["latitude"] = lat
                lon = self.__convert_to_degrees(gps_longitude)
                if len(gps_longitude_ref) and gps_longitude_ref[0] == 'W':
                    lon = 0 - lon
                gps["longitude"] = lon
        except Exception as e:
            self.__logger.warning("get_location failed on %s -> %s", self.__filename, e)
        return gps

    def get_orientation(self):
        try:
            val = self.__get_if_exist('Image Orientation')
            if val is not None:
                return val
            else:
                return 1
        except Exception as e:
            self.__logger.warning("get_orientation failed on %s -> %s", self.__filename, e)
            return 1

    def get_exif(self, key):
        try:                                                                # ISO prior 2.2, ISOSpeedRatings 2.2, PhotographicSensitivity 2.3
            iso_keys = ['EXIF ISOSpeedRatings', 'EXIF PhotographicSensitivity', 'EXIF ISO']
            if key in iso_keys:
                for iso in iso_keys:
                    val = self.__get_if_exist(iso)
                    if val:
                        if type(val) is tuple:                               # If ISO is returned as a tuple, take the first element
                            val = val[0]
                        break
            else:
                val = self.__get_if_exist(key)

            if val is None:
                grp, tag = key.split(" ", 1)
                if grp == "EXIF":
                    newkey = "Image" + " " + tag
                    val = self.__get_if_exist(newkey)
                elif grp == "Image":
                    newkey = "EXIF" + " " + tag
                    val = self.__get_if_exist(newkey)
            if val:
                if key == "EXIF ExposureTime":
                    val = str(Fraction(val))
                elif key == "EXIF FocalLength":
                    val = str(val)
                elif key == "EXIF FNumber":
                    val = float(val)
                return val
        except Exception as e:
            self.__logger.warning("get_exif failed on %s -> %s", self.__filename, e)
            return None

    @staticmethod
    def get_image_object(fname):
        try:
            image = Image.open(fname)
            if image.mode not in ("RGB", "RGBA"):                           # mat system needs RGB or more
                image = image.convert("RGB")
        except Exception as e:                                              # the system should be able to withstand files being moved etc without crashing
            logger = logging.getLogger(__name__)
            logger.warning("Can't open file: \"%s\"", fname)
            logger.warning("Cause: %s", e)
            image = None
        return image

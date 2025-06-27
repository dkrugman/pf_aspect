"""
Controller of picframe.

Controls image display, manages state, handles MQTT and HTTP interfaces.
"""

import logging
import time
import signal
import ssl
import asyncio
from datetime import datetime
from .async_timer import init_timer
from picframe.interface_peripherals import InterfacePeripherals
from picframe import import_photos

def make_date(txt: str) -> float:
    try:
        return datetime.strptime(txt, "%Y/%m/%d").timestamp()
    except ValueError:
        raise ValueError(f"Invalid date format: {txt}")

class Controller:
    """Controller of picframe."""

    def __init__(self, model, viewer):
        self.__logger = logging.getLogger(__name__)
        self.__logger.setLevel(model.get_model_config()['log_level'])
        self.__logger.info('Creating an instance of Controller')

        self.__model = model
        self.__viewer = viewer
        self.__http_config = model.get_http_config()
        self.__mqtt_config = model.get_mqtt_config()
        self.__time_delay = model.time_delay
        self.__import_interval = model.get_aspect_config()['import_interval']

        self.__paused = False
        self.__force_navigate = False
        self.__date_from = make_date('1901/12/15')
        self.__date_to = make_date('2038/1/1')

        self.publish_state = lambda x, y: None
        self.keep_looping = True

        self.__interface_peripherals = None
        self.__interface_mqtt = None
        self.__interface_http = None

    @property
    def paused(self):
        return self.__paused

    @paused.setter
    def paused(self, val: bool):
        self.__paused = val
        if self.__viewer.is_video_playing():
            self.__viewer.pause_video(val)
        pic = self.__model.get_current_pic()
        self.__viewer.reset_name_tm(pic, val)
        if self.__mqtt_config['use_mqtt']:
            self.publish_state()

    async def next(self):
        if self.paused:
            return

        if self.__viewer.is_video_playing():
            self.__viewer.stop_video()

        self.__viewer.reset_name_tm()
        pic = self.__model.get_next_file()
        if pic is None:
            self.__logger.warning("No image found.")
            return

        self.__logger.info("ADVANCE: %s", pic.fname)
        image_attr = self._build_image_attr(pic)
        if self.__mqtt_config['use_mqtt']:
            self.publish_state(pic.fname, image_attr)

        time_delay = self.__model.time_delay
        fade_time = self.__model.fade_time

        self.__model.pause_looping = self.__viewer.is_in_transition()
        self.__logger.debug("Slideshow transition: %s", pic.fname if pic else 'None')

        _, skip_image, video_playing = self.__viewer.slideshow_transition(pic, time_delay, fade_time, self.__paused)
        if skip_image or video_playing:
            self.__logger.debug("Skipping image or extending video playback.")

    def _build_image_attr(self, pic):
        image_attr = {}
        for key in self.__model.get_model_config()['image_attr']:
            if key == 'PICFRAME GPS':
                image_attr['latitude'] = pic.latitude
                image_attr['longitude'] = pic.longitude
            elif key == 'PICFRAME LOCATION':
                image_attr['location'] = pic.location
            else:
                field_name = self.__model.EXIF_TO_FIELD[key]
                image_attr[key] = getattr(pic, field_name, None)
        return image_attr

    async def back(self):
        if self.__viewer.is_video_playing():
            self.__viewer.stop_video()
        else:
            self.__force_navigate = True
        self.__model.set_next_file_to_previous_file()
        self.__viewer.reset_name_tm()

    def delete(self) -> None:
        if self.__viewer.is_video_playing():
            self.__viewer.stop_video()
        self.__model.delete_file()
        asyncio.create_task(self.next())

    def purge_files(self):
        self.__model.purge_files()

    async def import_wrapper(self):
        try:
            await self._import_photos.check_for_updates()
        except Exception as e:
            self.__logger.exception(f"Import task failed: {e}")

    async def start(self):
        signal.signal(signal.SIGINT, self.__signal_handler)
        self.__viewer.slideshow_start()
        self.__interface_peripherals = InterfacePeripherals(self.__model, self.__viewer, self)
        self._import_photos = import_photos.ImportPhotos(self.__model)
        self._import_task = asyncio.create_task(self.import_wrapper())

        self.__timer = init_timer(self.__model)
        self.__timer.register(self.next, interval=self.__time_delay, name='slideshow')
        self.__timer.register(self._import_photos.check_for_updates, interval=self.__import_interval, name='import')
        self.__timer.start()

        if self.__mqtt_config['use_mqtt']:
            from picframe import interface_mqtt
            try:
                self.__interface_mqtt = interface_mqtt.InterfaceMQTT(self, self.__mqtt_config)
            except Exception as e:
                self.__logger.error("Can't initialize MQTT: %s. Continuing without MQTT.", e)

        if self.__http_config['use_http']:
            from picframe import interface_http
            model_config = self.__model.get_model_config()
            self.__interface_http = interface_http.InterfaceHttp(
                self,
                self.__http_config['path'],
                model_config['pic_dir'],
                model_config['no_files_img'],
                self.__http_config['port'],
                self.__http_config['auth'],
                self.__http_config['username'],
                self.__http_config['password'],
            )
            if self.__http_config['use_ssl']:
                self.__interface_http.socket = ssl.wrap_socket(
                    self.__interface_http.socket,
                    keyfile=self.__http_config['keyfile'],
                    certfile=self.__http_config['certfile'],
                    server_side=True)

    def stop(self):
        self.keep_looping = False
        self.__interface_peripherals.stop()
        if self.__interface_mqtt:
            self.__interface_mqtt.stop()
        if self.__interface_http:
            self.__interface_http.stop()
        self.__model.stop_image_cache()
        self.__viewer.slideshow_stop()

    def __signal_handler(self, sig, frame):
        msg = 'Ctrl-c pressed, stopping picframe...' if sig == signal.SIGINT else f'Signal {sig} received, stopping picframe...'
        self.__logger.info(msg)
        self.keep_looping = False

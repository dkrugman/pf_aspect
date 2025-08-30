"""
Controller of picframe.

Controls image display, manages state, handles MQTT and HTTP interfaces.
"""

import asyncio
import logging
import os
import signal
import ssl
import sys
from datetime import datetime

from . import import_photos, process_images
from .async_timer import init_timer
from .interface_peripherals import InterfacePeripherals


def make_date(txt: str) -> float:
    try:
        return datetime.strptime(txt, "%Y/%m/%d").timestamp()
    except ValueError:
        raise ValueError(f"Invalid date format: {txt}")


class Controller:
    """Controller of picframe."""

    def __init__(self, model, viewer):
        self.__logger = logging.getLogger(__name__)
        self.__logger.setLevel(model.get_model_config()["log_level"])
        self.__logger.debug("Creating an instance of Controller")

        self.__model = model
        self.__viewer = viewer
        self.__http_config = model.get_http_config()
        self.__mqtt_config = model.get_mqtt_config()
        self.__time_delay = model.time_delay
        self.__import_interval = model.get_aspect_config()["import_interval"]
        self.__process_interval = model.get_aspect_config()["process_interval"]
        self.__paused = False
        self.__force_navigate = False
        self.__date_from = make_date("1901/12/15")
        self.__date_to = make_date("2038/1/1")

        self.publish_state = lambda x, y: None
        self.keep_looping = True
        self._stop_called = False  # Flag to prevent duplicate stop calls

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
        if self.__mqtt_config["use_mqtt"]:
            self.publish_state()

    async def next(self):
        self.__logger.debug("Timer fired: next() called")
        if self.paused:
            self.__logger.debug("Slideshow is paused, returning")
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
        if self.__mqtt_config["use_mqtt"]:
            self.publish_state(pic.fname, image_attr)

        time_delay = self.__model.time_delay
        fade_time = self.__model.fade_time

        self.__model.pause_looping = self.__viewer.is_in_transition()
        self.__logger.debug("Slideshow transition: %s", pic.fname if pic else "None")

        _, skip_image, video_playing = self.__viewer.slideshow_transition(pic, time_delay, fade_time, self.__paused)
        if skip_image or video_playing:
            self.__logger.debug("Skipping image or extending video playback.")

    def _build_image_attr(self, pic):
        image_attr = {}
        for key in self.__model.get_model_config()["image_attr"]:
            if key == "PICFRAME GPS":
                image_attr["latitude"] = pic.latitude
                image_attr["longitude"] = pic.longitude
            elif key == "PICFRAME LOCATION":
                image_attr["location"] = pic.location
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

    async def import_wrapper(self, model):
        try:
            # Create async importer
            async with self._import_photos as importer:
                # Check if already importing
                if importer.is_importing():
                    self.__logger.debug("Import already in progress...")
                    return

                # Start the import process
                self.__logger.info("Starting async photo import...")
                await importer.check_for_updates()
                self.__logger.info("Import completed!")

        except Exception as e:
            self.__logger.exception(f"Import task failed: {e}")

    # async def import_wrapper(self):
    #     try:
    #         await self._import_photos.check_for_updates()
    #     except Exception as e:
    #         self.__logger.exception(f"Import task failed: {e}")

    async def start(self):
        # Check for other picframe processes before starting
        self._check_for_duplicate_picframe()

        self.__viewer.slideshow_start()
        self.__interface_peripherals = InterfacePeripherals(self.__model, self.__viewer, self)
        self._import_photos = import_photos.ImportPhotos(self.__model)
        # Remove immediate import task - timer will handle initial import
        self._process_images = process_images.ProcessImages(self.__model)

        self.__timer = init_timer(self.__model)
        self.__timer.register(self.next, interval=self.__time_delay, name="slideshow")
        self.__timer.register(self._import_photos.check_for_updates, interval=self.__import_interval, name="import")
        self.__timer.register(
            self._process_images.process_images, interval=self.__process_interval, name="process_images"
        )
        self.__timer.start()

        if self.__mqtt_config["use_mqtt"]:
            from . import interface_mqtt

            try:
                self.__interface_mqtt = interface_mqtt.InterfaceMQTT(self, self.__mqtt_config)
            except Exception as e:
                self.__logger.error("Can't initialize MQTT: %s. Continuing without MQTT.", e)

        if self.__http_config["use_http"]:
            from . import interface_http

            model_config = self.__model.get_model_config()
            try:
                self.__interface_http = interface_http.InterfaceHttp(
                    self,
                    self.__http_config["path"],
                    model_config["pic_dir"],
                    model_config["no_files_img"],
                    self.__http_config["port"],
                    self.__http_config["auth"],
                    self.__http_config["username"],
                    self.__http_config["password"],
                )
                if self.__http_config["use_ssl"]:
                    self.__interface_http.socket = ssl.wrap_socket(
                        self.__interface_http.socket,
                        keyfile=self.__http_config["keyfile"],
                        certfile=self.__http_config["certfile"],
                        server_side=True,
                    )
            except OSError as e:
                if "Address already in use" in str(e):
                    self.__logger.error(
                        f"HTTP interface cannot start: Port {self.__http_config['port']} is already in use."
                    )

                    # Check if this is another picframe process
                    if "picframe process" in str(e):
                        if self.__http_config.get("auto_restart_on_conflict", True):
                            self.__logger.warning(
                                "Detected another picframe process running. "
                                "Automatically restarting to resolve the conflict..."
                            )
                            self.__interface_http = None
                            # Schedule automatic restart
                            asyncio.create_task(self._auto_restart())
                        else:
                            self.__logger.error(
                                "Port conflict detected but auto-restart is disabled. "
                                "Continuing without HTTP interface."
                            )
                            self.__interface_http = None
                    else:
                        self.__logger.error(
                            "Port is occupied by an unknown process. Continuing without HTTP interface."
                        )
                        self.__interface_http = None
                else:
                    self.__logger.error(f"HTTP interface failed to start: {e}. Continuing without HTTP interface.")
                    self.__interface_http = None
            except Exception as e:
                self.__logger.error(f"Can't initialize HTTP interface: {e}. Continuing without HTTP interface.")
                self.__interface_http = None

    def _check_for_duplicate_picframe(self):
        """Check if there are other picframe processes running and provide guidance."""
        other_pids = self._get_other_picframe_pids()

        if other_pids:
            self.__logger.warning(f"Found other picframe processes running: PIDs {', '.join(other_pids)}")
            self.__logger.warning("This Raspberry Pi is dedicated to picframe, so only one instance should be running.")
            self.__logger.warning("Consider stopping the other processes or restarting the system.")
        # else:
        # self.__logger.debug("No other picframe processes detected.")

    def _get_other_picframe_pids(self):
        """Get list of other picframe process PIDs (excluding current process)."""
        import subprocess

        try:
            # Get all processes with picframe in command line
            result = subprocess.run(["pgrep", "-f", "picframe"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                pids = result.stdout.strip().split("\n")
                current_pid = os.getpid()
                other_pids = []

                for pid in pids:
                    if not pid or not pid.strip() or pid == str(current_pid):
                        continue

                    # Get the full command line for this process
                    try:
                        cmd_result = subprocess.run(
                            ["ps", "-p", pid, "-o", "args="], capture_output=True, text=True, timeout=2
                        )
                        if cmd_result.returncode == 0:
                            cmd_line = cmd_result.stdout.strip()

                            # Exclude:
                            # 1. Python picframe processes
                            # 2. TCL unbuffer processes
                            # 3. Subprocess.run processes for checking running processes (ps, pgrep)
                            exclude = (
                                ("python" in cmd_line and "picframe" in cmd_line)
                                or
                                # TCL unbuffer processes
                                ("tcl" in cmd_line and "unbuffer" in cmd_line and "picframe" in cmd_line)
                                or ("pgrep" in cmd_line and "picframe" in cmd_line)
                                or ("ps" in cmd_line and "-p" in cmd_line and "args=" in cmd_line)
                            )

                            if not exclude:
                                other_pids.append(pid)

                    except Exception:
                        # If we can't get command line, skip this process
                        continue

                return other_pids
            return []
        except Exception as e:
            self.__logger.debug(f"Could not check for other picframe processes: {e}")
            return []

    async def _auto_restart(self):
        """Automatically restart picframe to resolve port conflicts."""
        import os
        import sys

        self.__logger.info("Initiating automatic picframe restart...")

        try:
            # Get the current script path and arguments
            script_path = sys.argv[0] if hasattr(sys, "argv") and sys.argv else None

            if script_path and os.path.exists(script_path):
                self.__logger.debug("Replacing current process with new picframe instance...")

                # Use exec to replace the current process instead of starting a new one
                # This ensures only one process exists at a time
                restart_cmd = [sys.executable, script_path] + sys.argv[1:]

                # Replace the current process with the new one
                os.execv(sys.executable, restart_cmd)
                # Note: execv never returns if successful
            else:
                self.__logger.error("Could not determine script path for restart. Please restart manually.")

        except Exception as e:
            self.__logger.error(f"Automatic restart failed: {e}. Please restart manually.")
            # Continue running without HTTP interface rather than crashing

    def stop(self):
        self.__logger.debug("Stopping picframe controller...")
        self.keep_looping = False

        # Stop timer FIRST to cancel all running tasks (including import)
        if hasattr(self, "_Controller__timer") and self._Controller__timer:
            try:
                self._Controller__timer.stop()
                self.__logger.debug("Controller timer stopped")
            except Exception as e:
                self.__logger.error(f"Error stopping controller timer: {e}")
        else:
            self.__logger.warning("No controller timer found")

        # Import task is now handled by timer, no need to cancel separately

        # Cleanup ImportPhotos database connections
        if hasattr(self, "_import_photos") and self._import_photos:
            self.__logger.debug("Cleaning up ImportPhotos...")
            try:
                self._import_photos.cleanup()
            except Exception as e:
                self.__logger.error(f"Error cleaning up ImportPhotos: {e}")

        # Stop peripheral interface if it exists
        if hasattr(self, "__interface_peripherals") and self.__interface_peripherals:
            self.__logger.debug("Stopping peripheral interface...")
            try:
                self.__interface_peripherals.stop()
            except Exception as e:
                self.__logger.error(f"Error stopping peripheral interface: {e}")

        # Stop MQTT interface if it exists
        if hasattr(self, "__interface_mqtt") and self.__interface_mqtt:
            self.__logger.debug("Stopping MQTT interface...")
            try:
                self.__interface_mqtt.stop()
            except Exception as e:
                self.__logger.error(f"Error stopping MQTT interface: {e}")

        # Stop HTTP interface if it exists
        if hasattr(self, "__interface_http") and self.__interface_http:
            self.__logger.debug("Stopping HTTP interface...")
            try:
                self.__interface_http.stop()
            except Exception as e:
                self.__logger.error(f"Error stopping HTTP interface: {e}")

        # Stop model components
        try:
            self.__model.stop_image_cache()
        except Exception as e:
            self.__logger.error(f"Error stopping image cache: {e}")

        # Stop slideshow
        try:
            self.__viewer.slideshow_stop()
        except Exception as e:
            self.__logger.error(f"Error stopping slideshow: {e}")

        # Re-enable cursor visibility
        try:
            sys.stdout.write("\x1b[?7h")
        except Exception as e:
            self.__logger.error(f"Error re-enabling cursor: {e}")

        self.__logger.debug("Picframe controller stopped successfully")

    def __signal_handler(self, sig, frame):
        msg = (
            "Ctrl-c pressed, stopping picframe..."
            if sig == signal.SIGINT
            else f"Signal {sig} received, stopping picframe..."
        )
        self.stop()
        self.__logger.info(msg)
        self.keep_looping = False

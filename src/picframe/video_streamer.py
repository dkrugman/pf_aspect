#!/usr/bin/python
import numpy as np
import ctypes
import subprocess
import threading
import signal
import time
import json
import vlc
import sys

class VideoInfo:
    def __init__(self, video_path): # TODO put whole thing in try/catch in case of different formats?
        self._parseReady = False
        self.player = self.width = self.height = self.fps = self.duration = None

        instance = vlc.Instance('--no-audio', "--vout=drm_vout", "--avcodec-hw=mmal")
        self.player = instance.media_player_new()
        media = instance.media_new_path(video_path)
        self.player.set_media(media)
        events = media.event_manager()
        events.event_attach(vlc.EventType.MediaParsedChanged, self.ParseReceived)
        media.parse_with_options(1, 0)
        while self._parseReady == False:
            time.sleep(0.1)
        self.width, self.height = self.player.video_get_size()
        self.duration = media.get_duration() # Total duration in milliseconds
        self.fps = self.player.get_fps()
    
    def  ParseReceived(self, event):
        self._parseReady = True


class VideoStreamer:
    def __init__(self, video_path):
        self.flag = False # use to signal new texture
        self.kill_thread = False
        self.pause_thread = False
        self._lock = threading.Lock()
        video_info = VideoInfo(video_path)
        self.frame_time = time.time()
        if video_info.width is not None:
            self.player = video_info.player
            self.W = video_info.width
            self.H = video_info.height
            self.fps = video_info.fps
            self.duration = video_info.duration
            self.paused_time = 0.0
            self.P = 3
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.image.shape = (self.H, self.W, self.P)
            self.image_p = self.image.ctypes.data_as(ctypes.c_void_p)
            self.image_initialized = False
            self.t = threading.Thread(target=self.pipe_thread)
            self.t.start()
        else: # couldn't get dimensions for some reason - assume not able to read video
            self.player = None
            self.W = 240
            self.H = 180
            self.P = 3
            self.fps = 1.0
            self.duration = 0.0
            self.paused_time = 0.0
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.image.shape = (self.H, self.W, self.P)
            self.image_p = self.image.ctypes.data_as(ctypes.c_void_p)
            self.image_initialized = False
            self.t = None


    def get_libvlc_lock_callback(self):
        @vlc.CallbackDecorators.VideoLockCb
        def _cb(opaque, planes):
            self._lock.acquire()
            planes[0] = self.image_p
        return _cb

    def get_libvlc_unlock_callback(self):
        @vlc.CallbackDecorators.VideoUnlockCb
        def _cb(opaque, picta, planes):
            self.image_initialized = True
            self._lock.release()
        return _cb

    def pipe_thread(self):
            paused = False


            # need to keep a reference to the CFUNCTYPEs or else it will get GCed
            _lock_cb = self.get_libvlc_lock_callback()
            _unlock_cb = self.get_libvlc_unlock_callback()
            
            test_player = False
            if (test_player) :
                self.player.set_fullscreen(True)
            else :
                self.player.video_set_callbacks(_lock_cb, _unlock_cb, None, None)

            self.player.video_set_format(
                "RV24", # this is basically RGB
                self.W,
                self.H,
                self.W * self.P)

            # frame_duration = 1.0 / self.fps
            frame_duration = 1.0 / 10.0
            last_frame_time = time.time()
            
             # this starts populating the surface's buf with pixels, from another thread
            self.player.play()
            while self.player.get_state() != vlc.State.Ended and self.kill_thread == False:

                if not paused and self.pause_thread: # pause
                    paused = True
                    self.player.set_pause(1)
                elif paused and not self.pause_thread: # resume
                    paused = False
                    self.player.set_pause(0)

                if not paused:
                    if self.image_initialized:
                        current_time = time.time()
                        elapsed_time = current_time - last_frame_time
                                            
                        if elapsed_time >= frame_duration:
                            last_frame_time = current_time
                            print('elapsed time {}'.format(time.time() - self.frame_time), file=sys.stderr)
                            self.frame_time = time.time()
                            self.flag = True
                        else:
                            time.sleep(frame_duration - elapsed_time)
                    else:
                        self.paused_time += 0.25
                        time.sleep(0.25)
            self.player.stop()

    def kill(self):
        self.kill_thread = True
        if self.t is not None:
            self.t.join()
        del self.image

    def pause(self):
        self.pause_thread = True

    def restart(self):
        self.pause_thread = False

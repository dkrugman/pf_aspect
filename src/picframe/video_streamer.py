#!/usr/bin/python
import numpy as np
import subprocess
import threading
import signal
import time
import json
import vlc


class VideoInfo:
    def __init__(self, video_path): # TODO put whole thing in try/catch in case of different formats?
        self._parseReady = False
        self.player = self.width = self.height = self.fps = self.duration = None

        instance = vlc.Instance('--no-audio')
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
        self.command = [ 'ffmpeg', '-i', video_path, '-f', 'image2pipe',
                        '-pix_fmt', 'rgb24', '-vcodec', 'rawvideo', '-']
        video_info = VideoInfo(video_path)
        if video_info.width is not None:
            self.W = video_info.width
            self.H = video_info.height
            self.fps = video_info.fps
            self.duration = video_info.duration
            self.paused_time = 0.0
            self.P = 3
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.t = threading.Thread(target=self.pipe_thread)
            self.t.start()
        else: # couldn't get dimensions for some reason - assume not able to read video
            self.W = 240
            self.H = 180
            self.P = 3
            self.fps = 1.0
            self.duration = 0.0
            self.paused_time = 0.0
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.t = None

    def pipe_thread(self):
        while not self.kill_thread:
            paused = False
            with subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1) as pipe:
                while pipe.poll() is None and not self.kill_thread:
                    if not paused and self.pause_thread: # stop thread running
                        paused = True
                        pipe.send_signal(signal.SIGSTOP)
                    elif paused and not self.pause_thread: # continue thread running
                        paused = False
                        pipe.send_signal(signal.SIGCONT)

                    if not paused:
                        st_tm = time.time()
                        self.flag = False
                        self.image = np.frombuffer(pipe.stdout.read(self.H * self.W * self.P), dtype='uint8') # overwrite array
                        self.image.shape = (self.H, self.W, self.P)
                        self.flag = True
                        step = time.time() - st_tm
                        time.sleep(max(0.04 - step, 0.0)) # adding fps info to ffmpeg doesn't seem to have any effect
                    else:
                        self.paused_time += 0.25
                        time.sleep(0.25)

    def kill(self):
        self.kill_thread = True
        if self.t is not None:
            self.t.join()
        del self.image

    def pause(self):
        self.pause_thread = True

    def restart(self):
        self.pause_thread = False

#!/usr/bin/python
import numpy as np
import subprocess
import threading
import time
import json

class VideoInfo:
    def __init__(self, video_path):
        probe_cmd = f'ffprobe -v error -show_entries stream=width,height,avg_frame_rate,duration -of json "{video_path}"'
        probe_result = subprocess.check_output(probe_cmd, shell=True, text=True)
        video_info_list = [vinfo for vinfo in json.loads(probe_result)['streams'] if 'width' in vinfo]
        if len(video_info_list) > 0:
            video_info = video_info_list[0] # use first if more than one!
            self.width = int(video_info['width'])
            self.height = int(video_info['height'])
            self.fps = eval(video_info['avg_frame_rate']) #TODO this is string in form '24/1' converted to float using eval - try/catch this?
            self.duration = float(video_info['duration'])
        else:
            self.width = self.height = self.fps = self.duration = None

class VideoStreamer:
    def __init__(self, video_path):
        self.flag = False # use to signal new texture
        self.kill_thread = False
        self.command = [ 'ffmpeg', '-i', video_path, '-f', 'image2pipe',
                        '-pix_fmt', 'rgb24', '-vcodec', 'rawvideo', '-']
        video_info = VideoInfo(video_path)
        if video_info.width is not None:
            self.W = video_info.width
            self.H = video_info.height
            self.fps = video_info.fps
            self.duration = video_info.duration
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
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.t = None

    def pipe_thread(self):
        while not self.kill_thread:
            with subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1) as pipe:
                while pipe.poll() is None and not self.kill_thread:
                    st_tm = time.time()
                    self.flag = False
                    self.image = np.frombuffer(pipe.stdout.read(self.H * self.W * self.P), dtype='uint8') # overwrite array
                    self.image.shape = (self.H, self.W, self.P)
                    self.flag = True
                    step = time.time() - st_tm
                    time.sleep(max(0.04 - step, 0.0)) # adding fps info to ffmpeg doesn't seem to have any effect

    def kill(self):
        self.kill_thread = True
        if self.t is not None:
            self.t.join()
        del self.image

#!/usr/bin/python
import numpy as np
import subprocess
import threading
import time
import json


def get_dimensions(video_path):
    probe_cmd = f'ffprobe -v error -show_entries stream=width,height,avg_frame_rate -of json "{video_path}"'
    probe_result = subprocess.check_output(probe_cmd, shell=True, text=True)
    video_info_list = [vinfo for vinfo in json.loads(probe_result)['streams'] if 'width' in vinfo]
    if len(video_info_list) > 0:
        video_info = video_info_list[0] # use first if more than one!
        return(video_info['width'], video_info['height'])
    else:
        return None

class VideoStreamer:
    def __init__(self, video_path):
        self.flag = False # use to signal new texture
        self.kill_thread = False
        self.command = [ 'ffmpeg', '-i', video_path, '-f', 'image2pipe',
                        '-pix_fmt', 'rgb24', '-vcodec', 'rawvideo', '-']
        dimensions = get_dimensions(video_path)
        if dimensions is not None:
            (self.W, self.H) = dimensions
            self.P = 3
            self.image = np.zeros((self.H, self.W, self.P), dtype='uint8')
            self.t = threading.Thread(target=self.pipe_thread)
            self.t.start()
        else: # couldn't get dimensions for some reason - assume not able to read video
            self.W = 240
            self.H = 180
            self.P = 3
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

import os
import sys
import time
import threading
import pi3d
import numpy as np

from picframe import model, viewer_display, controller

SAVE = False

class TestVD(viewer_display.ViewerDisplay):
    def __init__(self, config):
        super(TestVD, self).__init__(config)
        self.test_sample = None
        self.take_sample = False

    def slideshow_is_running(self, pics=None, time_delay = 200.0, fade_time = 10.0, paused=False):
        ret_val = super(TestVD, self).slideshow_is_running(pics, time_delay, fade_time, paused)
        if self.take_sample:
            self.test_sample = pi3d.masked_screenshot(0, 0, 200, 200)
            self.take_sample = False
        return ret_val

def delayed_sample(sample, v, c):
    time.sleep(10.0)
    v.take_sample = True
    time.sleep(0.2)
    sample[0] = v.test_sample
    time.sleep(0.25)
    c.stop()

os.system("rm ./test/pictureframe.db3")

m = model.Model("./test/configuration_test.yaml")
v = TestVD(m.get_viewer_config())
c = controller.Controller(m, v)
sample = [np.zeros((200,200,3), dtype=np.int)] # have to pass as array for pass by reference!
t = threading.Thread(target=delayed_sample, args=(sample, v, c))
t.start()

c.start()
c.loop()

sample = sample[0].astype(np.int)
if SAVE:
    np.save("./test/test_sample.npy", sample) # to save array after modifications to code
else:
    chk_sample = np.load("./test/test_sample.npy")
    min_diff = 100_000_000
    chk_diff = np.abs(sample - chk_sample).sum()
    print("diff = {}".format(chk_diff))
    assert(chk_diff < 1_000)

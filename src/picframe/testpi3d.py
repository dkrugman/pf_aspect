#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Really difficult getting this to work. One of the keys was making sure to use a windowed display with pi3d and then 
# setting display_config=pi3d.DISPLAY_CONFIG_HIDE_CURSOR | pi3d.DISPLAY_CONFIG_NO_FRAME to make it borderless
# use_glx=False and use_sdl2=True was also important.
#
# Some syntax differences with pi3d.ImageSprite and pi3d.Sprite were also confusing. They changed how you set the texture
# and the shader. So mixing and matching the syntax was a pain. Took me a while to realize I was defining the texture but
# not actually assigning it to the sprite.
#
# I ended up sticking with X11 because I was developing over SSH, but Wayland might be better.
#
# pi3d 2.54 is the latest version, but I think I may be using some of the older syntax.
#
# I forced a global environment variable DISPLAY=:0 in /etc/environment because it didn't seem to be sticking and  
# and critical error was X11 not running. Turned out that my settings were making it try to run fullscreen and it needed
# to be windowed the way I was doing it. Theoretically that shouldn't be the case.

import os, time, pi3d, sys, logging, subprocess, math, pprint, pathlib  #type: ignore
from typing import Optional, List, Tuple
from datetime import datetime
from PIL import Image, ImageOps             
#import numpy as np
# print("== testpi3d.py startup ==")
# print("sys.executable:", sys.executable)
# print("DISPLAY:", os.environ.get("DISPLAY"))
# print("XAUTHORITY:", os.environ.get("XAUTHORITY"), "exists:", os.path.exists(os.environ.get("XAUTHORITY","")))
# print("XDG_SESSION_TYPE:", os.environ.get("XDG_SESSION_TYPE"))
# print("XDG_RUNTIME_DIR:", os.environ.get("XDG_RUNTIME_DIR"))
# print("WAYLAND_DISPLAY:", os.environ.get("WAYLAND_DISPLAY"))
# Safety pin: force DISPLAY if somehow blank
if not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":0"

def make_sprite(path, rotation=False):
    print(f"Loading texture from: {path}")
    try:
        if rotation:
            ext = os.path.splitext(path)[1].lower()
            with Image.open(path) as im:
                if not(ext in ('.heif', '.heic')):  # heif and heic images are converted to PIL.Image obects and are alway in correct orienation # noqa: E501
                    im = ImageOps.exif_transpose(im).rotate(rotation, resample=False, expand=True)
                tex = pi3d.Texture(im, mipmap=False, blend=True, m_repeat=True, free_after_load=True)
        else:
            tex = pi3d.Texture(path, mipmap=False, blend=True, m_repeat=True, free_after_load=True)
        print(f"Texture loaded successfully: {tex}")
        spr = pi3d.ImageSprite(texture=tex, shader=SHADER, camera=CAM, w=W, h=H, x=0, y=0, z=5.0)
        spr.set_alpha(1.0)
        spr.unif[47] = 1.0  # Edge alpha
        spr.unif[54] = 0.0  # Shader blend type 0="blend", 1="burn", 2="bump" 
        spr.unif[55] = 1.0  # Brightness
        print(f"Sprite created successfully: {spr}")
        return spr
    except Exception as e:
        print(f"Error loading texture: {e}")

def draw(*sprites):
    DISPLAY.clear()
    for s in sprites:
        s.draw()
    DISPLAY.swap_buffers()

# ---------- Demo run ----------
DISPLAY = pi3d.Display.create(w=2894, h=2160, x=473, y=0, frames_per_second=20,
                              display_config=pi3d.DISPLAY_CONFIG_HIDE_CURSOR | pi3d.DISPLAY_CONFIG_NO_FRAME,
                              background=[0.2, 0.2, 0.3, 1.0], use_glx=False, use_sdl2=True)
W, H = DISPLAY.width, DISPLAY.height
CAM = pi3d.Camera(is_3d=False)
SHADER = pi3d.Shader("uv_flat")

spr_A = make_sprite("/home/pi/Pictures/Landscape/DSC_0069.jpeg")
spr_B = make_sprite("/home/pi/Pictures/Portrait/DSC_0046sharp_filtered_16bitcrop.jpeg", 1)

draw(spr_A, spr_B)
time.sleep(3)
draw(spr_B, spr_A)
time.sleep(3)

DISPLAY.destroy()
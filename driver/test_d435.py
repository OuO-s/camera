#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D435 连通性自检:枚举相机 → 每台拍一张彩图 + 深度统计。

用法(Windows 本机):  python test_d435.py
依赖:  pip install pyrealsense2 numpy
       (可选 pip install opencv-python 或 pillow,用于把图存成 PNG;
        都没装则存 .npy,同样说明相机工作正常)
"""
import os
import sys
import numpy as np
import pyrealsense2 as rs

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
os.makedirs(OUT, exist_ok=True)

KNOWN_SN = {"262322071271": "左臂腕相机", "262022070108": "右臂腕相机"}


def save_png(name, arr_bgr):
    """优先 cv2 → PIL → 兜底 .npy,返回用了哪种方式"""
    path = os.path.join(OUT, name)
    try:
        import cv2
        cv2.imwrite(path, arr_bgr)
        return "cv2"
    except ImportError:
        pass
    try:
        from PIL import Image
        Image.fromarray(arr_bgr[..., ::-1]).save(path)  # BGR→RGB
        return "PIL"
    except ImportError:
        np.save(path + ".npy", arr_bgr)
        return "npy"


ctx = rs.context()
devs = list(ctx.query_devices())
print("检测到 %d 台 RealSense:" % len(devs))
for i, d in enumerate(devs):
    sn = d.get_info(rs.camera_info.serial_number)
    tag = KNOWN_SN.get(sn, "")
    print("  [%d] %s  SN=%s  FW=%s  %s" % (
        i, d.get_info(rs.camera_info.name), sn,
        d.get_info(rs.camera_info.firmware_version),
        ("← " + tag) if tag else ""))

if not devs:
    print("\n没找到相机:检查是否插在 USB 3.0(蓝色)口、线是否支持 USB3,")
    print("以及是否已安装 RealSense.SDK 并重插过相机。")
    sys.exit(1)

for i in range(len(devs)):
    sn = devs[i].get_info(rs.camera_info.serial_number)
    print("\n=== [%d] SN=%s 开始取流 ===" % (i, sn))
    pipe = rs.pipeline(ctx)
    cfg = rs.config()
    cfg.enable_device(sn)
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 15)
    try:
        prof = pipe.start(cfg)
    except Exception as e:
        print("  取流失败:%s" % e)
        continue
    try:
        frames = None
        for _ in range(30):  # 丢前几帧,等自动曝光稳定
            frames = pipe.wait_for_frames(timeout_ms=5000)
        color = frames.get_color_frame()
        depth = frames.get_depth_frame()
        if color:
            img = np.asanyarray(color.get_data())
            how = save_png("cam%d_%s_color.png" % (i, sn), img)
            print("  彩图已存(via %s): cam%d_%s_color.png  shape=%s" % (how, i, sn, img.shape))
        if depth:
            scale = prof.get_device().first_depth_sensor().get_depth_scale()
            dep = np.asanyarray(depth.get_data()).astype(np.float32) * scale  # → 米
            valid = dep[dep > 0]
            if valid.size:
                h, w = dep.shape
                center = dep[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10]
                center = center[center > 0]
                print("  深度OK:全幅有效均值 %.3f m,中心区 %.3f m"
                      % (valid.mean(), center.mean() if center.size else float("nan")))
            else:
                print("  ⚠ 深度全零:镜头前太空旷/太近,对着 0.3~3 m 内物体再试")
    finally:
        pipe.stop()

print("\n完成。图片在 %s" % OUT)

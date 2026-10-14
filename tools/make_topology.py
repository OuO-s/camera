# -*- coding: utf-8 -*-
"""生成 docs/img/相机两种接法.png（README 里的拓扑示意图）。
只用中文与箭头/方块符号 —— 微软雅黑没有 emoji 字形。
"""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "img", "相机两种接法.png")
F_REG = r"C:\Windows\Fonts\msyh.ttc"
F_BLD = r"C:\Windows\Fonts\msyhbd.ttc"

BG = (247, 248, 250); INK = (28, 32, 38); SUB = (95, 105, 118); LINE = (196, 203, 212)
BLUE = (32, 110, 220); BLUE_BG = (232, 241, 255)
GREEN = (16, 138, 86); GREEN_BG = (228, 246, 238)
AMBER = (196, 118, 8); AMBER_BG = (255, 244, 224)
GREY = (238, 240, 244)


def font(size, bold=False):
    return ImageFont.truetype(F_BLD if bold else F_REG, size)


def box(d, xy, w, h, title, lines, col, bg, tsize=19, bsize=15.5):
    x, y = xy
    d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=bg, outline=col, width=2)
    d.text((x + 20, y + 14), title, font=font(tsize, True), fill=col)
    yy = y + 46
    for ln in lines:
        d.text((x + 20, yy), ln, font=font(bsize), fill=INK if ln else SUB)
        yy += 24


def arrow(d, x1, y, x2, label, col=SUB):
    d.line([(x1, y), (x2, y)], fill=col, width=3)
    d.polygon([(x2, y), (x2 - 12, y - 7), (x2 - 12, y + 7)], fill=col)
    if label:
        d.text(((x1 + x2) / 2, y - 20), label, font=font(15), fill=col, anchor="mm")


W, H = 1700, 860
im = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(im)

d.text((60, 36), "相机两种接法 —— 相机插在哪台机器，就用哪台机器的 SDK", font=font(34, True), fill=INK)
d.text((60, 88), "两种接法都不用改配置：调试台自动挑可用的那条链路（WS 流优先，相机回插大脑后自动回退到 PBVS 预览）",
       font=font(19), fill=SUB)

SLOT_W, GAP, X0 = 336, 76, 60


def slots(y, h, items, panel_tag, tag_col, labels):
    d.text((X0, y - 34), panel_tag, font=font(21, True), fill=tag_col)
    x = X0
    for i, (title, lines, col, bg) in enumerate(items):
        box(d, (x, y), SLOT_W, h, title, lines, col, bg)
        if i < len(items) - 1:
            arrow(d, x + SLOT_W + 8, y + h // 2, x + SLOT_W + GAP - 8,
                  labels[i] if i < len(labels) else "")
        x += SLOT_W + GAP


# 方案 A
slots(180, 220, [
    ("D435 ×2", ["左 262322071271", "右 262022070108", "", "USB 3.0 蓝口直插"], BLUE, BLUE_BG),
    ("Jetson 大脑", ["PBVS 实例 :7004/:7005", "用 pyrealsense2 直接读相机", "", "需要 Jetson 侧 ARM SDK"],
     GREEN, GREEN_BG),
    ("调试台 :7010", ["直接取 PBVS 的 preview", "链路最短、延迟最低", "", "只插大脑时的形状"], BLUE, BLUE_BG),
    ("操作员浏览器", ["看画面 / 下指令", "", "http://192.168.10.127:7010/"], GREY, GREY),
], "方案 A：相机插在【大脑】上", GREEN, ["USB3", "本地HTTP", "局域网"])

# 方案 B
slots(500, 240, [
    ("D435 ×2", ["左 262322071271", "右 262022070108", "", "USB 3.0 蓝口直插", "线可以很短，就近放"],
     BLUE, BLUE_BG),
    ("Windows 采集电脑", ["camera-check 服务", "网页 :8765 · WS :8766", "", "192.168.2.200", "本仓库就装在这台"],
     AMBER, AMBER_BG),
    ("Jetson 大脑", ["调试台 :7010 做 WS 中继", "再喂给 PBVS :7004/:7005", "", "相机从大脑拔走后自动走这条"],
     GREEN, GREEN_BG),
    ("操作员浏览器", ["画面与方案 A 完全一样", "", "http://192.168.10.127:7010/"], GREY, GREY),
], "方案 B：相机插在【电脑】上（本仓库）", AMBER, ["USB3", "网线WS", "局域网"])

d.text((60, 790), "判断当前是哪条：调试台相机区有画面 = 通；黑屏写 PBVS preview unavailable = 两条都不通，先查健康灯的「相机L/相机R」",
       font=font(18), fill=SUB)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
im.save(OUT)
print("saved", OUT, im.size)

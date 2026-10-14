> **从哪下载**：官方完整安装包 `RealSense.SDK-WIN10-2.58.4.10922.exe`（116MB）在本仓库的
> **Releases** 页面下载（GitHub 单文件上限 100MB，所以没进 git）：
> <https://github.com/OuO-s/camera/releases>
> 本目录已放**免安装单文件 Viewer** 与自检脚本，装 SDK 前可以先双击 Viewer 验机。
# D435 相机 SDK 包(Windows x64)

两台相机型号:**Intel RealSense D435**(深度相机,彩色+深度+点云)。

| 相机 | 序列号 SN |
|---|---|
| 左臂腕相机 | 262322071271 |
| 右臂腕相机 | 262022070108 |

序列号印在相机底部标签上,可对照确认拿的是哪台。两台可同时插。

## 包内容

| 文件 | 用途 |
|---|---|
| `RealSense.SDK-WIN10-2.58.4.10922.exe` | **官方 SDK 完整安装包**(v2.58.4):驱动 + RealSense Viewer + 深度质量工具 + C/C++/Python 支持,必装 |
| `RealSense.Viewer.exe` | 免安装单文件版 Viewer,装 SDK 前可先双击它快速验机 |
| `test_d435.py` | Python 连通性自检:枚举相机 + 拍彩图 + 深度统计 |
| `README_先看我.md` | 本说明 |

## 安装步骤

1. **双击 `RealSense.SDK-WIN10-2.58.4.10922.exe`** 安装(建议右键"以管理员身份运行",默认选项即可)。
2. 装完**重启电脑**。
3. 相机插**USB 3.0 蓝色口**(必须 USB3,深度流在 USB2 下会严重降级/报错;线也要是 USB3 的)。
4. 开始菜单打开 **RealSense Viewer**:两台相机会在左上角下拉框切换,能看到彩色+深度画面即成功。
   - Viewer 里若显示 `USB 3.2` 正常;显示 `USB 2.1` 说明口或线不对,深度会受限。
   - 若提示固件(firmware)版本不匹配:一般不更新也能用;要更新的话用 SDK 自带的 FW Update 工具。

## Python 使用(可选)

```bat
pip install pyrealsense2 numpy
python test_d435.py
```

脚本会列出所有相机(带序列号对照)、每台存一张彩图到 `snapshots\` 并打印深度均值。
如果 `pip install pyrealsense2` 装不上,先装 SDK(上面第 1 步)再试。

常用代码骨架:

```python
import pyrealsense2 as rs
pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_device("262322071271")            # 指定序列号选相机
cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 15)
pipe.start(cfg)
frames = pipe.wait_for_frames()
color = frames.get_color_frame()
depth = frames.get_depth_frame()            # depth.get_distance(x, y) → 该像素米数
```

## 注意

- Jetson 那边装的 SDK 是 ARM 版,和这个包互不相干;相机在哪台机器上插着,就用哪台机器的 SDK。
- D435 有效深度范围约 0.3~3 m(取决于分辨率/预设),太近会深度全零。
- 官方文档/源码:https://github.com/IntelRealSense/librealsense


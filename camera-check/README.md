# 双路 USB 相机检查台

这是一个局域网双路相机服务，通过 Intel RealSense SDK 按序列号读取两台 D435 的彩色流，同时提供网页、MJPEG 和 WebSocket 接口。

- 左臂腕相机：`262322071271`
- 右臂腕相机：`262022070108`

## 启动

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe camera-check\realsense_server.py
```

然后在 Edge 或 Chrome 打开 <http://localhost:8765>。相机由本地 Python SDK 读取，浏览器无需相机权限。

服务监听所有网卡：

- 网页：`http://192.168.2.200:8765`
- 左相机 MJPEG：`http://192.168.2.200:8765/stream/262322071271`
- 右相机 MJPEG：`http://192.168.2.200:8765/stream/262022070108`
- 左相机 WebSocket：`ws://192.168.2.200:8766/camera/262322071271`
- 右相机 WebSocket：`ws://192.168.2.200:8766/camera/262022070108`

WebSocket 连接建立后，服务端先发送一条 JSON 协议说明，随后每个二进制消息都是一张完整 JPEG。发送端始终读取相机的最新帧，不会因为 Jetson 推理较慢而积压旧画面。同一连接支持按像素查询与彩色图对齐的深度。

`hello` 中包含当前相机彩色流的设备内参：

```json
{
  "type": "hello",
  "serial": "262022070108",
  "width": 640,
  "height": 480,
  "intrinsics": {
    "width": 640,
    "height": 480,
    "fx": 606.148621,
    "fy": 605.491760,
    "ppx": 319.642242,
    "ppy": 256.904633,
    "model": "distortion.inverse_brown_conrady",
    "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0]
  }
}
```

内参在对应相机 `pipeline.start()` 后从实际彩色流 profile 读取；如果读取失败，服务端不会发送缺少内参的 `hello`。

### 深度查询协议

Jetson 发送 JSON 文本：

```json
{"type":"depth_query","request_id":"target-42","u":320,"v":240,"radius":2,"frame_number":184}
```

Windows 返回 JSON 文本：

```json
{
  "type": "depth_result",
  "request_id": "target-42",
  "serial": "262322071271",
  "u": 320,
  "v": 240,
  "radius": 2,
  "depth_m": 0.742,
  "depth_mm": 742.0,
  "valid_samples": 25,
  "frame_number": 184,
  "camera_timestamp_ms": 15624.27,
  "age_ms": 12.8,
  "aligned_to": "color"
}
```

- `u, v` 使用收到的 `640×480` 彩色图像像素坐标。
- `radius` 限制为 `0..10`；服务端返回邻域内非零深度的中值，默认 `2` 表示 `5×5` 邻域。
- `depth_m` 为 PBVS 推荐使用的米制深度；若邻域无有效深度则为 `null`。
- `request_id` 原样返回，用于异步匹配查询和结果。
- Jetson 接收器使用 `?frame_meta=1` 连接；服务端在每张 JPEG 前发送对应的 `frame_meta`。查询时回传该 `frame_number`，即可获得检测所用彩色帧的精确对齐深度。
- Windows 保留最近约 2 秒（30 帧）的深度历史；超过缓存范围会返回错误，PBVS 应丢弃该次结果。
- 查询错误返回 `{"type":"error", ...}`，不会断开视频流。

## Jetson 接收

将仓库 `camera-brain/` 下的 `jetson_ws_receiver.py` 和 `requirements-jetson.txt` 复制到 Jetson，然后执行：

```bash
python3 -m pip install -r requirements-jetson.txt
python3 jetson_ws_receiver.py --host 192.168.2.200
```

如果 Jetson 有桌面环境，可增加 `--display` 显示双路画面：

```bash
python3 jetson_ws_receiver.py --host 192.168.2.200 --display
```

验证彩色流和中心像素深度：

```bash
python3 jetson_ws_receiver.py --host 192.168.2.200 --display --probe-depth 320 240
```

视觉检测代码接入 `jetson_ws_receiver.py` 内的 `process_frame(serial, frame)` 函数即可。

## 常见问题

- 没发现设备：检查 D435 是否插在 USB 3.0 端口，并确认数据线支持 USB 3。
- 只发现一个设备：重新插拔离线相机后点击“刷新设备”。
- 相机连接失败：关闭 RealSense Viewer 或其他可能独占 D435 的程序。
- 两路不能选择同一序列号；页面会阻止重复选择。


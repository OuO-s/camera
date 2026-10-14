#!/usr/bin/env python3
"""双路 Intel RealSense D435 本地网页服务。"""

from __future__ import annotations

import json
import mimetypes
import signal
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np
import pyrealsense2 as rs
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve as serve_websocket


HOST = "0.0.0.0"
HTTP_PORT = 8765
WEBSOCKET_PORT = 8766
ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "262322071271": "左臂腕相机",
    "262022070108": "右臂腕相机",
}
WIDTH = 640
HEIGHT = 480
FPS = 15


class CameraWorker:
    """每台 D435 独占一个 pipeline，并把最新彩色帧编码为 JPEG。"""

    def __init__(self, serial: str):
        self.serial = serial
        self.condition = threading.Condition()
        self.frame: bytes | None = None
        self.frame_number = 0
        self.depth: np.ndarray | None = None
        self.depth_scale = 0.001
        self.depth_timestamp_ms = 0.0
        self.depth_received_ns = 0
        self.depth_history = deque(maxlen=30)
        self.intrinsics: dict | None = None
        self.streaming = False
        self.last_error = ""
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def ensure_started(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        self.frame = None
        self.depth = None
        self.depth_history.clear()
        self.intrinsics = None
        self.last_error = ""
        self.thread = threading.Thread(
            target=self._capture_loop,
            name=f"D435-{self.serial}",
            daemon=True,
        )
        self.thread.start()

    def _capture_loop(self) -> None:
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_device(self.serial)
        config.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, FPS)
        config.enable_stream(rs.stream.depth, WIDTH, HEIGHT, rs.format.z16, FPS)
        try:
            profile = pipeline.start(config)
            self.depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
            intr = (
                profile.get_stream(rs.stream.color)
                .as_video_stream_profile()
                .get_intrinsics()
            )
            with self.condition:
                self.intrinsics = {
                    "width": intr.width,
                    "height": intr.height,
                    "fx": intr.fx,
                    "fy": intr.fy,
                    "ppx": intr.ppx,
                    "ppy": intr.ppy,
                    "model": str(intr.model),
                    "coeffs": list(intr.coeffs),
                }
                self.condition.notify_all()
            align_to_color = rs.align(rs.stream.color)
            for _ in range(12):
                pipeline.wait_for_frames(timeout_ms=5000)
            self.streaming = True
            self.last_error = ""
            while not self.stopped.is_set():
                frames = align_to_color.process(
                    pipeline.wait_for_frames(timeout_ms=5000)
                )
                color = frames.get_color_frame()
                depth = frames.get_depth_frame()
                if not color or not depth:
                    continue
                image = np.asanyarray(color.get_data())
                depth_image = np.asanyarray(depth.get_data()).copy()
                ok, encoded = cv2.imencode(
                    ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 84]
                )
                if not ok:
                    continue
                with self.condition:
                    self.frame = encoded.tobytes()
                    self.depth = depth_image
                    self.depth_timestamp_ms = depth.get_timestamp()
                    self.depth_received_ns = time.monotonic_ns()
                    self.frame_number += 1
                    self.depth_history.append(
                        {
                            "frame_number": self.frame_number,
                            "depth": depth_image,
                            "camera_timestamp_ms": self.depth_timestamp_ms,
                            "received_ns": self.depth_received_ns,
                        }
                    )
                    self.condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - 错误需要回传给诊断页
            self.last_error = str(exc)
        finally:
            self.streaming = False
            try:
                pipeline.stop()
            except Exception:  # noqa: BLE001
                pass
            with self.condition:
                self.condition.notify_all()

    def wait_for_frame(self, previous: int, timeout: float = 3.0):
        with self.condition:
            self.condition.wait_for(
                lambda: self.frame_number != previous
                or not (self.thread and self.thread.is_alive()),
                timeout=timeout,
            )
            return self.frame_number, self.frame

    def wait_for_intrinsics(self, timeout: float = 8.0) -> dict | None:
        with self.condition:
            self.condition.wait_for(
                lambda: self.intrinsics is not None
                or bool(self.last_error)
                or not (self.thread and self.thread.is_alive()),
                timeout=timeout,
            )
            return dict(self.intrinsics) if self.intrinsics else None

    def stop(self) -> None:
        self.stopped.set()

    def query_depth(
        self,
        u: int,
        v: int,
        radius: int = 2,
        frame_number: int | None = None,
    ) -> dict:
        """查询与彩色图对齐的深度；使用邻域中值抑制空洞和噪声。"""

        with self.condition:
            if not self.depth_history:
                raise RuntimeError("深度帧尚未就绪")
            if frame_number is None:
                target = self.depth_history[-1]
            else:
                target = next(
                    (
                        item
                        for item in reversed(self.depth_history)
                        if item["frame_number"] == frame_number
                    ),
                    None,
                )
                if target is None:
                    oldest = self.depth_history[0]["frame_number"]
                    newest = self.depth_history[-1]["frame_number"]
                    raise LookupError(
                        f"深度帧 {frame_number} 不在缓存中；当前范围 {oldest}..{newest}"
                    )

            depth_image = target["depth"]
            height, width = depth_image.shape
            if not (0 <= u < width and 0 <= v < height):
                raise ValueError(f"像素越界：u={u}, v={v}, 图像={width}x{height}")
            radius = max(0, min(int(radius), 10))
            x0, x1 = max(0, u - radius), min(width, u + radius + 1)
            y0, y1 = max(0, v - radius), min(height, v + radius + 1)
            samples = depth_image[y0:y1, x0:x1]
            valid = samples[samples > 0]
            raw_depth = float(np.median(valid)) if valid.size else None
            depth_m = raw_depth * self.depth_scale if raw_depth is not None else None
            return {
                "u": u,
                "v": v,
                "radius": radius,
                "depth_m": round(depth_m, 6) if depth_m is not None else None,
                "depth_mm": round(depth_m * 1000, 3) if depth_m is not None else None,
                "valid_samples": int(valid.size),
                "frame_number": target["frame_number"],
                "camera_timestamp_ms": round(target["camera_timestamp_ms"], 3),
                "age_ms": round(
                    (time.monotonic_ns() - target["received_ns"]) / 1_000_000, 3
                ),
                "aligned_to": "color",
            }

    def frame_metadata(self, frame_number: int) -> dict:
        with self.condition:
            target = next(
                (
                    item
                    for item in reversed(self.depth_history)
                    if item["frame_number"] == frame_number
                ),
                None,
            )
            return {
                "type": "frame_meta",
                "serial": self.serial,
                "frame_number": frame_number,
                "camera_timestamp_ms": round(
                    target["camera_timestamp_ms"] if target else 0.0, 3
                ),
            }


workers: dict[str, CameraWorker] = {}
workers_lock = threading.Lock()


def enumerate_cameras() -> list[dict]:
    found: dict[str, dict] = {}
    try:
        for device in rs.context().query_devices():
            serial = device.get_info(rs.camera_info.serial_number)
            found[serial] = {
                "serial": serial,
                "name": device.get_info(rs.camera_info.name),
                "firmware": device.get_info(rs.camera_info.firmware_version),
                "usb": device.get_info(rs.camera_info.usb_type_descriptor),
            }
    except Exception:
        pass

    serials = list(EXPECTED)
    serials.extend(serial for serial in found if serial not in EXPECTED)
    result = []
    for serial in serials:
        item = found.get(serial, {"serial": serial, "name": "Intel RealSense D435"})
        worker = workers.get(serial)
        item.update(
            {
                "role": EXPECTED.get(serial, "未标记相机"),
                "online": serial in found,
                "streaming": bool(worker and worker.streaming),
                "last_error": worker.last_error if worker else "",
                "width": WIDTH,
                "height": HEIGHT,
                "fps": FPS,
            }
        )
        result.append(item)
    return result


def get_worker(serial: str) -> CameraWorker:
    with workers_lock:
        worker = workers.get(serial)
        if worker is None:
            worker = CameraWorker(serial)
            workers[serial] = worker
        worker.ensure_started()
        return worker


class Handler(BaseHTTPRequestHandler):
    server_version = "D435Check/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/cameras":
            cameras = enumerate_cameras()
            self.send_json(
                {
                    "sdk_version": rs.__version__,
                    "online_count": sum(camera["online"] for camera in cameras),
                    "http_port": HTTP_PORT,
                    "websocket_port": WEBSOCKET_PORT,
                    "cameras": cameras,
                }
            )
            return

        if parsed.path.startswith("/stream/"):
            serial = unquote(parsed.path.removeprefix("/stream/"))
            self.stream_camera(serial)
            return

        self.serve_static(parsed.path)

    def serve_static(self, request_path: str) -> None:
        files = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
        }
        name = files.get(request_path)
        if not name:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = ROOT / name
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def stream_camera(self, serial: str) -> None:
        online = {camera["serial"] for camera in enumerate_cameras() if camera["online"]}
        if serial not in online:
            self.send_json(
                {"error": f"RealSense SN {serial} 当前离线"}, HTTPStatus.NOT_FOUND
            )
            return

        worker = get_worker(serial)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        previous = -1
        try:
            while True:
                number, frame = worker.wait_for_frame(previous)
                if frame is None:
                    if worker.last_error:
                        break
                    continue
                previous = number
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def websocket_camera(connection) -> None:
    """每条 WebSocket 连接传输一路相机；每个二进制消息是一张 JPEG。"""

    parsed_request = urlparse(connection.request.path)
    path = parsed_request.path
    include_frame_meta = parse_qs(parsed_request.query).get("frame_meta") == ["1"]
    prefix = "/camera/"
    if not path.startswith(prefix):
        connection.close(1008, "路径格式应为 /camera/<serial>")
        return

    serial = unquote(path.removeprefix(prefix))
    online = {camera["serial"] for camera in enumerate_cameras() if camera["online"]}
    if serial not in online:
        connection.close(1013, f"RealSense SN {serial} 当前离线")
        return

    worker = get_worker(serial)
    intrinsics = worker.wait_for_intrinsics()
    if intrinsics is None:
        reason = worker.last_error or "等待彩色流内参超时"
        connection.close(1011, reason[:120])
        return
    hello = {
        "type": "hello",
        "serial": serial,
        "role": EXPECTED.get(serial, "未标记相机"),
        "codec": "jpeg",
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "intrinsics": intrinsics,
        "policy": "latest-frame",
        "frame_meta": include_frame_meta,
        "depth_query": {
            "request": {
                "type": "depth_query",
                "request_id": "任意字符串",
                "u": 320,
                "v": 240,
                "radius": 2,
                "frame_number": "可选；使用对应 JPEG 的 frame_meta 精确查询",
            },
            "response_type": "depth_result",
            "units": "meter",
            "aligned_to": "color",
        },
    }
    connection.send(json.dumps(hello, ensure_ascii=False))
    previous = -1
    try:
        while True:
            while True:
                try:
                    message = connection.recv(timeout=0)
                except TimeoutError:
                    break
                handle_websocket_message(connection, worker, serial, message)

            number, frame = worker.wait_for_frame(previous, timeout=0.1)
            if number == previous:
                if worker.last_error:
                    connection.close(1011, worker.last_error[:120])
                    break
                continue
            if frame is None:
                continue
            previous = number
            if include_frame_meta:
                send_websocket_json(connection, worker.frame_metadata(number))
            connection.send(frame)
    except (ConnectionClosed, OSError):
        pass


def send_websocket_json(connection, payload: dict) -> None:
    connection.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def handle_websocket_message(connection, worker, serial: str, message) -> None:
    request_id = None
    try:
        if not isinstance(message, str):
            raise ValueError("控制消息必须是 JSON 文本")
        request = json.loads(message)
        request_id = request.get("request_id")
        if request.get("type") != "depth_query":
            raise ValueError("不支持的消息类型")
        u = int(request["u"])
        v = int(request["v"])
        radius = int(request.get("radius", 2))
        frame_number_value = request.get("frame_number")
        frame_number = (
            int(frame_number_value) if frame_number_value is not None else None
        )
        result = worker.query_depth(u, v, radius, frame_number)
        send_websocket_json(
            connection,
            {
                "type": "depth_result",
                "request_id": request_id,
                "serial": serial,
                **result,
            },
        )
    except (
        KeyError,
        LookupError,
        TypeError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        send_websocket_json(
            connection,
            {
                "type": "error",
                "request_id": request_id,
                "code": "INVALID_DEPTH_QUERY",
                "message": str(exc),
            },
        )


def shutdown(*_args) -> None:
    for worker in workers.values():
        worker.stop()
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    http_server = ThreadingHTTPServer((HOST, HTTP_PORT), Handler)
    websocket_server = serve_websocket(
        websocket_camera,
        HOST,
        WEBSOCKET_PORT,
        compression=None,
        ping_interval=10,
        ping_timeout=10,
        max_size=None,
    )
    websocket_thread = threading.Thread(
        target=websocket_server.serve_forever,
        name="D435-WebSocket",
        daemon=True,
    )
    websocket_thread.start()
    print(f"双路 D435 检查台：http://localhost:{HTTP_PORT}")
    print(f"Jetson WebSocket：ws://<电脑IP>:{WEBSOCKET_PORT}/camera/<序列号>")
    print("目标相机：" + " / ".join(f"{role} SN={sn}" for sn, role in EXPECTED.items()))
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http_server.server_close()
        websocket_server.shutdown()
        for worker in workers.values():
            worker.stop()


if __name__ == "__main__":
    main()

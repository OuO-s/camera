#!/usr/bin/env python3
"""Jetson 双路 D435 WebSocket 接收示例：只消费最新帧，避免推理延迟堆积。"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import uuid

import cv2
import numpy as np
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


CAMERAS = {
    "262322071271": "左臂腕相机",
    "262022070108": "右臂腕相机",
}


class LatestFrames:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frames: dict[str, tuple[int, float, np.ndarray, int | None]] = {}
        self.sequence: dict[str, int] = {}

    def update(
        self, serial: str, frame: np.ndarray, frame_number: int | None = None
    ) -> None:
        with self.lock:
            sequence = self.sequence.get(serial, 0) + 1
            self.sequence[serial] = sequence
            self.frames[serial] = (sequence, time.monotonic(), frame, frame_number)

    def snapshot(self):
        with self.lock:
            return dict(self.frames)


class DepthQueries:
    """跨接收线程发送深度查询，并用 request_id 等待对应响应。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.outgoing = {serial: queue.SimpleQueue() for serial in CAMERAS}
        self.pending: dict[str, tuple[threading.Event, dict]] = {}

    def query(
        self,
        serial: str,
        u: int,
        v: int,
        radius: int = 2,
        frame_number: int | None = None,
        timeout: float = 1.0,
    ) -> dict:
        request_id = uuid.uuid4().hex
        event = threading.Event()
        holder: dict = {}
        with self.lock:
            self.pending[request_id] = (event, holder)
        request = {
            "type": "depth_query",
            "request_id": request_id,
            "u": int(u),
            "v": int(v),
            "radius": int(radius),
        }
        if frame_number is not None:
            request["frame_number"] = int(frame_number)
        self.outgoing[serial].put(json.dumps(request, separators=(",", ":")))
        if not event.wait(timeout):
            with self.lock:
                self.pending.pop(request_id, None)
            raise TimeoutError(f"深度查询超时：SN={serial}, ({u}, {v})")
        response = holder["response"]
        if response.get("type") == "error":
            raise RuntimeError(response.get("message", "深度查询失败"))
        return response

    def drain(self, serial: str):
        while True:
            try:
                yield self.outgoing[serial].get_nowait()
            except queue.Empty:
                break

    def resolve(self, response: dict) -> None:
        request_id = response.get("request_id")
        if not request_id:
            return
        with self.lock:
            pending = self.pending.pop(request_id, None)
        if pending:
            event, holder = pending
            holder["response"] = response
            event.set()


def receive_camera(
    host: str,
    port: int,
    serial: str,
    latest: LatestFrames,
    depth_queries: DepthQueries,
) -> None:
    url = f"ws://{host}:{port}/camera/{serial}?frame_meta=1"
    while True:
        try:
            print(f"[{CAMERAS.get(serial, serial)}] 正在连接 {url}", flush=True)
            with connect(
                url,
                open_timeout=5,
                ping_interval=10,
                ping_timeout=10,
                compression=None,
                max_size=None,
            ) as websocket:
                hello = websocket.recv()
                if not isinstance(hello, str):
                    raise RuntimeError("服务端未返回协议说明")
                metadata = json.loads(hello)
                print(f"[{CAMERAS.get(serial, serial)}] 已连接：{metadata}", flush=True)
                pending_frame_number = None
                while True:
                    for request in depth_queries.drain(serial):
                        websocket.send(request)
                    try:
                        payload = websocket.recv(timeout=0.1)
                    except TimeoutError:
                        continue
                    if isinstance(payload, str):
                        response = json.loads(payload)
                        if response.get("type") == "frame_meta":
                            pending_frame_number = response.get("frame_number")
                            continue
                        if response.get("type") in {"depth_result", "error"}:
                            depth_queries.resolve(response)
                        continue
                    encoded = np.frombuffer(payload, dtype=np.uint8)
                    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    if frame is not None:
                        latest.update(serial, frame, pending_frame_number)
                    pending_frame_number = None
        except (ConnectionClosed, OSError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[{CAMERAS.get(serial, serial)}] 连接中断：{exc}；1 秒后重试", flush=True)
            time.sleep(1)


def process_frame(
    serial: str,
    frame: np.ndarray,
    frame_number: int | None,
    depth_queries: DepthQueries,
) -> None:
    """在这里接入视觉检测；获得目标像素后可调用 depth_queries.query。"""

    # 示例：depth = depth_queries.query(
    #     serial, target_u, target_v, radius=2, frame_number=frame_number
    # )
    # PBVS 使用 depth["depth_m"]；为 None 时表示该邻域没有有效深度。
    _ = serial, frame, frame_number, depth_queries


def make_preview(
    frames: dict[str, tuple[int, float, np.ndarray, int | None]],
) -> np.ndarray:
    tiles = []
    for serial, role in CAMERAS.items():
        item = frames.get(serial)
        if item:
            tile = item[2].copy()
        else:
            tile = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                tile,
                "WAITING FOR STREAM",
                (150, 245),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (70, 255, 180),
                2,
                cv2.LINE_AA,
            )
        cv2.putText(
            tile,
            f"{role}  SN {serial}",
            (18, 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (70, 255, 180),
            2,
            cv2.LINE_AA,
        )
        tiles.append(tile)
    return np.hstack(tiles)


def main() -> None:
    parser = argparse.ArgumentParser(description="接收电脑发送的双路 D435 JPEG 帧")
    parser.add_argument("--host", default="192.168.2.200", help="采集电脑 IP")
    parser.add_argument("--port", type=int, default=8766, help="WebSocket 端口")
    parser.add_argument("--display", action="store_true", help="显示双路预览窗口")
    parser.add_argument(
        "--probe-depth",
        nargs=2,
        type=int,
        metavar=("U", "V"),
        help="周期查询指定彩色像素的对齐深度，例如 --probe-depth 320 240",
    )
    parser.add_argument("--depth-radius", type=int, default=2, help="深度查询邻域半径")
    parser.add_argument("--depth-interval", type=float, default=1.0, help="深度探测间隔秒")
    args = parser.parse_args()

    latest = LatestFrames()
    depth_queries = DepthQueries()
    for serial in CAMERAS:
        threading.Thread(
            target=receive_camera,
            args=(args.host, args.port, serial, latest, depth_queries),
            name=f"Receiver-{serial}",
            daemon=True,
        ).start()

    processed: dict[str, int] = {}
    stats_started = time.monotonic()
    processed_count = 0
    next_depth_probe = 0.0
    try:
        while True:
            frames = latest.snapshot()
            for serial, (sequence, _received_at, frame, frame_number) in frames.items():
                if processed.get(serial) == sequence:
                    continue
                processed[serial] = sequence
                process_frame(serial, frame, frame_number, depth_queries)
                processed_count += 1

            now = time.monotonic()
            if args.probe_depth and now >= next_depth_probe:
                u, v = args.probe_depth
                for serial, (_sequence, _received_at, _frame, frame_number) in frames.items():
                    try:
                        depth = depth_queries.query(
                            serial,
                            u,
                            v,
                            radius=args.depth_radius,
                            frame_number=frame_number,
                            timeout=1.0,
                        )
                        print(
                            f"[{CAMERAS[serial]}] ({u},{v}) "
                            f"depth={depth['depth_m']} m "
                            f"samples={depth['valid_samples']} "
                            f"age={depth['age_ms']} ms",
                            flush=True,
                        )
                    except (TimeoutError, RuntimeError) as exc:
                        print(f"[{CAMERAS[serial]}] 深度查询失败：{exc}", flush=True)
                next_depth_probe = now + args.depth_interval

            if now - stats_started >= 5:
                fps = processed_count / (now - stats_started)
                states = ", ".join(
                    f"{CAMERAS[serial]}={processed.get(serial, 0)} 帧" for serial in CAMERAS
                )
                print(f"接收处理速率 {fps:.1f} FPS；{states}", flush=True)
                stats_started = now
                processed_count = 0

            if args.display:
                cv2.imshow("Jetson D435 WebSocket Receiver", make_preview(frames))
                if cv2.waitKey(1) & 0xFF == 27:
                    break
            else:
                time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if args.display:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

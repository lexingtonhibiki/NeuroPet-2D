"""事件总线:模块间唯一合法的跨模块通信方式(除显式传入的接口对象)。

- 同步发布,订阅者按注册顺序执行;跨线程事件用 publish_threaded 投递,
  由主循环每帧 drain(保证 tkinter 单线程访问)。
- 主题命名:"域/事件",如 "perception/stimuli"、"brain/behavior"、"user/fed"、
  "user/grab"、"user/freeze"、"story/cold_positive"、"system/quit"。
- 通配订阅:"brain/*" 或 "*"。
"""
from __future__ import annotations

import queue
import threading
from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[str, dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._queue: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._lock = threading.Lock()
        self._any_subs: list[Handler] = []

    def subscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            if topic == "*":
                self._any_subs.append(handler)
            else:
                self._subs[topic].append(handler)

    def unsubscribe_all(self, handler: Handler) -> None:
        with self._lock:
            self._any_subs = [h for h in self._any_subs if h is not handler]
            for k in list(self._subs):
                self._subs[k] = [h for h in self._subs[k] if h is not handler]

    def publish(self, topic: str, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        with self._lock:
            handlers = list(self._subs.get(topic, ())) + list(self._any_subs)
        for h in handlers:
            try:
                h(topic, data)
            except Exception as exc:  # 总线不因单个订阅者崩溃
                print(f"[bus] handler error on {topic}: {exc!r}")

    def publish_threaded(self, topic: str, data: dict[str, Any] | None = None) -> None:
        """任意线程调用;主循环每帧 drain() 后在主线程重放。"""
        self._queue.put((topic, data or {}))

    def drain(self, limit: int = 200) -> int:
        """主线程调用:重放所有跨线程事件。返回处理条数。"""
        n = 0
        while n < limit:
            try:
                topic, data = self._queue.get_nowait()
            except queue.Empty:
                break
            self.publish(topic, data)
            n += 1
        return n

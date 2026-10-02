"""Byte-bounded LRU for rebuildable PIL images, including retained input images."""
from collections import OrderedDict
from threading import RLock
from PIL import Image


def image_bytes(value):
    seen = set()
    def count(item):
        if isinstance(item, Image.Image):
            if id(item) in seen:
                return 0
            seen.add(id(item))
            return item.width * item.height * len(item.getbands())
        if isinstance(item, (tuple, list)):
            return sum(count(part) for part in item)
        return 0
    return count(value)


class ImageLRU(OrderedDict):
    """Count every retained image in a value; oversized entries are never stored."""
    def __init__(self, max_bytes):
        super().__init__()
        self.max_bytes = int(max_bytes)
        self.bytes = 0
        self._sizes = {}
        self._lock = RLock()

    def get(self, key, default=None):
        with self._lock:
            if key not in self:
                return default
            super().move_to_end(key)
            return super().__getitem__(key)

    def __setitem__(self, key, value):
        with self._lock:
            if key in self:
                self.__delitem__(key)
            size = image_bytes(value)
            if size > self.max_bytes:
                return
            super().__setitem__(key, value)
            self._sizes[key] = size
            self.bytes += size
            while self.bytes > self.max_bytes:
                self.popitem(last=False)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)
            self.bytes -= self._sizes.pop(key)

    def popitem(self, last=True):
        with self._lock:
            key, value = super().popitem(last=last)
            self.bytes -= self._sizes.pop(key)
            return key, value

    def clear(self):
        with self._lock:
            super().clear()
            self._sizes.clear()
            self.bytes = 0

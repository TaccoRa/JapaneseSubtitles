"""Bound source preparation without keeping shutdown alive for network timeouts."""
from collections import OrderedDict
from concurrent.futures import Future
import logging
import threading

logger = logging.getLogger(__name__)


class SourceWorker:
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._closed = False
        for index in range(2):
            threading.Thread(target=self._run, name=f"subtitle-source-{index}", daemon=True).start()

    def submit(self, lane, callback):
        with self._condition:
            future = Future()
            if self._closed:
                future.cancel()
                return future
            previous = self._pending.pop(lane, None)
            if previous:
                previous[0].cancel()
            self._pending[lane] = (future, callback)
            self._condition.notify()
            return future

    def close(self):
        with self._condition:
            self._closed = True
            for future, _ in self._pending.values():
                future.cancel()
            self._pending.clear()
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                _, (future, callback) = self._pending.popitem(last=False)
                if not future.set_running_or_notify_cancel():
                    continue
            try:
                future.set_result(callback())
            except Exception as exc:
                future.set_exception(exc)
                logger.exception("Subtitle source preparation failed")

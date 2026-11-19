"""Small cancellable daemon pool: background searches never join at UI exit."""
from concurrent.futures import Future
import queue
import threading


class SearchExecutor:
    def __init__(self, max_workers=4):
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.threads = []
        self.max_workers = max_workers

    def submit(self, function, *args, **kwargs):
        with self.lock:
            if self.closed:
                raise RuntimeError('Search pool is closed')
            future = Future()
            self.queue.put((future, function, args, kwargs))
            self.threads = [thread for thread in self.threads if thread.is_alive()]
            if len(self.threads) < self.max_workers:
                worker = threading.Thread(target=self._work, name='NF-source', daemon=True)
                self.threads.append(worker)
                worker.start()
            return future

    def _work(self):
        while True:
            try:
                task = self.queue.get(timeout=.5)
            except queue.Empty:
                with self.lock:
                    if self.queue.empty():
                        current = threading.current_thread()
                        if current in self.threads:
                            self.threads.remove(current)
                        return
                continue
            if task is None:
                return
            future, function, args, kwargs = task
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(function(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)

    def shutdown(self, wait=False):
        with self.lock:
            if not self.closed:
                self.closed = True
                while True:
                    try:
                        task = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if task is not None:
                        task[0].cancel()
                        task[0].set_running_or_notify_cancel()
                for _ in self.threads:
                    self.queue.put(None)
        if wait:
            for thread in self.threads:
                thread.join()

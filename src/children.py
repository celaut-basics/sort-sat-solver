"""The child services of the sorter: the regression, the random CNF generator
and the solvers.

Each child service has at most one instance at a time. The instance starts
at the first call. The sorter stops it after CHILD_IDLE_TIMEOUT seconds
without a call, and at exit. A child server has a pool of threads, so one
instance can answer more than one call at the same time.
"""
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple, TypeVar

import grpc

from src.gateway import ChildInstance, Gateway

T = TypeVar("T")

# Codes that tell that the instance is not reachable. Then the sorter replaces
# the instance one time and repeats the call.
_REPLACE_ON = (grpc.StatusCode.UNAVAILABLE,)


class ChildService:

    def __init__(self, name: str, gateway: Gateway, service_id: str,
                 environment: Optional[Mapping[str, bytes]] = None,
                 service_dir: Optional[Path] = None,
                 metadata_file: Optional[Path] = None,
                 ready_timeout: float = 180,
                 log: Callable[[str], None] = lambda s: None,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.gateway = gateway
        self.service_id = service_id
        self.environment = dict(environment or {})
        self.service_dir = service_dir
        self.metadata_file = metadata_file
        self.ready_timeout = ready_timeout
        self.log = log
        self.clock = clock

        self._lock = threading.Lock()
        self._instance: Optional[ChildInstance] = None
        self._channel: Optional[grpc.Channel] = None
        self._calls = 0  # Calls in progress.
        self._last_use = clock()
        self._closed = False

    @property
    def running(self) -> bool:
        return self._instance is not None

    def _start_locked(self) -> grpc.Channel:
        self.log(f"Start an instance of {self.name} ({self.service_id}).")
        instance = self.gateway.start_service(
            self.service_id, self.environment,
            service_dir=self.service_dir, metadata_file=self.metadata_file,
        )
        channel = grpc.insecure_channel(instance.address)
        try:
            grpc.channel_ready_future(channel).result(timeout=self.ready_timeout)
        except grpc.FutureTimeoutError:
            channel.close()
            self._stop_instance(instance)
            raise TimeoutError(f"The instance of {self.name} at {instance.address} did not accept "
                               f"connections in {self.ready_timeout} s.")
        self.log(f"The instance of {self.name} is ready at {instance.address}.")
        self._instance, self._channel = instance, channel
        return channel

    def _stop_instance(self, instance: ChildInstance) -> None:
        try:
            self.gateway.stop_service(instance.token)
            self.log(f"Stopped the instance of {self.name} at {instance.address}.")
        except Exception as e:
            self.log(f"Could not stop the instance of {self.name} at {instance.address}: {e}")

    def _drop_locked(self) -> Optional[ChildInstance]:
        instance, channel = self._instance, self._channel
        self._instance, self._channel = None, None
        if channel is not None:
            channel.close()
        return instance

    def _acquire(self, replace: Optional[ChildInstance] = None) -> Tuple[grpc.Channel, ChildInstance]:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"The child service {self.name} is closed.")
            if replace is not None and self._instance == replace:
                self._stop_instance(self._drop_locked())
            channel = self._channel or self._start_locked()
            self._calls += 1
            self._last_use = self.clock()
            return channel, self._instance

    def _release(self) -> None:
        with self._lock:
            self._calls -= 1
            self._last_use = self.clock()

    def call(self, function: Callable[[grpc.Channel], T]) -> T:
        """Run `function` with a channel to the instance and return its result.

        If the instance is not reachable, replace it one time and run
        `function` again.
        """
        replace = None
        for attempt in (1, 2):
            channel, instance = self._acquire(replace)
            try:
                return function(channel)
            except grpc.RpcError as e:
                if attempt == 2 or e.code() not in _REPLACE_ON:
                    raise
                self.log(f"The instance of {self.name} is not reachable ({e.details()}). Replace it.")
                replace = instance
            finally:
                self._release()
        raise AssertionError("unreachable")

    def stop_if_idle(self, idle_timeout: float) -> bool:
        """Stop the instance if it had no call for `idle_timeout` seconds."""
        with self._lock:
            if self._instance is None or self._calls > 0 or idle_timeout <= 0:
                return False
            if self.clock() - self._last_use < idle_timeout:
                return False
            instance = self._drop_locked()
        self.log(f"The instance of {self.name} is idle.")
        self._stop_instance(instance)
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            instance = self._drop_locked()
        if instance is not None:
            self._stop_instance(instance)


class Children:
    """All the child services, with a thread that stops the idle instances."""

    def __init__(self, idle_timeout: float, log: Callable[[str], None] = lambda s: None,
                 check_interval: float = 30):
        self.idle_timeout = idle_timeout
        self.check_interval = check_interval
        self.log = log
        self._children: Dict[str, ChildService] = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add(self, child: ChildService) -> ChildService:
        with self._lock:
            self._children[child.name] = child
        return child

    def all(self):
        with self._lock:
            return list(self._children.values())

    def stop_idle(self) -> int:
        return sum(1 for child in self.all() if child.stop_if_idle(self.idle_timeout))

    def _maintenance(self) -> None:
        while not self._closed.wait(self.check_interval):
            try:
                self.stop_idle()
            except Exception as e:
                self.log(f"Error while the sorter stopped idle instances: {e}")

    def start(self) -> None:
        if self.idle_timeout > 0 and self._thread is None:
            self._thread = threading.Thread(target=self._maintenance, name="Children", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._closed.set()
        for child in self.all():
            child.close()

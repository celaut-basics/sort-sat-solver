"""The dataset of the sorter and the loop that sends it to the regression service."""
import hashlib
import threading
from typing import Callable, Optional

import grpc
from bee_rpc.client import client_grpc

from protos import regresion_pb2, regresion_pb2_grpc, solvers_dataset_pb2 as sd_pb2
from src import dataset
from src.children import ChildService
from src.ranker import Ranker


def make_regression(channel: grpc.Channel, data_set: sd_pb2.DataSet, timeout: Optional[float]) -> regresion_pb2.Tensor:
    try:
        return next(client_grpc(
            method=regresion_pb2_grpc.RegresionStub(channel).MakeRegresion,
            input=data_set,
            indices_serializer=sd_pb2.DataSet,
            indices_parser=regresion_pb2.Tensor,
            partitions_message_mode_parser=True,
            timeout=timeout,
        ))
    except StopIteration:
        raise RuntimeError("The regression service ended the call without a Tensor.")


class Regression:

    def __init__(self, child: Optional[ChildService], ranker: Ranker, interval: float,
                 call_timeout: float = 600,
                 log: Callable[[str], None] = lambda s: None):
        self.child = child
        self.ranker = ranker
        self.interval = interval
        self.call_timeout = call_timeout
        self.log = log
        self._data_set = sd_pb2.DataSet()
        self._lock = threading.Lock()
        self._last_digest: Optional[bytes] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_data(self, new_data_set: sd_pb2.DataSet) -> None:
        with self._lock:
            ignored = dataset.merge(self._data_set, new_data_set)
            entries = dataset.size(self._data_set)
        if ignored:
            self.log(f"Ignored {ignored} invalid dataset entries.")
        self.log(f"Dataset updated: {entries} entries.")

    def get_data_set(self) -> sd_pb2.DataSet:
        with self._lock:
            copy = sd_pb2.DataSet()
            copy.CopyFrom(self._data_set)
            return copy

    def run_once(self) -> bool:
        """Make a regression if the dataset changed. Return True if the models changed."""
        if self.child is None:
            return False
        data_set = self.get_data_set()
        if not data_set.data:
            return False
        digest = hashlib.sha3_256(data_set.SerializeToString(deterministic=True)).digest()
        if digest == self._last_digest:
            return False
        tensor = self.child.call(lambda channel: make_regression(channel, data_set, self.call_timeout))
        models = self.ranker.update(tensor)
        self._last_digest = digest
        self.log(f"New regression: {models} models.")
        return True

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            except Exception as e:
                self.log(f"Regression error: {e}")

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="Regression", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

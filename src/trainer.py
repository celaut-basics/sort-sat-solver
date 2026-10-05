"""The training loop.

In each round the trainer gets a random CNF, gives it to each solver
configuration with a time limit and scores the answers (src/dataset.py).
After SAVE_TRAIN_DATA rounds it adds the scores to the dataset of the
regression.
"""
import threading
from typing import Callable, Dict, Optional

import grpc
from bee_rpc.client import client_grpc

from protos import api_pb2, api_pb2_grpc, solvers_dataset_pb2 as sd_pb2
from src import cnf as cnf_utils
from src import dataset
from src.children import ChildService
from src.regression import Regression
from src.solvers import SolverRegistry


def random_cnf(channel: grpc.Channel, timeout: Optional[float]) -> api_pb2.Cnf:
    try:
        return next(client_grpc(
            method=api_pb2_grpc.RandomStub(channel).RandomCnf,
            indices_parser=api_pb2.Cnf,
            partitions_message_mode_parser=True,
            timeout=timeout,
        ))
    except StopIteration:
        raise RuntimeError("The random CNF service ended the call without a CNF.")


class Trainer:

    def __init__(self, random_child: Optional[ChildService], solvers: SolverRegistry,
                 regression: Regression, rounds_per_update: int, solver_timeout: float,
                 log: Callable[[str], None] = lambda s: None,
                 random_timeout: float = 60, error_pause: float = 5):
        self.random_child = random_child
        self.solvers = solvers
        self.regression = regression
        self.rounds_per_update = max(1, rounds_per_update)
        self.solver_timeout = solver_timeout
        self.random_timeout = random_timeout
        self.error_pause = error_pause
        self.log = log
        self._pending = sd_pb2.DataSet()
        self._rounds = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_round(self) -> Dict[str, float]:
        """Train one round. Return the score of each solver configuration."""
        if self.random_child is None:
            raise RuntimeError("No random CNF service: the .dependencies file has no RANDOM key.")
        solvers = self.solvers.all()
        if not solvers:
            raise RuntimeError("No solver to train. Add one with UploadSolver.")
        cnf = self.random_child.call(lambda channel: random_cnf(channel, self.random_timeout))
        results = {}
        for solver in solvers:
            if self._stop.is_set():
                return {}
            results[solver.config_id] = solver.try_solve(cnf, self.solver_timeout, log=self.log)
        scores = dataset.score_round(cnf, results)
        key = cnf_utils.shape_key(cnf)
        with self._lock:
            for solver in solvers:
                instance = dataset.instance_for(
                    self._pending, bytes.fromhex(solver.config_id), bytes.fromhex(solver.service_id))
                dataset.add_sample(instance, key, scores[solver.config_id])
            self._rounds += 1
            if self._rounds >= self.rounds_per_update:
                self.flush_locked()
        return scores

    def flush_locked(self) -> None:
        if self._pending.data:
            self.regression.add_data(self._pending)
        self._pending = sd_pb2.DataSet()
        self._rounds = 0

    def _loop(self) -> None:
        self.log("Training started.")
        while not self._stop.is_set():
            try:
                scores = self.run_round()
                if scores:
                    self.log("Round scores: " + ", ".join(f"{k[:8]}={v:.3f}" for k, v in scores.items()))
            except Exception as e:
                self.log(f"Training error: {e}")
                self._stop.wait(self.error_pause)
        with self._lock:
            self.flush_locked()
        self.log("Training stopped.")

    def start(self) -> bool:
        """Start the training thread. Return False if it runs already."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="Trainer", daemon=True)
            self._thread.start()
            return True

    def stop(self, wait: bool = True) -> None:
        """Stop after the current solver call. Keep the scores of the finished rounds."""
        self._stop.set()
        thread = self._thread
        if wait and thread is not None:
            thread.join()

"""The solver configurations that the sorter knows, and the call to a solver.

A solver is a service that implements api.Solver/Solve over bee-rpc
(see protos/api.proto). A solver configuration is a solver service with the
values of its environment variables. Its id is the SHA3-256 of the
serialized api.SolverConfiguration.
"""
import hashlib
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

import grpc
from bee_rpc.client import client_grpc

from protos import api_pb2, api_pb2_grpc, celaut_pb2
from src.children import ChildService, Children
from src.config import is_service_id
from src.dataset import Result
from src.gateway import SHA3_256_ID, Gateway


def configuration_id(configuration: api_pb2.SolverConfiguration) -> str:
    return hashlib.sha3_256(configuration.SerializeToString(deterministic=True)).hexdigest()


def service_id_of(metadata: celaut_pb2.Metadata) -> Optional[str]:
    """The SHA3-256 id of a service, from its metadata."""
    for _hash in metadata.hashtag.hash:
        if _hash.type == SHA3_256_ID:
            return _hash.value.hex()
    return None


def solve(channel: grpc.Channel, cnf: api_pb2.Cnf, timeout: Optional[float]) -> api_pb2.Interpretation:
    """Call api.Solver/Solve. A timeout of 0 or None means no limit."""
    try:
        return next(client_grpc(
            method=api_pb2_grpc.SolverStub(channel).Solve,
            input=cnf,
            indices_serializer=api_pb2.Cnf,
            indices_parser=api_pb2.Interpretation,
            partitions_message_mode_parser=True,
            timeout=timeout or None,
        ))
    except StopIteration:
        raise RuntimeError("The solver ended the call without an interpretation.")


@dataclass
class Solver:
    config_id: str
    service_id: str
    configuration: api_pb2.SolverConfiguration
    child: ChildService

    def solve(self, cnf: api_pb2.Cnf, timeout: Optional[float]) -> api_pb2.Interpretation:
        return self.child.call(lambda channel: solve(channel, cnf, timeout))

    def try_solve(self, cnf: api_pb2.Cnf, timeout: Optional[float],
                  log: Callable[[str], None] = lambda s: None) -> Result:
        """Return (interpretation, seconds). The interpretation is None if the
        solver gave no answer: timeout, error or a stopped instance."""
        start = time.monotonic()
        try:
            interpretation = self.solve(cnf, timeout)
        except Exception as e:
            details = e.details() if isinstance(e, grpc.RpcError) else str(e)
            log(f"No answer from the solver {self.config_id[:16]}: {details}")
            return None, time.monotonic() - start
        return interpretation, time.monotonic() - start


class SolverRegistry:

    def __init__(self, gateway: Gateway, children: Children,
                 dynamic_services_dir: Path, dynamic_metadata_dir: Path,
                 ready_timeout: float = 180,
                 log: Callable[[str], None] = lambda s: None):
        self.gateway = gateway
        self.children = children
        self.dynamic_services_dir = dynamic_services_dir
        self.dynamic_metadata_dir = dynamic_metadata_dir
        self.ready_timeout = ready_timeout
        self.log = log
        self._solvers: Dict[str, Solver] = {}
        self._lock = threading.Lock()

    def add(self, service_id: str, metadata: celaut_pb2.Metadata,
            service_dir: Optional[Path], metadata_file: Optional[Path],
            environment: Optional[Mapping[str, bytes]] = None) -> str:
        """Add a solver configuration and return its id. Adding it again does nothing."""
        if not is_service_id(service_id):
            raise ValueError(f"Not a SHA3-256 service id: {service_id!r}.")
        environment = dict(environment or {})
        configuration = api_pb2.SolverConfiguration(meta=metadata)
        for key, value in sorted(environment.items()):
            configuration.environment_variables.add(key=key, value=value)
        config_id = configuration_id(configuration)
        with self._lock:
            if config_id in self._solvers:
                return config_id
            child = self.children.add(ChildService(
                name=f"solver {config_id[:16]}",
                gateway=self.gateway,
                service_id=service_id,
                environment=environment,
                service_dir=service_dir,
                metadata_file=metadata_file,
                ready_timeout=self.ready_timeout,
                log=self.log,
            ))
            self._solvers[config_id] = Solver(config_id, service_id, configuration, child)
        self.log(f"Added the solver configuration {config_id} (service {service_id}).")
        return config_id

    def add_uploaded(self, metadata: celaut_pb2.Metadata, service_dir: str) -> str:
        """Add a solver that UploadSolver received.

        Move the service to the dynamic directory, so that the sorter can send
        it to the node if the node does not have it.
        """
        service_id = service_id_of(metadata)
        if not is_service_id(service_id):
            raise ValueError("The metadata of the solver has no SHA3-256 hash.")
        self.dynamic_services_dir.mkdir(parents=True, exist_ok=True)
        self.dynamic_metadata_dir.mkdir(parents=True, exist_ok=True)
        target = self.dynamic_services_dir / service_id
        if not target.exists():
            shutil.move(service_dir, target)
        elif Path(service_dir).is_dir():
            shutil.rmtree(service_dir, ignore_errors=True)
        else:
            Path(service_dir).unlink(missing_ok=True)
        metadata_file = self.dynamic_metadata_dir / service_id
        metadata_file.write_bytes(metadata.SerializeToString())
        return self.add(service_id, metadata, service_dir=target, metadata_file=metadata_file)

    def get(self, config_id: str) -> Optional[Solver]:
        with self._lock:
            return self._solvers.get(config_id)

    def all(self) -> List[Solver]:
        with self._lock:
            return list(self._solvers.values())

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._solvers)

    def __len__(self) -> int:
        with self._lock:
            return len(self._solvers)

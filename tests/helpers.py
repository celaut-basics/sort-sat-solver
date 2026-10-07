"""Test helpers: a fake node gateway and the child services as local processes.

No test needs a node. The fake gateway speaks bee-rpc with the request
indices of nodo protos/gateway_bee.py, like the real gateway.
"""
import os
import socket
import subprocess
import sys
import threading
from concurrent import futures
from pathlib import Path
from typing import Dict, List, Optional

import grpc
from bee_rpc import buffer_pb2
from bee_rpc.client import Dir, parse_from_buffer, serialize_to_buffer

from protos import celaut_pb2, celaut_pb2_grpc

ROOT = Path(__file__).resolve().parent.parent

# A copy of nodo protos/gateway_bee.py (dev, 698e6583), on purpose not imported
# from src.gateway: the fake gateway must not trust the code under test.
NODO_START_SERVICE_INDICES = {
    1: celaut_pb2.Client,
    2: celaut_pb2.RecursionGuard,
    3: celaut_pb2.Configuration,
    4: celaut_pb2.Metadata.HashTag.Hash,
    5: celaut_pb2.Metadata,
    6: celaut_pb2.Service,
}
NODO_START_SERVICE_MESSAGE_MODE = {1: True, 2: True, 3: True, 4: True, 5: True, 6: False}
SHA3_256_ID = bytes.fromhex("a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instance_message(address: str, internal_port: int = 1) -> celaut_pb2.Instance:
    ip, port = address.rsplit(":", 1)
    instance = celaut_pb2.Instance()
    instance.uri_slot.add(internal_port=internal_port).uri.add(ip=ip, port=int(port))
    return instance


class FakeGateway(celaut_pb2_grpc.GatewayServicer):
    """StartService and StopService of a node, for the tests.

    `addresses` maps a service id to the address of a running server. The
    "registry" is the set of service ids that the node has. StartService with
    an unknown service fails like the node does, unless the request has the
    service (index 6).
    """

    def __init__(self, addresses: Dict[str, str], registry: Optional[set] = None):
        self.addresses = addresses
        self.registry = set(addresses) if registry is None else set(registry)
        self.requests: List[list] = []
        self.started: Dict[str, str] = {}  # token -> service id
        self.stopped: List[str] = []
        self._lock = threading.Lock()
        self._next = 0

    def StartService(self, request_iterator, context):
        messages = list(parse_from_buffer(
            request_iterator=request_iterator,
            indices=dict(NODO_START_SERVICE_INDICES),
            partitions_message_mode=dict(NODO_START_SERVICE_MESSAGE_MODE),
        ))
        with self._lock:
            self.requests.append(messages)
        hashes = [m for m in messages if isinstance(m, celaut_pb2.Metadata.HashTag.Hash)]
        service_id = next((h.value.hex() for h in hashes if h.type == SHA3_256_ID), None)
        bodies = [m for m in messages if isinstance(m, Dir) and m.type == celaut_pb2.Service]
        if service_id is None:
            raise Exception("No hash of the type that this node uses.")
        if service_id not in self.registry:
            if not bodies:
                raise Exception(f"This node does not have the service {service_id}, "
                                "and the request does not contain it.")
            if not any(isinstance(m, celaut_pb2.Metadata) for m in messages):
                raise Exception("No metadata for the service.")
            self.registry.add(service_id)
        if service_id not in self.addresses:
            raise Exception(f"No server for {service_id}.")
        with self._lock:
            self._next += 1
            token = f"token-{self._next}"
            self.started[token] = service_id
        # The node sends a signal buffer before the answer.
        yield buffer_pb2.Buffer(signal=True)
        yield from serialize_to_buffer(
            message_iterator=celaut_pb2.ServiceInstance(
                token=token, instance=instance_message(self.addresses[service_id])),
            indices={1: celaut_pb2.ServiceInstance},
        )

    def StopService(self, request_iterator, context):
        token = next(parse_from_buffer(
            request_iterator=request_iterator,
            indices={1: celaut_pb2.TokenMessage},
            partitions_message_mode=True,
        )).token
        with self._lock:
            self.stopped.append(token)
        yield from serialize_to_buffer(
            message_iterator=celaut_pb2.Refund(),
            indices={1: celaut_pb2.Refund},
        )

    def running(self) -> List[str]:
        with self._lock:
            return [t for t in self.started if t not in self.stopped]


def serve_gateway(gateway: FakeGateway):
    port = free_port()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    celaut_pb2_grpc.add_GatewayServicer_to_server(gateway, server)
    server.add_insecure_port(f"127.0.0.1:{port}")
    server.start()
    return server, f"127.0.0.1:{port}"


class ChildProcess:
    """A service of dependencies/ or solvers/ in its own process.

    Each service has its own api.proto, so it runs in its own process, as it
    does in its own microVM.
    """

    def __init__(self, directory: str, env: Optional[Dict[str, str]] = None):
        self.port = free_port()
        self.address = f"127.0.0.1:{self.port}"
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-c", f"import start; start.serve({self.port}).wait_for_termination()"],
            cwd=ROOT / directory,
            env={**os.environ, **(env or {})},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = 60) -> None:
        channel = grpc.insecure_channel(self.address)
        grpc.channel_ready_future(channel).result(timeout=timeout)
        channel.close()

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()

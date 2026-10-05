"""A small client of the gateway of the node (celaut-project/nodo, branch dev).

The sorter uses three gateway calls: StartService and StopService for its
child services, and the address of the gateway from __config__.

- The sorter is a local instance of the node. The gateway identifies a local
  instance by its address, so the sorter does not send a celaut.Client
  (nodo src/gateway/client_gate.py, require_caller).
- A launch from a local instance starts a new recursion tree, so the sorter
  does not send a celaut.RecursionGuard (nodo docs/RECURSION_GUARD.md).
- The request indices are those of nodo protos/gateway_bee.py
  (StartService_input_indices). tests/test_gateway.py checks the requests
  against a fake gateway that parses them with the same indices.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, Mapping, Optional

import grpc
from bee_rpc.client import Dir, client_grpc

from protos import celaut_pb2, celaut_pb2_grpc

# The hash type of the service ids: SHA3-256 (the default of the node).
SHA3_256_ID = bytes.fromhex("a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a")

# nodo protos/gateway_bee.py: StartService_input_indices.
START_SERVICE_INDICES = {
    1: celaut_pb2.Client,
    2: celaut_pb2.RecursionGuard,
    3: celaut_pb2.Configuration,
    4: celaut_pb2.Metadata.HashTag.Hash,
    5: celaut_pb2.Metadata,
    6: celaut_pb2.Service,
}
START_SERVICE_OUTPUT = {1: celaut_pb2.ServiceInstance}
STOP_SERVICE_INDICES = {1: celaut_pb2.TokenMessage}
STOP_SERVICE_OUTPUT = {1: celaut_pb2.Refund}

# The node answers with these codes when it cannot be reached or when the
# call is too slow. Then it does not help to send the service.
_DO_NOT_SEND_SERVICE = (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)


class GatewayError(Exception):
    pass


@dataclass(frozen=True)
class ChildInstance:
    token: str
    address: str  # "<ip>:<port>"


def address_of(instance: celaut_pb2.Instance, port: Optional[int] = None) -> str:
    """Return "<ip>:<port>" of the first address of an instance.

    With `port`, use only the addresses of that port of the instance.
    """
    for slot in instance.uri_slot:
        if port is not None and slot.internal_port != port:
            continue
        for uri in slot.uri:
            return f"{uri.ip}:{uri.port}"
    # An empty list of addresses means that the caller must use
    # Gateway.ServiceTunnel (nodo docs/TUNNELING.md). The sorter does not.
    raise GatewayError(f"The instance has no address{'' if port is None else f' for the port {port}'}.")


def configuration(environment: Mapping[str, bytes]) -> celaut_pb2.Configuration:
    config = celaut_pb2.Configuration()
    for key, value in sorted(environment.items()):
        config.environment_variables.add(key=key, value=value)
    return config


def service_hash(service_id: str) -> celaut_pb2.Metadata.HashTag.Hash:
    return celaut_pb2.Metadata.HashTag.Hash(type=SHA3_256_ID, value=bytes.fromhex(service_id))


def minimal_metadata(service_id: str) -> celaut_pb2.Metadata:
    return celaut_pb2.Metadata(hashtag=celaut_pb2.Metadata.HashTag(hash=[service_hash(service_id)]))


def read_metadata(path: Optional[Path]) -> Optional[celaut_pb2.Metadata]:
    if path is None or not path.is_file():
        return None
    metadata = celaut_pb2.Metadata()
    metadata.ParseFromString(path.read_bytes())
    return metadata


def start_service_request(service_id: str,
                          environment: Mapping[str, bytes],
                          metadata: Optional[celaut_pb2.Metadata] = None,
                          service_dir: Optional[Path] = None) -> Iterator[object]:
    """The messages of a StartService request.

    The Configuration goes before the hash: the node starts the service when it
    knows it, and it reads the Configuration that it received before that.
    bee-rpc does not send an empty message, so an empty Configuration does not
    arrive. For the node, that is the same as no Configuration.
    """
    yield configuration(environment)
    yield service_hash(service_id)
    if service_dir is not None:
        if metadata is not None:
            yield metadata
        yield Dir(dir=str(service_dir), _type=celaut_pb2.Service)


class Gateway:

    def __init__(self, address: str,
                 start_timeout: float = 600,
                 stop_timeout: float = 60,
                 log: Callable[[str], None] = lambda s: None,
                 stub: Optional[celaut_pb2_grpc.GatewayStub] = None):
        self.address = address
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.log = log
        self.stub = stub or celaut_pb2_grpc.GatewayStub(grpc.insecure_channel(address))

    def _start(self, request: Iterator[object]) -> celaut_pb2.ServiceInstance:
        try:
            return next(client_grpc(
                method=self.stub.StartService,
                input=request,
                indices_serializer=dict(START_SERVICE_INDICES),
                indices_parser=dict(START_SERVICE_OUTPUT),
                partitions_message_mode_parser=True,
                timeout=self.start_timeout,
            ))
        except StopIteration:
            raise GatewayError("StartService ended without a ServiceInstance.")

    def start_service(self, service_id: str,
                      environment: Optional[Mapping[str, bytes]] = None,
                      service_dir: Optional[Path] = None,
                      metadata_file: Optional[Path] = None,
                      port: Optional[int] = None) -> ChildInstance:
        """Start an instance of a service and return its token and address.

        First the request has only the hash, because the node keeps the services
        that it received before. If the node does not have the service, the
        request also sends the metadata and the service from `service_dir`.
        """
        environment = environment or {}
        try:
            instance = self._start(start_service_request(service_id, environment))
        except grpc.RpcError as e:
            if service_dir is None or not service_dir.exists() or e.code() in _DO_NOT_SEND_SERVICE:
                raise
            self.log(f"StartService with the hash of {service_id} failed ({e.code()}: {e.details()}). "
                     f"Send the service.")
            # The node must receive a Metadata with the service: it saves it in
            # its registry. bee-rpc does not send an empty message, so a
            # Metadata without a file has the hash.
            instance = self._start(start_service_request(
                service_id, environment,
                metadata=read_metadata(metadata_file) or minimal_metadata(service_id),
                service_dir=service_dir,
            ))
        if not instance.token:
            raise GatewayError(f"StartService of {service_id} gave no instance token.")
        return ChildInstance(token=instance.token, address=address_of(instance.instance, port))

    def stop_service(self, token: str) -> None:
        try:
            next(client_grpc(
                method=self.stub.StopService,
                input=celaut_pb2.TokenMessage(token=token),
                indices_serializer=dict(STOP_SERVICE_INDICES),
                indices_parser=dict(STOP_SERVICE_OUTPUT),
                partitions_message_mode_parser=True,
                timeout=self.stop_timeout,
            ))
        except StopIteration:
            pass


def gateway_address(config: celaut_pb2.ConfigurationFile) -> str:
    """The address of the gateway, from the ConfigurationFile of the instance."""
    return address_of(config.gateway)


def read_configuration_file(path: Path) -> celaut_pb2.ConfigurationFile:
    config = celaut_pb2.ConfigurationFile()
    config.ParseFromString(path.read_bytes())
    return config


def environment_bytes(values: Mapping[str, object]) -> Dict[str, bytes]:
    return {key: str(value).encode() for key, value in values.items()}

"""The requests of src/gateway.py, parsed by a fake gateway with the indices of nodo."""
import tempfile
import unittest
from pathlib import Path

import grpc
from bee_rpc.client import Dir
from bee_rpc.utils import modify_env

from protos import celaut_pb2
from src.gateway import Gateway
from tests.helpers import FakeGateway, SHA3_256_ID, serve_gateway

SERVICE_ID = "ab" * 32


class GatewayTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        modify_env(cache_dir=str(tmp / "cache") + "/", block_dir=str(tmp / "block") + "/")
        (tmp / "cache" / "grpcbigbuffer").mkdir(parents=True)
        (tmp / "block").mkdir()
        # A service without blocks is a file with the serialized celaut.Service.
        self.service_file = tmp / SERVICE_ID
        self.service_file.write_bytes(celaut_pb2.Service(
            container=celaut_pb2.Service.Container(architecture=celaut_pb2.Service.Container.Architecture(
                tags=["linux/amd64"])),
        ).SerializeToString())
        self.metadata_file = tmp / "metadata"
        self.metadata_file.write_bytes(celaut_pb2.Metadata(
            hashtag=celaut_pb2.Metadata.HashTag(tag=["frontier"])).SerializeToString())

    def tearDown(self):
        self.server.stop(None)
        self.tmp.cleanup()

    def start(self, registry):
        self.fake = FakeGateway({SERVICE_ID: "10.0.0.5:8080"}, registry=registry)
        self.server, address = serve_gateway(self.fake)
        return Gateway(address, start_timeout=30, stop_timeout=30)

    def test_known_service_sends_only_the_hash(self):
        gateway = self.start(registry={SERVICE_ID})
        child = gateway.start_service(SERVICE_ID, {"FRONTIER_TIMEOUT": b"5"},
                                      service_dir=self.service_file, metadata_file=self.metadata_file)
        self.assertEqual(child.address, "10.0.0.5:8080")
        self.assertEqual(len(self.fake.requests), 1)
        request = self.fake.requests[0]
        self.assertEqual([type(m) for m in request],
                         [celaut_pb2.Configuration, celaut_pb2.Metadata.HashTag.Hash])
        config, _hash = request
        self.assertEqual({kv.key: kv.value for kv in config.environment_variables}, {"FRONTIER_TIMEOUT": b"5"})
        self.assertEqual((_hash.type, _hash.value.hex()), (SHA3_256_ID, SERVICE_ID))
        # A local instance sends no Client and no RecursionGuard.
        self.assertFalse(any(isinstance(m, (celaut_pb2.Client, celaut_pb2.RecursionGuard)) for m in request))

    def test_unknown_service_is_sent(self):
        gateway = self.start(registry=set())
        child = gateway.start_service(SERVICE_ID, service_dir=self.service_file, metadata_file=self.metadata_file)
        self.assertEqual(child.token, "token-1")
        self.assertEqual(len(self.fake.requests), 2)
        second = self.fake.requests[1]
        # bee-rpc does not send the empty Configuration.
        self.assertEqual([type(m) for m in second],
                         [celaut_pb2.Metadata.HashTag.Hash, celaut_pb2.Metadata, Dir])
        self.assertEqual(list(second[1].hashtag.tag), ["frontier"])
        received = celaut_pb2.Service()
        received.ParseFromString(Path(second[2].dir).read_bytes())
        self.assertEqual(list(received.container.architecture.tags), ["linux/amd64"])

    def test_unknown_service_without_a_copy_fails(self):
        gateway = self.start(registry=set())
        with self.assertRaises(grpc.RpcError):
            gateway.start_service(SERVICE_ID, service_dir=None)
        self.assertEqual(len(self.fake.requests), 1)

    def test_missing_metadata_sends_the_hash_in_a_metadata(self):
        # bee-rpc does not send an empty message, and the node needs a Metadata.
        gateway = self.start(registry=set())
        gateway.start_service(SERVICE_ID, service_dir=self.service_file, metadata_file=Path(self.tmp.name) / "x")
        metadata = self.fake.requests[1][1]
        self.assertIsInstance(metadata, celaut_pb2.Metadata)
        self.assertEqual([h.value.hex() for h in metadata.hashtag.hash], [SERVICE_ID])

    def test_stop_service(self):
        gateway = self.start(registry={SERVICE_ID})
        child = gateway.start_service(SERVICE_ID)
        gateway.stop_service(child.token)
        self.assertEqual(self.fake.stopped, [child.token])


if __name__ == "__main__":
    unittest.main()

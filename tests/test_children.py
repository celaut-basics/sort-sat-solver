import unittest
from concurrent import futures

import grpc

from src.children import ChildService, Children
from src.gateway import ChildInstance
from tests.helpers import free_port


class FakeRpcError(grpc.RpcError):

    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code

    def details(self):
        return str(self._code)


class StubGateway:
    """Starts nothing: every instance is the same local gRPC server."""

    def __init__(self, address):
        self.address = address
        self.started = []
        self.stopped = []

    def start_service(self, service_id, environment, service_dir=None, metadata_file=None):
        token = f"t{len(self.started) + 1}"
        self.started.append((service_id, dict(environment)))
        return ChildInstance(token=token, address=self.address)

    def stop_service(self, token):
        self.stopped.append(token)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ChildServiceTest(unittest.TestCase):

    def setUp(self):
        port = free_port()
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        self.server.add_insecure_port(f"127.0.0.1:{port}")
        self.server.start()
        self.gateway = StubGateway(f"127.0.0.1:{port}")
        self.clock = Clock()
        self.child = ChildService("test", self.gateway, "ab" * 32, {"A": b"1"},
                                  ready_timeout=10, clock=self.clock)

    def tearDown(self):
        self.server.stop(None)

    def test_starts_at_the_first_call_only(self):
        self.assertFalse(self.child.running)
        self.assertEqual(self.child.call(lambda channel: 1), 1)
        self.assertEqual(self.child.call(lambda channel: 2), 2)
        self.assertEqual(self.gateway.started, [("ab" * 32, {"A": b"1"})])

    def test_replaces_an_unreachable_instance_one_time(self):
        calls = []

        def unavailable_once(channel):
            calls.append(1)
            if len(calls) == 1:
                raise FakeRpcError(grpc.StatusCode.UNAVAILABLE)
            return "ok"

        self.assertEqual(self.child.call(unavailable_once), "ok")
        self.assertEqual(len(self.gateway.started), 2)
        self.assertEqual(self.gateway.stopped, ["t1"])

        with self.assertRaises(FakeRpcError):
            self.child.call(lambda channel: (_ for _ in ()).throw(FakeRpcError(grpc.StatusCode.UNAVAILABLE)))
        self.assertEqual(len(self.gateway.started), 3)

    def test_other_errors_do_not_replace(self):
        with self.assertRaises(FakeRpcError):
            self.child.call(lambda channel: (_ for _ in ()).throw(FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)))
        self.assertEqual(len(self.gateway.started), 1)
        self.assertEqual(self.gateway.stopped, [])

    def test_idle_instance_stops(self):
        self.child.call(lambda channel: None)
        self.clock.now = 50
        self.assertFalse(self.child.stop_if_idle(100))
        self.clock.now = 200
        self.assertTrue(self.child.stop_if_idle(100))
        self.assertEqual(self.gateway.stopped, ["t1"])
        self.assertFalse(self.child.running)
        # The next call starts a new instance.
        self.child.call(lambda channel: None)
        self.assertEqual(len(self.gateway.started), 2)

    def test_busy_instance_does_not_stop(self):
        def long_call(channel):
            self.clock.now = 1000
            self.assertFalse(self.child.stop_if_idle(100))

        self.child.call(long_call)

    def test_not_ready_instance_is_stopped(self):
        self.gateway.address = f"127.0.0.1:{free_port()}"  # Nothing listens there.
        self.child.ready_timeout = 0.5
        with self.assertRaises(TimeoutError):
            self.child.call(lambda channel: None)
        self.assertEqual(self.gateway.stopped, ["t1"])
        self.assertFalse(self.child.running)

    def test_children_close_stops_all(self):
        children = Children(idle_timeout=0)
        other = ChildService("other", self.gateway, "cd" * 32, clock=self.clock)
        children.add(self.child)
        children.add(other)
        self.child.call(lambda channel: None)
        other.call(lambda channel: None)
        children.close()
        self.assertEqual(sorted(self.gateway.stopped), ["t1", "t2"])
        # A second close does nothing.
        children.close()
        self.assertEqual(len(self.gateway.stopped), 2)


if __name__ == "__main__":
    unittest.main()

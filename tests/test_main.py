"""Run the sorter process (python3 -m src.main) as the node runs it.

The test copies the files of the service to a temporary directory with a
__config__ file (as `nodo ggconf` writes it) and a .dependencies file (as the
packer writes it). The fake gateway gives the addresses of local child
processes. At SIGTERM the sorter must stop the instances that it started.
"""
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import grpc
from bee_rpc import buffer_pb2
from bee_rpc.client import client_grpc

from protos import api_pb2, api_pb2_grpc, celaut_pb2
from src import cnf as cnf_utils, config
from src.gateway import minimal_metadata
from tests.helpers import ROOT, ChildProcess, FakeGateway, instance_message, serve_gateway

RANDOM_ID = "11" * 32
REGRESSION_ID = "22" * 32
FRONTIER_ID = "33" * 32


class MainTest(unittest.TestCase):

    def setUp(self):
        with socket_free(config.PORT) as free:
            if not free:
                self.skipTest(f"The port {config.PORT} is in use.")
        self.tmp = tempfile.TemporaryDirectory()
        app = Path(self.tmp.name)
        for name in ("src", "protos"):
            shutil.copytree(ROOT / name, app / name, ignore=shutil.ignore_patterns("__pycache__"))

        self.frontier = ChildProcess("solvers/frontier")
        self.frontier.wait_ready()
        self.fake = FakeGateway({FRONTIER_ID: self.frontier.address})
        self.gateway_server, gateway_address = serve_gateway(self.fake)

        configuration_file = celaut_pb2.ConfigurationFile(gateway=instance_message(gateway_address))
        configuration_file.config.environment_variables.add(key="SOLVE_TIMEOUT", value=b"20")
        (app / "__config__").write_bytes(configuration_file.SerializeToString())
        (app / ".dependencies").write_text(
            f"REGRESSION={REGRESSION_ID}\nRANDOM={RANDOM_ID}\nSOLVER_FRONTIER={FRONTIER_ID}\n")
        (app / "__metadata__").mkdir()
        (app / "__metadata__" / FRONTIER_ID).write_bytes(minimal_metadata(FRONTIER_ID).SerializeToString())

        self.process = subprocess.Popen([sys.executable, "-B", "-m", "src.main"], cwd=app,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.app = app

    def tearDown(self):
        if self.process.poll() is None:
            self.process.kill()
        self.frontier.stop()
        self.gateway_server.stop(None)
        self.tmp.cleanup()

    def test_solve_and_stop(self):
        channel = grpc.insecure_channel(f"127.0.0.1:{config.PORT}")
        grpc.channel_ready_future(channel).result(timeout=60)
        cnf = cnf_utils.from_dimacs(["1 2 0", "-1 2 0", "-2 3 0"])
        answer = next(client_grpc(
            method=api_pb2_grpc.SolverStub(channel).Solve,
            input=cnf, indices_serializer=api_pb2.Cnf,
            indices_parser={1: api_pb2.Interpretation, 2: buffer_pb2.Empty},
            partitions_message_mode_parser=True, timeout=60,
        ))
        self.assertTrue(cnf_utils.is_model(cnf, answer))
        self.assertEqual(len(self.fake.running()), 1)

        # The command line client of tools/client.py.
        problem = self.app / "problem.cnf"
        problem.write_text("p cnf 3 3\n1 2 0\n-1 2 0\n-2 3 0\n")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "client.py"),
                                 f"127.0.0.1:{config.PORT}", "solve", str(problem)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "SAT")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "client.py"),
                                 f"127.0.0.1:{config.PORT}", "get-dataset", str(self.app / "dataset.bin")],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.process.send_signal(signal.SIGTERM)
        output, _ = self.process.communicate(timeout=60)
        self.assertEqual(self.process.returncode, 0, output)
        self.assertEqual(self.fake.running(), [], output)
        self.assertIn("Stopped.", output)
        self.assertTrue((self.app / "app.log").is_file())


class socket_free:
    def __init__(self, port):
        import socket
        self.socket = socket.socket()
        self.port = port

    def __enter__(self):
        try:
            self.socket.bind(("0.0.0.0", self.port))
            return True
        except OSError:
            return False

    def __exit__(self, *args):
        self.socket.close()


if __name__ == "__main__":
    unittest.main()

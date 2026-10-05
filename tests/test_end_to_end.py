"""The sorter with its real child services and a fake node gateway.

The random CNF generator, the frontier solver and the regression run as local
processes. The fake gateway "starts" a service: it gives the address of the
process of that service. The sorter runs in this process with its real gRPC
server, and the test calls it over gRPC with bee-rpc, as a client does.
"""
import tempfile
import time
import unittest
from pathlib import Path

import grpc
from bee_rpc import buffer_pb2
from bee_rpc.client import Dir, client_grpc
from bee_rpc.utils import modify_env

from protos import api_pb2, api_pb2_grpc, celaut_pb2, solvers_dataset_pb2 as sd_pb2
from src.children import ChildService, Children
from src.gateway import Gateway, minimal_metadata
from src.ranker import Ranker
from src.regression import Regression
from src.server import SorterServicer, serve
from src.solvers import SolverRegistry
from src.trainer import Trainer
from src import config, cnf as cnf_utils
from tests.helpers import ChildProcess, FakeGateway, free_port, serve_gateway

RANDOM_ID = "11" * 32
REGRESSION_ID = "22" * 32
FRONTIER_ID = "33" * 32
UPLOADED_ID = "44" * 32


class EndToEndTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import skl2onnx  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f"The regression packages are not installed: {e}")
        cls.processes = [
            # At most 2 clauses for each variable: almost each CNF is SAT, so the
            # solver does not wait for its time limit.
            ChildProcess("dependencies/random_cnf_generator",
                         {"MIN_VARIABLES": "20", "MAX_VARIABLES": "30", "MIN_CLAUSES": "5", "MAX_CLAUSES": "40"}),
            ChildProcess("dependencies/regresion_cnf", {"MIN_SAMPLES": "2"}),
            ChildProcess("solvers/frontier"),
        ]
        for process in cls.processes:
            process.wait_ready()

    @classmethod
    def tearDownClass(cls):
        for process in cls.processes:
            process.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        modify_env(cache_dir=str(tmp / "cache") + "/", block_dir=str(tmp / "block") + "/",
                   skip_wbp_generation=True)
        (tmp / "cache" / "grpcbigbuffer").mkdir(parents=True)
        (tmp / "block").mkdir()
        random_process, regression_process, frontier_process = self.processes
        self.fake = FakeGateway({
            RANDOM_ID: random_process.address,
            REGRESSION_ID: regression_process.address,
            FRONTIER_ID: frontier_process.address,
            UPLOADED_ID: frontier_process.address,
        }, registry={RANDOM_ID, REGRESSION_ID, FRONTIER_ID})
        self.gateway_server, address = serve_gateway(self.fake)
        gateway = Gateway(address, start_timeout=30)

        self.children = Children(idle_timeout=0)
        regression_child = self.children.add(ChildService("regression", gateway, REGRESSION_ID, ready_timeout=30))
        random_child = self.children.add(ChildService("random", gateway, RANDOM_ID, ready_timeout=30))
        self.solvers = SolverRegistry(gateway, self.children, tmp / "dyn" / "services", tmp / "dyn" / "metadata",
                                      ready_timeout=30)
        self.frontier_id = self.solvers.add(FRONTIER_ID, minimal_metadata(FRONTIER_ID), None, None)
        self.ranker = Ranker()
        self.regression = Regression(regression_child, self.ranker, interval=3600, call_timeout=120)
        self.trainer = Trainer(random_child, self.solvers, self.regression, rounds_per_update=3,
                               solver_timeout=10, error_pause=0.1)
        envs = config.parse_envs({'SOLVE_TIMEOUT': '20'})
        servicer = SorterServicer(self.solvers, self.ranker, self.regression, self.trainer, envs)
        port = free_port()
        self.server = serve(servicer, port, max_workers=8)
        self.stub = api_pb2_grpc.SolverStub(grpc.insecure_channel(f"localhost:{port}"))

    def tearDown(self):
        self.trainer.stop()
        self.server.stop(None)
        self.children.close()
        self.gateway_server.stop(None)
        self.tmp.cleanup()

    def solve(self, cnf):
        return next(client_grpc(
            method=self.stub.Solve,
            input=cnf,
            indices_serializer=api_pb2.Cnf,
            indices_parser={1: api_pb2.Interpretation, 2: buffer_pb2.Empty},
            partitions_message_mode_parser=True,
            timeout=60,
        ), None)

    def call_empty(self, method):
        list(client_grpc(method=method, timeout=60))

    def test_solve_train_and_rank(self):
        cnf = cnf_utils.from_dimacs(["1 -2 3 0", "-1 2 0", "2 3 0", "-3 1 0"])
        # Before the training there is no model: the sorter uses its only solver.
        interpretation = self.solve(cnf)
        self.assertIsInstance(interpretation, api_pb2.Interpretation)
        self.assertTrue(cnf_utils.is_model(cnf, interpretation))

        answered = 0
        for _ in range(6):
            scores = self.trainer.run_round()
            self.assertEqual(list(scores), [self.frontier_id])
            self.assertGreaterEqual(scores[self.frontier_id], 0)
            answered += scores[self.frontier_id] > 0
        self.assertGreater(answered, 0)

        data_set = next(client_grpc(method=self.stub.GetDataSet, indices_parser=sd_pb2.DataSet,
                                    partitions_message_mode_parser=True, timeout=60))
        self.assertEqual(len(data_set.data), 1)
        self.assertEqual(data_set.data[0].configuration_hash.hex(), self.frontier_id)
        self.assertEqual(data_set.data[0].service_hash.hex(), FRONTIER_ID)
        self.assertEqual(sum(d.index for d in data_set.data[0].data.values()), 6)

        self.assertTrue(self.regression.run_once())
        self.assertFalse(self.regression.run_once())  # The dataset did not change.
        tensor = next(client_grpc(method=self.stub.GetTensor, indices_parser=api_pb2.Tensor,
                                  partitions_message_mode_parser=True, timeout=60))
        self.assertEqual(len(tensor.non_escalar.non_escalar), 1)
        element = tensor.non_escalar.non_escalar[0]
        self.assertEqual(element.element.meta.hashtag.hash[0].value.hex(), FRONTIER_ID)
        self.assertGreater(len(element.escalar), 0)

        interpretation = self.solve(cnf)
        self.assertTrue(cnf_utils.is_model(cnf, interpretation))

        # One instance for each child service, all running until close().
        self.assertEqual(len(self.fake.started), 3)
        self.children.close()
        self.assertEqual(self.fake.running(), [])

    def test_unsat_and_invalid(self):
        unsat = cnf_utils.from_dimacs(["1 0", "-1 0", "2 0"])
        unsat.clause.add()  # An empty clause: frontier can prove UNSAT.
        interpretation = self.solve(unsat)
        self.assertFalse(cnf_utils.claims_sat(interpretation))

        with self.assertRaises(grpc.RpcError) as e:
            self.solve(api_pb2.Cnf(clause=[api_pb2.Clause(literal=[1, 0])]))
        self.assertEqual(e.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

        with self.assertRaises(grpc.RpcError) as e:
            self.call_empty(self.stub.AddTensor)
        self.assertEqual(e.exception.code(), grpc.StatusCode.UNIMPLEMENTED)

    def test_no_answer(self):
        # frontier cannot prove UNSAT without an empty clause: no answer in the time limit.
        self.solvers.get(self.frontier_id)  # Registered.
        servicer_envs = config.parse_envs({'SOLVE_TIMEOUT': '1', 'MAX_ERRORS_FOR_SOLVER': '1'})
        port = free_port()
        server = serve(SorterServicer(self.solvers, self.ranker, self.regression, self.trainer, servicer_envs),
                       port, max_workers=2)
        try:
            stub = api_pb2_grpc.SolverStub(grpc.insecure_channel(f"localhost:{port}"))
            answer = next(client_grpc(
                method=stub.Solve, input=cnf_utils.from_dimacs(["1 0", "-1 0"]),
                indices_serializer=api_pb2.Cnf,
                indices_parser={1: api_pb2.Interpretation, 2: buffer_pb2.Empty},
                partitions_message_mode_parser=True, timeout=60,
            ), None)
            # bee-rpc does not send the empty message, so the stream is empty.
            self.assertNotIsInstance(answer, api_pb2.Interpretation)
        finally:
            server.stop(None)

    def test_upload_solver_and_add_data_set(self):
        service_file = Path(self.tmp.name) / "uploaded-service"
        service_file.write_bytes(celaut_pb2.Service(
            container=celaut_pb2.Service.Container(
                architecture=celaut_pb2.Service.Container.Architecture(tags=["linux/amd64"]))
        ).SerializeToString())
        metadata = minimal_metadata(UPLOADED_ID)
        metadata.hashtag.tag.append("uploaded-frontier")
        list(client_grpc(
            method=self.stub.UploadSolver,
            input=(metadata, Dir(dir=str(service_file), _type=celaut_pb2.Service)),
            indices_serializer={1: celaut_pb2.Metadata, 2: celaut_pb2.Service},
            timeout=60,
        ))
        self.assertEqual(len(self.solvers), 2)
        stored = Path(self.tmp.name) / "dyn" / "services" / UPLOADED_ID
        self.assertTrue(stored.exists())

        # The node does not have the uploaded solver, so the sorter sends it.
        scores = self.trainer.run_round()
        self.assertEqual(len(scores), 2)
        uploaded_requests = [r for r in self.fake.requests
                             if any(getattr(m, "value", b"").hex() == UPLOADED_ID for m in r)]
        self.assertEqual(len(uploaded_requests), 2)
        self.assertTrue(any(isinstance(m, Dir) for m in uploaded_requests[1]))

        new = sd_pb2.DataSet()
        instance = new.data.add(configuration_hash=b"\x05" * 32)
        instance.data["3:4"].score, instance.data["3:4"].index = 0.5, 2
        list(client_grpc(method=self.stub.AddDataSet, input=new, indices_serializer=sd_pb2.DataSet, timeout=60))
        self.assertIn(b"\x05" * 32, [i.configuration_hash for i in self.regression.get_data_set().data])

    def test_start_and_stop_train(self):
        self.call_empty(self.stub.StartTrain)
        deadline = time.monotonic() + 60
        while self.regression.get_data_set().ByteSize() == 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        self.call_empty(self.stub.StopTrain)
        self.assertFalse(self.trainer.running)
        self.assertGreater(len(self.regression.get_data_set().data), 0)


if __name__ == "__main__":
    unittest.main()

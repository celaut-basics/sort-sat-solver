"""The gRPC API of the sorter (api.Solver in protos/api.proto, over bee-rpc)."""
import time
from concurrent import futures
from pathlib import Path
from typing import Callable, Dict, Optional

import grpc
from bee_rpc import buffer_pb2
from bee_rpc.client import Dir, parse_from_buffer, serialize_to_buffer

from protos import api_pb2, api_pb2_grpc, celaut_pb2, solvers_dataset_pb2 as sd_pb2
from src import cnf as cnf_utils
from src.ranker import Ranker
from src.regression import Regression
from src.solvers import SolverRegistry
from src.trainer import Trainer

SOLVE_OUTPUT_INDICES = {1: api_pb2.Interpretation, 2: buffer_pb2.Empty}
UPLOAD_SOLVER_INDICES = {1: celaut_pb2.Metadata, 2: celaut_pb2.Service}
# The service is large: bee-rpc writes it to a directory (Dir), not to memory.
UPLOAD_SOLVER_MESSAGE_MODE = {1: True, 2: False}

LOG_CHUNK_SIZE = 64 * 1024


class SorterServicer(api_pb2_grpc.SolverServicer):

    def __init__(self, solvers: SolverRegistry, ranker: Ranker, regression: Regression,
                 trainer: Trainer, envs: Dict[str, object], log_file: Optional[Path] = None,
                 log: Callable[[str], None] = lambda s: None):
        self.solvers = solvers
        self.ranker = ranker
        self.regression = regression
        self.trainer = trainer
        self.envs = envs
        self.log_file = log_file
        self.log = log

    # -- Solve ----------------------------------------------------------------

    def Solve(self, request_iterator, context):
        cnf = next(parse_from_buffer(
            request_iterator=request_iterator,
            indices=api_pb2.Cnf,
            partitions_message_mode=True,
        ))
        try:
            cnf_utils.validate(cnf)
        except cnf_utils.InvalidCnf as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

        ranking = self.ranker.rank(cnf, self.solvers.ids())
        for config_id in ranking[:self.envs['MAX_ERRORS_FOR_SOLVER']]:
            solver = self.solvers.get(config_id)
            if solver is None or not context.is_active():
                continue
            self.log(f"Solve a CNF of shape {cnf_utils.shape_key(cnf)} with {config_id[:16]}.")
            interpretation, seconds = solver.try_solve(cnf, self.envs['SOLVE_TIMEOUT'], log=self.log)
            if interpretation is None:
                continue
            if cnf_utils.claims_sat(interpretation) and not cnf_utils.is_model(cnf, interpretation):
                self.log(f"The solver {config_id[:16]} gave a false model. Try the next solver.")
                continue
            self.log(f"Solved in {seconds:.3f} s by {config_id[:16]}.")
            yield from serialize_to_buffer(message_iterator=interpretation, indices=SOLVE_OUTPUT_INDICES)
            return

        self.log("No solver gave an answer." if ranking else "No solver. Add one with UploadSolver.")
        yield from serialize_to_buffer(message_iterator=buffer_pb2.Empty(), indices=SOLVE_OUTPUT_INDICES)

    # -- Solvers --------------------------------------------------------------

    def UploadSolver(self, request_iterator, context):
        metadata, service_dir = None, None
        for message in parse_from_buffer(
                request_iterator=request_iterator,
                indices=UPLOAD_SOLVER_INDICES,
                partitions_message_mode=UPLOAD_SOLVER_MESSAGE_MODE,
        ):
            if isinstance(message, celaut_pb2.Metadata):
                metadata = message
            elif isinstance(message, Dir) and message.type == celaut_pb2.Service:
                service_dir = message.dir
        if metadata is None or service_dir is None:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          "UploadSolver needs 1: celaut.Metadata and 2: celaut.Service.")
        try:
            config_id = self.solvers.add_uploaded(metadata, service_dir)
        except ValueError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        self.log(f"Uploaded the solver configuration {config_id}.")
        yield from serialize_to_buffer()

    # -- Training -------------------------------------------------------------

    def StartTrain(self, request_iterator, context):
        if not self.trainer.start():
            self.log("The training runs already.")
        yield from serialize_to_buffer()

    def StopTrain(self, request_iterator, context):
        self.trainer.stop()
        yield from serialize_to_buffer()

    # -- Models and data ------------------------------------------------------

    def GetTensor(self, request_iterator, context):
        tensor = api_pb2.Tensor()
        for config_id, model in sorted(self.ranker.models().items()):
            solver = self.solvers.get(config_id)
            if solver is None:
                continue
            tensor.non_escalar.non_escalar.add(element=solver.configuration, escalar=model)
        yield from serialize_to_buffer(message_iterator=tensor)

    def AddTensor(self, request_iterator, context):
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "AddTensor is not implemented. Use AddDataSet.")

    def GetDataSet(self, request_iterator, context):
        yield from serialize_to_buffer(message_iterator=self.regression.get_data_set())

    def AddDataSet(self, request_iterator, context):
        self.regression.add_data(next(parse_from_buffer(
            request_iterator=request_iterator,
            indices=sd_pb2.DataSet,
            partitions_message_mode=True,
        )))
        yield from serialize_to_buffer()

    # -- Logs -----------------------------------------------------------------

    def StreamLogs(self, request_iterator, context):
        """Send the log of the sorter, then the new lines, until the caller cancels."""
        if self.log_file is None or not self.log_file.is_file():
            return
        with open(self.log_file, errors="replace") as file:
            while context.is_active():
                chunk = file.read(LOG_CHUNK_SIZE)
                if chunk:
                    yield from serialize_to_buffer(message_iterator=api_pb2.File(file=chunk))
                else:
                    time.sleep(0.5)


def serve(servicer: SorterServicer, port: int, max_workers: int) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    api_pb2_grpc.add_SolverServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server

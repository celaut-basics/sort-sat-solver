"""gRPC entry point of the frontier solver (api.Solver/Solve, bee-rpc)."""
import os
import time
from concurrent import futures

import grpc
from bee_rpc.client import parse_from_buffer, serialize_to_buffer

import api_pb2
import api_pb2_grpc
import frontier

PORT = 8080  # Must agree with .service/service.json.
# Search time limit of one Solve call in seconds. 0 means no limit: the search
# stops only when the caller cancels the call (for example, at its timeout).
TIMEOUT = float(os.environ.get("FRONTIER_TIMEOUT", "0") or 0)
MAX_WORKERS = 4


class Solver(api_pb2_grpc.SolverServicer):

    def Solve(self, request_iterator, context):
        # bee-rpc does not send an empty message: no message is an empty CNF.
        cnf = next(parse_from_buffer(
            request_iterator=request_iterator,
            indices=api_pb2.Cnf,
            partitions_message_mode=True
        ), api_pb2.Cnf())
        deadline = time.monotonic() + TIMEOUT if TIMEOUT > 0 else None

        def should_stop() -> bool:
            # Stop when the caller goes away, so that an abandoned search does
            # not keep the worker thread busy.
            return not context.is_active() or (deadline is not None and time.monotonic() > deadline)

        try:
            model = frontier.solve(
                [list(clause.literal) for clause in cnf.clause],
                should_stop=should_stop
            )
        except frontier.InvalidCnf as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except frontier.Unsatisfiable:
            yield from serialize_to_buffer(message_iterator=api_pb2.Interpretation(satisfiable=False))
            return

        if model is None:
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "No model found in the time limit.")
        yield from serialize_to_buffer(
            message_iterator=api_pb2.Interpretation(variable=model, satisfiable=True)
        )


def serve(port: int = PORT) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    api_pb2_grpc.add_SolverServicer_to_server(Solver(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    return server


if __name__ == "__main__":
    print(f"Starting server. Listening on port {PORT}.", flush=True)
    serve().wait_for_termination()

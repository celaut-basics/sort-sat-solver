"""gRPC entry point of the random CNF generator (api.Random/RandomCnf, bee-rpc)."""
import os
from concurrent import futures

import grpc
from bee_rpc.client import serialize_to_buffer

import api_pb2
import api_pb2_grpc
from random_cnf import Shape, random_clauses

PORT = 8000  # Must agree with .service/service.json.
MAX_WORKERS = 10


class RandomCnf(api_pb2_grpc.RandomServicer):

    def __init__(self, shape: Shape):
        self.shape = shape

    def RandomCnf(self, request_iterator, context):
        cnf = api_pb2.Cnf()
        for clause in random_clauses(self.shape):
            cnf.clause.add().literal.extend(clause)
        yield from serialize_to_buffer(message_iterator=cnf)


def serve(port: int = PORT, shape: Shape = None) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    api_pb2_grpc.add_RandomServicer_to_server(RandomCnf(shape or Shape.from_environ(os.environ)), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    return server


if __name__ == "__main__":
    print(f"Starting server. Listening on port {PORT}.", flush=True)
    serve().wait_for_termination()

"""gRPC entry point of the regression service (Regresion/MakeRegresion, bee-rpc).

This service does not call the node gateway, so it does not read __config__.
The node gives the declared variables as environment variables.
"""
import logging
import os
import sys
from concurrent import futures

import grpc
from bee_rpc.client import parse_from_buffer, serialize_to_buffer

import regresion
import regresion_pb2
import regresion_pb2_grpc
from solvers_dataset_pb2 import DataSet

PORT = 9999  # Must agree with .service/service.json.
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
# One worker for the regression and one for StreamLogs.
MAX_WORKERS = 2


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_REGRESSION_DEGREE = env_int("MAX_REGRESSION_DEGREE", regresion.DEFAULT_MAX_DEGREE)
MIN_SAMPLES = env_int("MIN_SAMPLES", regresion.DEFAULT_MIN_SAMPLES)

LOGGER = logging.getLogger("regresion")


class RegresionServicer(regresion_pb2_grpc.RegresionServicer):

    def StreamLogs(self, request_iterator, context):
        try:
            with open(LOG_FILE) as file:
                content = file.read()
        except OSError:
            content = ""
        yield from serialize_to_buffer(message_iterator=regresion_pb2.File(file=content))

    def MakeRegresion(self, request_iterator, context):
        # bee-rpc does not send an empty message: no message is an empty dataset.
        data_set = next(parse_from_buffer(
            request_iterator=request_iterator,
            indices=DataSet,
            partitions_message_mode=True,
        ), DataSet())
        yield from serialize_to_buffer(
            message_iterator=regresion.iterate_regression(
                data_set=data_set,
                max_degree=MAX_REGRESSION_DEGREE,
                min_samples=MIN_SAMPLES,
                log=LOGGER.info,
            )
        )


def serve(port: int = PORT) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    regresion_pb2_grpc.add_RegresionServicer_to_server(RegresionServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    return server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )
    LOGGER.info(f"Starting regresion. Listening on port {PORT}.")
    serve().wait_for_termination()

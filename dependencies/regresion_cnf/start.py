import logging
from bee_rpc.client import serialize_to_buffer, parse_from_buffer
from typing import Callable

from solvers_dataset_pb2 import DataSet

logging.basicConfig(filename='app.log', level=logging.DEBUG, format='%(asctime)s %(levelname)-8s %(message)s')
LOGGER: Callable = lambda message: logging.getLogger().debug(message + '\n')

DIR = '/regresioncnf/'
ENVS = {
    'MAX_REGRESSION_DEGREE': 100,
}

if __name__ == "__main__":

    import grpc, regresion_pb2, regresion_pb2_grpc, regresion
    from node_controller.gateway.protos import celaut_pb2
    from concurrent import futures

    # Read __config__ file.
    config = celaut_pb2.ConfigurationFile()
    config.ParseFromString(
        open('/__config__', 'rb').read()
    )

    """
    for env_var in config.config.enviroment_variables:
        ENVS[env_var] = type(ENVS[env_var])(
            config.config.enviroment_variables[env_var]
            )    
    """


    class RegresionServicer(regresion_pb2_grpc.RegresionServicer):

        def StreamLogs(self, request_iterator, context):
            if hasattr(self.StreamLogs, 'has_been_called'):
                raise Exception('Only can call this method once.')
            else:
                self.StreamLogs.__func__.has_been_called = True
            with open('app.log') as file:
                yield from serialize_to_buffer(
                        message_iterator=regresion_pb2.File(
                            file=file.read()
                        )
                )

        def MakeRegresion(self, request_iterator, context):
            yield from serialize_to_buffer(
                    message_iterator=regresion.iterate_regression(
                        data_set=next(parse_from_buffer(
                            request_iterator=request_iterator,
                            indices=DataSet,
                            partitions_message_mode=True,
                        )),
                        max_degree=ENVS['MAX_REGRESSION_DEGREE'],
                        log=LOGGER
                    )
            )


    # create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))  # One for regression and the other for stream logs.

    regresion_pb2_grpc.add_RegresionServicer_to_server(
        RegresionServicer(), server)

    # listen on port 9999
    LOGGER('Starting regresion. Listening on port 9999.')
    server.add_insecure_port('[::]:9999')
    server.start()
    server.wait_for_termination()

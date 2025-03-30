from threading import Lock
from time import time as time_now
from typing import Optional, Dict

import grpc
from node_controller.dependency_manager.dependency_manager import DependencyManager
from node_controller.dependency_manager.service_interface import ServiceInterface
from bee_rpc.client import client_grpc

from protos import api_pb2, api_pb2_grpc, solvers_dataset_pb2
from node_controller.gateway.protos import celaut_pb2 as celaut
from src.envs import LOGGER, SHA3_256
from src.utils.singleton import Singleton


class Session(metaclass=Singleton):

    def __init__(self):

        LOGGER('INIT SOLVE SESSION ....')
        self.solvers: Dict[str, ServiceInterface] = {}
        self.lock = Lock()

    def cnf(self, cnf: api_pb2.Cnf, solver_config_id: Optional[str], timeout=None):
        LOGGER(str(timeout) + 'cnf want solvers lock' + str(self.lock.locked()))

        if not solver_config_id:
            if len(self.solvers) > 0:
                solver_config_id = list(self.solvers.keys())[0]
            else:
                raise Exception("Don't 've solvers.")

        solver_interface: ServiceInterface = self.solvers[solver_config_id]
        instance = solver_interface.get_instance()

        try:
            # Takes into account the response time and deserialization of the buffer.
            start_time = time_now()
            LOGGER('    resolving cnf on ' + str(solver_config_id))
            interpretation = next(client_grpc(
                method=api_pb2_grpc.SolverStub(grpc.insecure_channel(instance.uri)).Solve,
                input=cnf,
                indices_parser=api_pb2.Interpretation,
                partitions_message_mode_parser=True,
                indices_serializer=api_pb2.Cnf,
                timeout=timeout
            ))
            time = time_now() - start_time
            LOGGER(str(time) + '    resolved cnf on ' + str(solver_config_id))
            # If we receive communication indicating an interpretation, it will be considered satisfactory.
            # If no interpretation is provided, we assume it is identified as unsatisfactory.
            # If an error occurs (except for exceeding the timeout), the interpretation is left empty (None) to,
            # after ensuring the instance, raise an exception.
            instance.reset_timers()
            LOGGER('INTERPRETATION --> ' + str(interpretation.variable))

        except Exception as e:
            response: str = instance.compute_exception(e)
            if response == 'timeout':
                interpretation, time = api_pb2.Interpretation(), timeout
                interpretation.satisfiable = False

            elif response == 'error':
                interpretation, time = None, timeout

            else:
                LOGGER('Exception not controlled on trainer ' + str(e))
                interpretation, time = None, timeout

        finally:
            solver_interface.push_instance(instance)

        if interpretation:
            return interpretation, time

        else:
            raise Exception

    def add_solver(self,
                   solver_configuration: solvers_dataset_pb2.SolverConfiguration,
                   solver_config_id: Optional[str],
                   solver_hash: str
                   ):

        if not solver_config_id:
            solver_config_id = SHA3_256(value=solver_configuration.SerializeToString()).hex()

        elif solver_config_id != SHA3_256(
                value=solver_configuration.SerializeToString()
                # This service not touch metadata, so it can use the hash for id.
        ).hex():
            LOGGER('Solver config not  valid ', solver_configuration, solver_config_id)
            raise Exception('Solver config not valid ', solver_configuration, solver_config_id)

        with self.lock:
            self.solvers.update({
                # It assumes that the cache service has been moved to the dynamic service directory.
                solver_config_id: DependencyManager().add_service(
                    service_hash=solver_hash,
                    config=celaut.Configuration(
                        enviroment_variables=solver_configuration.enviroment_variables
                    ),
                    dynamic=True
                )
            })
        try:
            LOGGER(f'ADDED NEW SOLVER {solver_config_id} \n'
                   f'def_ids -> {solver_configuration.meta.hashtag.hash[0].value.hex()}')
        except:
            LOGGER('ADDED NEW SOLVER ' + str(solver_config_id))

    def get_solver_with_config(self, solver_config_id: str) -> solvers_dataset_pb2.SolverConfiguration:
        sc = self.solvers[solver_config_id].sc.get_solver_with_config()
        return solvers_dataset_pb2.SolverConfiguration(
            meta=sc.service.metadata,
            enviroment_variables=sc.config.enviroment_variables
        )

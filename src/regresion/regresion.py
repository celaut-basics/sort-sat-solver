import shutil
import threading
from time import sleep

import os

import grpc
from node_controller.gateway.protos import celaut_pb2
from node_controller.dependency_manager.service_interface import ServiceInterface
from node_controller.dependency_manager.dependency_manager import DependencyManager
from node_controller.dependency_manager.service_instance import ServiceInstance
from bee_rpc.client import client_grpc, Dir
from typing import Generator, Optional, Dict

from protos import api_pb2, regresion_pb2_grpc, solvers_dataset_pb2 as sd_pb2, regresion_pb2
from src.envs import REGRESSION_SHA3_256, LOGGER, SHA3_256
from src.utils.singleton import Singleton
from src.utils.general import read_file


MAX_CNF_GROUPS = 5  # 5² groups.
MAX_LITERALS = 100
MAX_CLAUSES = 100
TYPE_CNF_SEPARATOR_SYMBOL = ":"

class Session(metaclass=Singleton):

    def __init__(self, time_for_each_regression_loop: int) -> None:
        self.data_set: Optional[sd_pb2.DataSet] = None

        # set used envs on variables.
        self.TIME_FOR_EACH_LOOP: int = time_for_each_regression_loop

        self.dataset_lock = threading.Lock()

        self.service: ServiceInterface = DependencyManager().add_service(
            service_hash=REGRESSION_SHA3_256,
            config=celaut_pb2.Configuration(),
            dynamic=False
        )

        # for maintain.
        self.data_set_hash = ""
        threading.Thread(target=self.maintenance, name='Regresion').start()

    def maintenance(self):

        while True:
            sleep(self.TIME_FOR_EACH_LOOP)
            # Obtiene una hash del dataset para saber si se han añadido datos.
            if self.data_set:
                actual_hash = SHA3_256(
                    value=self.data_set.SerializeToString()
                ).hex()
                LOGGER('Check if dataset was modified ' + actual_hash + self.data_set_hash)
                if actual_hash != self.data_set_hash:
                    LOGGER('Perform other regresion.')
                    self.data_set_hash = actual_hash

                    # Se evita crear condiciones de carrera.
                    self.dataset_lock.acquire()
                    data_set = sd_pb2.DataSet()
                    data_set.CopyFrom(self.data_set)
                    self.dataset_lock.release()

                    LOGGER('..........')
                    try:
                        shutil.move(
                            self.iterate_regression(
                                data_set=data_set
                            ),
                            '__tensor__'
                        )
                    except Exception as e:
                        LOGGER('Exception with regresion service, ' + str(e))
                        continue

    def get_tensor(self) -> Optional[regresion_pb2.Tensor]:
        # No hay condiciones de carrera aunque lo reescriba en ese momento.
        if os.path.isfile('__tensor__'):
            tensor = regresion_pb2.Tensor()
            tensor.ParseFromString(
                read_file('__tensor__')
            )
            return tensor
        else:
            return None
        
    def determine_cnf_group(self, cnf) -> str:
        cnf = cnf.split(TYPE_CNF_SEPARATOR_SYMBOL)
        literals = int(cnf[0])
        clauses = int(cnf[1])
        # Esta es una función de ejemplo que asigna CNF a grupos basados en alguna lógica.
        # Deberías reemplazar esta lógica con la que sea adecuada para tu caso de uso.
        # Por ejemplo, podrías usar k-means o alguna otra técnica de clustering para determinar los grupos.
        literal_group = literals // (MAX_LITERALS // MAX_CNF_GROUPS)
        clause_group = clauses // (MAX_CLAUSES // MAX_CNF_GROUPS)
        return f"{literal_group}{TYPE_CNF_SEPARATOR_SYMBOL}{clause_group}"

    # Add new data
    def add_data(self, new_data_set: sd_pb2.DataSet) -> None:
        with self.dataset_lock:
            if not self.data_set:
                self.data_set = sd_pb2.DataSet()

            __local_instances: Dict[bytes: sd_pb2.DataSetInstance] = {instance.configuration_hash: instance
                                                                                for instance in self.data_set.data}
            self.data_set = None

            # Add the new data set to the dataset on regression module.

            # TODO This don't work.
            for new_instance in new_data_set.data:
                __current: sd_pb2.DataSetInstance = __local_instances.get(new_instance.configuration_hash)
                
                if __current is None:
                    __current = sd_pb2.DataSetInstance()
                    __current.configuration_hash = new_instance.configuration_hash
                    __current.service_hash = new_instance.service_hash
                    # Stores it on instances
                    __local_instances[new_instance.configuration_hash] = __current
                
                for cnf, new_data in new_instance.data.items():
                    group_key: str = self.determine_cnf_group(cnf)
                    if group_key in __current.data:
                        _prev: sd_pb2.Data = __current.data[group_key] 
                        _prev.score = sum([
                            (_prev.index * _prev.score),
                            new_data.index * new_data.score,
                        ]) / (_prev.index + new_data.index)
                        _prev.index = _prev.index + new_data.index

                    else:
                        __current.data[group_key].CopyFrom(new_data)

            self.data_set = sd_pb2.DataSet()
            self.data_set.data.extend(__local_instances.values())
        LOGGER(f'\n\nDataset updated size: {self.data_set.ByteSize()}\n\n')

    # Hasta que se implemente AddTensor en el clasificador.
    def get_data_set(self) -> sd_pb2.DataSet:
        return self.data_set

    # Stream logs Grpc method. TODO CHECK
    def stream_logs(self) -> Generator[api_pb2.File, None, None]:

        instance: ServiceInstance = self.service.get_instance()
        while True:
            try:
                for i in range(1):
                    try:
                        yield from client_grpc(
                                method=regresion_pb2_grpc.RegresionStub(grpc.insecure_channel(instance.uri)).StreamLogs,
                                indices_parser=regresion_pb2.File,
                                partitions_message_mode_parser=True,
                                timeout=self.service.sc.timeout
                        )

                    except Exception as e:
                        instance.compute_exception(e=e)
            finally:
                self.service.push_instance(instance)

    # Make regression Grpc method. Return the Tensor buffer.
    def iterate_regression(self, data_set: sd_pb2.DataSet) -> str:
        instance: ServiceInstance = self.service.get_instance()
        if False: # Test with local regresion service.
            import grpc
            _uri = "localhost:9999"
            regresion_stub = regresion_pb2_grpc.RegresionStub(grpc.insecure_channel(_uri))
        else:
            regresion_stub = regresion_pb2_grpc.RegresionStub(grpc.insecure_channel(instance.uri))
        try:
            dataset: Dir = next(client_grpc(
                method=regresion_stub.MakeRegresion,
                input=data_set,
                indices_serializer=sd_pb2.DataSet,
                indices_parser=regresion_pb2.Tensor,
                partitions_message_mode_parser=False,
            ))
            if dataset.type != regresion_pb2.Tensor:
                raise Exception("Incorrect regression type.")
            return dataset.dir
        except Exception as e:
            instance.compute_exception(e)

        self.service.push_instance(instance)

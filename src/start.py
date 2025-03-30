import os
import zipfile
from threading import Thread
from typing import Optional

from node_controller.dependency_manager.dependency_manager import DependencyManager
from iterators import TimeoutIterator

from resource_manager.resourcemanager import ResourceManager, mem_manager
from src.envs import DEPENDENCIES_ARE_ZIP, ENVS, LOGGER, DIR, DEV_ENVS, DEV_MODE, BLOCK_DIRECTORY, SERVICE_DIRECTORY, METADATA_DIRECTORY, \
    CACHE_DIRECTORY, IGNORE_SERVICE_PROTO_TYPE
from src.utils.modify_resources import MODIFY_SYSTEM_RESOURCES_LAMBDA
from src.utils.general import read_file, get_grpc_uri

from time import sleep
from src.solve import _solve, _get
from src.train import train
from threading import get_ident
import grpc
from protos import api_pb2, api_pb2_grpc, api_pb2_grpcbf
from src.regresion import regresion
from concurrent import futures
from bee_rpc import client as grpcbf, buffer_pb2
from bee_rpc.utils import modify_env

# Read __config__ file.
if not DEV_MODE:
    config = api_pb2.celaut__pb2.ConfigurationFile()
    config.ParseFromString(
        read_file('/__config__')
    )

    gateway_uri = get_grpc_uri(config.gateway)
    ENVS['GATEWAY_MAIN_DIR'] = gateway_uri.ip + ':' + str(gateway_uri.port)
    mem_limit: int = config.initial_sysresources.mem_limit

    """
    for env_var in config.config.enviroment_variables:
        ENVS[env_var] = type(ENVS[env_var])(
            config.config.enviroment_variables[env_var].value
            )
    """

else:
    ENVS['GATEWAY_MAIN_DIR'] = DEV_ENVS['GATEWAY_MAIN_DIR']
    mem_limit: int = DEV_ENVS['MEM_LIMIT']
    os.makedirs(SERVICE_DIRECTORY, exist_ok=True)
    os.makedirs(BLOCK_DIRECTORY, exist_ok=True)
    os.makedirs(METADATA_DIRECTORY, exist_ok=True)
    os.makedirs(CACHE_DIRECTORY, exist_ok=True)

LOGGER('INIT START THREAD ' + str(get_ident()))


#  Services and blocks directories.
static_service_directory: str = os.path.join(DIR, SERVICE_DIRECTORY)
static_metadata_directory: str = os.path.join(DIR, METADATA_DIRECTORY)

dynamic_service_directory: str = os.path.join(DIR, SERVICE_DIRECTORY)
dynamic_metadata_directory: str = os.path.join(DIR, METADATA_DIRECTORY)

block_directory: str = os.path.join(DIR, BLOCK_DIRECTORY)
cache_directory: str = os.path.join(DIR, CACHE_DIRECTORY)

if not DEV_MODE:
    os.makedirs(dynamic_service_directory, exist_ok=True)
    os.makedirs(dynamic_metadata_directory, exist_ok=True)
    os.makedirs(block_directory, exist_ok=True)
    os.makedirs(cache_directory, exist_ok=True)

    def unzip_registry():
        LOGGER("Start unzip registry")
        # Without the service_zip_folder, the services added by UploadSolver could not be rewritten before
        #  decompressing services.zip
        # Create a folder to store the extracted services from the zip file
        services_zip_folder: str = "services_zip"

        # Extract services.zip to the services_zip_folder
        with zipfile.ZipFile(os.path.join(DIR, 'services.zip'), 'r') as zip_ref:
            zip_ref.extractall(os.path.join(DIR, services_zip_folder))

        LOGGER("Extracted services zip to the services zip folder")

        # Remove the original services.zip file
        os.remove(os.path.join(DIR, 'services.zip'))

        LOGGER("Removed the original services.zip file")

        # Move extracted service folders to the main directory
        for folder in os.listdir(f"{os.path.join(DIR, services_zip_folder)}"):
            os.system(f"mv {os.path.join(DIR, services_zip_folder, folder, '*')} {os.path.join(DIR, folder)} ")

        LOGGER("Moved extracted service folders to the main directory")

        # Remove the temporary services_zip_folder
        os.system(f'rm -rf {os.path.join(DIR, services_zip_folder)}')

        LOGGER("Removed the temporary services zip folder")

        # Log that the services files have been extracted
        LOGGER('Services files extracted.')

    if DEPENDENCIES_ARE_ZIP:
        LOGGER("Dependencies folder is on zip, so will unzip it.")
        Thread(target=unzip_registry).start()  # Only use if .service/pre-compile.json["zip"] is True

ResourceManager(
    log=LOGGER,
    ram_pool_method=lambda: mem_limit,
    modify_resources=MODIFY_SYSTEM_RESOURCES_LAMBDA
)

DependencyManager(
    node_url=ENVS['GATEWAY_MAIN_DIR'],
    maintenance_sleep_time=ENVS['MAINTENANCE_SLEEP_TIME'],
    timeout=ENVS['TRAIN_SOLVERS_TIMEOUT'],
    failed_attempts=ENVS['SOLVER_FAILED_ATTEMPTS'],
    pass_timeout_times=ENVS['SOLVER_PASS_TIMEOUT_TIMES'],
    dev_client=DEV_ENVS['CLIENT_ID'] if DEV_MODE else None,
    static_service_directory=static_service_directory,
    static_metadata_directory=static_metadata_directory,
    dynamic_service_directory=dynamic_service_directory,
    dynamic_metadata_directory=dynamic_metadata_directory
)

modify_env(
    mem_manager=mem_manager, 
    cache_dir=cache_directory+"/", 
    block_dir=block_directory+"/",
    skip_wbp_generation=IGNORE_SERVICE_PROTO_TYPE
)

_regresion = regresion.Session(
    time_for_each_regression_loop=ENVS['TIME_FOR_EACH_REGRESSION_LOOP']
)
trainer = train.Session(
    save_train_data=ENVS['SAVE_TRAIN_DATA'],
    train_solvers_timeout=ENVS['TRAIN_SOLVERS_TIMEOUT'],
    time_for_each_regression_loop=ENVS['TIME_FOR_EACH_REGRESSION_LOOP']
)
_solver = _solve.Session()


class SolverServicer(api_pb2_grpc.SolverServicer):

    def Solve(self, request_iterator, context):
        cnf = next(grpcbf.parse_from_buffer(
            request_iterator=request_iterator,
            indices=api_pb2.Cnf,
            partitions_message_mode=True
        ))
        try:
            while True:
                solver_config_id: Optional[str] = _get.cnf(
                    cnf=cnf,
                    tensors=_regresion.get_tensor()
                )
                LOGGER('USING SOLVER --> ' + str(solver_config_id))

                for i in range(ENVS['MAX_ERRORS_FOR_SOLVER']):
                    try:
                        yield from grpcbf.serialize_to_buffer(
                            message_iterator=_solver.cnf(
                                cnf=cnf,
                                solver_config_id=solver_config_id
                            )[0],
                            indices=api_pb2.Interpretation
                        )
                    except Exception as e:
                        LOGGER(str(i) + ' ERROR SOLVING A CNF ON Solve ' + str(e))
                        sleep(ENVS['TIME_SLEEP_WHEN_SOLVER_ERROR_OCCURS'])
                        continue

        except Exception as e:
            LOGGER('Wait more for it, tensor is not ready yet. '+str(e))
            yield from grpcbf.serialize_to_buffer(
                indices={1: api_pb2.Interpretation, 2: buffer_pb2.Empty}
            )

    def StreamLogs(self, request_iterator, context):
        if hasattr(self.StreamLogs, 'has_been_called'):
            raise Exception('Only can call this method once.')
        else:
            self.StreamLogs.__func__.has_been_called = True

        stream_regresion_logs = _regresion.stream_logs()
        with open('../app.log') as file:
            while True:
                try:
                    f = api_pb2.File()
                    f.file = next(file)
                    for b in grpcbf.serialize_to_buffer(message_iterator=f): yield b
                except:
                    pass
                yield from grpcbf.serialize_to_buffer(
                    message_iterator=next(TimeoutIterator(
                        stream_regresion_logs,
                        timeout=0.2
                    ))
                )
                # TODO Could be async. (needs the async grpc lib.)

    def UploadSolver(self, request_iterator, context):
        import psutil
        LOGGER('New solver ...')
        metadata: Optional[api_pb2_grpcbf.celaut.Any.Metadata] = None
        service_dir: Optional[str] = None
        try:
            for _e in grpcbf.parse_from_buffer(
                debug=lambda s: LOGGER(f"  - rpc: {psutil.virtual_memory().used / (1024 ** 2):.2f} MB {s}"),
                request_iterator=request_iterator,
                indices=api_pb2_grpcbf.UploadSolver_input_indices,
                partitions_message_mode=api_pb2_grpcbf.UploadSolver_input_message_mode
            ):
                if type(_e) is api_pb2_grpcbf.celaut.Any.Metadata:
                    metadata = _e
                elif type(_e) is grpcbf.Dir and _e.type == api_pb2_grpcbf.celaut.Service:
                    service_dir = _e.dir
                else:
                    LOGGER('Upload solver error: incorrect message type.')
        except Exception as e:
            LOGGER(f"metadata -> {type(metadata)}")
            LOGGER(f"service dir -> {str(service_dir)}")
            LOGGER(f"Exception -> {e}")
            raise e
        
        LOGGER("exit the parse from buffer.")
        trainer.load_solver(
            metadata=metadata,
            service_dir=service_dir
        )
        yield from grpcbf.serialize_to_buffer()

    def GetTensor(self, request, context):
        raise Exception("Not implemented.")
        # TODO hay que reimplementar el get_service_with_config de NodeDriver
        """
                tensor_with_ids = _regresion.get_tensor()
                tensor_with_definitions = api_pb2.Tensor()
                for solver_config_id_tensor in tensor_with_ids.non_escalar.non_escalar:
                    tensor_with_definitions.non_escalar.non_escalar.append(
                        api_pb2.Tensor.NonEscalarDimension.NonEscalar(
                            element=DependencyManager().get_service_with_config(
                                service_config_id=solver_config_id_tensor.element,
                                mem_manager=lambda x: mem_manager(len=x)
                            ),
                            escalar=solver_config_id_tensor.escalar
                        )
                    )
                yield from grpcbf.serialize_to_buffer(message_iterator=tensor_with_definitions)
        """

    def StartTrain(self, request, context):
        trainer.start()
        yield from grpcbf.serialize_to_buffer()

    def StopTrain(self, request, context):
        trainer.stop()
        yield from grpcbf.serialize_to_buffer()

    # Integrate other tensor
    def AddTensor(self, request_iterator, context):
        raise Exception("Not implemented.")
        # TODO hay que repasar el funcionamiento de este método.
        """
                tensor = next(grpcbf.parse_from_buffer(request_iterator=request_iterator, indices=api_pb2.Tensor))
                new_data_set = solvers_dataset_pb2.DataSet()
                for solver_with_config_tensor in tensor.non_escalar.non_escalar:
                    # Si no se posee ese solver, lo añade y añade, al mismo tiempo, en _solve con una configuración que el trainer considere,
                    #  añade despues la configuración del tensor en la session de solve manager (_solve).
                    # La configuración que tiene el tensor no será probada por el trainer, puesto que este tiene la competencia
                    #  de probar las configuraciones que considere.
        
                    solver_config_id = hashlib.sha3_256(
                        solver_with_config_tensor.element.SerializeToString()
                    ).hexdigest()
        
                    _solver.add_solver(
                        solver_with_config=solver_with_config_tensor.element,
                        solver_config_id=solver_config_id,
                        solver_hash=trainer.load_solver(
                            solver=solver_with_config_tensor.element.definition,
                            metadata=solver_with_config_tensor.element.meta
                        )
                    )
        
                    # Se extraen datos del tensor.
                    # Debe de probar el nivel de ajuste que tiene en un punto determinado
                    #  para obtener un indice.
                    # data.data.update({})
                    # TODO
                    new_data_set.data.update({
                        solver_config_id: solvers_dataset_pb2.DataSetInstance()
                        # generate_data(solver_with_config_tensor.escalar)
                    })
        
                _regresion.add_data(
                    new_data_set=new_data_set
                )
                for b in grpcbf.serialize_to_buffer(): yield b        
        """


    def GetDataSet(self, request_iterator, context):
        yield from grpcbf.serialize_to_buffer(
            message_iterator=_regresion.get_data_set()
        )

    # Hasta que se implemente AddTensor.
    def AddDataSet(self, request_iterator, context):
        _regresion.add_data(
            new_data_set=next(grpcbf.parse_from_buffer(
                request_iterator=request_iterator,
                indices=api_pb2.solvers__dataset__pb2.DataSet,
                partitions_message_mode=True
            ))
        )
        yield from grpcbf.serialize_to_buffer()


with mem_manager(len=mem_limit * 0.25):
    # create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=ENVS['MAX_WORKERS']))

    api_pb2_grpc.add_SolverServicer_to_server(
        SolverServicer(), server)

    # listen on port 8080
    LOGGER('Starting server. Listening on port 8081.')
    server.add_insecure_port('[::]:8081')
    server.start()
    server.wait_for_termination()

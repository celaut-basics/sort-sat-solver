"""Entry point of the sorter: python3 -m src.main (see start.sh)."""
import signal
import threading

from bee_rpc.utils import modify_env

from src import config
from src.children import ChildService, Children
from src.gateway import Gateway, environment_bytes, gateway_address, read_configuration_file, read_metadata
from src.ranker import Ranker
from src.regression import Regression
from src.server import SorterServicer, serve
from src.solvers import SolverRegistry
from src.trainer import Trainer


def dependency_paths(service_id: str):
    return config.SERVICES_DIR / service_id, config.METADATA_DIR / service_id


def main() -> None:
    logger = config.setup_logging()
    log = logger.info

    configuration_file = read_configuration_file(config.find_config_file())
    envs = config.load_envs(configuration_file, log=log)
    log(f"Environment: {envs}")

    # bee-rpc keeps the blocks of the services in BLOCK_DIR. The packer puts the
    # blocks of the dependencies there (pack_config.json blocks_directory).
    # The sorter only forwards the services, so it does not need the wbp.bin files.
    modify_env(cache_dir=str(config.CACHE_DIR) + "/", block_dir=str(config.BLOCK_DIR) + "/",
               skip_wbp_generation=True)

    gateway = Gateway(gateway_address(configuration_file),
                      start_timeout=envs['START_SERVICE_TIMEOUT'], log=log)
    log(f"Gateway: {gateway.address}")
    children = Children(idle_timeout=envs['CHILD_IDLE_TIMEOUT'], log=log)

    dependencies = config.read_dependencies()
    log(f"Dependencies: {dependencies}")

    def child(name: str, key: str, environment=None):
        service_id = dependencies.get(key)
        if not config.is_service_id(service_id):
            log(f"No valid {key} dependency in {config.DEPENDENCIES_FILE}. The sorter runs without {name}.")
            return None
        service_dir, metadata_file = dependency_paths(service_id)
        return children.add(ChildService(
            name=name, gateway=gateway, service_id=service_id, environment=environment,
            service_dir=service_dir, metadata_file=metadata_file,
            ready_timeout=envs['CHILD_READY_TIMEOUT'], log=log,
        ))

    regression_child = child("regression", config.REGRESSION_KEY,
                             environment_bytes({'MAX_REGRESSION_DEGREE': envs['MAX_REGRESSION_DEGREE']}))
    random_child = child("random CNF generator", config.RANDOM_KEY)

    solvers = SolverRegistry(gateway, children, config.DYNAMIC_SERVICES_DIR, config.DYNAMIC_METADATA_DIR,
                             ready_timeout=envs['CHILD_READY_TIMEOUT'], log=log)
    for key, service_id in sorted(dependencies.items()):
        if not key.startswith(config.SOLVER_KEY_PREFIX):
            continue
        if not config.is_service_id(service_id):
            log(f"Ignore the solver dependency {key}: {service_id!r} is not a service id.")
            continue
        service_dir, metadata_file = dependency_paths(service_id)
        metadata = read_metadata(metadata_file)
        if metadata is None:
            log(f"Ignore the solver dependency {key}: no metadata file {metadata_file}.")
            continue
        solvers.add(service_id, metadata, service_dir, metadata_file)

    ranker = Ranker(log=log)
    regression = Regression(regression_child, ranker, interval=envs['TIME_FOR_EACH_REGRESSION_LOOP'], log=log)
    trainer = Trainer(random_child, solvers, regression,
                      rounds_per_update=envs['SAVE_TRAIN_DATA'],
                      solver_timeout=envs['TRAIN_SOLVERS_TIMEOUT'], log=log)
    servicer = SorterServicer(solvers, ranker, regression, trainer, envs, log_file=config.LOG_FILE, log=log)

    children.start()
    regression.start()
    server = serve(servicer, config.PORT, envs['MAX_WORKERS'])
    log(f"Listening on port {config.PORT}.")

    def stop_server():
        trainer.stop(wait=False)
        regression.stop()
        server.stop(grace=5)

    def on_signal(signum, frame):
        log(f"Signal {signum}: stop the server and the child instances.")
        # A signal handler must not block, so stop the server in a thread.
        threading.Thread(target=stop_server, name="Shutdown").start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    server.wait_for_termination()
    # Stop the child instances, so that the node does not keep them (and their
    # cost) after the sorter.
    children.close()
    log("Stopped.")


if __name__ == "__main__":
    main()

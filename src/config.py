"""Configuration of the sorter: paths, environment variables and logging.

This module has no side effects on import. src/main.py calls its functions.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional

# The service directory: /satsorter in the image, the repository root in a
# development run (nodo ggconf).
APP_DIR = Path(__file__).resolve().parent.parent

PORT = 8081  # Must agree with .service/service.json.

# Directories of the pack_config.json dependencies (zip: false).
SERVICES_DIR = APP_DIR / "__services__"
METADATA_DIR = APP_DIR / "__metadata__"
BLOCK_DIR = APP_DIR / "__block__"
# Solvers received by UploadSolver.
DYNAMIC_SERVICES_DIR = APP_DIR / "__dynamic_services__"
DYNAMIC_METADATA_DIR = APP_DIR / "__dynamic_metadata__"
CACHE_DIR = APP_DIR / "__cache__"
DEPENDENCIES_FILE = APP_DIR / ".dependencies"
LOG_FILE = APP_DIR / "app.log"

# The keys of .dependencies (pack_config.json "dependencies").
REGRESSION_KEY = "REGRESSION"
RANDOM_KEY = "RANDOM"
# Each dependency whose key starts with this prefix is a solver.
SOLVER_KEY_PREFIX = "SOLVER_"

# Environment variables (service.json "envs") and their defaults.
# The type of the default is the type of the variable.
DEFAULT_ENVS = {
    # Training rounds between two updates of the regression dataset.
    'SAVE_TRAIN_DATA': 10,
    # Seconds between two maintenance loops of the dependency manager.
    'MAINTENANCE_SLEEP_TIME': 60,
    # The dependency manager stops an instance after this number of timeouts
    # (if the instance does not answer a TCP connection).
    'SOLVER_PASS_TIMEOUT_TIMES': 5,
    # The dependency manager stops an instance after this number of errors.
    'SOLVER_FAILED_ATTEMPTS': 20,
    # Time limit of a solver during training, in seconds.
    'TRAIN_SOLVERS_TIMEOUT': 30,
    # Time limit of a solver for a Solve call, in seconds. 0 means no limit.
    'SOLVE_TIMEOUT': 300,
    # Maximum degree of the regression polynomials. It goes to the regression service.
    'MAX_REGRESSION_DEGREE': 6,
    # Seconds between two regressions. A regression runs only if the dataset changed.
    'TIME_FOR_EACH_REGRESSION_LOOP': 900,
    # Threads of the gRPC server.
    'MAX_WORKERS': 20,
    # Maximum number of solver calls for one Solve call.
    'MAX_ERRORS_FOR_SOLVER': 5,
    # Seconds to wait after a solver error in a Solve call.
    'TIME_SLEEP_WHEN_SOLVER_ERROR_OCCURS': 1,
}


def parse_envs(values: Mapping[str, object], log=lambda s: None) -> Dict[str, object]:
    """Return DEFAULT_ENVS with the valid values of `values`."""
    envs = dict(DEFAULT_ENVS)
    for name, default in DEFAULT_ENVS.items():
        if name not in values:
            continue
        raw = values[name]
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', errors='replace')
        try:
            value = type(default)(str(raw).strip())
        except ValueError:
            log(f"Ignore the value {raw!r} of {name}: it is not a {type(default).__name__}.")
            continue
        if value < 0:
            log(f"Ignore the value {raw!r} of {name}: it is negative.")
            continue
        envs[name] = value
    return envs


def config_environment(config) -> Dict[str, bytes]:
    """Return the environment variables of a celaut ConfigurationFile.

    The last entry of a key is the value, as for a protobuf map.
    """
    return {kv.key: kv.value for kv in config.config.environment_variables}


def load_envs(config=None, environ: Mapping[str, str] = os.environ, log=lambda s: None) -> Dict[str, object]:
    """Read the environment variables.

    The node gives the variables in __config__. It also gives most of them as
    process environment variables. A value in __config__ has priority, because
    it keeps the exact bytes.
    """
    values: Dict[str, object] = {name: environ[name] for name in DEFAULT_ENVS if name in environ}
    if config is not None:
        values.update(config_environment(config))
    return parse_envs(values, log=log)


def find_config_file() -> Path:
    """Return the path of the celaut ConfigurationFile.

    A microVM instance has it at /__config__. `nodo ggconf <repository>` writes
    it in the repository for a development run.
    """
    for path in (Path("/__config__"), APP_DIR / "__config__"):
        if path.is_file():
            return path
    raise FileNotFoundError("No __config__ file. Run the sorter in a node instance or after `nodo ggconf`.")


def read_dependencies(path: Path = DEPENDENCIES_FILE) -> Dict[str, str]:
    """Read the KEY=<service id> lines that the packer writes (dependencies_env)."""
    dependencies: Dict[str, str] = {}
    if not path.is_file():
        return dependencies
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if value.strip():
            dependencies[key.strip()] = value.strip()
    return dependencies


def is_service_id(value: Optional[str]) -> bool:
    """True for a SHA3-256 hex digest. Ids are also paths, so check them before use."""
    if not value or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return value == value.lower()


def setup_logging(log_file: Path = LOG_FILE) -> logging.Logger:
    logger = logging.getLogger("satsorter")
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(threadName)s %(message)s')
        for handler in (logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

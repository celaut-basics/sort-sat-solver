"""Configuration of the sorter: paths, environment variables and logging.

This module has no side effects on import. src/main.py calls its functions.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

# The service directory: /satsorter in the service filesystem, the repository
# root in a development run (nodo ggconf).
APP_DIR = Path(__file__).resolve().parent.parent

PORT = 8081  # Must agree with .service/service.json.

# The directories of the pack_config.json dependencies (zip: false).
SERVICES_DIR = APP_DIR / "__services__"
METADATA_DIR = APP_DIR / "__metadata__"
BLOCK_DIR = APP_DIR / "__block__"
# The solvers that UploadSolver receives.
DYNAMIC_SERVICES_DIR = APP_DIR / "__dynamic__" / "services"
DYNAMIC_METADATA_DIR = APP_DIR / "__dynamic__" / "metadata"
CACHE_DIR = APP_DIR / "__cache__"
DEPENDENCIES_FILE = APP_DIR / ".dependencies"
LOG_FILE = APP_DIR / "app.log"

# The keys of .dependencies (pack_config.json "dependencies").
REGRESSION_KEY = "REGRESSION"
RANDOM_KEY = "RANDOM"
# Each dependency with a key that starts with this prefix is a solver.
SOLVER_KEY_PREFIX = "SOLVER_"

# The environment variables (service.json "envs") and their defaults.
# The type of the default is the type of the variable.
DEFAULT_ENVS = {
    # Training rounds between two updates of the regression dataset.
    'SAVE_TRAIN_DATA': 10,
    # Time limit of one solver for one training CNF, in seconds.
    'TRAIN_SOLVERS_TIMEOUT': 30,
    # Time limit of one solver attempt in a Solve call, in seconds. 0 means no limit.
    'SOLVE_TIMEOUT': 300,
    # Maximum number of solver attempts in one Solve call.
    'MAX_ERRORS_FOR_SOLVER': 5,
    # Maximum degree of the regression polynomials. The sorter gives it to the
    # regression service.
    'MAX_REGRESSION_DEGREE': 6,
    # Seconds between two regressions. A regression runs only if the dataset changed.
    'TIME_FOR_EACH_REGRESSION_LOOP': 900,
    # Threads of the gRPC server.
    'MAX_WORKERS': 20,
    # The sorter stops a child instance after this number of seconds without
    # use. 0 means that it does not stop idle instances.
    'CHILD_IDLE_TIMEOUT': 600,
    # Time limit of a Gateway.StartService call, in seconds.
    'START_SERVICE_TIMEOUT': 600,
    # Time limit for a new child instance to accept connections, in seconds.
    'CHILD_READY_TIMEOUT': 180,
}


def parse_envs(values: Mapping[str, object], log: Callable[[str], None] = lambda s: None) -> Dict[str, object]:
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
    """Return the environment variables of a celaut.ConfigurationFile.

    If a key occurs more than one time, the last value is used.
    """
    return {kv.key: kv.value for kv in config.config.environment_variables}


def load_envs(config=None, environ: Mapping[str, str] = os.environ,
              log: Callable[[str], None] = lambda s: None) -> Dict[str, object]:
    """Read the environment variables.

    The node writes the variables in __config__. It also gives most of them to
    the process as environment variables. A value in __config__ has priority,
    because it keeps the exact bytes.
    """
    values: Dict[str, object] = {name: environ[name] for name in DEFAULT_ENVS if name in environ}
    if config is not None:
        values.update(config_environment(config))
    return parse_envs(values, log=log)


def find_config_file() -> Path:
    """Return the path of the celaut.ConfigurationFile.

    In an instance, the node writes it at /__config__. For a development run,
    `nodo ggconf <repository>` writes it in the repository.
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
    """True for a lowercase SHA3-256 hex digest.

    A service id is also a file name, so check it before it becomes a path.
    """
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def setup_logging(log_file: Path = LOG_FILE) -> logging.Logger:
    logger = logging.getLogger("satsorter")
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(threadName)s %(message)s')
        for handler in (logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

"""The training data: scores of the solver configurations for each CNF shape.

All functions in this module are pure. They do not call other services.
"""
import math
import re
from typing import Dict, Mapping, Optional, Tuple

from protos import api_pb2, solvers_dataset_pb2 as sd_pb2
from src import cnf as cnf_utils

# The score of a correct answer is in (0, 1]. A faster answer has a higher score.
NO_ANSWER_SCORE = 0.0  # Timeout or error.
WRONG_ANSWER_SCORE = -1.0  # A false model, or UNSAT for a CNF with a known model.

_KEY_RE = re.compile(r"[0-9]+" + re.escape(cnf_utils.KEY_SEPARATOR) + r"[0-9]+")

# The answer of one solver for one CNF: the interpretation (None if there was
# no answer) and the time in seconds.
Result = Tuple[Optional[api_pb2.Interpretation], float]


def correct_score(seconds: float) -> float:
    return 1.0 / (1.0 + max(0.0, seconds))


def score_round(cnf: api_pb2.Cnf, results: Mapping[str, Result]) -> Dict[str, float]:
    """Give a score to each solver configuration for one CNF.

    A model is checked against the CNF. An UNSAT answer cannot be checked:
    it is correct if no solver found a model, and wrong if a solver did.
    """
    sat = any(
        interpretation is not None
        and cnf_utils.claims_sat(interpretation)
        and cnf_utils.is_model(cnf, interpretation)
        for interpretation, _ in results.values()
    )
    scores = {}
    for config_id, (interpretation, seconds) in results.items():
        if interpretation is None:
            scores[config_id] = NO_ANSWER_SCORE
        elif cnf_utils.claims_sat(interpretation):
            scores[config_id] = correct_score(seconds) if cnf_utils.is_model(cnf, interpretation) \
                else WRONG_ANSWER_SCORE
        else:
            scores[config_id] = WRONG_ANSWER_SCORE if sat else correct_score(seconds)
    return scores


def add_sample(instance: sd_pb2.DataSetInstance, key: str, score: float, weight: int = 1) -> None:
    """Add a score with a weight to the mean of the CNF shape `key`."""
    if weight <= 0:
        return
    data = instance.data[key]
    total = data.index + weight
    data.score = (data.score * data.index + score * weight) / total
    data.index = total


def instance_for(data_set: sd_pb2.DataSet, configuration_hash: bytes,
                 service_hash: bytes = b"") -> sd_pb2.DataSetInstance:
    for instance in data_set.data:
        if instance.configuration_hash == configuration_hash:
            if service_hash and not instance.service_hash:
                instance.service_hash = service_hash
            return instance
    return data_set.data.add(configuration_hash=configuration_hash, service_hash=service_hash)


def is_valid_key(key: str) -> bool:
    return bool(_KEY_RE.fullmatch(key))


def merge(into: sd_pb2.DataSet, new: sd_pb2.DataSet) -> int:
    """Add the samples of `new` to `into`. The means are weighted by index.

    Ignore the entries with a key that is not a CNF shape, a score that is not
    a finite number or an index that is not positive. Return their number.
    """
    ignored = 0
    for new_instance in new.data:
        instance = instance_for(into, new_instance.configuration_hash, new_instance.service_hash)
        for key, data in new_instance.data.items():
            if not is_valid_key(key) or not math.isfinite(data.score) or data.index <= 0:
                ignored += 1
                continue
            add_sample(instance, key, data.score, data.index)
    return ignored


def size(data_set: sd_pb2.DataSet) -> int:
    """The number of (solver configuration, CNF shape) entries."""
    return sum(len(instance.data) for instance in data_set.data)

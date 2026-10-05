"""Fit one model for each solver configuration: (clauses, variables) -> score.

The model is StandardScaler -> PolynomialFeatures(degree) -> Ridge. The degree
is selected with k-fold cross-validation, because the training score always
increases with the degree. The models are exported to ONNX, so that the sorter
only needs onnxruntime to use them.
"""
from typing import Callable, Dict, Tuple

import numpy as np
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

import regresion_pb2
import solvers_dataset_pb2

KEY_SEPARATOR = ":"
DEFAULT_MAX_DEGREE = 6
# A degree above this value has no use with two features and makes the
# polynomial terms numerically unstable.
MAX_DEGREE_LIMIT = 12
DEFAULT_MIN_SAMPLES = 5
MAX_FOLDS = 5
RIDGE_ALPHA = 1e-3
TARGET_OPSET = {"": 17, "ai.onnx.ml": 3}


def parse_key(key: str) -> Tuple[float, float]:
    """Parse a dataset key "<clauses>:<variables>"."""
    clauses, variables = key.split(KEY_SEPARATOR)
    return float(clauses), float(variables)


def samples(data: Dict[str, "solvers_dataset_pb2.Data"]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the features, the scores and the sample weights of a solver."""
    keys = sorted(data.keys())
    features = np.array([parse_key(key) for key in keys], dtype=np.float64).reshape(-1, 2)
    scores = np.array([data[key].score for key in keys], dtype=np.float64)
    weights = np.array([max(1, data[key].index) for key in keys], dtype=np.float64)
    return features, scores, weights


def make_model(degree: int):
    return make_pipeline(
        StandardScaler(),
        PolynomialFeatures(degree=degree, include_bias=False),
        Ridge(alpha=RIDGE_ALPHA),
    )


def n_terms(degree: int) -> int:
    # Polynomial terms of two features, without the bias term.
    return (degree + 1) * (degree + 2) // 2 - 1


def cross_validation_error(degree: int, x, y, w, folds: int) -> float:
    order = np.arange(len(y))
    error, total = 0.0, 0.0
    for fold in range(folds):
        test = order % folds == fold
        model = make_model(degree).fit(x[~test], y[~test], ridge__sample_weight=w[~test])
        residual = model.predict(x[test]) - y[test]
        error += float(np.sum(w[test] * residual ** 2))
        total += float(np.sum(w[test]))
    return error / total


def select_degree(x, y, w, max_degree: int) -> int:
    folds = min(MAX_FOLDS, len(y))
    if folds < 3:
        return 1
    best_degree, best_error = 1, np.inf
    for degree in range(1, max_degree + 1):
        # Each training fold must have more samples than terms.
        if n_terms(degree) + 1 >= len(y) * (folds - 1) / folds:
            break
        error = cross_validation_error(degree, x, y, w, folds)
        if error < best_error:
            best_degree, best_error = degree, error
    return best_degree


def fit_solver(data, max_degree: int = DEFAULT_MAX_DEGREE, log: Callable[[str], None] = lambda s: None) -> bytes:
    """Fit the model of one solver. Return a serialized onnx.ModelProto."""
    x, y, w = samples(data)
    max_degree = min(max(1, max_degree), MAX_DEGREE_LIMIT)
    degree = select_degree(x, y, w, max_degree)
    model = make_model(degree).fit(x, y, ridge__sample_weight=w)
    log(f"DEGREE --> {degree} R2 --> {model.score(x, y, sample_weight=w) if len(y) > 1 else 'n/a'}")
    onnx_model = convert_sklearn(
        model,
        initial_types=[("X", FloatTensorType([None, 2]))],
        target_opset=TARGET_OPSET,
    )
    return onnx_model.SerializeToString()


def iterate_regression(data_set, max_degree: int = DEFAULT_MAX_DEGREE,
                       min_samples: int = DEFAULT_MIN_SAMPLES,
                       log: Callable[[str], None] = lambda s: None) -> "regresion_pb2.Tensor":
    log("ITERATING REGRESSION")
    tensor = regresion_pb2.Tensor()
    for instance in data_set.data:
        if len(instance.data) < min_samples:
            continue
        solver_config_id = bytes(instance.configuration_hash).hex()
        log(f"SOLVER --> {solver_config_id}")
        try:
            model = fit_solver(instance.data, max_degree=max_degree, log=log)
        except Exception as e:
            # One bad solver must not stop the models of the others.
            log(f"Regression error on {solver_config_id}: {e}")
            continue
        tensor.non_escalar.non_escalar.add(element=solver_config_id, escalar=model)
    return tensor

"""Rank the solver configurations for a CNF with the models of the regression.

Each model is an ONNX model with the input "X" of shape [N, 2] (clauses,
variables) and one output: the predicted score. A higher score is better.
"""
import threading
from typing import Callable, Dict, List, Sequence

import numpy as np
import onnxruntime as rt

from protos import api_pb2, regresion_pb2
from src import cnf as cnf_utils


class Ranker:

    def __init__(self, log: Callable[[str], None] = lambda s: None):
        self.log = log
        self._lock = threading.Lock()
        self._models: Dict[str, bytes] = {}
        # One inference session for each model, made one time when the model
        # changes, not for each Solve call.
        self._sessions: Dict[str, rt.InferenceSession] = {}

    def update(self, tensor: regresion_pb2.Tensor) -> int:
        """Use the models of a regression Tensor. Return the number of models."""
        models, sessions = {}, {}
        for item in tensor.non_escalar.non_escalar:
            if item.WhichOneof("model") != "escalar":
                continue
            try:
                sessions[item.element] = rt.InferenceSession(item.escalar, providers=["CPUExecutionProvider"])
            except Exception as e:
                self.log(f"Ignore the model of {item.element[:16]}: {e}")
                continue
            models[item.element] = item.escalar
        with self._lock:
            self._models, self._sessions = models, sessions
        return len(models)

    def models(self) -> Dict[str, bytes]:
        with self._lock:
            return dict(self._models)

    def predict(self, cnf: api_pb2.Cnf) -> Dict[str, float]:
        """The predicted score of each solver configuration that has a model."""
        with self._lock:
            sessions = dict(self._sessions)
        x = np.array([cnf_utils.features(cnf)], dtype=np.float32)
        scores = {}
        for config_id, session in sessions.items():
            try:
                output = session.run(None, {session.get_inputs()[0].name: x})[0]
                scores[config_id] = float(np.asarray(output).reshape(-1)[0])
            except Exception as e:
                self.log(f"The model of {config_id[:16]} failed: {e}")
        return scores

    def rank(self, cnf: api_pb2.Cnf, config_ids: Sequence[str]) -> List[str]:
        """Sort `config_ids` from the best predicted score to the worst.

        The configurations without a model go last, in their given order.
        """
        scores = self.predict(cnf)
        with_model = sorted((i for i in config_ids if i in scores), key=lambda i: scores[i], reverse=True)
        without_model = [i for i in config_ids if i not in scores]
        return with_model + without_model

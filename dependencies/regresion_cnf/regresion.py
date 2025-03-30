from typing import Dict, Callable
import numpy as np
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import Int64TensorType
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures

import regresion_pb2
import solvers_dataset_pb2


def regression_with_degree(degree: int, poly_features: np.array, output: np.array):
    input_poly = poly_features[:, :degree +1]
    model = LinearRegression().fit(input_poly, output)
    return {'coefficient': model.score(input_poly, output), 'model': model}


def solver_regression(solver: Dict[str, solvers_dataset_pb2.Data], max_degree, LOGGER: Callable):
    input = np.array([[int(var) for var in cnf.split(':')] for cnf in solver]).reshape(-1, 2)
    output = np.array([value.score for value in solver.values()]).reshape(-1, 1)

    if len(input) != len(output):
        raise Exception('Error en solvers dataset, faltan scores.')

    # Compute polynomial features up to the max degree once.
    poly = PolynomialFeatures(degree=max_degree, include_bias=False)
    poly_features = poly.fit_transform(input)

    best_tensor = {'coefficient': -np.inf}
    log_messages = []

    for degree in range(1, max_degree + 1):
        tensor = regression_with_degree(degree, poly_features, output)
        log_messages.append(f'DEGREE --> {degree} R2 --> {tensor["coefficient"]}')
        if tensor['coefficient'] > best_tensor['coefficient']:
            best_tensor = tensor

    LOGGER('\n'.join(log_messages))

    # Convert the best model into ONNX format only once.
    if 'model' in best_tensor:
        return convert_sklearn(
            best_tensor['model'],
            initial_types=[('X', Int64TensorType([None, input.shape[1]]))]
        )
    else:
        raise Exception('No valid model found.')


def iterate_regression(max_degree: int, data_set: solvers_dataset_pb2.DataSet, log: Callable) -> regresion_pb2.Tensor:
    log('ITERATING REGRESSION')
    onnx = regresion_pb2.Tensor()

    for instance in data_set.data:
        if len(instance.data) < 5:
            continue
        solver_config_id = instance.configuration_hash.hex()
        log(f'SOLVER --> {solver_config_id}')

        tensor = solver_regression(solver=instance.data, max_degree=max_degree, LOGGER=log)

        # Directly use the ONNX tensor without unnecessary serialization/deserialization.
        onnx_tensor = regresion_pb2.Tensor.NonEscalarDimension.NonEscalar()
        onnx_tensor.element = solver_config_id
        # onnx_tensor.escalar.CopyFrom(tensor)
        onnx_tensor.escalar.ParseFromString(tensor.SerializeToString())
        onnx.non_escalar.non_escalar.append(onnx_tensor)
        log(' ****** ')

    return onnx

"""CNF helpers: validation, features, model checks and DIMACS."""
from typing import Iterable, Tuple

from protos import api_pb2

# The key of a CNF shape in the dataset: "<clauses>:<variables>".
# dependencies/regresion_cnf/regresion.py reads the same format.
KEY_SEPARATOR = ":"


class InvalidCnf(ValueError):
    pass


def validate(cnf: api_pb2.Cnf) -> None:
    for clause in cnf.clause:
        for literal in clause.literal:
            if literal == 0:
                raise InvalidCnf("A literal cannot be 0.")


def num_variables(cnf: api_pb2.Cnf) -> int:
    return max((abs(literal) for clause in cnf.clause for literal in clause.literal), default=0)


def features(cnf: api_pb2.Cnf) -> Tuple[int, int]:
    """The features of the regression: (number of clauses, number of variables)."""
    return len(cnf.clause), num_variables(cnf)


def shape_key(cnf: api_pb2.Cnf) -> str:
    clauses, variables = features(cnf)
    return f"{clauses}{KEY_SEPARATOR}{variables}"


def claims_sat(interpretation: api_pb2.Interpretation) -> bool:
    """True if the answer says SAT, False if it says UNSAT (see api.proto)."""
    if interpretation.HasField('satisfiable'):
        return interpretation.satisfiable
    return len(interpretation.variable) > 0


def is_model(cnf: api_pb2.Cnf, interpretation: api_pb2.Interpretation) -> bool:
    """True if the interpretation is consistent and makes each clause true."""
    true_literals = set(interpretation.variable)
    if any(-literal in true_literals for literal in true_literals):
        return False
    return all(
        any(literal in true_literals for literal in clause.literal)
        for clause in cnf.clause
    )


def from_dimacs(lines: Iterable[str]) -> api_pb2.Cnf:
    """Parse a CNF in the DIMACS format. A clause can continue on more than one line."""
    cnf = api_pb2.Cnf()
    clause = []
    for line in lines:
        line = line.strip()
        if not line or line[0] in "cp%":
            continue
        for token in line.split():
            try:
                literal = int(token)
            except ValueError:
                raise InvalidCnf(f"Not a literal: {token!r}.")
            if literal == 0:
                cnf.clause.add().literal.extend(clause)
                clause = []
            else:
                clause.append(literal)
    if clause:
        cnf.clause.add().literal.extend(clause)
    return cnf

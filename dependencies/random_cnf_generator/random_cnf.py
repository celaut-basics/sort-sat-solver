"""Random k-CNF generator."""
import random
from typing import List, Optional


def env_int(environ, name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(environ.get(name, default)))
    except (TypeError, ValueError):
        return default


class Shape:
    """The ranges of the generated CNFs. The bounds are inclusive."""

    def __init__(self, min_variables=3, max_variables=100,
                 min_clauses=1, max_clauses=100, clause_length=3):
        self.clause_length = max(1, clause_length)
        # A clause has distinct variables, so a CNF needs at least
        # clause_length variables.
        self.min_variables = max(min_variables, self.clause_length)
        self.max_variables = max(max_variables, self.min_variables)
        self.min_clauses = max(1, min_clauses)
        self.max_clauses = max(max_clauses, self.min_clauses)

    @classmethod
    def from_environ(cls, environ) -> "Shape":
        return cls(
            min_variables=env_int(environ, "MIN_VARIABLES", 3),
            max_variables=env_int(environ, "MAX_VARIABLES", 100),
            min_clauses=env_int(environ, "MIN_CLAUSES", 1),
            max_clauses=env_int(environ, "MAX_CLAUSES", 100),
            clause_length=env_int(environ, "CLAUSE_LENGTH", 3),
        )


def random_clauses(shape: Shape, rng: Optional[random.Random] = None) -> List[List[int]]:
    rng = rng or random.Random()
    num_variables = rng.randint(shape.min_variables, shape.max_variables)
    num_clauses = rng.randint(shape.min_clauses, shape.max_clauses)
    variables = range(1, num_variables + 1)
    return [
        [v if rng.random() < 0.5 else -v for v in rng.sample(variables, shape.clause_length)]
        for _ in range(num_clauses)
    ]

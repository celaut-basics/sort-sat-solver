"""Frontier: a WalkSAT-like local search SAT solver.

The solver keeps a "frontier": the best interpretations that it found in
earlier tries. A new try starts from a frontier interpretation or from a
random one.

This module has no dependencies outside the standard library. The gRPC
solver (solvers/frontier) and the HTTP solver (solvers/frontier_http) use
an identical copy of this file. tests/test_frontier.py makes sure that the
two copies stay the same.

Local search cannot prove that a formula is unsatisfiable (an empty clause
is the only exception). For an unsatisfiable formula, solve() continues
until should_stop() returns True.
"""

import random
import sys
from typing import Callable, List, Optional, Sequence, Tuple

# Probability to walk to a random literal of the clause when no flip is free.
DEFAULT_OMEGA = 0.4
# Flips in one try, per variable.
DEFAULT_MAX_FLIPS_PROPORTION = 4


class InvalidCnf(ValueError):
    pass


class Unsatisfiable(Exception):
    """The CNF has an empty clause, so it has no model."""


class _IndexedSet:
    """A set with O(1) add, remove and random choice."""

    def __init__(self):
        self._items: List[int] = []
        self._position = {}

    def add(self, item: int):
        if item not in self._position:
            self._position[item] = len(self._items)
            self._items.append(item)

    def remove(self, item: int):
        position = self._position.pop(item, None)
        if position is None:
            return
        last = self._items.pop()
        if position < len(self._items):
            self._items[position] = last
            self._position[last] = position

    def choice(self, rng: random.Random) -> int:
        return self._items[rng.randrange(len(self._items))]

    def __len__(self):
        return len(self._items)


def parse(clauses_input: Sequence[Sequence[int]]) -> Tuple[List[List[int]], int, List[List[int]]]:
    """Return the clauses, the number of variables and the clause index of each literal.

    lit_clauses[literal + n_vars] is the list of clauses that contain the literal.
    """
    clauses = []
    n_vars = 0
    for clause_literals in clauses_input:
        clause = [int(literal) for literal in clause_literals]
        for literal in clause:
            if literal == 0:
                raise InvalidCnf("A literal cannot be 0.")
            n_vars = max(n_vars, abs(literal))
        clauses.append(clause)

    lit_clauses: List[List[int]] = [[] for _ in range(n_vars * 2 + 1)]
    for index, clause in enumerate(clauses):
        for literal in clause:
            lit_clauses[literal + n_vars].append(index)
    return clauses, n_vars, lit_clauses


def _random_interpretation(n_vars: int, rng: random.Random) -> List[int]:
    # Position 0 is not a variable. It is there to index the list by variable.
    return [i if rng.random() < 0.5 else -i for i in range(n_vars + 1)]


def _start_interpretation(n_vars, n_clauses, frontier, threshold, rng) -> List[int]:
    # When the threshold is low (good interpretations are known), start from
    # the frontier more frequently.
    if frontier and rng.random() > threshold / n_clauses:
        return list(rng.choice(frontier)[0])
    return _random_interpretation(n_vars, rng)


def _true_literals(clauses, interpretation) -> List[int]:
    counts = [0] * len(clauses)
    for index, clause in enumerate(clauses):
        for literal in clause:
            if interpretation[abs(literal)] == literal:
                counts[index] += 1
    return counts


def _pick_literal(clause, true_count, lit_clauses, n_vars, omega, rng) -> Tuple[int, bool]:
    """Return the literal to flip and True if it is a random walk step."""
    min_damage = sys.maxsize
    best_literals: List[int] = []
    for literal in clause:
        damage = 0
        # Clauses that only the current (opposite) literal satisfies become false.
        for clause_index in lit_clauses[-literal + n_vars]:
            if true_count[clause_index] == 1:
                damage += 1
        # False clauses that the literal makes true.
        for clause_index in lit_clauses[literal + n_vars]:
            if true_count[clause_index] == 0:
                damage -= 1
        if damage < min_damage:
            min_damage = damage
            best_literals = [literal]
        elif damage == min_damage:
            best_literals.append(literal)

    if min_damage > 0 and rng.random() < omega:
        # No free flip: with probability omega, walk to any literal of the clause.
        return rng.choice(clause), True
    return rng.choice(best_literals), False


def solve(clauses_input: Sequence[Sequence[int]],
          should_stop: Callable[[], bool] = lambda: False,
          seed: Optional[int] = None,
          omega: float = DEFAULT_OMEGA,
          max_flips_proportion: int = DEFAULT_MAX_FLIPS_PROPORTION) -> Optional[List[int]]:
    """Search a model of the CNF.

    Return the model as a list of signed literals (one for each variable,
    variable 1 first), or None when should_stop() returns True first.
    Raise Unsatisfiable when the CNF has an empty clause, and InvalidCnf
    when a literal is 0.
    """
    rng = random.Random(seed)
    clauses, n_vars, lit_clauses = parse(clauses_input)
    if not clauses:
        return _random_interpretation(n_vars, rng)[1:]
    if any(len(clause) == 0 for clause in clauses):
        # An empty clause is always false. This is the only proof of
        # unsatisfiability that local search can give.
        raise Unsatisfiable()

    n_clauses = len(clauses)
    max_flips = max(1, n_vars * max_flips_proportion)
    # Each frontier item is (interpretation, number of false clauses).
    frontier: List[Tuple[List[int], int]] = []
    threshold = n_clauses

    while not should_stop():
        interpretation = _start_interpretation(n_vars, n_clauses, frontier, threshold, rng)
        true_count = _true_literals(clauses, interpretation)
        unsatisfied = _IndexedSet()
        for index, count in enumerate(true_count):
            if count == 0:
                unsatisfied.add(index)

        for _ in range(max_flips):
            if not unsatisfied:
                return interpretation[1:]

            clause = clauses[unsatisfied.choice(rng)]
            literal, random_walk = _pick_literal(
                clause, true_count, lit_clauses, n_vars, omega, rng
            )
            if random_walk:
                threshold = len(unsatisfied)
                frontier = [item for item in frontier if item[1] < threshold]

            # The literal becomes true and its opposite becomes false.
            for clause_index in lit_clauses[literal + n_vars]:
                true_count[clause_index] += 1
                if true_count[clause_index] == 1:
                    unsatisfied.remove(clause_index)
            for clause_index in lit_clauses[-literal + n_vars]:
                true_count[clause_index] -= 1
                if true_count[clause_index] == 0:
                    unsatisfied.add(clause_index)
            interpretation[abs(literal)] = literal

        if not unsatisfied:
            return interpretation[1:]
        if len(unsatisfied) < threshold:
            frontier.append((list(interpretation), len(unsatisfied)))

    return None

import random
from random import randint
from api_pb2 import Cnf


# Classes

class Clause():
    """A Boolean clause randomly generated"""

    def __init__(self, num_vars, clause_length):
        """
        Initialization
        length: Clause length
        lits: List of literals
        """
        self.length = clause_length
        self.lits = None
        self.gen_random_clause(num_vars)

    def gen_random_clause(self, num_vars):
        """ Generate random clause"""
        self.lits = []
        while len(self.lits) < self.length:  # Set the variables of the clause
            new_lit = random.randint(1, num_vars)  # New random variable
            if new_lit not in self.lits:  # If the variable is not already in the clause
                self.lits.append(new_lit)  # Add it to the clause
        for i in range(len(self.lits)):  # Sets a negative sense with a 50% probability
            if random.random() < 0.5:
                self.lits[i] *= -1  # Change the sense of the literal


class CNF:
    """A CNF formula randomly generated"""

    def __init__(self, num_vars, num_clauses, clause_length):
        """
        Initialization
        num_vars: Number of variables
        num_clauses: Number of clauses
        clause_length: Length of the clauses        : List of clauses
        """
        self.num_vars = num_vars
        self.num_clauses = num_clauses
        self.clause_length = clause_length
        self.clauses = None
        self.gen_random_clauses()

    def gen_random_clauses(self):
        """Generate random clauses"""
        self.clauses = []
        for i in range(self.num_clauses):
            c = Clause(self.num_vars, self.clause_length)
            self.clauses.append(c)

    def ok(self):
        cnf = Cnf()
        for c in self.clauses:
            clause = cnf.clause.add()
            clause.literal.extend(c.lits)
        return cnf


def ok():
    cnf = CNF(randint(1, 100), randint(1, 100), 3)
    return cnf.ok()
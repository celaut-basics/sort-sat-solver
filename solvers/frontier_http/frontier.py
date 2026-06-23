#!/usr/bin/python3

import random
import sys


def parse(clauses_input):
    """Parsea la lista de cláusulas y genera las estructuras internas."""
    clauses = []
    n_vars = 0
    
    for clause_literals in clauses_input:
        c_list = []
        for literal in clause_literals:
            c_list.append(literal)
            if n_vars < abs(literal):
                n_vars = abs(literal)
        clauses.append(c_list)
    
    count = 0
    lit_clauses = [[] for _ in range(n_vars * 2 + 1)]
    for clause in clauses:
        for literal in clause:
            lit_clauses[literal + n_vars].append(count)
        count += 1
    
    return clauses, n_vars, lit_clauses


def get_random_interpretation(n_vars, n_clauses, frontera):
    valor_0_1 = frontera[1] / n_clauses
    omega = valor_0_1 ** (1 / 1)
    if random.random() > omega and len(frontera[0]) != 0:
        return random.choice(frontera[0])
    else:
        return [i if random.random() < 0.5 else -i for i in range(n_vars + 1)]


def get_true_sat_lit(clauses, interpretation):
    true_sat_lit = [0 for _ in clauses]
    for index, clause in enumerate(clauses):
        for lit in clause:
            if interpretation[abs(lit)] == lit:
                true_sat_lit[index] += 1
    return true_sat_lit


def update_tsl(literal_to_flip, true_sat_lit, lit_clause):
    for clause_index in lit_clause[literal_to_flip]:
        true_sat_lit[clause_index] += 1
    for clause_index in lit_clause[-literal_to_flip]:
        true_sat_lit[clause_index] -= 1


def compute_broken(clause, true_sat_lit, lit_clause, omega=0.4):
    min_daño = sys.maxsize
    up_frontera = False
    best_literals = []
    
    for literal in clause:
        daño = 0

        for clause_index in lit_clause[-literal]:
            if true_sat_lit[clause_index] == 1:
                daño += 1

        for clause_index in lit_clause[literal]:
            if true_sat_lit[clause_index] == 0:
                daño -= 1

        if daño < min_daño:
            min_daño = daño
            best_literals = [literal]
        elif daño == min_daño:
            best_literals.append(literal)

    if min_daño > 0 and random.random() < omega:
        best_literals = clause
        up_frontera = True

    return random.choice(best_literals), up_frontera


def prune(frontera):
    new = []
    for interpretacion in frontera[0]:
        if interpretacion[1] < frontera[1]:
            new.append(interpretacion)
    return (new, frontera[1])


def run_sat(clauses, n_vars, lit_clause, max_flips_proportion=4):
    max_flips = n_vars * max_flips_proportion
    frontera = ([], len(clauses))
    
    while 1:
        interpretation = get_random_interpretation(n_vars, len(clauses), frontera)
        true_sat_lit = get_true_sat_lit(clauses, interpretation)
        
        for _ in range(max_flips):
            unsatisfied_clauses_index = [index for index, true_lit in enumerate(true_sat_lit) 
                                         if not true_lit]

            if not unsatisfied_clauses_index:
                return interpretation[1:]

            clause_index = random.choice(unsatisfied_clauses_index)
            unsatisfied_clause = clauses[clause_index]

            lit_to_flip, up_frontera = compute_broken(unsatisfied_clause, true_sat_lit, lit_clause)
            
            if up_frontera:
                frontera = (frontera[0], len(unsatisfied_clauses_index))
                frontera = prune(frontera)

            update_tsl(lit_to_flip, true_sat_lit, lit_clause)
            interpretation[abs(lit_to_flip)] *= -1

        if unsatisfied_clauses_index and len(unsatisfied_clauses_index) < frontera[1]:
            frontera[0].append(interpretation)


def ok(clauses_input):
    """Función principal: recibe lista de cláusulas, devuelve lista de variables."""
    clauses, n_vars, lit_clause = parse(clauses_input)
    solution = run_sat(clauses, n_vars, lit_clause)
    return solution
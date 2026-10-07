import unittest

from protos import api_pb2
from src import cnf as cnf_utils


def make_cnf(clauses):
    cnf = api_pb2.Cnf()
    for clause in clauses:
        cnf.clause.add().literal.extend(clause)
    return cnf


class CnfTest(unittest.TestCase):

    def test_features_and_key(self):
        cnf = make_cnf([[1, -2], [3, -7, 2]])
        self.assertEqual(cnf_utils.features(cnf), (2, 7))
        self.assertEqual(cnf_utils.shape_key(cnf), "2:7")
        self.assertEqual(cnf_utils.features(api_pb2.Cnf()), (0, 0))

    def test_validate(self):
        cnf_utils.validate(make_cnf([[1, -2]]))
        with self.assertRaises(cnf_utils.InvalidCnf):
            cnf_utils.validate(make_cnf([[1, 0]]))

    def test_claims_sat(self):
        self.assertTrue(cnf_utils.claims_sat(api_pb2.Interpretation(variable=[1])))
        self.assertFalse(cnf_utils.claims_sat(api_pb2.Interpretation()))
        self.assertFalse(cnf_utils.claims_sat(api_pb2.Interpretation(satisfiable=False)))
        self.assertTrue(cnf_utils.claims_sat(api_pb2.Interpretation(satisfiable=True)))

    def test_is_model(self):
        cnf = make_cnf([[1, -2], [2, 3]])
        self.assertTrue(cnf_utils.is_model(cnf, api_pb2.Interpretation(variable=[1, 2, -3])))
        self.assertFalse(cnf_utils.is_model(cnf, api_pb2.Interpretation(variable=[-1, 2, -3])))
        # An inconsistent interpretation is never a model.
        self.assertFalse(cnf_utils.is_model(cnf, api_pb2.Interpretation(variable=[1, -1, 2])))
        self.assertTrue(cnf_utils.is_model(api_pb2.Cnf(), api_pb2.Interpretation()))

    def test_from_dimacs(self):
        cnf = cnf_utils.from_dimacs([
            "c a comment",
            "p cnf 3 2",
            "1 -2 0",
            "2 3",
            " -1 0",
        ])
        self.assertEqual([list(c.literal) for c in cnf.clause], [[1, -2], [2, 3, -1]])
        with self.assertRaises(cnf_utils.InvalidCnf):
            cnf_utils.from_dimacs(["1 x 0"])


if __name__ == "__main__":
    unittest.main()

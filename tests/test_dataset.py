import math
import unittest

from protos import api_pb2, solvers_dataset_pb2 as sd_pb2
from src import dataset
from tests.test_cnf import make_cnf

SAT_CNF = make_cnf([[1, 2], [-1, 2]])  # Model: 2 is true.
MODEL = api_pb2.Interpretation(variable=[1, 2], satisfiable=True)
FALSE_MODEL = api_pb2.Interpretation(variable=[1, -2], satisfiable=True)
UNSAT = api_pb2.Interpretation(satisfiable=False)


class ScoreTest(unittest.TestCase):

    def test_faster_is_better(self):
        self.assertGreater(dataset.correct_score(0.1), dataset.correct_score(1))
        self.assertEqual(dataset.correct_score(0), 1.0)
        self.assertGreater(dataset.correct_score(1000), dataset.NO_ANSWER_SCORE)

    def test_sat_round(self):
        scores = dataset.score_round(SAT_CNF, {
            "fast": (MODEL, 0.5),
            "slow": (MODEL, 5.0),
            "false": (FALSE_MODEL, 0.1),
            "unsat": (UNSAT, 0.1),
            "timeout": (None, 30.0),
        })
        self.assertGreater(scores["fast"], scores["slow"])
        self.assertGreater(scores["slow"], scores["timeout"])
        self.assertEqual(scores["false"], dataset.WRONG_ANSWER_SCORE)
        self.assertEqual(scores["unsat"], dataset.WRONG_ANSWER_SCORE)
        self.assertEqual(scores["timeout"], dataset.NO_ANSWER_SCORE)

    def test_unsat_claim_without_a_model(self):
        cnf = make_cnf([[1], [-1]])
        scores = dataset.score_round(cnf, {
            "unsat": (UNSAT, 2.0),
            "false": (api_pb2.Interpretation(variable=[1]), 0.1),
            "timeout": (None, 30.0),
        })
        self.assertEqual(scores["unsat"], dataset.correct_score(2.0))
        self.assertEqual(scores["false"], dataset.WRONG_ANSWER_SCORE)
        self.assertEqual(scores["timeout"], dataset.NO_ANSWER_SCORE)


class DataSetTest(unittest.TestCase):

    def test_add_sample_is_a_mean(self):
        instance = sd_pb2.DataSetInstance()
        dataset.add_sample(instance, "2:3", 1.0)
        dataset.add_sample(instance, "2:3", 0.0)
        dataset.add_sample(instance, "2:3", 0.5, weight=2)
        self.assertEqual(instance.data["2:3"].index, 4)
        self.assertAlmostEqual(instance.data["2:3"].score, 0.5)
        dataset.add_sample(instance, "2:3", 9.0, weight=0)
        self.assertEqual(instance.data["2:3"].index, 4)

    def test_merge_is_weighted(self):
        into = sd_pb2.DataSet()
        a = dataset.instance_for(into, b"\x01", b"\x0a")
        dataset.add_sample(a, "1:3", 1.0, weight=3)

        new = sd_pb2.DataSet()
        b = new.data.add(configuration_hash=b"\x01", service_hash=b"\x0a")
        b.data["1:3"].score, b.data["1:3"].index = 0.0, 1
        b.data["4:5"].score, b.data["4:5"].index = 0.25, 2
        c = new.data.add(configuration_hash=b"\x02", service_hash=b"\x0b")
        c.data["7:7"].score, c.data["7:7"].index = 0.5, 1

        self.assertEqual(dataset.merge(into, new), 0)
        self.assertEqual(len(into.data), 2)
        first = dataset.instance_for(into, b"\x01")
        self.assertEqual(first.data["1:3"].index, 4)
        self.assertAlmostEqual(first.data["1:3"].score, 0.75)
        self.assertEqual(first.data["4:5"].index, 2)
        self.assertEqual(dataset.size(into), 3)

    def test_merge_ignores_bad_entries(self):
        new = sd_pb2.DataSet()
        instance = new.data.add(configuration_hash=b"\x01")
        instance.data["not a shape"].index = 1
        instance.data["1:2\n"].index = 1
        instance.data["3:4"].index = 0
        instance.data["5:6"].index = 1
        instance.data["5:6"].score = math.nan
        instance.data["7:8"].index = 1
        into = sd_pb2.DataSet()
        self.assertEqual(dataset.merge(into, new), 4)
        self.assertEqual(list(into.data[0].data), ["7:8"])


if __name__ == "__main__":
    unittest.main()

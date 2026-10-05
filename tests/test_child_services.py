"""The code of the services in dependencies/ and solvers/, without gRPC.

Each service directory is a separate Python program with its own api_pb2, so
this test imports its pure modules by path.
"""
import filecmp
import importlib.util
import random
import unittest

from tests.helpers import ROOT


def load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontier = load("frontier_under_test", ROOT / "solvers" / "frontier" / "frontier.py")
random_cnf = load("random_cnf_under_test", ROOT / "dependencies" / "random_cnf_generator" / "random_cnf.py")


def satisfies(clauses, model) -> bool:
    true = set(model)
    return all(any(literal in true for literal in clause) for clause in clauses)


def planted_cnf(rng, variables, clauses, length=3):
    """A random CNF that has the model `solution`."""
    solution = [v if rng.random() < 0.5 else -v for v in range(1, variables + 1)]
    result = []
    while len(result) < clauses:
        chosen = rng.sample(range(1, variables + 1), length)
        clause = [v if rng.random() < 0.5 else -v for v in chosen]
        if satisfies([clause], solution):
            result.append(clause)
    return result


class FrontierTest(unittest.TestCase):

    def test_the_two_copies_are_identical(self):
        self.assertTrue(filecmp.cmp(ROOT / "solvers" / "frontier" / "frontier.py",
                                    ROOT / "solvers" / "frontier_http" / "frontier.py", shallow=False))

    def test_finds_models(self):
        rng = random.Random(1)
        for _ in range(30):
            clauses = planted_cnf(rng, variables=rng.randint(3, 40), clauses=rng.randint(1, 120))
            model = frontier.solve(clauses, seed=rng.randint(0, 10 ** 6))
            self.assertIsNotNone(model)
            self.assertTrue(satisfies(clauses, model), clauses)
            self.assertEqual(sorted(abs(v) for v in model), list(range(1, len(model) + 1)))

    def test_no_clause(self):
        self.assertEqual(frontier.solve([]), [])

    def test_empty_clause_is_unsat(self):
        with self.assertRaises(frontier.Unsatisfiable):
            frontier.solve([[1], []])

    def test_zero_literal_is_invalid(self):
        with self.assertRaises(frontier.InvalidCnf):
            frontier.solve([[1, 0]])

    def test_unsat_stops_when_asked(self):
        calls = []

        def should_stop():
            calls.append(1)
            return len(calls) > 20

        self.assertIsNone(frontier.solve([[1], [-1]], should_stop=should_stop))


class RandomCnfTest(unittest.TestCase):

    def test_shapes_are_in_the_ranges(self):
        rng = random.Random(2)
        shape = random_cnf.Shape(min_variables=3, max_variables=10, min_clauses=2, max_clauses=5)
        for _ in range(200):
            clauses = random_cnf.random_clauses(shape, rng)
            self.assertTrue(2 <= len(clauses) <= 5)
            for clause in clauses:
                self.assertEqual(len(clause), 3)
                self.assertEqual(len({abs(v) for v in clause}), 3)
                self.assertTrue(all(1 <= abs(v) <= 10 for v in clause))

    def test_too_few_variables_is_corrected(self):
        # The old generator looped forever with fewer variables than the clause length.
        shape = random_cnf.Shape(min_variables=1, max_variables=1, clause_length=3)
        clauses = random_cnf.random_clauses(shape, random.Random(3))
        self.assertTrue(all(len(clause) == 3 for clause in clauses))

    def test_from_environ(self):
        shape = random_cnf.Shape.from_environ({"MAX_VARIABLES": "7", "MIN_CLAUSES": "x", "CLAUSE_LENGTH": "2"})
        self.assertEqual(shape.max_variables, 7)
        self.assertEqual(shape.min_clauses, 1)
        self.assertEqual(shape.clause_length, 2)


class FrontierHttpTest(unittest.TestCase):
    """The HTTP solver, if FastAPI is installed (solvers/frontier_http/requirements.txt)."""

    def test_solve(self):
        import subprocess
        import sys
        script = r"""
from fastapi.testclient import TestClient
import start
client = TestClient(start.app)
r = client.post("/solve", json={"clauses": [[1, -2], [2, 3], [-1, -3]]})
assert r.status_code == 200 and r.json()["satisfiable"] is True, r.text
r = client.post("/solve", json={"clauses": [[1], []]})
assert r.json() == {"satisfiable": False}, r.text
r = client.post("/solve", json={"clauses": [[1, 0]]})
assert r.status_code == 422, r.text
print("ok")
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT / "solvers" / "frontier_http",
                                capture_output=True, text=True, timeout=120)
        if "No module named 'fastapi'" in result.stderr or "No module named 'httpx'" in result.stderr:
            self.skipTest("FastAPI or httpx is not installed.")
        self.assertEqual(result.returncode, 0, result.stderr)


class RegressionTest(unittest.TestCase):
    """The regression code, if its packages are installed (dependencies/regresion_cnf/requirements.txt)."""

    @classmethod
    def setUpClass(cls):
        try:
            import onnxruntime  # noqa: F401
            import skl2onnx  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f"The regression packages are not installed: {e}")

    def test_fit_and_predict(self):
        import subprocess
        import sys
        # regresion.py imports the protos of the regression service, which have
        # the same file names as protos/. Run it in a separate process.
        script = r"""
import sys, numpy as np, onnxruntime as rt
import regresion, solvers_dataset_pb2 as sd
ds = sd.DataSet()
good = ds.data.add(configuration_hash=b"\x01" * 32)
bad = ds.data.add(configuration_hash=b"\x02" * 32)
few = ds.data.add(configuration_hash=b"\x03" * 32)
for c in range(5, 100, 10):
    for v in range(3, 60, 8):
        good.data[f"{c}:{v}"].score, good.data[f"{c}:{v}"].index = 1 / (1 + 0.001 * c * v), 1
        bad.data[f"{c}:{v}"].score, bad.data[f"{c}:{v}"].index = 1 / (1 + 0.1 * c * v), 1
few.data["1:3"].score, few.data["1:3"].index = 1.0, 1
tensor = regresion.iterate_regression(ds, max_degree=3, min_samples=5)
models = {e.element: e.escalar for e in tensor.non_escalar.non_escalar}
assert set(models) == {"01" * 32, "02" * 32}, models.keys()
x = np.array([[50, 30]], dtype=np.float32)
p = {k: float(rt.InferenceSession(m).run(None, {"X": x})[0].reshape(-1)[0]) for k, m in models.items()}
assert p["01" * 32] > p["02" * 32], p
print("ok")
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT / "dependencies" / "regresion_cnf",
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()

"""The copies of the protos must agree with the protos of the sorter."""
import filecmp
import subprocess
import sys
import unittest

from google.protobuf import descriptor_pb2

from protos import api_pb2
from tests.helpers import ROOT

# A child service directory, and the messages of protos/api.proto that it copies.
API_COPIES = {
    "solvers/frontier": ["Interpretation", "Clause", "Cnf"],
    "dependencies/random_cnf_generator": ["Clause", "Cnf"],
}


def message_descriptor(module, name) -> bytes:
    proto = descriptor_pb2.DescriptorProto()
    module.DESCRIPTOR.message_types_by_name[name].CopyToProto(proto)
    return proto.SerializeToString(deterministic=True)


class ProtosTest(unittest.TestCase):

    def test_solver_methods(self):
        # AddTensor was removed in v4 (it was never implemented). A change of this list
        # is an API change: write it in the release notes.
        methods = [m.name for m in api_pb2.DESCRIPTOR.services_by_name["Solver"].methods]
        self.assertEqual(
            ["StartTrain", "StopTrain", "GetTensor", "UploadSolver", "StreamLogs", "Solve",
             "GetDataSet", "AddDataSet"],
            methods,
        )

    def test_regression_protos_are_identical(self):
        for name in ("regresion.proto", "solvers_dataset.proto"):
            self.assertTrue(filecmp.cmp(ROOT / "protos" / name, ROOT / "dependencies" / "regresion_cnf" / name,
                                        shallow=False), name)

    def test_api_copies_have_the_same_messages(self):
        for directory, names in API_COPIES.items():
            # The copy has the file name api.proto too, so read it in another process.
            script = (
                "import sys, api_pb2\n"
                "from google.protobuf import descriptor_pb2\n"
                "for name in sys.argv[1:]:\n"
                "    proto = descriptor_pb2.DescriptorProto()\n"
                "    api_pb2.DESCRIPTOR.message_types_by_name[name].CopyToProto(proto)\n"
                "    print(proto.SerializeToString(deterministic=True).hex())\n"
            )
            result = subprocess.run([sys.executable, "-B", "-c", script, *names], cwd=ROOT / directory,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            copies = result.stdout.split()
            for name, copy in zip(names, copies):
                self.assertEqual(bytes.fromhex(copy), message_descriptor(api_pb2, name), f"{directory}: {name}")

    def test_generated_code_is_up_to_date(self):
        # Generate the code again in a temporary directory and compare.
        try:
            from grpc_tools import protoc  # noqa: F401
        except ImportError:
            self.skipTest("grpcio-tools is not installed.")
        import tempfile
        from importlib.metadata import version
        if version("grpcio-tools") != "1.56.0":
            self.skipTest("protos/generate.py needs grpcio-tools 1.56.0.")
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import sys, pathlib, protos.generate as g\n"
                "out = pathlib.Path(sys.argv[1])\n"
                "g.buffer_descriptor_set(out / 'buffer.pb')\n"
                "import shutil\n"
                "for d, package, files in g.TARGETS:\n"
                "    target = out / d\n"
                "    target.mkdir(parents=True)\n"
                "    for f in files: shutil.copy(g.ROOT / d / f, target / f)\n"
                "    if d == 'dependencies/regresion_cnf':\n"
                "        for f in g.SHARED_WITH_REGRESSION: shutil.copy(g.ROOT / 'protos' / f, target / f)\n"
                "    g.compile_target(target, package, files, out / 'buffer.pb')\n"
            )
            result = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", script, tmp], cwd=ROOT,
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            from pathlib import Path
            for generated in Path(tmp).rglob("*_pb2*.py"):
                committed = ROOT / generated.relative_to(tmp)
                self.assertTrue(committed.exists(), committed)
                self.assertEqual(generated.read_text(), committed.read_text(),
                                 f"{committed} is not up to date. Run protos/generate.py.")


if __name__ == "__main__":
    unittest.main()

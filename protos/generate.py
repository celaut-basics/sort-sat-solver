"""Generate the Python protobuf and gRPC code of the sorter and of its services.

Usage (from any directory):

    pip install grpcio-tools==1.56.0 "bee-rpc @ git+https://github.com/bee-rpc-protocol/bee-rpc-over-grpc-py@8049fd86261c0d85445e6c27d363e0c82d5b2958"
    python3 protos/generate.py

grpcio-tools 1.56.0 contains protoc 23.1. Its code runs with protobuf 4.23 or
newer, the same as the node (celaut-project/nodo, bash/generate_protos.sh).

buffer.proto is not compiled. bee_rpc has the only buffer_pb2 module. A second
module with the same proto file name stops protobuf with "duplicate file name".
This script reads the schema of buffer.proto from bee_rpc and changes the
generated imports to use bee_rpc.buffer_pb2.

The script also copies the protos of the regression service from protos/ to
dependencies/regresion_cnf/, because the two copies must be identical.
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRPC_TOOLS_VERSION = "1.56.0"

# (directory, Python package of the generated modules or "", proto files)
TARGETS = [
    ("protos", "protos", ["celaut.proto", "solvers_dataset.proto", "regresion.proto", "api.proto"]),
    ("dependencies/regresion_cnf", "", ["solvers_dataset.proto", "regresion.proto"]),
    ("dependencies/random_cnf_generator", "", ["api.proto"]),
    ("solvers/frontier", "", ["api.proto"]),
]

# Files of protos/ that the regression service uses without a change.
SHARED_WITH_REGRESSION = ["solvers_dataset.proto", "regresion.proto"]

IMPORT_RE = re.compile(r"^import (\w+_pb2) as (\w+)$", re.MULTILINE)


def buffer_descriptor_set(path: Path) -> None:
    from bee_rpc import buffer_pb2
    from google.protobuf import descriptor_pb2

    descriptor_set = descriptor_pb2.FileDescriptorSet()
    buffer_pb2.DESCRIPTOR.CopyToProto(descriptor_set.file.add())
    path.write_bytes(descriptor_set.SerializeToString())


def fix_imports(path: Path, package: str) -> None:
    def replace(match: re.Match) -> str:
        module, alias = match.group(1), match.group(2)
        if module == "buffer_pb2":
            return f"from bee_rpc import buffer_pb2 as {alias}"
        if package:
            return f"from {package} import {module} as {alias}"
        return match.group(0)

    path.write_text(IMPORT_RE.sub(replace, path.read_text()))


def compile_target(directory: Path, package: str, protos, descriptor_set: Path) -> None:
    from grpc_tools import protoc

    for proto in protos:
        arguments = [
            "grpc_tools.protoc",
            f"-I{directory}",
            f"--descriptor_set_in={descriptor_set}",
            f"--python_out={directory}",
            f"--grpc_python_out={directory}",
            str(directory / proto),
        ]
        if protoc.main(arguments) != 0:
            sys.exit(f"protoc failed for {directory / proto}")

        stem = proto[:-len(".proto")]
        grpc_file = directory / f"{stem}_pb2_grpc.py"
        if "Stub" not in grpc_file.read_text():
            # protoc writes a gRPC module also for a proto without a service.
            grpc_file.unlink()
        for generated in (directory / f"{stem}_pb2.py", grpc_file):
            if generated.exists():
                fix_imports(generated, package)


def main() -> None:
    from importlib.metadata import version

    if version("grpcio-tools") != GRPC_TOOLS_VERSION:
        sys.exit(f"Use grpcio-tools=={GRPC_TOOLS_VERSION}, not {version('grpcio-tools')}.")

    for name in SHARED_WITH_REGRESSION:
        shutil.copyfile(ROOT / "protos" / name, ROOT / "dependencies" / "regresion_cnf" / name)

    with tempfile.TemporaryDirectory() as tmp:
        descriptor_set = Path(tmp) / "buffer.pb"
        buffer_descriptor_set(descriptor_set)
        for directory, package, protos in TARGETS:
            compile_target(ROOT / directory, package, protos, descriptor_set)
            print(f"Generated {directory}: {', '.join(protos)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Generate the Python protobuf and gRPC code of the sorter and of the services
# in dependencies/ and solvers/.
#
# Requirements: grpcio-tools==1.56.0 (protoc 23.1, the same version that the
# celaut libraries use), bee_rpc and the celaut service libraries
# (node_controller). See tests/requirements.txt.
#
# celaut.proto and buffer.proto are NOT compiled here. At runtime there must be
# only one module for each of them: buffer_pb2 comes from bee_rpc and
# celaut_pb2 comes from node_controller. If a second copy registers the same
# file name, protobuf stops with "duplicate file name". This script changes the
# generated imports to point at those modules. It also reads the two imported
# schemas from those installed modules, so that the generated code always
# agrees with the modules that it imports at runtime.
#
# Usage: protos/generate.sh [python]
set -euo pipefail

PYTHON="${1:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IMPORTS="$(mktemp)"
trap 'rm -f "$IMPORTS"' EXIT
"$PYTHON" - "$IMPORTS" <<'PY'
import sys
from google.protobuf import descriptor_pb2
from bee_rpc import buffer_pb2
from node_controller.gateway.protos import celaut_pb2

descriptor_set = descriptor_pb2.FileDescriptorSet()
for module in (buffer_pb2, celaut_pb2):
    module.DESCRIPTOR.CopyToProto(descriptor_set.file.add())
with open(sys.argv[1], "wb") as f:
    f.write(descriptor_set.SerializeToString())
PY

compile() {
    # compile <directory> <package prefix or ""> <proto file>...
    local dir="$1" prefix="$2"
    shift 2
    (cd "$dir" && "$PYTHON" -W ignore -m grpc_tools.protoc -I. --descriptor_set_in="$IMPORTS" \
        --python_out=. --grpc_python_out=. --experimental_allow_proto3_optional "$@")
    local f
    for f in "$dir"/*_pb2.py "$dir"/*_pb2_grpc.py; do
        [ -e "$f" ] || continue
        sed -i -E \
            -e 's/^import buffer_pb2 as /from bee_rpc import buffer_pb2 as /' \
            -e 's/^import celaut_pb2 as /from node_controller.gateway.protos import celaut_pb2 as /' \
            "$f"
        if [ -n "$prefix" ]; then
            sed -i -E "s/^import ([a-z_]+_pb2) as /from $prefix import \\1 as /" "$f"
        fi
    done
}

# protoc writes a *_pb2_grpc.py file for each proto, also when it has no service.
# Remove the files that only contain the generated header.
remove_empty_grpc() {
    local f
    for f in "$@"; do
        if ! grep -q "Stub\|Servicer" "$f"; then
            rm -f "$f"
        fi
    done
}

compile "$ROOT/protos" protos api.proto solvers_dataset.proto regresion.proto
remove_empty_grpc "$ROOT/protos/solvers_dataset_pb2_grpc.py"

compile "$ROOT/dependencies/regresion_cnf" "" regresion.proto solvers_dataset.proto
remove_empty_grpc "$ROOT/dependencies/regresion_cnf/solvers_dataset_pb2_grpc.py"

compile "$ROOT/dependencies/random_cnf_generator" "" api.proto
compile "$ROOT/solvers/frontier" "" api.proto

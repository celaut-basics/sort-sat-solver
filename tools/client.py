"""A command line client of the sorter API (api.Solver over bee-rpc).

Run it from the root of the repository:

    python3 tools/client.py <sorter address> solve problem.cnf
    python3 tools/client.py <sorter address> start-train
    python3 tools/client.py <sorter address> stop-train
    python3 tools/client.py <sorter address> get-dataset dataset.bin
    python3 tools/client.py <sorter address> add-dataset dataset.bin
    python3 tools/client.py <sorter address> get-tensor tensor.bin
    python3 tools/client.py <sorter address> logs
    python3 tools/client.py <sorter address> upload-solver <service id> [--storage /nodo/storage]

<sorter address> is "<ip>:<port>" of the sorter instance (see `nodo instances`).
upload-solver reads the service from the registry of the local node:
<storage>/__registry__/<id>, <storage>/__metadata__/<id> and the blocks in
<storage>/__block__/. It needs read access to these directories.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grpc  # noqa: E402
from bee_rpc import buffer_pb2  # noqa: E402
from bee_rpc.client import Dir, client_grpc  # noqa: E402
from bee_rpc.utils import modify_env  # noqa: E402

from protos import api_pb2, api_pb2_grpc, celaut_pb2, solvers_dataset_pb2 as sd_pb2  # noqa: E402
from src import cnf as cnf_utils  # noqa: E402
from src.config import is_service_id  # noqa: E402


def empty_call(method, timeout):
    # bee-rpc does not send an empty message, so an Empty answer is an empty stream.
    list(client_grpc(method=method, timeout=timeout))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address")
    parser.add_argument("command", choices=["solve", "start-train", "stop-train", "get-dataset", "add-dataset",
                                            "get-tensor", "logs", "upload-solver"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--timeout", type=float, default=600, help="seconds (default 600)")
    parser.add_argument("--storage", default="/nodo/storage", help="main.STORAGE of the node")
    args = parser.parse_args()

    stub = api_pb2_grpc.SolverStub(grpc.insecure_channel(args.address))

    if args.command in ("solve", "get-dataset", "add-dataset", "get-tensor", "upload-solver") and not args.argument:
        parser.error(f"{args.command} needs an argument.")

    if args.command == "solve":
        with open(args.argument) as f:
            cnf = cnf_utils.from_dimacs(f)
        answer = next(client_grpc(
            method=stub.Solve, input=cnf, indices_serializer=api_pb2.Cnf,
            indices_parser={1: api_pb2.Interpretation, 2: buffer_pb2.Empty},
            partitions_message_mode_parser=True, timeout=args.timeout,
        ), None)
        if not isinstance(answer, api_pb2.Interpretation):
            print("UNKNOWN (no solver gave an answer)")
            return 2
        if cnf_utils.claims_sat(answer):
            print("SAT" if cnf_utils.is_model(cnf, answer) else "SAT (the model is not valid)")
            print(" ".join(str(v) for v in answer.variable) + " 0")
        else:
            print("UNSAT")
    elif args.command == "start-train":
        empty_call(stub.StartTrain, args.timeout)
    elif args.command == "stop-train":
        empty_call(stub.StopTrain, args.timeout)
    elif args.command == "get-dataset":
        data_set = next(client_grpc(method=stub.GetDataSet, indices_parser=sd_pb2.DataSet,
                                    partitions_message_mode_parser=True, timeout=args.timeout), sd_pb2.DataSet())
        Path(args.argument).write_bytes(data_set.SerializeToString())
        print(f"{len(data_set.data)} solver configurations.")
    elif args.command == "add-dataset":
        data_set = sd_pb2.DataSet()
        data_set.ParseFromString(Path(args.argument).read_bytes())
        list(client_grpc(method=stub.AddDataSet, input=data_set, indices_serializer=sd_pb2.DataSet,
                         timeout=args.timeout))
    elif args.command == "get-tensor":
        tensor = next(client_grpc(method=stub.GetTensor, indices_parser=api_pb2.Tensor,
                                  partitions_message_mode_parser=True, timeout=args.timeout), api_pb2.Tensor())
        Path(args.argument).write_bytes(tensor.SerializeToString())
        print(f"{len(tensor.non_escalar.non_escalar)} models.")
    elif args.command == "logs":
        for message in client_grpc(method=stub.StreamLogs, indices_parser=api_pb2.File,
                                   partitions_message_mode_parser=True):
            print(message.file, end="", flush=True)
    elif args.command == "upload-solver":
        service_id = args.argument
        if not is_service_id(service_id):
            parser.error("The service id must be a SHA3-256 hex digest.")
        storage = Path(args.storage)
        modify_env(block_dir=str(storage / "__block__") + "/")
        metadata = celaut_pb2.Metadata()
        metadata.ParseFromString((storage / "__metadata__" / service_id).read_bytes())
        list(client_grpc(
            method=stub.UploadSolver,
            input=(metadata, Dir(dir=str(storage / "__registry__" / service_id), _type=celaut_pb2.Service)),
            indices_serializer={1: celaut_pb2.Metadata, 2: celaut_pb2.Service},
            timeout=args.timeout,
        ))
        print(f"Uploaded {service_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

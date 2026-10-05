# sort-sat-solver

The sorter is a Celaut service that solves SAT problems in CNF. It does not
solve them itself. It knows a set of SAT solver services, it learns which
solver is the fastest for each shape of CNF, and it gives each CNF to the
best solver.

The sorter:

1. Trains: it gets random CNFs from the random CNF generator, gives each CNF
   to each solver with a time limit, and gives a score to each answer.
2. Fits a regression: the regression service fits one model for each solver
   configuration. The model predicts the score from the number of clauses and
   the number of variables of the CNF.
3. Solves: for a CNF, it tries the solvers from the best predicted score to
   the worst. It returns the first answer. It returns a model only if the
   model satisfies the CNF.

The sorter, the regression and the random generator run in separate
instances. The sorter starts its child instances through the gateway of the
node that runs it (celaut-project/nodo, branch `dev`).

## Repository

| Path | Content |
|------|---------|
| `.service/`, `start.sh`, `src/`, `requirements.txt` | The sorter service. |
| `protos/` | The API of the sorter (`api.proto`), of the regression (`regresion.proto`), the dataset (`solvers_dataset.proto`) and a copy of `celaut.proto` from nodo `dev`. |
| `dependencies/regresion_cnf/` | The regression service (scikit-learn, models exported to ONNX). |
| `dependencies/random_cnf_generator/` | The random k-CNF generator. |
| `solvers/frontier/` | A WalkSAT-like solver with the solver API (gRPC, bee-rpc). |
| `solvers/frontier_http/` | The same solver with an HTTP API. It is a standalone example. The sorter does not use it. |
| `tools/client.py` | A command line client of the sorter API. |
| `tests/` | Tests that run without a node. |

## How the sorter uses the node

- The packer adds the services of `.service/pack_config.json`
  `dependencies` to the filesystem of the sorter (`__services__`,
  `__metadata__`, `__block__`). It writes their service ids in
  `.dependencies`: `REGRESSION`, `RANDOM`, and one `SOLVER_<NAME>` key for
  each solver.
- The sorter reads the gateway address and its environment variables from
  `/__config__`.
- At the first call to a child service, the sorter calls
  `Gateway.StartService` with the hash of the service. If the node does not
  have the service, the sorter sends it from its filesystem. The sorter is a
  local instance of the node, so it sends no `Client` and no
  `RecursionGuard`.
- The sorter stops a child instance with `Gateway.StopService` after
  `CHILD_IDLE_TIMEOUT` seconds without use, and when its process receives
  `SIGTERM`. `nodo kill` does not send `SIGTERM` (see "Pack and run").
- The sorter declares no `network`. The node lets an instance reach the
  gateway and the instances that it started (nodo `docs/FIREWALL.md`).

## Pack and run

Use a node with the `dev` branch. Read nodo `docs/PACKING.md` first.

All the services of this repository use the architecture `linux/amd64`. The
packer can only build for the architecture of its host. For an arm64 host,
change `architecture` to `linux/arm64` in the five `service.json` files.

Pack the sorter from the root of the repository. The packer also packs the
three local dependencies. A pack can take a long time.

```bash
nodo pack .
```

Start the sorter. `-e` sets a variable of the table below.

```bash
nodo execute -e SAVE_TRAIN_DATA 10 <sorter service id>
nodo instances
```

`nodo instances` shows the address of the sorter instance. Use it with the
client (install the packages first: `pip install -r requirements.txt`):

```bash
python3 tools/client.py <address> start-train
python3 tools/client.py <address> solve problem.cnf      # DIMACS format
python3 tools/client.py <address> get-dataset dataset.bin
python3 tools/client.py <address> stop-train
```

Stop the sorter. `nodo kill` stops the microVM at once (SIGKILL), so the
sorter cannot stop its child instances, and the node does not stop them
either. Stop them yourself: `nodo instances --grouped` shows the instances
of each parent.

```bash
python3 tools/client.py <address> stop-train
sudo nodo kill <sorter instance id>
nodo instances --grouped
sudo nodo kill <child instance id>    # for each child instance of the sorter
```

While the sorter runs, it stops a child instance after `CHILD_IDLE_TIMEOUT`
seconds without use. It also stops all of them when its process receives
`SIGTERM` (for example in a development run).

### Development run without a pack

`nodo ggconf` writes `__config__` and `.dependencies` in the repository and
copies the dependencies from the registry of the node. The dependencies must
be in the registry: pack them one time first.

```bash
nodo pack dependencies/regresion_cnf
nodo pack dependencies/random_cnf_generator
nodo pack solvers/frontier
nodo ggconf .
python3 -m src.main
```

## Environment variables of the sorter

| Name | Default | Meaning |
|------|---------|---------|
| `SAVE_TRAIN_DATA` | 10 | Training rounds between two updates of the regression dataset. |
| `TRAIN_SOLVERS_TIMEOUT` | 30 | Time limit of one solver for one training CNF, in seconds. |
| `SOLVE_TIMEOUT` | 300 | Time limit of one solver attempt in `Solve`, in seconds. 0 means no limit. |
| `MAX_ERRORS_FOR_SOLVER` | 5 | Maximum number of solver attempts in one `Solve` call. |
| `MAX_REGRESSION_DEGREE` | 6 | Maximum degree of the regression polynomials (the regression limits it to 12). |
| `TIME_FOR_EACH_REGRESSION_LOOP` | 900 | Seconds between two regressions. A regression runs only if the dataset changed. |
| `MAX_WORKERS` | 20 | Threads of the gRPC server. |
| `CHILD_IDLE_TIMEOUT` | 600 | Seconds without use before the sorter stops a child instance. 0 means never. |
| `START_SERVICE_TIMEOUT` | 600 | Time limit of a `Gateway.StartService` call, in seconds. |
| `CHILD_READY_TIMEOUT` | 180 | Time limit for a new child instance to accept connections, in seconds. |

The random generator has `MIN_VARIABLES`, `MAX_VARIABLES`, `MIN_CLAUSES`,
`MAX_CLAUSES` and `CLAUSE_LENGTH`. The regression has `MAX_REGRESSION_DEGREE`
and `MIN_SAMPLES`. The frontier solvers have `FRONTIER_TIMEOUT`.

## API of the sorter

`api.Solver` in `protos/api.proto`, port 8081. Each method is a stream of
`buffer.Buffer` in each direction (bee-rpc).

| Method | Input | Output |
|--------|-------|--------|
| `Solve` | `Cnf` | 1: `Interpretation`. No message if no solver gave an answer. |
| `StartTrain`, `StopTrain` | nothing | nothing |
| `UploadSolver` | 1: `celaut.Metadata`, 2: `celaut.Service` | nothing |
| `GetTensor` | nothing | `Tensor`: the ONNX model of each solver configuration |
| `GetDataSet` | nothing | `dataset.DataSet` |
| `AddDataSet` | `dataset.DataSet` | nothing |
| `StreamLogs` | nothing | `File` messages with the log, until the client cancels |
| `AddTensor` | `Tensor` | Not implemented (`UNIMPLEMENTED`). |

bee-rpc does not send a message without fields. Thus an empty answer is a
stream without a message.

### Scores

A correct answer has the score `1 / (1 + seconds)`. No answer (time limit or
error) has the score 0. A wrong answer has the score -1. A model is checked
against the CNF. An UNSAT answer is correct if no solver found a model for the
same CNF.

## Add a solver

A solver is a service that implements `api.Solver/Solve` with the messages of
`solvers/frontier/api.proto`, over bee-rpc. There are two ways to add one:

- Add it to `dependencies` in `.service/pack_config.json` with a key that
  starts with `SOLVER_`, then pack the sorter again.
- Send it to a running sorter with `UploadSolver`. For a service in the
  registry of the local node:
  `python3 tools/client.py <address> upload-solver <service id>`.

## Tests

The tests do not need a node. A fake gateway parses the requests with the
indices of nodo `protos/gateway_bee.py`. The child services run as local
processes.

```bash
pip install -r tests/requirements.txt
python3 -m unittest discover -s tests -t .
```

## Protos

`protos/celaut.proto` is a copy of `protos/celaut.proto` of nodo `dev`. To
update it, copy it again (keep the first two comment lines) and generate the
code:

```bash
pip install grpcio-tools==1.56.0 "setuptools<81"
python3 protos/generate.py
```

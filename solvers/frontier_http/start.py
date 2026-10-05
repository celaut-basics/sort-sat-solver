"""HTTP entry point of the frontier solver.

POST /solve with {"clauses": [[1, -2, 3], [-1, 2]]}. The answer is
{"satisfiable": true, "variable": [...]} or {"satisfiable": false}.

This service is a standalone example. The sorter does not use it: the sorter
calls solvers through api.Solver/Solve over gRPC (see solvers/frontier).
"""
import os
import time
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import frontier

PORT = 8080  # Must agree with .service/service.json.
# Search time limit of one request in seconds.
TIMEOUT = float(os.environ.get("FRONTIER_TIMEOUT", "60") or 60)

app = FastAPI()


class CNFRequest(BaseModel):
    clauses: List[List[int]]


# A plain "def": FastAPI runs it in a worker thread, so that a long search does
# not block the event loop.
@app.post("/solve")
def solve(req: CNFRequest):
    deadline = time.monotonic() + TIMEOUT
    try:
        model = frontier.solve(req.clauses, should_stop=lambda: time.monotonic() > deadline)
    except frontier.InvalidCnf as e:
        raise HTTPException(status_code=422, detail=str(e))
    except frontier.Unsatisfiable:
        return {"satisfiable": False}
    if model is None:
        raise HTTPException(status_code=504, detail="No model found in the time limit.")
    return {"satisfiable": True, "variable": model}


if __name__ == "__main__":
    print(f"Starting HTTP server. Listening on port {PORT}.", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT)


# Example request:
#
# curl -X POST http://<instance address>/solve \
#      -H "Content-Type: application/json" \
#      -d '{"clauses": [[1, -2, 3], [-1, 2, 4], [-3, -4, 5], [2, 3, -5]]}'
#
# Example answer:
# {"satisfiable":true,"variable":[1,2,3,-4,5]}

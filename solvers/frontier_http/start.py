from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import frontier  # Importamos el módulo con la lógica SAT
import uvicorn

app = FastAPI()


class CNFRequest(BaseModel):
    # Estructura JSON: {"clauses": [[1, -2, 3], [-1, 2], ...]}
    clauses: List[List[int]]


@app.post("/solve")
async def solve(req: CNFRequest):
    # Llamamos directamente a la lógica de negocio con las cláusulas como lista
    solution = frontier.ok(req.clauses)

    # Devolvemos la solución como JSON
    return {"variable": solution}


if __name__ == "__main__":
    print('Starting HTTP server. Listening on port 8080.')
    uvicorn.run(app, host="0.0.0.0", port=8080)


# =============================================================
# curl para probar el servicio:
#
# curl -X POST http://localhost:8080/solve \
#      -H "Content-Type: application/json" \
#      -d '{"clauses": [[1, -2, 3], [-1, 2, 4], [-3, -4, 5], [2, 3, -5]]}'
#
# Respuesta esperada (ejemplo):
# {"variable":[1,2,3,4,5]}
# =============================================================
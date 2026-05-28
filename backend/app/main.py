from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal, init_db
from app.routers import employees, policy, submissions
from app.llm.resolve import resolve_llm_provider
from app.services.policy_indexer import index_policies
from app.services.seed import seed_employees


def _index_policies_in_background() -> None:
    try:
        count = index_policies()
        print(f"Policy RAG index (Weaviate): {count} chunks loaded")
    except Exception as exc:
        print(f"Policy indexing skipped or failed: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    print(f"LLM provider: {resolve_llm_provider()}")
    db = SessionLocal()
    try:
        seed_employees(db)
        # Start API quickly; build policy index in background.
        threading.Thread(target=_index_policies_in_background, daemon=True).start()
    finally:
        db.close()
    yield


app = FastAPI(title="Northwind Expense Pre-Review API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(employees.router, prefix="/api")
app.include_router(submissions.router, prefix="/api")
app.include_router(policy.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}

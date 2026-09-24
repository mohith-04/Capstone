"""
AI Ambulance Route Optimization System — Backend Entry Point
============================================================
This is the root FastAPI application. It wires together all routers and
WebSocket endpoints from the individual modules (routing, signal priority,
hospital alerting). The server is the single contact point for the React
frontend and any future mobile clients.

Design rationale
----------------
FastAPI was chosen over Flask/Django because:
  1. Native async/await — essential for non-blocking WebSocket handling when
     multiple ambulance simulations run concurrently.
  2. Automatic OpenAPI docs at /docs — useful for viva demos without needing
     a separate API testing tool.
  3. Pydantic-based request/response validation out of the box.

Run in development:
    python -m uvicorn main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from routers import health
from routers import route as route_router
from routers import simulation as sim_router
from routers import signals as signals_router
from routers import alerts as alerts_router
from services.graph import load_graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — runs once at startup and once at shutdown.
#
# We load the OSMnx graph here (rather than at import time or on first
# request) because:
#   - It takes 30–60 s on first run (downloads from OSM) and ~1 s on
#     subsequent runs (from disk cache).
#   - Doing it at startup means every request handler can assume the graph
#     is ready; the /route router returns 503 if it somehow isn't.
#   - Using a lifespan context (not @app.on_event, which is deprecated)
#     is the recommended FastAPI pattern as of v0.95+.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Startup: loading road network graph ===")
    try:
        G = load_graph()
        # Inject the graph singleton into the route router so every endpoint
        # can access it via FastAPI's dependency injection system.
        route_router.set_graph(G)
        sim_router.set_graph(G)
        logger.info("=== Graph ready: %d nodes, %d edges ===", G.number_of_nodes(), G.number_of_edges())
    except Exception:
        logger.exception(
            "Graph load failed — /route endpoints will return 503. "
            "Check internet connectivity for first-run OSM download."
        )
    yield
    # Shutdown cleanup (nothing needed for now; placeholder for future teardown)
    logger.info("=== Shutdown ===")


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AI Ambulance Route Optimization API",
    description=(
        "Real-time backend for ambulance routing, traffic signal priority, "
        "and hospital pre-alerting. Simulation-based — no live hardware required."
    ),
    version="0.5.0",
    docs_url="/docs",        # Swagger UI — handy for viva demo
    redoc_url="/redoc",      # ReDoc alternative
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the React dev server (localhost:3000) to call this API.
# In a production deployment this list would be locked to the real origin.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# Each feature module lives in its own router file.
#
# Stage 1: /health
# Stage 2: /route, /congestion
# Stage 3: /sim          (ambulance simulator -- REST + WebSocket)
# Stage 4: /signals      (signal priority -- REST; real-time via /sim WS)
# Stage 5: /alerts       (hospital alerts -- added in Stage 5)
# ---------------------------------------------------------------------------
app.include_router(health.router)
app.include_router(route_router.router)
app.include_router(sim_router.router)
app.include_router(signals_router.router)
app.include_router(alerts_router.router)


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

"""FastAPI app: JSON API + server-rendered screens from the same process (spec 04 §11.1)."""

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from tlc.api.routers import runs  # noqa: E402
from tlc.web.views import router as web_router  # noqa: E402

app = FastAPI(title="TLC plate readout", version="0.6.0")
app.include_router(runs.router)
app.include_router(web_router)
_static = Path(__file__).resolve().parent.parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

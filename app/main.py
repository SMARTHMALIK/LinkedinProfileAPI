from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import scraper, parser
from app.auth import session
from app.models import ProfileResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up LinkedIn session at startup so the first request isn't slow."""
    try:
        session.login()
        print("LinkedIn session established.")
        # Call /me exactly once at startup and cache it.
        # Never called per-request — avoids cookie invalidation from cloud IPs.
        scraper.warm_me_cache()
    except Exception as exc:
        print(f"Warning: LinkedIn login failed at startup: {exc}")
    yield


app = FastAPI(
    title="LinkedIn Profile API",
    description=(
        "Accepts a LinkedIn profile URL and returns structured profile data "
        "by calling LinkedIn's internal Voyager API."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_static = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_static / "index.html")


@app.get("/health", tags=["Meta"])
def health():
    """Simple liveness check."""
    return {"status": "ok"}


@app.get(
    "/profile",
    response_model=ProfileResponse,
    tags=["Profile"],
    summary="Fetch a LinkedIn profile",
    responses={
        400: {"description": "Invalid or unparseable LinkedIn URL"},
        404: {"description": "Profile not found or private"},
        429: {"description": "LinkedIn rate limit reached"},
        503: {"description": "LinkedIn authentication failure"},
    },
)
def get_profile(
    url: str = Query(
        ...,
        description="LinkedIn profile URL (e.g. https://www.linkedin.com/in/john-doe) or bare slug (john-doe)",
        examples=["https://www.linkedin.com/in/john-doe"],
    )
):
    try:
        raw = scraper.fetch_all(url)
        result = parser.build_response(raw)
        return result

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    except PermissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

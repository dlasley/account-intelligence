import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    debug = os.environ.get("FASTAPI_DEBUG", "").lower() == "true"
    app = FastAPI(
        title="Account Intelligence Worker",
        docs_url="/docs" if debug else None,
        redoc_url=None,
    )

    # CORS_ORIGINS is a comma-separated list of allowed origins.
    # In local dev, set CORS_ORIGINS=http://localhost:3000.
    # In Cloud Run, set to the Vercel frontend URL.
    # Unset means no cross-origin requests are allowed.
    cors_origins_raw = os.environ.get("CORS_ORIGINS", "")
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    from src.server.routes.event import router as event_router
    from src.server.routes.event_js import router as event_js_router
    from src.server.routes.inbound import router as inbound_router
    from src.server.routes.outreach import router as outreach_router
    from src.server.routes.polls import router as polls_router
    from src.server.routes.scheduler import router as scheduler_router
    from src.server.routes.signal import router as signal_router

    app.include_router(inbound_router)
    app.include_router(scheduler_router)
    app.include_router(polls_router)
    app.include_router(outreach_router)
    app.include_router(event_router)
    app.include_router(event_js_router)
    app.include_router(signal_router)
    return app

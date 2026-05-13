from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

_EVENT_JS_PATH = Path(__file__).parent.parent / "static" / "dist" / "event.js"


@router.get("/event.js")
async def event_js() -> Response:
    body = _EVENT_JS_PATH.read_bytes()
    return Response(
        content=body,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class AudioSizeLimitMiddleware:
    """ASGI middleware that rejects requests whose Content-Length exceeds max.

    This is an early fast-fail based on the header. It does not protect against
    spoofed Content-Length or chunked uploads (no Content-Length header).
    """

    def __init__(self, app: ASGIApp, max_content_length: int) -> None:
        self.app = app
        self.max = max_content_length

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            cl = None
            for k, v in scope.get("headers", []):
                if k == b"content-length":
                    try:
                        cl = int(v)
                    except Exception:
                        cl = None
                    break

            if cl is not None and cl > self.max:
                response = JSONResponse(
                    {"detail": "Audio file too large"}, status_code=413)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

import zlib
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.types import Message

from app.error_handlers import AppError, ErrorType

# Matches Caddy's cap on uncompressed bodies.
MAX_DECOMPRESSED_BODY_BYTES = 10 * 1024 * 1024

_GZIP_ENCODINGS = {"gzip", "x-gzip"}
_IDENTITY_ENCODINGS = {"", "identity"}


def decompress_gzip(data: bytes, max_size: int = MAX_DECOMPRESSED_BODY_BYTES) -> bytes:
    """Decompress a single-member gzip body of at most `max_size` bytes.

    Args:
        data: The compressed request body.
        max_size: Largest decompressed size accepted, in bytes.

    Returns:
        bytes: The decompressed body.

    Raises:
        AppError: 413 past `max_size`, 400 for invalid gzip.
    """

    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        # max_length stops a gzip bomb from inflating further.
        body = decompressor.decompress(data, max_size + 1)
    except zlib.error as err:
        raise AppError(
            status_code=400,
            error_type=ErrorType.BAD_REQUEST,
            message="Request body is not valid gzip.",
        ) from err

    if len(body) > max_size:
        raise AppError(
            status_code=413,
            error_type=ErrorType.PAYLOAD_TOO_LARGE,
            message=f"Decompressed request body exceeds the {max_size // (1024 * 1024)} MB limit.",
            extra={"max_size_bytes": max_size},
        )

    if not decompressor.eof or decompressor.unused_data:
        raise AppError(
            status_code=400,
            error_type=ErrorType.BAD_REQUEST,
            message="Request body is not valid gzip.",
        )

    return body


async def decode_request(request: Request) -> Request:
    """Return the request with its gzip body decompressed, or unchanged if not gzip.

    Raises:
        AppError: 415 for any other Content-Encoding, or the errors from `decompress_gzip`.
    """

    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding in _IDENTITY_ENCODINGS:
        return request

    if encoding not in _GZIP_ENCODINGS:
        raise AppError(
            status_code=415,
            error_type=ErrorType.UNSUPPORTED_MEDIA_TYPE,
            message=f"Unsupported Content-Encoding '{encoding}': send gzip or no encoding.",
        )

    body = decompress_gzip(await request.body())
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Later messages, such as a disconnect, come from the server.
        return await request.receive()

    return Request(request.scope, receive)


class GzipRequestRoute(APIRoute):
    """Route that accepts gzip-compressed and uncompressed request bodies.

    Decompresses before FastAPI reads the body, because FastAPI turns errors raised
    while reading it into a generic 400.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def gzip_aware_handler(request: Request) -> Response:
            return await handler(await decode_request(request))

        return gzip_aware_handler

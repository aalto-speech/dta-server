"""Gzip request decoding, without HTTP."""

import asyncio
import gzip
import zlib

import pytest
from fastapi import Request

from app.error_handlers import AppError, ErrorType
from app.utils.gzip_request import decode_request, decompress_gzip


def test_decompress_gzip_round_trips():
    assert decompress_gzip(gzip.compress(b'{"a":1}')) == b'{"a":1}'


def test_decompress_gzip_accepts_exactly_the_limit():
    assert decompress_gzip(gzip.compress(b"x" * 100), max_size=100) == b"x" * 100


def test_decompress_gzip_refuses_to_inflate_past_the_limit():
    with pytest.raises(AppError) as err:
        decompress_gzip(gzip.compress(b"x" * 101), max_size=100)

    assert err.value.status_code == 413
    assert err.value.error_type == ErrorType.PAYLOAD_TOO_LARGE
    assert err.value.extra == {"max_size_bytes": 100}


@pytest.mark.parametrize("data", [
    b"not gzip at all",
    gzip.compress(b'{"a":1}')[:-6],
    gzip.compress(b'{"a":1}') + b"trailing",
    gzip.compress(b"a") + gzip.compress(b"b"),
    zlib.compress(b'{"a":1}'),
    b"",
], ids=["garbage", "truncated", "trailing_data", "multi_member", "zlib_not_gzip", "empty"])
def test_decompress_gzip_rejects_invalid_input(data: bytes):
    with pytest.raises(AppError) as err:
        decompress_gzip(data)

    assert err.value.status_code == 400
    assert err.value.error_type == ErrorType.BAD_REQUEST


def _request(body: bytes, encoding: str | None, after_body: list[dict] | None = None) -> Request:
    messages = [{"type": "http.request", "body": body, "more_body": False}, *(after_body or [])]

    async def receive():
        return messages.pop(0)

    headers = [(b"content-encoding", encoding.encode())] if encoding is not None else []
    return Request({"type": "http", "method": "POST", "path": "/events", "headers": headers},
                   receive)


@pytest.mark.parametrize("encoding", [None, "", "identity", " Identity "])
def test_decode_request_leaves_uncompressed_requests_alone(encoding: str | None):
    request = _request(b"{}", encoding)

    assert asyncio.run(decode_request(request)) is request


@pytest.mark.parametrize("encoding", ["gzip", "x-gzip", "GZIP", " gzip "])
def test_decode_request_decompresses_gzip(encoding: str):
    async def run():
        decoded = await decode_request(_request(gzip.compress(b'{"a":1}'), encoding))
        return await decoded.body()

    assert asyncio.run(run()) == b'{"a":1}'


@pytest.mark.parametrize("encoding", ["br", "deflate", "gzip, gzip"])
def test_decode_request_rejects_other_encodings(encoding: str):
    with pytest.raises(AppError) as err:
        asyncio.run(decode_request(_request(b"{}", encoding)))

    assert err.value.status_code == 415
    assert err.value.error_type == ErrorType.UNSUPPORTED_MEDIA_TYPE


def test_decoded_request_passes_later_messages_through():
    """After the body, receive() returns the server's messages, such as a disconnect."""

    async def run():
        decoded = await decode_request(_request(
            gzip.compress(b"{}"), "gzip", after_body=[{"type": "http.disconnect"}]))
        first = await decoded.receive()
        second = await decoded.receive()
        return first, second

    first, second = asyncio.run(run())

    assert first == {"type": "http.request", "body": b"{}", "more_body": False}
    assert second == {"type": "http.disconnect"}

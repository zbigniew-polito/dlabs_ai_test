import hashlib
import io
import time
from collections import defaultdict
from email.utils import formatdate
from functools import wraps
from typing import Any, Callable, DefaultDict, Dict, Mapping, Optional

import aiofiles
from starlette.responses import Response
from starlette.types import Receive, Scope, Send
from starlette.background import BackgroundTask
from datetime import datetime


def current_time_millis():
    return round(time.time() * 1000)


separator = object()


def cache(timeout_in_ms: int = 1000) -> Callable[[], Any]:
    def wrapper(func: Callable[[], Any]) -> Callable[[], Any]:
        store: DefaultDict[Any, Dict[float, Any]] = defaultdict(dict)

        @wraps(func)
        async def cached_func(*args: Any, **kwargs: Any):

            call_time = current_time_millis()
            key = args + (separator,) + tuple(sorted(kwargs.items()))
            result = None
            if key in store:
                for _time, _result in list(store[key].items()):
                    if call_time - _time < timeout_in_ms:
                        result = _result
                    else:
                        store[key].pop(_time)

            if result:
                if isinstance(result, Dict):
                    result["query_time"] = current_time_millis() - call_time
                return result

            result = await func(*args, **kwargs)
            store[key][call_time] = result
            if isinstance(result, Dict):
                result["query_time"] = current_time_millis() - call_time
            return result

        return cached_func

    return wrapper


class BytesIOResponse(Response):
    chunk_size = 4096

    def __init__(
        self,
        bytes_io: io.BytesIO,
        status_code: int = 200,
        headers: Optional[Mapping[str, str]] = None,
        media_type: Optional[str] = None,
        background: Optional[BackgroundTask] = None,
        method: Optional[str] = None,
        last_modified: Optional[datetime] = None,
    ) -> None:
        assert aiofiles is not None, "'aiofiles' must be installed to use FileResponse"
        self.bytes_io = bytes_io
        self.status_code = status_code
        self.send_header_only = method is not None and method.upper() == "HEAD"
        assert (
            media_type is not None
        ), "'media_type' must be specified to use BytesIOResponse"
        self.last_modified = last_modified
        self.media_type = media_type
        self.background = background
        self.init_headers(headers)
        self.set_headers()

    def set_headers(self) -> None:
        content_length = str(self.bytes_io.getbuffer().nbytes)
        if self.last_modified:
            etag_base = (
                str(int(self.last_modified.timestamp() * 1000))
                + "-"
                + str(self.bytes_io.getbuffer().nbytes)
            )
            etag: str = hashlib.md5(etag_base.encode()).hexdigest()

            self.last_modified = formatdate(self.last_modified.timestamp(), usegmt=True)
            self.headers.setdefault("last-modified", self.last_modified)
            self.headers.setdefault("etag", etag)

        self.headers.setdefault("content-length", content_length)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        if self.send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            # async with aiofiles.open(self.path, mode="rb") as file:
            more_body = True
            while more_body:
                chunk = self.bytes_io.read(self.chunk_size)
                more_body = len(chunk) == self.chunk_size
                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": more_body,
                    }
                )
        if self.background is not None:
            await self.background()

import asyncio
import hashlib
import imghdr
import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from math import floor
from typing import Callable, Dict, Iterable, Optional, Tuple, Union

import aiofiles as aiof
import aiosqlite
from aiocache import Cache
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
    Request,
)
from fastapi.responses import HTMLResponse
from PIL import Image

from .icon import favicon_bytes
from .metadata import tags_metadata
from .tools import BytesIOResponse
from .tools import cache as z_cache
from .tools import current_time_millis

import time

CACHE_TIME_IN_SEC = 60 * 60

loop = asyncio.get_event_loop()
cache = Cache(ttl=CACHE_TIME_IN_SEC)
thread_pool = ThreadPoolExecutor()
app = FastAPI(openapi_tags=tags_metadata)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


class Mode(str, Enum):
    auto = "auto"
    match_height = "match_height"
    match_width = "match_width"
    stretch = "stretch"


class DBEntry(int, Enum):
    id = 0
    filename = 1
    oryginal_filename = 2
    hash = 3
    created = 4


@app.get("/favicon.ico", include_in_schema=False)
@z_cache(timeout_in_ms=1000 * 60 * 60 * 24)
async def favicon() -> Response:
    return Response(
        content=favicon_bytes,
        headers={
            "Cache-Control": f"max-age={60*60*24}",
        },
        media_type="image/x-icon",
    )


@app.get("/images/{target_w}x{target_h}")
async def get(target_w: int, target_h: int, mode: Mode = Mode.auto) -> BytesIOResponse:

    try:
        target_w = max(0, min(int(target_w), 4096 * 2))
        target_h = max(0, min(int(target_h), 4096 * 2))
    except:
        raise HTTPException(
            status_code=400,
            detail="Bad parameters, width 0-4096 height 0-8192, 0-8192x0-8192",
        )

    def create_scaled_image(
        _target_w: int, _target_h: int, _mode: Mode, _fname: str
    ) -> io.BytesIO:

        image: Image = Image.open(f"files/{_fname}")
        source_w: int
        source_h: int
        source_w, source_h = image.size

        match_width: Callable[
            [int, int, int, int], Tuple[int, int]
        ] = lambda __source_w, __source_h, __target_w, __target_h: (
            __target_w,
            floor(__target_w * float(__source_h) / float(__source_w)),
        )

        match_height: Callable[
            [int, int, int, int], Tuple[int, int]
        ] = lambda __source_w, __source_h, __target_w, __target_h: (
            floor(__target_h * float(__source_w) / float(__source_h)),
            _target_h,
        )

        stretch: Callable[
            [int, int, int, int], Tuple[int, int]
        ] = lambda __source_w, __source_h, __target_w, __target_h: (
            __target_w,
            __target_h,
        )

        auto: Callable[
            [int, int, int, int], Tuple[int, int]
        ] = lambda __source_w, __source_h, __target_w, __target_h: (
            match_width(__source_w, __source_h, __target_w, __target_h)
            if (__source_w > __source_h)
            else match_height(__source_w, __source_h, __target_w, __target_h)
        )

        switch: Dict[Mode, Callable[[int, int, int, int], Tuple[int, int]]] = {
            Mode.match_width: match_width,
            Mode.match_height: match_height,
            Mode.stretch: stretch,
            Mode.auto: auto,
        }

        _target_w, _target_h = switch[_mode](source_w, source_h, _target_w, _target_h)

        image = image.resize((_target_w, _target_h))
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        return img_byte_arr

    cache_key: str = f"{target_w}_{target_h}_{mode}"

    img_byte_arr: Optional[io.BytesIO]
    oryg_filename: Optional[str]
    creation_date: Optional[Union[str, datetime]]
    modification_date: Optional[Union[str, datetime]]
    fname: Optional[str]
    (
        fname,
        creation_date,
        modification_date,
        oryg_filename,
        img_byte_arr,
    ) = await cache.get(cache_key) or (None, None, None, None, None)

    if not img_byte_arr:
        async with aiosqlite.connect("db.db") as db:
            cursor: aiosqlite.Cursor = await db.execute(
                f" SELECT * FROM IMAGES ORDER BY RANDOM() LIMIT 1"
            )
            row: Union[aiosqlite.Row, None] = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Not Found.")

        fname = row[DBEntry.filename]
        oryg_filename = row[DBEntry.oryginal_filename]

        creation_date = datetime.utcfromtimestamp(float(row[DBEntry.created] / 1000))

        img_byte_arr = await loop.run_in_executor(
            thread_pool, partial(create_scaled_image, target_w, target_h, mode, fname)
        )
        modification_date = datetime.now()
        await cache.set(
            cache_key,
            (fname, creation_date, modification_date, oryg_filename, img_byte_arr),
        )

    img_byte_arr.seek(0)

    max_age: int = int(
        abs(
            (
                datetime.now()
                - (modification_date + timedelta(seconds=CACHE_TIME_IN_SEC))
            ).total_seconds()
        )
    )
    last_modified = modification_date
    creation_date = creation_date.strftime("%a, %d %b %y %T %z")
    modification_date = modification_date.strftime("%a, %d %b %y %T %z")
    read_date = datetime.now().strftime("%a, %d %b %y %T %z")

    return BytesIOResponse(
        img_byte_arr,
        media_type="image/png",
        headers={
            "Content-disposition": f'inline;filename="{fname}_{target_w}x{target_h}_{oryg_filename}";creation-date="{creation_date}";modification-date="{modification_date}";read-date="{read_date}"',
            "Cache-Control": f"max-age={max_age}",
        },
        last_modified=last_modified,
    )


@app.post("/images", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile = File(...)):
    try:
        if file.content_type.split("/")[0] != "image":
            raise HTTPException(
                status_code=400,
                detail="Unknown File Format, accepts: jpeg, png, gif, bmp, webp",
            )
    except:
        raise HTTPException(
            status_code=400,
            detail="Malformed header. Content-Type",
        )

    data: bytes = await file.read()
    hdr: str = imghdr.what(None, h=data)
    if hdr not in ["jpeg", "png", "gif", "bmp", "webp"]:
        raise HTTPException(
            status_code=400,
            detail="Unknown File Format, accepts: jpeg, png, gif, bmp, webp",
        )

    if not os.path.exists("files"):
        os.makedirs("files")

    hash: str = hashlib.md5(data).hexdigest()

    async with aiosqlite.connect("db.db") as db:
        cursor: aiosqlite.Cursor = await db.execute(
            f"SELECT * FROM IMAGES WHERE hash=?;", (hash,)
        )
        rows: Iterable[aiosqlite.Row] = await cursor.fetchall()
        if rows:
            raise HTTPException(status_code=409, detail="Entry already exists.")

    filename: str = uuid.uuid4().hex
    ext: str = file.filename.split(".")[1]

    async with aiof.open(f"files/{filename}.{ext}", "wb") as out:
        await out.write(data)
        await out.flush()

    async with aiosqlite.connect("db.db") as db:
        await db.execute(
            "INSERT INTO IMAGES(FILENAME,FILENAME_ORYGINAL,HASH,CREATED) VALUES(?,?,?,?);",
            (f"{filename}.{ext}", file.filename, hash, current_time_millis()),
        )
        await db.commit()
    return


@app.get(
    "/browser_cache_headers_test/{width}x{height}/{mode}", response_class=HTMLResponse
)
async def browser_cache_headers_test(width: int, height: int, mode: Mode = Mode.auto):
    return f'<html><head><title>Test</title></head><body><img src="/images/{width}x{height}?mode={mode}"></body></html>'

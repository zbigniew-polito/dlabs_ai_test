#!/usr/bin/python
from asyncio.events import AbstractEventLoop
import aiosqlite
import os
import asyncio
import os
import glob

filename: str = "db.db"
os.remove(filename)

create_query: str = "CREATE TABLE IMAGES( id  INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_oryginal TEXT NOT NULL, hash TEXT NOT NULL,created INTEGER NOT NULL );"


async def create() -> None:
    async with aiosqlite.connect("db.db") as db:
        await db.execute(create_query)
        await db.commit()

    files = glob.glob("files/*")
    for f in files:
        os.remove(f)


if __name__ == "__main__":
    loop: AbstractEventLoop = asyncio.get_event_loop()
    loop.run_until_complete(create())
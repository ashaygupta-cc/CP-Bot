import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    print(os.getenv("DATABASE_URL"))

    conn = await asyncpg.connect(
        os.getenv("DATABASE_URL"),
        ssl="require"
    )

    print("Connected!")
    await conn.close()

asyncio.run(main())
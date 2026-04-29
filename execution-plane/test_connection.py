import asyncio
import psycopg2
from temporalio.client import Client

async def main():
    print("Testing PostgreSQL connection...")
    try:
        conn = psycopg2.connect(
            dbname="devflow",
            user="postgres",
            password="postgres",
            host="localhost",
            port="5433"
        )
        print("Successfully connected to PostgreSQL.")
        conn.close()
    except Exception as e:
        print(f"Failed to connect to PostgreSQL: {e}")

    print("Testing Temporal connection...")
    try:
        client = await Client.connect("localhost:7233")
        print("Successfully connected to Temporal.")
    except Exception as e:
        print(f"Failed to connect to Temporal: {e}")

if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import registered_activities

TASK_QUEUE = "DEVFLOW_TASK_QUEUE"
DEFAULT_TEMPORAL_TARGET = "localhost:7233"


async def create_worker(client: Client, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        activities=registered_activities(),
    )


async def run_worker() -> None:
    temporal_target = os.getenv("TEMPORAL_TARGET", DEFAULT_TEMPORAL_TARGET)
    client = await Client.connect(temporal_target)
    worker = await create_worker(client)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())

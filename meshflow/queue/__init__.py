"""Background task queue for MeshFlow — SQLite-backed async job queue.

Provides durable, inspectable task queuing for long-running governed workflows.
Tasks survive process restarts; workers pick them up on reconnection.

Usage::

    from meshflow.queue import TaskQueue, QueueWorker, TaskItem

    queue = TaskQueue("meshflow_queue.db")
    task_id = await queue.push({"workflow": "review.yaml", "task": "Draft NDA"})

    # In a separate process / coroutine
    worker = QueueWorker(queue)
    await worker.run(concurrency=4)
"""

from meshflow.queue.core import TaskItem, TaskQueue, QueueWorker, TaskStatus

__all__ = ["TaskItem", "TaskQueue", "QueueWorker", "TaskStatus"]

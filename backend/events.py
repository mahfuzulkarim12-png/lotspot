"""In-process pub/sub for Server-Sent Events.

One Broadcaster per app instance. Each SSE client gets its own bounded
asyncio.Queue; publish() never blocks and never raises — if a client stalls
and its queue fills up, new events for that client are dropped (the client
will resync on reconnect, and the UI refetches on connect anyway).
"""

import asyncio

DEFAULT_MAX_QUEUE_SIZE = 100


class Broadcaster:
    def __init__(self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE):
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue_size = max_queue_size

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # stalled client: drop, it resyncs on reconnect

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import TypeAlias

from villageragent_visualizer.dto import JSONValue, RunState
from villageragent_visualizer.runtime_graph import RuntimeGraphService
from villageragent_visualizer.runs import RunRepository


STREAM_PROTOCOL_VERSION = "1.0"
StreamEnvelope: TypeAlias = dict[str, JSONValue]
CheckpointMarker: TypeAlias = tuple[int, int, int] | None


class LatestEventQueue:
    def __init__(self) -> None:
        self._available = asyncio.Event()
        self._snapshot: StreamEnvelope | None = None
        self._control: StreamEnvelope | None = None

    async def get(self) -> StreamEnvelope:
        await self._available.wait()
        if self._snapshot is not None:
            event = self._snapshot
            self._snapshot = None
        elif self._control is not None:
            event = self._control
            self._control = None
        else:
            raise RuntimeError("Stream queue was signaled without an event")
        if self._snapshot is None and self._control is None:
            self._available.clear()
        return event

    def offer(self, event: StreamEnvelope) -> None:
        if event["type"] == "snapshot":
            self._snapshot = event
        elif event["type"] != "heartbeat" or (
            self._snapshot is None and self._control is None
        ):
            self._control = event
        self._available.set()


class StreamSubscription:
    def __init__(
        self,
        manager: SnapshotStreamManager,
        hub: _RunStreamHub,
        queue: LatestEventQueue,
        initial: StreamEnvelope,
    ) -> None:
        self._manager = manager
        self._hub = hub
        self.queue = queue
        self.initial = initial

    async def close(self) -> None:
        await self._manager.unsubscribe(self._hub, self.queue)


class SnapshotStreamManager:
    def __init__(
        self,
        *,
        result_root: Path,
        runs: RunRepository,
        runtime_graphs: RuntimeGraphService,
        poll_interval: float = 0.5,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.result_root = result_root
        self.runs = runs
        self.runtime_graphs = runtime_graphs
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self._hubs: dict[str, _RunStreamHub] = {}

    @property
    def active_watcher_count(self) -> int:
        return len(self._hubs)

    def subscribe(self, run_id: str) -> StreamSubscription:
        hub = self._hubs.get(run_id)
        if hub is None:
            hub = _RunStreamHub(manager=self, run_id=run_id)
            self._hubs[run_id] = hub
        queue = LatestEventQueue()
        hub.subscribers.add(queue)
        initial = hub.current_event()
        hub.start()
        return StreamSubscription(self, hub, queue, initial)

    async def unsubscribe(
        self,
        hub: _RunStreamHub,
        queue: LatestEventQueue,
    ) -> None:
        hub.subscribers.discard(queue)
        if hub.subscribers:
            return
        self._hubs.pop(hub.run_id, None)
        await hub.stop()


class _RunStreamHub:
    def __init__(self, *, manager: SnapshotStreamManager, run_id: str) -> None:
        self.manager = manager
        self.run_id = run_id
        self.subscribers: set[LatestEventQueue] = set()
        self.revision = 0
        self.marker: CheckpointMarker = None
        self.available: bool | None = None
        self.terminal_state: RunState | None = None
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    def current_event(self) -> StreamEnvelope:
        self.marker = self._checkpoint_marker()
        manifest = self.manager.runs.get_run(self.run_id)
        self.available = manifest is not None
        if manifest is None:
            return self._event("run_unavailable", {"message": "Run is unavailable."})
        result = self.manager.runtime_graphs.load_checkpoint(self.run_id)
        if result.graph is not None:
            return self._event("snapshot", asdict(result.graph))
        if result.error is None:
            return self._event(
                "error",
                {"code": "empty_result", "message": "Checkpoint result is empty."},
            )
        return self._event(
            "error",
            {
                "code": result.error.code.value,
                "message": result.error.message,
                "warnings": [asdict(warning) for warning in result.error.warnings],
            },
        )

    async def _watch(self) -> None:
        last_heartbeat = monotonic()
        try:
            while self.subscribers:
                manifest = self.manager.runs.get_run(self.run_id)
                if manifest is None:
                    self.marker = None
                    if self.available is not False:
                        self.available = False
                        self._publish(
                            self._event(
                                "run_unavailable",
                                {"message": "Run is unavailable."},
                            )
                        )
                else:
                    marker = self._checkpoint_marker()
                    if self.available is False or marker != self.marker:
                        self.marker = marker
                        self._publish(self.current_event())
                    self.available = True
                if (
                    manifest is not None
                    and manifest.state
                    in {RunState.COMPLETED, RunState.FAILED, RunState.TIMED_OUT}
                    and manifest.state != self.terminal_state
                ):
                    self.terminal_state = manifest.state
                    self._publish(
                        self._event(
                            "run_completed",
                            {"state": manifest.state.value},
                        )
                    )
                if monotonic() - last_heartbeat >= self.manager.heartbeat_interval:
                    self._publish(self._event("heartbeat", {}))
                    last_heartbeat = monotonic()
                await asyncio.sleep(self.manager.poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._publish(
                self._event(
                    "error",
                    {"code": "watcher_error", "message": type(error).__name__},
                )
            )

    def _checkpoint_marker(self) -> CheckpointMarker:
        if self.manager.runs.get_run(self.run_id) is None:
            return None
        path = (
            self.manager.result_root
            / self.run_id
            / ".runtime"
            / "runtime_result.json"
        )
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError, ValueError):
            return None
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _publish(self, event: StreamEnvelope) -> None:
        for subscriber in tuple(self.subscribers):
            subscriber.offer(event)

    def _event(self, event_type: str, payload: JSONValue) -> StreamEnvelope:
        self.revision += 1
        return {
            "version": STREAM_PROTOCOL_VERSION,
            "type": event_type,
            "run_id": self.run_id,
            "revision": self.revision,
            "emitted_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "payload": payload,
        }

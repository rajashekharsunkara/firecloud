"""Two-way folder sync.

watchdog watches the folder and pushes local changes through the node;
a periodic loop compares the manifest against the folder and pulls down
files uploaded elsewhere.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from firecloud.node import Node

logger = logging.getLogger("firecloud.sync")

# Wait this long after the last filesystem event before acting on it.
# Editors that write-to-temp-then-rename fire several events per save.
_DEBOUNCE_SECONDS = 0.5


class _SyncEventHandler(FileSystemEventHandler):
    """Collects filesystem events and feeds them to the async sync loop."""

    def __init__(self, sync: "FolderSync") -> None:
        super().__init__()
        self.sync = sync

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.sync._schedule_event("created", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.sync._schedule_event("modified", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.sync._schedule_event("deleted", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.sync._schedule_event("deleted", event.src_path)
            if hasattr(event, "dest_path"):
                self.sync._schedule_event("created", event.dest_path)


class FolderSync:
    """Watches a folder and syncs it through a node.

    Outbound: local creates/modifies/deletes are uploaded or tombstoned.
    Inbound: manifest files missing locally get downloaded periodically.
    """

    def __init__(self, node: "Node", folder: Path | str) -> None:
        self.node = node
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

        self._observer: Observer | None = None
        self._running = False
        self._incoming_task: asyncio.Task | None = None
        self._debounce_task: asyncio.Task | None = None

        # Pending events: path -> (event_type, timestamp)
        self._pending: dict[str, tuple[str, float]] = {}
        self._pending_lock = threading.Lock()

        # file_id/filename mappings, needed to propagate deletes
        self._name_to_id: dict[str, str] = {}
        self._id_to_name: dict[str, str] = {}

        # Files mid-download; watchdog events for them must not re-upload.
        self._downloading: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start watching the folder for changes."""
        if self._running:
            return

        self._rebuild_name_map()

        self._running = True
        self._observer = Observer()
        handler = _SyncEventHandler(self)
        self._observer.schedule(handler, str(self.folder), recursive=False)
        self._observer.start()

        self._incoming_task = asyncio.create_task(self._incoming_loop())
        self._debounce_task = asyncio.create_task(self._debounce_loop())

        logger.info(f"Folder sync started for {self.folder}")

    async def stop(self) -> None:
        """Stop watching the folder."""
        if not self._running:
            return
        self._running = False

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

        for task in (self._incoming_task, self._debounce_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info(f"Folder sync stopped for {self.folder}")

    # ------------------------------------------------------------------
    # Event scheduling (called from watchdog thread)
    # ------------------------------------------------------------------

    def _schedule_event(self, event_type: str, path: str) -> None:
        """Record a filesystem event for debounced processing."""
        filename = Path(path).name
        if filename in self._downloading:
            return

        with self._pending_lock:
            self._pending[path] = (event_type, time.monotonic())

    # ------------------------------------------------------------------
    # Debounce loop (runs on asyncio loop)
    # ------------------------------------------------------------------

    async def _debounce_loop(self) -> None:
        """Process filesystem events after the debounce window elapses."""
        try:
            while self._running:
                await asyncio.sleep(_DEBOUNCE_SECONDS)
                now = time.monotonic()

                # Collect events that have settled
                ready: list[tuple[str, str]] = []
                with self._pending_lock:
                    for path, (event_type, ts) in list(self._pending.items()):
                        if now - ts >= _DEBOUNCE_SECONDS:
                            ready.append((path, event_type))
                            del self._pending[path]

                for path, event_type in ready:
                    try:
                        await self._handle_event(event_type, path)
                    except Exception as exc:
                        logger.error(f"Sync event error for {path}: {exc}")
        except asyncio.CancelledError:
            pass

    async def _handle_event(self, event_type: str, path: str) -> None:
        """Process a single filesystem event."""
        filepath = Path(path)
        filename = filepath.name

        if event_type in ("created", "modified"):
            if filepath.is_file():
                file_id = await self.node.upload(filepath)
                self._name_to_id[filename] = file_id
                self._id_to_name[file_id] = filename
                logger.debug(f"Sync uploaded {filename} as {file_id}")

        elif event_type == "deleted":
            file_id = self._name_to_id.get(filename)
            if file_id:
                try:
                    await self.node.delete(file_id)
                    del self._name_to_id[filename]
                    del self._id_to_name[file_id]
                    logger.debug(f"Sync deleted {filename} ({file_id})")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Incoming file download loop
    # ------------------------------------------------------------------

    async def _incoming_loop(self) -> None:
        """Periodically check the manifest for files missing from the folder."""
        try:
            while self._running:
                await asyncio.sleep(5)
                try:
                    await self._pull_incoming()
                except Exception as exc:
                    logger.error(f"Incoming sync error: {exc}")
        except asyncio.CancelledError:
            pass

    async def _pull_incoming(self) -> None:
        """Download manifest files that are missing locally or newer remotely."""
        # Per filename, keep the entry with the highest Lamport timestamp.
        latest_entries = {}
        for entry in self.node.manifest.list_files():
            filename = entry.name
            current = latest_entries.get(filename)
            if current is None or entry.lamport_ts > current.lamport_ts:
                latest_entries[filename] = entry

        for filename, entry in latest_entries.items():
            local_path = self.folder / filename

            # Local copy already maps to this file_id: up to date.
            mapped_id = self._name_to_id.get(filename)
            if local_path.exists() and mapped_id == entry.file_id:
                continue

            # Local copy maps to a different file_id; only replace it if
            # the remote entry is actually newer.
            if local_path.exists() and mapped_id is not None:
                try:
                    local_entry = self.node.manifest.get_file(mapped_id)
                    if entry.lamport_ts <= local_entry.lamport_ts:
                        continue
                except Exception:
                    pass

            if filename in self._downloading:
                continue

            if entry.file_id in self._id_to_name and entry.deleted:
                continue

            try:
                self._downloading.add(filename)
                await self.node.download(entry.file_id, local_path)
                self._name_to_id[filename] = entry.file_id
                self._id_to_name[entry.file_id] = filename
                logger.debug(f"Sync downloaded {filename} from network (latest file_id: {entry.file_id})")
            except Exception as exc:
                logger.error(f"Failed to download {filename}: {exc}")
            finally:
                self._downloading.discard(filename)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rebuild_name_map(self) -> None:
        """Seed the name/id mapping from the manifest.

        Only files that exist in the folder are mapped; anything else stays
        eligible for the first inbound pull.
        """
        self._name_to_id.clear()
        self._id_to_name.clear()
        for entry in self.node.manifest.list_files():
            local_path = self.folder / entry.name
            if local_path.exists():
                self._name_to_id[entry.name] = entry.file_id
                self._id_to_name[entry.file_id] = entry.name

"""FireCloud Folder Sync — watchdog-based bi-directional folder synchronization.

Uses :pypi:`watchdog` to monitor a local folder for file changes and
automatically uploads / deletes files through the :class:`~firecloud.node.Node`.
Incoming files from remote peers are downloaded periodically by comparing the
manifest against the sync folder contents.
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

# Debounce window — how long (in seconds) to wait after the last filesystem
# event before processing the change.  This handles editors that do
# write-to-temp-then-rename.
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
    """Watches a folder and syncs its contents through a FireCloud node.

    - **Outbound:** local file changes (create / modify / delete) are uploaded
      or tombstoned via the node.
    - **Inbound:** new files in the manifest that are missing from the local
      folder are downloaded periodically.
    """

    def __init__(self, node: "Node", folder: Path | str) -> None:
        self.node = node
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

        self._observer: Observer | None = None
        self._running = False
        self._incoming_task: asyncio.Task | None = None
        self._debounce_task: asyncio.Task | None = None

        # Pending events: path → (event_type, timestamp)
        self._pending: dict[str, tuple[str, float]] = {}
        self._pending_lock = threading.Lock()

        # Track file_id ↔ filename mappings for delete propagation
        self._name_to_id: dict[str, str] = {}
        self._id_to_name: dict[str, str] = {}

        # Files we are currently downloading — skip outbound re-upload
        self._downloading: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start watching the folder for changes."""
        if self._running:
            return

        # Seed name ↔ id mapping from existing manifest
        self._rebuild_name_map()

        self._running = True
        self._observer = Observer()
        handler = _SyncEventHandler(self)
        self._observer.schedule(handler, str(self.folder), recursive=False)
        self._observer.start()

        # Background task to check for incoming files every 5 seconds
        self._incoming_task = asyncio.create_task(self._incoming_loop())

        # Background task to process debounced events
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
        # Skip files we are downloading ourselves
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
                logger.debug(f"Sync uploaded {filename} → {file_id}")

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
        """Download any manifest files that are not present locally or are newer on remote."""
        # Group entries by filename and find the one with the highest Lamport timestamp
        latest_entries = {}
        for entry in self.node.manifest.list_files():
            filename = entry.name
            current = latest_entries.get(filename)
            if current is None or entry.lamport_ts > current.lamport_ts:
                latest_entries[filename] = entry

        for filename, entry in latest_entries.items():
            local_path = self.folder / filename

            # If the file exists locally and we already have this file_id mapped, it is up to date
            mapped_id = self._name_to_id.get(filename)
            if local_path.exists() and mapped_id == entry.file_id:
                continue

            # If the file exists locally but corresponds to a different file_id
            if local_path.exists() and mapped_id is not None:
                # If the remote version is not newer than our locally mapped version, skip it
                try:
                    local_entry = self.node.manifest.get_file(mapped_id)
                    if entry.lamport_ts <= local_entry.lamport_ts:
                        continue
                except Exception:
                    pass

            # Skip if we are currently downloading this file
            if filename in self._downloading:
                continue

            # Already tracked by us (skip if it has been tombstoned / deleted)
            if entry.file_id in self._id_to_name and entry.deleted:
                continue

            # Download from the network
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
        """Populate the name ↔ id mapping from the current manifest.

        Only includes files that actually exist in the sync folder, so that
        files uploaded by remote peers can be downloaded on first sync start.
        """
        self._name_to_id.clear()
        self._id_to_name.clear()
        for entry in self.node.manifest.list_files():
            local_path = self.folder / entry.name
            if local_path.exists():
                self._name_to_id[entry.name] = entry.file_id
                self._id_to_name[entry.file_id] = entry.name

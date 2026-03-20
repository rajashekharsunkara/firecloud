from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from firecloud.controller import FireCloudController


class FireCloudTUI(App[None]):
    TITLE = "FireCloud Python MVP"
    SUB_TITLE = "Local cluster simulation"
    BINDINGS = [Binding("r", "refresh", "Refresh"), Binding("q", "quit", "Quit")]

    def __init__(self, controller: FireCloudController) -> None:
        super().__init__()
        self.controller = controller

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="files-table")
            yield DataTable(id="nodes-table")
        yield Static(
            "Commands: upload <path> | download <file_id> <path> | "
            "delete <file_id> | offline <node_id> | online <node_id> | repair <file_id> | verify",
            id="help",
        )
        yield Input(placeholder="Enter command...", id="command")
        yield Static("Ready", id="status")
        yield Footer()

    def on_mount(self) -> None:
        files_table = self.query_one("#files-table", DataTable)
        files_table.add_columns("file_id", "name", "size", "created_at")
        nodes_table = self.query_one("#nodes-table", DataTable)
        nodes_table.add_columns("node_id", "online", "symbols")
        self._refresh_tables()

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _refresh_tables(self) -> None:
        files_table = self.query_one("#files-table", DataTable)
        nodes_table = self.query_one("#nodes-table", DataTable)
        files_table.clear(columns=False)
        nodes_table.clear(columns=False)

        for item in self.controller.list_files():
            files_table.add_row(item.file_id, item.file_name, str(item.file_size), item.created_at)
        for node in self.controller.list_nodes():
            nodes_table.add_row(node.node_id, str(node.online), str(node.symbol_count))

    def action_refresh(self) -> None:
        self._refresh_tables()
        self._set_status("Refreshed")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        event.input.value = ""
        if not command:
            return
        parts = command.split()
        action = parts[0]

        try:
            if action == "upload" and len(parts) == 2:
                file_id = self.controller.upload_file(Path(parts[1]))
                self._set_status(f"Uploaded as {file_id}")
            elif action == "download" and len(parts) == 3:
                out = self.controller.download_file(parts[1], Path(parts[2]))
                self._set_status(f"Downloaded to {out}")
            elif action == "delete" and len(parts) == 2:
                self.controller.delete_file(parts[1])
                self._set_status(f"Deleted {parts[1]}")
            elif action == "offline" and len(parts) == 2:
                self.controller.set_node_online(parts[1], False)
                self._set_status(f"{parts[1]} set offline")
            elif action == "online" and len(parts) == 2:
                self.controller.set_node_online(parts[1], True)
                self._set_status(f"{parts[1]} set online")
            elif action == "repair" and len(parts) == 2:
                repaired = self.controller.repair_file(parts[1])
                self._set_status(f"Repair done. Symbols restored: {repaired}")
            elif action == "verify" and len(parts) == 1:
                valid, details = self.controller.verify_audit_chain()
                self._set_status(f"audit_valid={valid} | {details}")
            else:
                self._set_status("Invalid command syntax")
        except (ValueError, RuntimeError) as exc:
            self._set_status(f"Error: {exc}")
        finally:
            self._refresh_tables()

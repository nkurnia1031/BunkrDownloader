"""Provide a `ProgressManager` class for tracking and displaying the progress of tasks.

It uses the Rich library to create dynamic, formatted progress bars and tables for
monitoring task completion.
"""

from __future__ import annotations

import shutil
import time
from collections import deque

from rich import filesize
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    Task,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Column, Table
from rich.text import Text

from src.config import PROGRESS_MANAGER_COLORS
from src.models import ProgressConfig, ProgressState, TransferProgress


class _KnownTimeRemainingColumn(TimeRemainingColumn):
    """Hide ETA until Rich has enough progress samples to estimate it."""

    def render(self, task: Task) -> Text:
        if task.time_remaining is None:
            return Text("")
        return super().render(task)


class ProgressManager:
    """Manage and tracks the progress of multiple tasks.

    Displays individual progress bars for each task alongside an overall progress bar.
    """

    def __init__(self, task_name: str, item_description: str) -> None:
        """Initialize a progress tracking system for a specific task."""
        # Grouping progress-related configurations into a single object
        self.config = ProgressConfig(task_name, item_description)
        self.overall_progress = self._create_progress_bar(
            show_count=True,
            show_transfer=True,
        )
        self.task_progress = self._create_progress_bar(
            show_time=True,
            show_transfer=True,
        )
        self.state = ProgressState()

    def get_panel_width(self) -> int:
        """Return the width of the panel."""
        return self.config.panel_width

    def add_overall_task(
        self,
        description: str,
        num_tasks: int,
        *,
        completed: int = 0,
        completed_size_bytes: int = 0,
    ) -> None:
        """Add an overall task with its total and any files already completed."""
        if not 0 <= completed <= num_tasks:
            error_message = "Completed tasks must be between zero and the total."
            raise ValueError(error_message)
        if completed_size_bytes < 0:
            error_message = "Completed size cannot be negative."
            raise ValueError(error_message)

        with self.state.lock:
            self.state.num_tasks = num_tasks
            self.state.initial_completed_tasks = completed
            self.state.initial_completed_size_bytes = completed_size_bytes
            self.state.current_album_tasks = {}
            overall_task_id = self.overall_progress.add_task(
                f"[{self.config.color}]{escape(description)}",
                total=num_tasks,
                completed=completed,
                completed_count=completed,
                transfer=self._overall_transfer_text(),
                speed=self._format_speed(0),
            )
            self.state.current_overall_task_id = overall_task_id
            self._buffer_finished_overall_task(overall_task_id)

    def add_task(
        self,
        current_task: int = 0,
        total: int = 100,
        *,
        description: str | None = None,
    ) -> int:
        """Add an individual task to the task progress bar."""
        task_prefix = f"{current_task + 1}/{self.state.num_tasks} "
        task_name = description or (
            f"Resolving {self.config.item_description.lower()}..."
        )
        task_description = f"[{self.config.color}]{task_prefix}{escape(task_name)}"
        with self.state.lock:
            task_id = self.task_progress.add_task(
                task_description,
                total=total,
                transfer=self._format_transfer(0, None),
                speed=self._format_speed(0),
            )
            self.state.current_album_tasks[task_id] = 0.0
            self.state.task_prefixes[task_id] = task_prefix
            self.state.task_downloaded_bytes[task_id] = 0
            self.state.task_transfer_speeds[task_id] = 0.0
            self.state.task_network_bytes[task_id] = 0
            self.state.task_transfer_samples[task_id] = deque()
            return task_id

    def update_task_description(self, task_id: int, description: str) -> None:
        """Replace the resolving label with the resolved filename."""
        with self.state.lock:
            task_prefix = self.state.task_prefixes.get(task_id, "")
            self.task_progress.update(
                task_id,
                description=(
                    f"[{self.config.color}]{task_prefix}{escape(description)}"
                ),
            )

    def update_task(
        self,
        task_id: int,
        completed: int | None = None,
        advance: int = 0,
        *,
        visible: bool = True,
        transfer: TransferProgress | None = None,
    ) -> None:
        """Update the progress of an individual task and the overall progress."""
        with self.state.lock:
            if transfer is not None:
                self._update_transfer_state(
                    task_id,
                    downloaded_bytes=transfer.downloaded_bytes,
                    total_bytes=transfer.total_bytes,
                    transferred_bytes=transfer.transferred_bytes,
                )

            if not visible:
                self.state.task_transfer_speeds[task_id] = 0.0

            self.task_progress.update(
                task_id,
                completed=completed if completed is not None else None,
                advance=advance if completed is None else None,
                visible=visible,
                transfer=self._task_transfer_text(task_id),
                speed=self._format_speed(
                    self.state.task_transfer_speeds.get(task_id, 0.0),
                ),
            )
            self._update_overall_task(task_id)

    def create_progress_table(self, min_panel_width: int = 30) -> Table:
        """Create stacked full-width panels for album and file progress."""
        terminal_width, _ = shutil.get_terminal_size()
        panel_width = max(min_panel_width, terminal_width - 2)
        self.config.panel_width = panel_width
        progress_table = Table.grid()
        progress_table.add_row(
            Panel(
                self.overall_progress,
                title=f"[bold {self.config.color}]Overall Progress",
                border_style=PROGRESS_MANAGER_COLORS["overall_border_color"],
                padding=(1, 1),
                width=panel_width,
            ),
        )
        progress_table.add_row(
            Panel(
                self.task_progress,
                title=f"[bold {self.config.color}]{self.config.task_name} Progress",
                border_style=PROGRESS_MANAGER_COLORS["task_border_color"],
                padding=(1, 1),
                width=panel_width,
            ),
        )
        return progress_table

    # Private methods
    def _update_overall_task(self, task_id: int) -> None:
        """Advance the overall progress bar and removes old tasks.

        Safe to call even after the overall task for this album has already been cleaned
        up (e.g. a late progress callback from a retry, or from a slow parallel-chunk
        worker that finishes after the rest): in that case current_overall_task_id is
        None and this is a no-op instead of crashing.
        """
        overall_task_id = self.state.current_overall_task_id
        if overall_task_id is None:
            return

        current_overall_task = self._get_overall_task(overall_task_id)
        if current_overall_task is None:
            # The task was removed from under us; forget the stale id.
            self.state.current_overall_task_id = None
            return

        # If the individual task is finished, advance the overall progress once.
        individual_task = self.task_progress.tasks[task_id]
        if individual_task.finished and task_id not in self.state.completed_task_ids:
            self.state.completed_task_ids.add(task_id)
            self.state.current_album_tasks[task_id] = 1.0
            self.state.task_transfer_speeds[task_id] = 0.0
            self.task_progress.update(
                task_id,
                visible=False,
                speed=self._format_speed(0),
            )

        elif task_id not in self.state.completed_task_ids:
            task_total = individual_task.total or 100
            fraction = max(0.0, min(individual_task.completed / task_total, 0.99))
            self.state.current_album_tasks[task_id] = fraction

        overall_completed = self.state.initial_completed_tasks + sum(
            self.state.current_album_tasks.values(),
        )
        completed_count = self.state.initial_completed_tasks + sum(
            current_task_id in self.state.completed_task_ids
            for current_task_id in self.state.current_album_tasks
        )
        self.overall_progress.update(
            overall_task_id,
            completed=overall_completed,
            completed_count=completed_count,
            transfer=self._overall_transfer_text(),
            speed=self._format_speed(
                sum(
                    self.state.task_transfer_speeds.get(current_task_id, 0.0)
                    for current_task_id in self.state.current_album_tasks
                ),
            ),
        )
        self._buffer_finished_overall_task(overall_task_id)

    def _buffer_finished_overall_task(self, task_id: TaskID) -> None:
        """Queue a finished overall task once and remove the oldest buffered task."""
        task = self._get_overall_task(task_id)
        if (
            task is not None
            and task.finished
            and task_id not in self.state.buffered_overall_task_ids
        ):
            self.state.buffered_overall_task_ids.add(task_id)
            self.config.overall_buffer.append(task)

        self._cleanup_completed_overall_tasks()

    def _get_overall_task(self, task_id: TaskID):  # noqa: ANN202
        """Look up an overall task by id, returning None if it no longer exists."""
        return next(
            (task for task in self.overall_progress.tasks if task.id == task_id),
            None,
        )

    def _cleanup_completed_overall_tasks(self) -> None:
        """Remove the oldest completed overall task from the buffer and progress bar."""
        if len(self.config.overall_buffer) == self.config.overall_buffer.maxlen:
            completed_overall_id = self.config.overall_buffer.popleft().id
            self.overall_progress.remove_task(completed_overall_id)
            self.state.buffered_overall_task_ids.discard(completed_overall_id)

            # If the task we just removed was the one still being tracked as "current"
            # (e.g. lingering retries for a finished album kept it alive in the buffer),
            # clear the reference so any further stray update becomes a safe no-op
            # instead of touching a removed task.
            if completed_overall_id == self.state.current_overall_task_id:
                self.state.current_overall_task_id = None

    def _update_transfer_state(
        self,
        task_id: int,
        *,
        downloaded_bytes: int | None,
        total_bytes: int | None,
        transferred_bytes: int | None,
    ) -> None:
        """Update one file's byte totals and its rolling transfer speed."""
        if total_bytes is not None and total_bytes >= 0:
            self.state.task_total_bytes[task_id] = total_bytes
        if downloaded_bytes is not None:
            self.state.task_downloaded_bytes[task_id] = max(0, downloaded_bytes)

        if transferred_bytes is None:
            return

        now = time.monotonic()
        network_bytes = self.state.task_network_bytes.get(task_id, 0)
        network_bytes += max(0, transferred_bytes)
        self.state.task_network_bytes[task_id] = network_bytes
        samples = self.state.task_transfer_samples.setdefault(task_id, deque())
        samples.append((now, network_bytes))
        cutoff = now - 1.5
        while len(samples) > 1 and samples[1][0] < cutoff:
            samples.popleft()

        speed = 0.0
        if len(samples) > 1:
            elapsed = samples[-1][0] - samples[0][0]
            if elapsed > 0:
                speed = max(0, samples[-1][1] - samples[0][1]) / elapsed
        self.state.task_transfer_speeds[task_id] = speed

    def _task_transfer_text(self, task_id: int) -> str:
        """Format the current file's downloaded and total bytes."""
        downloaded = self.state.task_downloaded_bytes.get(task_id, 0)
        total = self.state.task_total_bytes.get(task_id)
        return self._format_transfer(downloaded, total)

    def _overall_transfer_text(self) -> str:
        """Format album bytes and mark a growing total as a lower bound."""
        current_tasks = self.state.current_album_tasks
        downloaded = self.state.initial_completed_size_bytes + sum(
            self.state.task_downloaded_bytes.get(task_id, 0)
            for task_id in current_tasks
        )
        total = self.state.initial_completed_size_bytes + sum(
            self.state.task_total_bytes.get(task_id, 0)
            for task_id in current_tasks
        )
        known_sizes = self.state.initial_completed_tasks + sum(
            task_id in self.state.task_total_bytes
            for task_id in current_tasks
        )

        if not known_sizes:
            return self._format_transfer(downloaded, None)

        transfer = self._format_transfer(downloaded, total)
        if known_sizes < self.state.num_tasks:
            downloaded_text, total_text = transfer.split("/", maxsplit=1)
            return f"{downloaded_text}/≥{total_text}"
        return transfer

    @staticmethod
    def _format_transfer(downloaded: int, total: int | None) -> str:
        """Format downloaded and total bytes using a shared decimal unit."""
        if total is None:
            return f"{ProgressManager._format_size(downloaded)}/?"

        unit, suffix = ProgressManager._pick_size_unit(max(downloaded, total))
        precision = 0 if unit == 1 else 1
        downloaded_text = f"{downloaded / unit:,.{precision}f}"
        total_text = f"{total / unit:,.{precision}f}"
        return f"{downloaded_text}/{total_text} {suffix}"

    @staticmethod
    def _format_speed(speed_bytes_per_second: float) -> str:
        """Format aggregate or individual byte throughput."""
        return f"{ProgressManager._format_size(int(speed_bytes_per_second))}/s"

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format a byte amount with a short decimal unit label."""
        unit, suffix = ProgressManager._pick_size_unit(size_bytes)
        precision = 0 if unit == 1 else 1
        label = "B" if suffix == "bytes" else suffix
        return f"{size_bytes / unit:,.{precision}f} {label}"

    @staticmethod
    def _pick_size_unit(size_bytes: int) -> tuple[int, str]:
        """Choose a decimal unit appropriate for a byte amount."""
        return filesize.pick_unit_and_suffix(
            size_bytes,
            ["bytes", "kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"],
            1000,
        )

    # Static methods
    @staticmethod
    def _create_progress_bar(
        columns: list[Column] | None = None,
        *,
        show_time: bool = False,
        show_count: bool = False,
        show_transfer: bool = False,
    ) -> Progress:
        """Create and returns a progress bar for tracking download progress."""
        if columns is None:
            columns = [
                TextColumn(
                    "{task.description}",
                    table_column=Column(
                        ratio=1,
                        max_width=48,
                        overflow="fold",
                    ),
                ),
                BarColumn(),
            ]
            if show_count:
                columns.append(
                    TextColumn("{task.fields[completed_count]}/{task.total:.0f}"),
                )

            columns.append(
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            )

            if show_transfer:
                columns.extend([
                    TextColumn("{task.fields[transfer]}"),
                    TextColumn("{task.fields[speed]}"),
                ])

        if show_time:
            columns.append(_KnownTimeRemainingColumn(compact=True))

        return Progress(*columns)

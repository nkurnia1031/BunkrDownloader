"""Provide a `ProgressManager` class for tracking and displaying the progress of tasks.

It uses the Rich library to create dynamic, formatted progress bars and tables for
monitoring task completion.
"""

from __future__ import annotations

import shutil

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Column, Table

from src.config import PROGRESS_COLUMNS_SEPARATOR, PROGRESS_MANAGER_COLORS
from src.models import ProgressConfig, ProgressState


class ProgressManager:
    """Manage and tracks the progress of multiple tasks.

    Displays individual progress bars for each task alongside an overall progress bar.
    """

    def __init__(self, task_name: str, item_description: str) -> None:
        """Initialize a progress tracking system for a specific task."""
        # Grouping progress-related configurations into a single object
        self.config = ProgressConfig(task_name, item_description)
        self.overall_progress = self._create_progress_bar(show_count=True)
        self.task_progress = self._create_progress_bar(show_time=True)
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
    ) -> None:
        """Add an overall task with its total and any files already completed."""
        if not 0 <= completed <= num_tasks:
            error_message = "Completed tasks must be between zero and the total."
            raise ValueError(error_message)

        with self.state.lock:
            self.state.num_tasks = num_tasks
            self.state.initial_completed_tasks = completed
            self.state.current_album_tasks = {}
            overall_description = self._adjust_description(description)
            overall_task_id = self.overall_progress.add_task(
                f"[{self.config.color}]{overall_description}",
                total=num_tasks,
                completed=completed,
            )
            self.state.current_overall_task_id = overall_task_id
            self._buffer_finished_overall_task(overall_task_id)

    def add_task(self, current_task: int = 0, total: int = 100) -> int:
        """Add an individual task to the task progress bar."""
        task_description = (
            f"[{self.config.color}]{self.config.item_description} "
            f"{current_task + 1}/{self.state.num_tasks}"
        )
        with self.state.lock:
            task_id = self.task_progress.add_task(task_description, total=total)
            self.state.current_album_tasks[task_id] = 0.0
            return task_id

    def update_task(
        self,
        task_id: int,
        completed: int | None = None,
        advance: int = 0,
        *,
        visible: bool = True,
    ) -> None:
        """Update the progress of an individual task and the overall progress."""
        with self.state.lock:
            self.task_progress.update(
                task_id,
                completed=completed if completed is not None else None,
                advance=advance if completed is None else None,
                visible=visible,
            )
            self._update_overall_task(task_id)

    def create_progress_table(self, min_panel_width: int = 30) -> Table:
        """Create a formatted progress table for tracking the download."""
        terminal_width, _ = shutil.get_terminal_size()
        panel_width = max(min_panel_width, terminal_width // 2)
        progress_table = Table.grid()
        progress_table.add_row(
            Panel.fit(
                self.overall_progress,
                title=f"[bold {self.config.color}]Overall Progress",
                border_style=PROGRESS_MANAGER_COLORS["overall_border_color"],
                padding=(1, 1),
                width=panel_width,
            ),
            Panel.fit(
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
            self.task_progress.update(task_id, visible=False)

        elif task_id not in self.state.completed_task_ids:
            task_total = individual_task.total or 100
            fraction = max(0.0, min(individual_task.completed / task_total, 0.99))
            self.state.current_album_tasks[task_id] = fraction

        overall_completed = self.state.initial_completed_tasks + sum(
            self.state.current_album_tasks.values(),
        )
        self.overall_progress.update(overall_task_id, completed=overall_completed)
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

    # Static methods
    @staticmethod
    def _adjust_description(description: str, max_length: int = 8) -> str:
        """Truncate a string to a specified maximum length adding an ellipsis."""
        return (
            description[:max_length] + "..."
            if len(description) > max_length
            else description
        )

    @staticmethod
    def _create_progress_bar(
        columns: list[Column] | None = None,
        *,
        show_time: bool = False,
        show_count: bool = False,
    ) -> Progress:
        """Create and returns a progress bar for tracking download progress."""
        if columns is None:
            columns = [SpinnerColumn(), BarColumn()]
            if show_count:
                columns.append(TextColumn("{task.completed:.0f}/{task.total:.0f}"))

            columns.append(
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            )

        if show_time:
            columns += [PROGRESS_COLUMNS_SEPARATOR, TimeRemainingColumn()]

        return Progress("{task.description}", *columns)

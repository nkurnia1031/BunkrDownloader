"""Regression coverage for starting overall progress with completed files."""

from __future__ import annotations

import unittest
from io import StringIO

from rich.console import Console

from src.managers.progress_manager import ProgressManager


class ProgressPrefillTests(unittest.TestCase):
    """Verify precompleted files contribute to album progress from the start."""

    def test_overall_progress_starts_prefilled_and_advances_remaining_work(
        self,
    ) -> None:
        """The initial count is retained as pending files report progress."""
        manager = ProgressManager(task_name="Album", item_description="File")
        manager.add_overall_task("album", num_tasks=3, completed=2)

        overall_task = manager.overall_progress.tasks[0]
        self.assertEqual(overall_task.completed, 2)
        self.assertEqual(overall_task.total, 3)
        output = StringIO()
        Console(file=output, width=100).print(manager.overall_progress)
        self.assertIn("2/3", output.getvalue())
        self.assertIn("67%", output.getvalue())

        remaining_task = manager.add_task(current_task=2)
        manager.update_task(remaining_task, completed=100)

        self.assertEqual(overall_task.completed, 3)


if __name__ == "__main__":
    unittest.main()

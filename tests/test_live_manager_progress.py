"""Coverage for album progress setup through the live-manager interface."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, call

from src.enums import SkippedReason
from src.managers.live_manager import LiveManager


class LiveManagerProgressTests(unittest.TestCase):
    """Keep progress initialization and completed-file reporting together."""

    def test_add_album_progress_counts_and_reports_each_completed_file(self) -> None:
        """Precompleted outputs initialize progress and are reported once each."""
        progress_manager = Mock()
        logger_table = Mock()
        summary_manager = Mock()
        manager = LiveManager(
            progress_manager,
            logger_table,
            summary_manager,
            disable_ui=True,
        )
        logger_table.reset_mock()
        summary_manager.reset_mock()

        manager.add_album_progress(
            description="album",
            num_tasks=3,
            completed_filenames=["one.mp4", "two.mp4"],
        )

        progress_manager.add_overall_task.assert_called_once_with(
            "album",
            3,
            completed=2,
        )
        logger_table.log.assert_has_calls(
            [
                call(
                    "Skipped download",
                    "one.mp4 has already been downloaded "
                    "(found during the initial scan).",
                    disable_ui=True,
                ),
                call(
                    "Skipped download",
                    "two.mp4 has already been downloaded "
                    "(found during the initial scan).",
                    disable_ui=True,
                ),
            ],
        )
        self.assertEqual(logger_table.log.call_count, 2)
        summary_manager.update_result.assert_has_calls(
            [
                call(SkippedReason.ALREADY_DOWNLOADED),
                call(SkippedReason.ALREADY_DOWNLOADED),
            ],
        )
        self.assertEqual(summary_manager.update_result.call_count, 2)


if __name__ == "__main__":
    unittest.main()

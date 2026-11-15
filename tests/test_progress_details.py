"""Coverage for named progress rows and transfer-size details."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.downloaders.download_utils import download_chunks
from src.managers.progress_manager import ProgressManager
from src.models import DownloadConfig, DownloadPlan, TransferProgress


class ProgressDetailsTests(unittest.TestCase):
    """Show full labels and byte statistics at album and file levels."""

    def test_album_and_file_rows_include_names_size_and_speed(self) -> None:
        """Aggregate and individual rows reflect byte updates and transfer rates."""
        manager = ProgressManager(task_name="Album", item_description="File")
        manager.add_overall_task(
            "A much longer album name",
            num_tasks=2,
            completed=1,
            completed_size_bytes=1000,
        )
        task_id = manager.add_task(
            current_task=1,
            description="Resolving...",
        )
        manager.update_task_description(task_id, "video clip.mp4")

        with patch(
            "src.managers.progress_manager.time.monotonic",
            side_effect=[10.0, 11.0],
        ):
            manager.update_task(
                task_id,
                completed=10,
                transfer=TransferProgress(
                    downloaded_bytes=100,
                    total_bytes=1000,
                    transferred_bytes=0,
                ),
            )
            manager.update_task(
                task_id,
                completed=40,
                transfer=TransferProgress(
                    downloaded_bytes=400,
                    total_bytes=1000,
                    transferred_bytes=300,
                ),
            )

        overall = manager.overall_progress.tasks[0]
        file_task = manager.task_progress.tasks[0]
        self.assertIn("A much longer album name", overall.description)
        self.assertIn("video clip.mp4", file_task.description)
        self.assertEqual(overall.completed, 1.4)
        self.assertEqual(overall.fields["completed_count"], 1)
        self.assertEqual(overall.fields["transfer"], "1.4/2.0 kB")
        self.assertEqual(overall.fields["speed"], "300 B/s")
        self.assertEqual(file_task.fields["transfer"], "0.4/1.0 kB")
        self.assertEqual(file_task.fields["speed"], "300 B/s")

        manager.update_task(task_id, completed=100, visible=False)
        self.assertEqual(overall.fields["completed_count"], 2)

    def test_unknown_file_size_still_shows_bytes_and_speed(self) -> None:
        """Unknown totals are explicit while the received size remains visible."""
        manager = ProgressManager(task_name="Album", item_description="File")
        manager.add_overall_task("album", num_tasks=1)
        task_id = manager.add_task(description="unknown-size.bin")

        manager.update_task(
            task_id,
            transfer=TransferProgress(
                downloaded_bytes=321,
                transferred_bytes=321,
            ),
        )

        self.assertEqual(
            manager.task_progress.tasks[0].fields["transfer"],
            "321 B/?",
        )
        self.assertEqual(
            manager.overall_progress.tasks[0].fields["transfer"],
            "321 B/?",
        )

    def test_progress_panels_use_full_width_and_stack(self) -> None:
        """Album and item progress get separate wide rows."""
        manager = ProgressManager(task_name="Album", item_description="File")
        with patch(
            "src.managers.progress_manager.shutil.get_terminal_size",
            return_value=os.terminal_size((120, 30)),
        ):
            table = manager.create_progress_table()

        self.assertEqual(len(table.rows), 2)
        self.assertEqual(manager.get_panel_width(), 118)

    def test_parallel_download_progress_reports_bytes_after_retry_rollback(
        self,
    ) -> None:
        """Failed chunk bytes roll back while actual network bytes feed speed."""
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "video.mp4"
            chunk_path = Path(directory) / "video.mp4.part0"
            plan = DownloadPlan(
                ranges=[(0, 3)],
                num_ranges=1,
                chunk_paths=[chunk_path],
                expected_sizes=[4],
            )
            live_manager = Mock()

            def download_with_retry(_url, _byte_range, _path, chunk_info):
                chunk_info.on_progress(3)
                chunk_info.on_progress(-3)
                chunk_info.on_progress(4)
                return False

            with (
                patch(
                    "src.downloaders.download_utils._build_download_plan",
                    return_value=plan,
                ),
                patch(
                    "src.downloaders.download_utils._download_single_chunk",
                    side_effect=download_with_retry,
                ),
            ):
                download_chunks(
                    "https://example.test/video",
                    base_path,
                    7,
                    live_manager,
                    DownloadConfig(
                        content_length=4,
                        num_connections=1,
                        headers={},
                    ),
                )

        live_manager.update_task.assert_any_call(
            7,
            completed=99.0,
            transfer=TransferProgress(
                downloaded_bytes=4,
                total_bytes=4,
                transferred_bytes=4,
            ),
        )


if __name__ == "__main__":
    unittest.main()

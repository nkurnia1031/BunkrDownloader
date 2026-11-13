"""Offline end-to-end coverage for recoded files in the batch entry point."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bs4 import BeautifulSoup

import main
from src.managers.state_manager import load_album_state, save_album_state


class RecodeBatchEndToEndTests(unittest.TestCase):
    """Exercise the real batch pipeline with only the remote album page stubbed."""

    def _run_main(
        self,
        directory: str,
        album_url: str,
        *,
        item_page_response: BeautifulSoup,
        download_info_response: tuple[str, str, None],
    ) -> dict[str, object]:
        """Run the batch entry point with offline network-boundary stubs."""
        item_url = "https://bunkr.cr/f/item123"
        args = Namespace(
            dry_run=False,
            max_concurrent_urls=1,
            rate_limit=None,
            disable_ui=True,
            disable_disk_check=True,
            custom_path=directory,
            no_album_folder=True,
            no_download_folder=True,
            clean_name=False,
            max_retries=1,
            connections=1,
            ignore=[],
            include=[],
        )
        live_manager = Mock()
        live_manager.live = nullcontext()
        live_manager.add_task.return_value = 1
        album_page = BeautifulSoup("", "html.parser")
        fetch_album_page = AsyncMock(return_value=album_page)
        fetch_item_page = AsyncMock(return_value=item_page_response)
        resolve_download = AsyncMock(return_value=download_info_response)
        detect_range_support = Mock(
            side_effect=AssertionError("an existing recode must skip network probes"),
        )
        download_file = Mock(
            side_effect=AssertionError("an existing recode must skip HTTP downloads"),
        )
        log_failed_urls = Mock()

        with (
            patch("main.clear_terminal"),
            patch("main.check_python_version"),
            patch("main.parse_arguments", return_value=args),
            patch("main.configure_session"),
            patch("downloader.get_album_name", return_value="Test album"),
            patch("main.log_session_start"),
            patch("main.create_urls_file_backup"),
            patch("main.read_file", return_value=[album_url]),
            patch("main.log_failed_urls", new=log_failed_urls),
            patch("downloader.fetch_page", new=fetch_album_page),
            patch(
                "src.downloaders.album_downloader.fetch_page",
                new=fetch_item_page,
            ),
            patch(
                "src.downloaders.album_downloader.get_download_info",
                new=resolve_download,
            ),
            patch(
                "src.downloaders.media_downloader.detect_range_support",
                new=detect_range_support,
            ),
            patch(
                "src.downloaders.media_downloader.requests.get",
                new=download_file,
            ),
            patch(
                "src.services.run_utils.initialize_managers",
                return_value=live_manager,
            ),
        ):
            asyncio.run(main.main())

        return {
            "fetch_album_page": fetch_album_page,
            "fetch_item_page": fetch_item_page,
            "resolve_download": resolve_download,
            "detect_range_support": detect_range_support,
            "download_file": download_file,
            "log_failed_urls": log_failed_urls,
            "live_manager": live_manager,
        }

    def test_cached_recoded_album_file_skips_item_page_and_download(self) -> None:
        """A recoded output is enough to skip a cached album item end to end."""
        album_url = "https://bunkr.cr/a/album123"
        item_url = "https://bunkr.cr/f/item123"
        album_id = "album123"

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "video_recode.mp4").write_bytes(b"encoded")
            save_album_state(
                directory,
                album_id,
                [item_url],
                {item_url: {"filename": "video.mp4", "status": "completed"}},
            )
            result = self._run_main(
                directory,
                album_url,
                item_page_response=BeautifulSoup("", "html.parser"),
                download_info_response=("", "", None),
            )

            result["fetch_album_page"].assert_awaited_once_with(album_url)
            result["fetch_item_page"].assert_not_awaited()
            result["resolve_download"].assert_not_awaited()
            result["detect_range_support"].assert_not_called()
            result["download_file"].assert_not_called()
            result["log_failed_urls"].assert_not_called()
            result["live_manager"].update_summary.assert_called_once()

    def test_uncached_recoded_file_is_persisted_as_complete(self) -> None:
        """A recoded file found after resolution is skipped and saved as complete."""
        album_url = "https://bunkr.cr/a/album123"
        item_url = "https://bunkr.cr/f/item123"

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "video_recode.mp4").write_bytes(b"encoded")
            save_album_state(directory, "album123", [item_url], {})

            result = self._run_main(
                directory,
                album_url,
                item_page_response=BeautifulSoup("", "html.parser"),
                download_info_response=(
                    "https://example.test/video",
                    "video.mp4",
                    None,
                ),
            )

            result["fetch_album_page"].assert_awaited_once_with(album_url)
            result["fetch_item_page"].assert_awaited_once_with(item_url)
            result["resolve_download"].assert_awaited_once()
            result["detect_range_support"].assert_not_called()
            result["download_file"].assert_not_called()
            result["log_failed_urls"].assert_not_called()

            state = load_album_state(directory)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["items"][item_url]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

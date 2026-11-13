"""Regression coverage for recognizing recoded media as completed downloads."""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bs4 import BeautifulSoup
from rich.console import Console

from src.downloaders.media_downloader import MediaDownloader
from src.enums import SkippedReason
from src.misc.file_utils import find_existing_download_path
from src.models import (
    AlbumInfo,
    DownloadInfo,
    RetryConfig,
    SessionInfo,
)
from src.services.dry_run import execute_dry_run


class RecodeSkipTests(unittest.TestCase):
    """Check the filesystem and download paths that determine completed status."""

    def setUp(self) -> None:
        """Create a temporary folder for expected and recoded files."""
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.expected_path = Path(self.directory) / "video.mp4"
        self.recoded_path = Path(self.directory) / "video_recode.mp4"

    def test_finds_recoded_path_and_prefers_exact_path(self) -> None:
        """Match only the canonical recode sibling and prefer the expected file."""
        self.recoded_path.write_bytes(b"recoded")
        self.assertEqual(
            find_existing_download_path(self.expected_path),
            self.recoded_path,
        )

        self.expected_path.write_bytes(b"original")
        self.assertEqual(
            find_existing_download_path(self.expected_path),
            self.expected_path,
        )

    def test_does_not_match_near_names_or_partial_files(self) -> None:
        """A different stem, extension, or unfinished file is not a completed match."""
        for filename in (
            "video-copy.mp4",
            "video_recode.mov",
            "video_recode.mp4.part0",
            "video_recode.mp4.temp",
        ):
            with self.subTest(filename=filename):
                candidate = Path(self.directory) / filename
                candidate.touch()
                self.assertIsNone(find_existing_download_path(self.expected_path))
                candidate.unlink()

    def test_media_downloader_skips_recoded_path(self) -> None:
        """The ordinary download path reports a recoded file as already downloaded."""
        self.recoded_path.write_bytes(b"recoded")
        live_manager = Mock()
        args = Namespace(ignore=[], include=[], disable_server_check=True)
        downloader = MediaDownloader(
            SessionInfo(args, {}, self.directory),
            DownloadInfo(
                "https://example.test/f/item",
                "https://example.test/video",
                self.expected_path.name,
                1,
            ),
            live_manager,
            RetryConfig(retries=1),
        )
        downloader.attempt_download = Mock()  # type: ignore[method-assign]

        self.assertFalse(downloader.download())

        downloader.attempt_download.assert_not_called()
        live_manager.update_summary.assert_called_once_with(
            SkippedReason.ALREADY_DOWNLOADED,
        )

    def test_dry_run_reports_recoded_cached_file_as_downloaded(self) -> None:
        """Dry-run status and size come from the recoded file on disk."""
        self.recoded_path.write_bytes(b"1234567")
        item_url = "https://example.test/f/item"
        album_info = AlbumInfo(
            album_id="album",
            item_pages=[item_url],
            cached_items={
                item_url: {"filename": "video.mp4", "status": "completed"},
            },
        )
        session_info = SessionInfo(
            Namespace(clean_name=False, ignore=[], include=[]),
            {},
            self.directory,
        )
        output = io.StringIO()
        fetch_page = AsyncMock()

        with patch("src.services.dry_run.fetch_page", new=fetch_page):
            asyncio.run(
                execute_dry_run(album_info, session_info, Console(file=output)),
            )

        fetch_page.assert_not_awaited()
        self.assertIn("Already downloaded: 1 file(s)", output.getvalue())
        self.assertIn("7.0 B", output.getvalue())

    def test_dry_run_resolves_uncached_recoded_file_as_downloaded(self) -> None:
        """A file found after resolving its URL is still previewed as complete."""
        self.recoded_path.write_bytes(b"1234567")
        item_url = "https://example.test/f/item"
        album_info = AlbumInfo(album_id="album", item_pages=[item_url])
        session_info = SessionInfo(
            Namespace(clean_name=False, ignore=[], include=[]),
            {},
            self.directory,
        )
        output = io.StringIO()
        fetch_page = AsyncMock(return_value=BeautifulSoup("", "html.parser"))
        get_download_info = AsyncMock(
            return_value=("https://example.test/video", "video.mp4", None),
        )
        detect_range_support = Mock(
            side_effect=AssertionError(
                "existing recoded file has no remote size probe",
            ),
        )

        with (
            patch(
                "src.services.dry_run.fetch_page",
                new=fetch_page,
            ),
            patch(
                "src.services.dry_run.get_download_info",
                new=get_download_info,
            ),
            patch(
                "src.services.dry_run.detect_range_support",
                new=detect_range_support,
            ),
        ):
            asyncio.run(
                execute_dry_run(album_info, session_info, Console(file=output)),
            )

        fetch_page.assert_awaited_once_with(item_url)
        get_download_info.assert_awaited_once()
        detect_range_support.assert_not_called()
        self.assertIn("Already downloaded: 1 file(s)", output.getvalue())


if __name__ == "__main__":
    unittest.main()

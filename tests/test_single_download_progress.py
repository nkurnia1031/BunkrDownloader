"""Coverage for named progress setup on direct media downloads."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from downloader import handle_download_process
from src.enums import UrlType
from src.models import SessionInfo, UrlInfo


class SingleDownloadProgressTests(unittest.TestCase):
    """Use the filename for both overall and individual progress rows."""

    def test_direct_media_progress_uses_resolved_filename(self) -> None:
        """Direct media setup passes the real filename into both progress rows."""
        url_info = UrlInfo(
            url="https://bunkr.cr/f/item123",
            url_type=UrlType.MEDIA,
            soup=BeautifulSoup("", "html.parser"),
        )
        live_manager = Mock()
        live_manager.add_task.return_value = 1
        media_downloader = Mock()
        media_downloader.download.return_value = False
        session_info = SessionInfo(
            args=Namespace(clean_name=False),
            bunkr_status={},
            download_path=tempfile.gettempdir(),
        )

        with (
            patch(
                "downloader.get_download_info",
                return_value=("https://example.test/video", "video.mp4", None),
            ),
            patch(
                "downloader.MediaDownloader",
                return_value=media_downloader,
            ),
        ):
            failed = asyncio.run(
                handle_download_process(
                    url_info,
                    session_info,
                    live_manager,
                    max_retries=1,
                ),
            )

        self.assertFalse(failed)
        live_manager.add_download_progress.assert_called_once_with(
            description="video.mp4",
            num_tasks=1,
        )
        live_manager.add_task.assert_called_once_with(description="video.mp4")

"""Coverage for matching cached album entries to finished files on disk."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from src.managers.state_manager import find_existing_album_downloads


class CompletedItemScanTests(unittest.TestCase):
    """Count final exact and recoded files, not partial or missing outputs."""

    def setUp(self) -> None:
        """Create an isolated download folder."""
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def test_finds_exact_and_recoded_outputs_and_ignores_partials(self) -> None:
        """The on-disk final output wins over stale cache status and partial files."""
        exact_page = "https://example.test/f/exact"
        recoded_page = "https://example.test/f/recoded"
        partial_page = "https://example.test/f/partial"
        missing_page = "https://example.test/f/missing"

        (Path(self.directory) / "exact.mp4").touch()
        (Path(self.directory) / "recoded_recode.mp4").touch()
        (Path(self.directory) / "partial_recode.mp4.part0").touch()

        existing = find_existing_album_downloads(
            self.directory,
            [exact_page, recoded_page, partial_page, missing_page],
            {
                exact_page: {"filename": "exact.mp4", "status": "completed"},
                recoded_page: {"filename": "recoded.mp4", "status": "failed"},
                partial_page: {"filename": "partial.mp4", "status": "completed"},
                missing_page: {"filename": "missing.mp4", "status": "completed"},
            },
        )

        self.assertEqual(
            existing,
            {exact_page: "exact.mp4", recoded_page: "recoded.mp4"},
        )


if __name__ == "__main__":
    unittest.main()

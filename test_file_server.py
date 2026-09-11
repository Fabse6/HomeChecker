import unittest

from main import badge_for_extension, categorize_file, preview_kind_for_file


class TestFileServerMetadata(unittest.TestCase):
    def test_pdf_category_and_badge(self):
        self.assertEqual(categorize_file("report.pdf"), "pdf")
        self.assertEqual(badge_for_extension("report.pdf"), "PDF")

    def test_python_category_and_badge(self):
        self.assertEqual(categorize_file("script.py"), "code")
        self.assertEqual(badge_for_extension("script.py"), "PY")

    def test_audio_category_and_badge(self):
        self.assertEqual(categorize_file("song.mp3"), "audio")
        self.assertEqual(badge_for_extension("song.mp3"), "MP3")

    def test_preview_kind_detection(self):
        self.assertEqual(preview_kind_for_file("report.pdf"), "pdf")
        self.assertEqual(preview_kind_for_file("main.py"), "text")
        self.assertEqual(preview_kind_for_file("photo.png"), "image")


if __name__ == "__main__":
    unittest.main()

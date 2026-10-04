import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.visual_questions import WikimediaQuestionImageResolver


class VisualImageResolverTests(unittest.TestCase):
    def test_search_rejects_moonwalk_photo_without_michael_jackson(self):
        session = Mock()
        response = session.get.return_value
        response.json.return_value = {
            "query": {"pages": [{
                "title": "File:Moonwalk in Madrid.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "url": "https://example.test/madrid.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 2.0"}},
                }],
            }]}
        }

        resolver = WikimediaQuestionImageResolver(session=session)
        with self.assertRaisesRegex(ValueError, "uygun görsel bulunamadı"):
            resolver._search("Michael Jackson moonwalk")

    def test_search_rejects_impersonator_even_when_name_matches(self):
        session = Mock()
        session.get.return_value.json.return_value = {
            "query": {"pages": [{
                "title": "File:Michael Jackson impersonator.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "url": "https://example.test/impersonator.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 2.0"}},
                }],
            }]}
        }

        with self.assertRaisesRegex(ValueError, "uygun görsel bulunamadı"):
            WikimediaQuestionImageResolver(session=session)._search("Michael Jackson portrait")

    def test_resolve_creates_browser_accessible_firebase_download_url(self):
        session = Mock()
        session.get.return_value.content = b"image bytes"
        session.get.return_value.headers = {"Content-Type": "image/jpeg"}
        session.get.return_value.status_code = 200
        blob = Mock()
        blob.exists.return_value = False
        bucket = Mock(name="bucket")
        bucket.name = "example-bucket"
        bucket.blob.return_value = blob
        resolver = WikimediaQuestionImageResolver(bucket=bucket, session=session)
        question = SimpleNamespace(question_image_search_query="Michael Jackson portrait", question_image_url=None)

        with patch.object(resolver, "_search", return_value={
            "url": "https://example.test/photo.jpg",
            "mime": "image/jpeg",
            "source_url": "https://commons.wikimedia.org/wiki/File:Michael_Jackson.jpg",
            "license": "CC BY-SA 2.0",
            "artist": "Photographer",
        }):
            url = resolver.resolve(question)

        self.assertTrue(url.startswith("https://firebasestorage.googleapis.com/v0/b/example-bucket/o/"))
        self.assertIn("?alt=media&token=", url)
        self.assertTrue(blob.metadata["firebaseStorageDownloadTokens"])
        self.assertIsNone(question.question_image_search_query)

    def test_resolve_rejects_inaccessible_firebase_download_url(self):
        session = Mock()
        downloaded = Mock(content=b"image bytes", headers={"Content-Type": "image/jpeg"})
        inaccessible = Mock(status_code=412, headers={"Content-Type": "application/json"})
        session.get.side_effect = [downloaded, inaccessible]
        bucket = Mock()
        bucket.name = "example-bucket"
        bucket.blob.return_value.exists.return_value = False
        resolver = WikimediaQuestionImageResolver(bucket=bucket, session=session)
        question = SimpleNamespace(question_image_search_query="subject photograph", question_image_url=None)

        with patch.object(resolver, "_search", return_value={
            "url": "https://example.test/photo.jpg",
            "mime": "image/jpeg",
            "source_url": "https://commons.wikimedia.org/wiki/File:Subject.jpg",
            "license": "CC BY-SA 2.0",
            "artist": "Photographer",
        }):
            with self.assertRaisesRegex(ValueError, "HTTP 412"):
                resolver.resolve(question)


if __name__ == "__main__":
    unittest.main()

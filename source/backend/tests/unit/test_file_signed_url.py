from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import unittest

from app.services.file_service import build_signed_download_url, verify_download_signature


class FileSignedUrlTestCase(unittest.TestCase):
    def test_build_and_verify_signed_download_url(self) -> None:
        url = build_signed_download_url(
            base_url='http://127.0.0.1:8000',
            file_id=123,
            user_id=7,
            expires_in=3600,
        )
        parsed = urlparse(url)
        self.assertTrue(parsed.path.endswith('/api/v1/files/download/123'))
        query = parse_qs(parsed.query)
        uid = int(query['uid'][0])
        exp = int(query['exp'][0])
        sig = query['sig'][0]

        self.assertEqual(uid, 7)
        self.assertTrue(verify_download_signature(file_id=123, user_id=7, exp=exp, sig=sig))

    def test_verify_signed_download_url_rejects_wrong_user(self) -> None:
        url = build_signed_download_url(
            base_url='http://127.0.0.1:8000',
            file_id=88,
            user_id=9,
            expires_in=3600,
        )
        query = parse_qs(urlparse(url).query)
        exp = int(query['exp'][0])
        sig = query['sig'][0]

        self.assertFalse(verify_download_signature(file_id=88, user_id=10, exp=exp, sig=sig))


if __name__ == '__main__':
    unittest.main()


from unittest.mock import patch

from szurubooru import config, model
from szurubooru.func import external_import


def test_search_e621_post_uses_hash_search_without_api_key():
    post = model.Post()
    post.post_id = 123
    post.mime_type = "image/jpeg"
    config.config = {}

    with patch("szurubooru.func.external_import._request_json") as request_json:
        request_json.return_value = [
            {"site": "e621", "site_id": 555, "distance": 3}
        ]

        result = external_import._search_e621_post(b"test-image", post)

        assert result["site_id"] == 555
        request_json.assert_called_once_with(
            external_import._get_fuzzysearch_hashes_url(b"test-image"),
            method="GET",
            expected_statuses=[200],
            service_name="FuzzySearch",
        )


def test_search_e621_post_uses_image_search_with_api_key():
    post = model.Post()
    post.post_id = 123
    post.mime_type = "image/jpeg"
    config.config = {"fuzzysearch_api_key": "test-key"}

    with patch("szurubooru.func.external_import._request_json") as request_json:
        request_json.return_value = [
            {"site": "e621", "site_id": 555, "distance": 3}
        ]

        result = external_import._search_e621_post(b"test-image", post)

        assert result["site_id"] == 555
        args, kwargs = request_json.call_args
        assert args[0] == external_import.FUZZYSEARCH_IMAGE_URL
        assert kwargs["method"] == "POST"
        assert kwargs["expected_statuses"] == [200]
        assert kwargs["service_name"] == "FuzzySearch"
        assert kwargs["headers"]["x-api-key"] == "test-key"
        assert kwargs["headers"]["Content-Type"].startswith(
            "multipart/form-data; boundary="
        )


def test_get_e621_post_uses_query_string_credentials():
    config.config = {
        "e621_login": "hexerade",
        "e621_api_key": "secret-key",
    }

    with patch("szurubooru.func.external_import._request_json") as request_json:
        request_json.return_value = {"post": {"id": 123}}

        result = external_import._get_e621_post(123)

        assert result == {"id": 123}
        args, kwargs = request_json.call_args
        assert (
            args[0]
            == "https://e621.net/posts/123.json?login=hexerade&api_key=secret-key"
        )
        assert kwargs["headers"]["Accept"] == "application/json"
        assert "User-Agent" in kwargs["headers"]

from unittest.mock import patch

import pytest

from szurubooru import config, model
from szurubooru.func import external_import


def test_import_post_metadata_uses_fuzzysearch_payload():
    post = model.Post()
    post.post_id = 123
    post.type = model.Post.TYPE_IMAGE

    with patch("szurubooru.func.external_import.files.get") as get_file:
        with patch(
            "szurubooru.func.external_import.posts.get_post_content_path"
        ) as get_content_path:
            with patch(
                "szurubooru.func.external_import._search_e621_post"
            ) as search_post:
                get_content_path.return_value = "ignored"
                get_file.return_value = b"test-image"
                search_post.return_value = {
                    "site": "e621",
                    "site_id": 555,
                    "url": "https://e621.net/posts/555",
                    "distance": 2,
                    "tags": ["tag1", "tag2", "tag1"],
                    "site_info": {
                        "sources": [
                            "https://source.example/a",
                            "https://source.example/a",
                        ]
                    },
                }

                result = external_import.import_post_metadata(post)

                assert result == {
                    "site": "e621",
                    "postId": 555,
                    "postUrl": "https://e621.net/posts/555",
                    "distance": 2,
                    "tags": ["tag1", "tag2"],
                    "sources": [
                        "https://e621.net/posts/555",
                        "https://source.example/a",
                    ],
                }


def test_search_e621_post_requires_api_key():
    post = model.Post()
    post.post_id = 123
    post.mime_type = "image/jpeg"
    config.config = {}

    with pytest.raises(external_import.errors.ThirdPartyError):
        external_import._search_e621_post(b"test-image", post)


def test_search_e621_post_uses_image_search_with_api_key():
    post = model.Post()
    post.post_id = 123
    post.mime_type = "image/jpeg"
    config.config = {
        "fuzzysearch_api_key": "test-key",
        "user_agent": "szuru-test-agent",
    }

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
        assert kwargs["headers"]["User-Agent"] == "szuru-test-agent"
        assert kwargs["headers"]["Content-Type"].startswith(
            "multipart/form-data; boundary="
        )


def test_search_e621_post_uses_default_user_agent_for_fuzzysearch():
    post = model.Post()
    post.post_id = 123
    post.mime_type = "image/jpeg"
    config.config = {
        "fuzzysearch_api_key": "test-key",
        "user_agent": "",
    }

    with patch("szurubooru.func.external_import._request_json") as request_json:
        request_json.return_value = [
            {"site": "e621", "site_id": 555, "distance": 3}
        ]

        external_import._search_e621_post(b"test-image", post)

        _, kwargs = request_json.call_args
        assert (
            kwargs["headers"]["User-Agent"]
            == "szurubooru-external-import/1.0"
        )


def test_raise_http_error_for_fuzzysearch_403_includes_payload():
    with pytest.raises(external_import.errors.ThirdPartyError) as ex:
        external_import._raise_http_error(
            "FuzzySearch", 403, '{"message":"forbidden"}'
        )

    assert 'HTTP 403: {"message":"forbidden"}.' in str(ex.value)

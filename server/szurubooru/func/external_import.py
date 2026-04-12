import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from szurubooru import config, errors, model
from szurubooru.func import files, posts


FUZZYSEARCH_IMAGE_URL = "https://api-next.fuzzysearch.net/v1/image"
E621_POST_URL = "https://e621.net/posts/{post_id}.json"
E621_POST_PAGE_URL = "https://e621.net/posts/{post_id}"
TAG_CATEGORY_ORDER = [
    "artist",
    "copyright",
    "character",
    "species",
    "general",
    "lore",
    "meta",
    "contributor",
]


class E621PostNotFoundError(errors.NotFoundError):
    pass


class FuzzySearchNotConfiguredError(errors.ThirdPartyError):
    pass


def import_post_metadata(post: model.Post) -> Dict[str, Any]:
    if post.type not in [model.Post.TYPE_IMAGE, model.Post.TYPE_ANIMATION]:
        raise errors.ValidationError(
            "Only image posts can be imported from e621."
        )

    content = files.get(posts.get_post_content_path(post))
    if not content:
        raise errors.ProcessingError("Post content is unavailable.")

    search_result = _search_e621_post(content, post)
    e621_post = _get_e621_post(int(search_result["site_id"]))

    post_id = int(search_result["site_id"])
    post_url = E621_POST_PAGE_URL.format(post_id=post_id)
    fuzzysearch_sources = _deduplicate_sources(
        [post_url] + search_result.get("site_info", {}).get("sources", [])
    )

    return {
        "site": "e621",
        "postId": post_id,
        "postUrl": post_url,
        "distance": search_result.get("distance"),
        "tags": _extract_e621_tags(e621_post),
        "sources": fuzzysearch_sources,
    }


def _search_e621_post(content: bytes, post: model.Post) -> Dict[str, Any]:
    api_key = config.config.get("fuzzysearch_api_key")
    if not api_key:
        raise FuzzySearchNotConfiguredError(
            "FuzzySearch API key is not configured."
        )

    mime_type = post.mime_type or "application/octet-stream"
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    body, content_type = _encode_multipart_formdata(
        {},
        {
            "image": (
                f"post-{post.post_id}{extension}",
                content,
                mime_type,
            )
        },
    )
    response = _request_json(
        FUZZYSEARCH_IMAGE_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "Content-Type": content_type,
        },
        method="POST",
        expected_statuses=[200],
        service_name="FuzzySearch",
    )

    e621_results = [
        result for result in response if result.get("site") == "e621"
    ]
    e621_results.sort(
        key=lambda item: (
            item.get("distance") is None,
            item.get("distance") or 0,
            item.get("site_id") or 0,
        )
    )
    if not e621_results:
        raise E621PostNotFoundError(
            "This image has no matching page on e621."
        )
    return e621_results[0]


def _get_e621_post(post_id: int) -> Dict[str, Any]:
    response = _request_json(
        E621_POST_URL.format(post_id=post_id),
        headers={
            "User-Agent": _get_e621_user_agent(),
            "Accept": "application/json",
        },
        method="GET",
        expected_statuses=[200],
        service_name="e621",
    )
    post = response.get("post")
    if not post:
        raise E621PostNotFoundError("Matching e621 post was not found.")
    return post


def _extract_e621_tags(post: Dict[str, Any]) -> List[str]:
    post_tags = post.get("tags") or {}
    tag_names: List[str] = []
    for category in TAG_CATEGORY_ORDER:
        tag_names.extend(post_tags.get(category) or [])
    return _deduplicate_sources(tag_names)


def _deduplicate_sources(values: List[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        value = (value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _get_e621_user_agent() -> str:
    return (
        config.config.get("e621_user_agent")
        or config.config.get("user_agent")
        or "szurubooru-e621-import/1.0"
    )


def _request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    method: str = "GET",
    expected_statuses: Optional[List[int]] = None,
    service_name: str = "remote service",
) -> Any:
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if "Accept" not in (headers or {}):
        request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request) as response:
            if expected_statuses and response.status not in expected_statuses:
                raise errors.ProcessingError(
                    f"{service_name} returned unexpected HTTP {response.status}."
                )
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as ex:
        payload = ex.read().decode("utf-8", errors="replace")
        _raise_http_error(service_name, ex.code, payload)
    except urllib.error.URLError as ex:
        raise errors.ThirdPartyError(
            f"Unable to reach {service_name}."
        ) from ex
    except json.JSONDecodeError as ex:
        raise errors.ProcessingError(
            f"{service_name} returned invalid JSON."
        ) from ex


def _raise_http_error(service_name: str, status: int, payload: str) -> None:
    if service_name == "FuzzySearch":
        if status == 400:
            raise errors.ValidationError(
                f"FuzzySearch rejected the image: {payload}."
            )
        if status == 401:
            raise errors.ThirdPartyError(
                "FuzzySearch API key is invalid or missing."
            )
        if status == 413:
            raise errors.ValidationError(
                "Image is too large for FuzzySearch."
            )
        if status == 429:
            raise errors.ProcessingError(
                "FuzzySearch rate limit has been exhausted."
            )
    if service_name == "e621":
        if status == 404:
            raise E621PostNotFoundError("Matching e621 post was not found.")
        if status == 403:
            raise errors.ThirdPartyError(
                "e621 rejected the request. Check the configured User-Agent."
            )
        if status == 429:
            raise errors.ProcessingError("e621 rate limit has been exhausted.")
    raise errors.ProcessingError(
        f"{service_name} returned HTTP {status}."
    )


def _encode_multipart_formdata(
    fields: Dict[str, str],
    files_to_send: Dict[str, Any],
) -> Any:
    boundary = f"----szuru-{uuid.uuid4().hex}"
    chunks: List[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                ).encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, (filename, content, content_type) in files_to_send.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"

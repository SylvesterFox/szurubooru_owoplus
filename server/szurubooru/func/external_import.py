import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from szurubooru import config, errors, model
from szurubooru.func import files, posts


FUZZYSEARCH_IMAGE_URL = "https://api-next.fuzzysearch.net/v1/image"
E621_POST_PAGE_URL = "https://e621.net/posts/{post_id}"


class E621PostNotFoundError(errors.NotFoundError):
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
    post_id = int(search_result["site_id"])
    post_url = search_result.get("url") or E621_POST_PAGE_URL.format(
        post_id=post_id
    )
    fuzzysearch_sources = _deduplicate_sources(
        [post_url] + search_result.get("site_info", {}).get("sources", [])
    )

    return {
        "site": "e621",
        "postId": post_id,
        "postUrl": post_url,
        "distance": search_result.get("distance"),
        "tags": _deduplicate_sources(search_result.get("tags") or []),
        "sources": fuzzysearch_sources,
    }


def _search_e621_post(content: bytes, post: model.Post) -> Dict[str, Any]:
    api_key = config.config.get("fuzzysearch_api_key")
    if not api_key:
        raise errors.ThirdPartyError(
            "FuzzySearch API key is not configured. "
            "The /v1/hashes endpoint requires an image hash value, "
            "not md5/sha1/sha256 file hashes."
        )

    response = _search_fuzzysearch_by_image(content, post, api_key)

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


def _search_fuzzysearch_by_image(
    content: bytes, post: model.Post, api_key: str
) -> List[Dict[str, Any]]:
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
    headers = {
        "x-api-key": api_key,
        "Content-Type": content_type,
    }
    user_agent = _get_external_import_user_agent()
    if user_agent:
        headers["User-Agent"] = user_agent

    return _request_json(
        FUZZYSEARCH_IMAGE_URL,
        data=body,
        headers=headers,
        method="POST",
        expected_statuses=[200],
        service_name="FuzzySearch",
    )


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


def merge_sources(
    existing_source: Optional[str], imported_sources: List[str]
) -> str:
    values = (existing_source or "").splitlines() + (imported_sources or [])
    return "\n".join(_deduplicate_sources(values))


def get_post_metadata_update(
    post: model.Post, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    imported_tags = _deduplicate_sources(metadata.get("tags") or [])
    imported_sources = _deduplicate_sources(metadata.get("sources") or [])
    existing_tags = [tag.first_name for tag in post.tags]
    existing_sources = _deduplicate_sources((post.source or "").splitlines())
    merged_tags = _deduplicate_sources(existing_tags + imported_tags)
    merged_source = merge_sources(post.source, imported_sources)

    return {
        "tags": merged_tags,
        "source": merged_source or None,
        "addedTags": len(
            [tag_name for tag_name in merged_tags if tag_name not in existing_tags]
        ),
        "addedSources": len(
            [
                source
                for source in merged_source.splitlines()
                if source not in existing_sources
            ]
        ),
        "hasChanges": (
            merged_tags != existing_tags or (merged_source or None) != post.source
        ),
    }


def _get_external_import_user_agent() -> str:
    return (
        config.config.get("user_agent")
        or "szurubooru-external-import/1.0"
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
        if status == 403:
            details = payload.strip()
            if details:
                raise errors.ThirdPartyError(
                    f"FuzzySearch rejected the request with HTTP 403: {details}."
                )
            raise errors.ThirdPartyError(
                "FuzzySearch rejected the request with HTTP 403."
            )
        if status == 413:
            raise errors.ValidationError(
                "Image is too large for FuzzySearch."
            )
        if status == 429:
            raise errors.ProcessingError(
                "FuzzySearch rate limit has been exhausted."
            )
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

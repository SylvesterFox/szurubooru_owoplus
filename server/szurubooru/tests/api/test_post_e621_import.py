from unittest.mock import patch
from datetime import datetime

import pytest

from szurubooru import api, db, errors, model
from szurubooru.func import external_import


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    config_injector(
        {
            "privileges": {
                "posts:edit:tags": model.User.RANK_REGULAR,
                "posts:edit:source": model.User.RANK_REGULAR,
                "posts:bulk-edit:import-e621": model.User.RANK_ADMINISTRATOR,
            },
        }
    )


def test_importing_e621_metadata(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1)
    db.session.add(post)
    db.session.flush()

    with patch("szurubooru.func.external_import.import_post_metadata"):
        external_import.import_post_metadata.return_value = {
            "postId": 123,
            "postUrl": "https://e621.net/posts/123",
            "tags": ["tag1", "tag2"],
            "sources": ["https://source.example/test"],
        }

        result = api.post_api.import_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": post.post_id},
        )

        assert result == {
            "postId": 123,
            "postUrl": "https://e621.net/posts/123",
            "tags": ["tag1", "tag2"],
            "sources": ["https://source.example/test"],
        }
        external_import.import_post_metadata.assert_called_once_with(post)


def test_importing_e621_metadata_requires_tag_edit_privilege(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1)
    db.session.add(post)
    db.session.flush()

    with pytest.raises(errors.AuthError):
        api.post_api.import_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_ANONYMOUS),
            ),
            {"post_id": post.post_id},
        )


def test_importing_e621_metadata_for_missing_post(
    context_factory, user_factory
):
    with pytest.raises(errors.NotFoundError):
        api.post_api.import_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": 999},
        )


def test_applying_e621_metadata(
    context_factory, post_factory, tag_factory, user_factory
):
    post = post_factory(id=1)
    post.source = "https://existing.example/source"
    post.tags = [tag_factory(names=["tag1"])]
    db.session.add(post)
    db.session.flush()

    with patch("szurubooru.func.external_import.import_post_metadata"):
        external_import.import_post_metadata.return_value = {
            "postId": 123,
            "postUrl": "https://e621.net/posts/123",
            "tags": ["tag1", "tag2"],
            "sources": [
                "https://existing.example/source",
                "https://source.example/test",
            ],
        }

        result = api.post_api.apply_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_ADMINISTRATOR),
            ),
            {"post_id": post.post_id},
        )

        assert result == {
            "status": "updated",
            "addedTags": 1,
            "addedSources": 1,
        }
        assert sorted(tag.first_name for tag in post.tags) == ["tag1", "tag2"]
        assert post.source == "\n".join(
            [
                "https://existing.example/source",
                "https://source.example/test",
            ]
        )


def test_applying_e621_metadata_skips_posts_without_match(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1)
    db.session.add(post)
    db.session.flush()

    with patch("szurubooru.func.external_import.import_post_metadata"):
        external_import.import_post_metadata.side_effect = (
            external_import.E621PostNotFoundError("not found")
        )

        result = api.post_api.apply_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_ADMINISTRATOR),
            ),
            {"post_id": post.post_id},
        )

        assert result == {"status": "skipped", "reason": "not-found"}


def test_applying_e621_metadata_requires_admin_privilege(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1)
    db.session.add(post)
    db.session.flush()

    with pytest.raises(errors.AuthError):
        api.post_api.apply_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": post.post_id},
        )


def test_applying_e621_metadata_uses_not_found_cache(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1, checksum="checksum-1")
    db.session.add(post)
    db.session.add(
        model.PostE621ImportCache(
            post_id=post.post_id,
            checksum=post.checksum,
            status=model.PostE621ImportCache.STATUS_NOT_FOUND,
            checked_time=datetime(2000, 1, 1),
        )
    )
    db.session.flush()

    with patch("szurubooru.func.external_import.import_post_metadata"):
        result = api.post_api.apply_e621_metadata(
            context_factory(
                user=user_factory(rank=model.User.RANK_ADMINISTRATOR),
            ),
            {"post_id": post.post_id},
        )

        assert result == {"status": "skipped", "reason": "cached-not-found"}
        assert not external_import.import_post_metadata.called


def test_purging_e621_import_cache(
    context_factory, post_factory, user_factory
):
    post = post_factory(id=1, checksum="checksum-1")
    db.session.add(post)
    db.session.add(
        model.PostE621ImportCache(
            post_id=post.post_id,
            checksum=post.checksum,
            status=model.PostE621ImportCache.STATUS_NOT_FOUND,
            checked_time=datetime(2000, 1, 1),
        )
    )
    db.session.flush()

    result = api.post_api.purge_e621_import_cache(
        context_factory(
            user=user_factory(rank=model.User.RANK_ADMINISTRATOR),
        )
    )

    assert result == {"deleted": 1}
    assert (
        db.session.query(model.PostE621ImportCache).count() == 0
    )

"use strict";

const router = require("../router.js");
const api = require("../api.js");
const settings = require("../models/settings.js");
const uri = require("../util/uri.js");
const PostList = require("../models/post_list.js");
const topNavigation = require("../models/top_navigation.js");
const PageController = require("../controllers/page_controller.js");
const PostsHeaderView = require("../views/posts_header_view.js");
const PostsPageView = require("../views/posts_page_view.js");
const EmptyView = require("../views/empty_view.js");

const fields = [
    "id",
    "thumbnailUrl",
    "type",
    "safety",
    "score",
    "favoriteCount",
    "commentCount",
    "tags",
    "version",
];

class PostListController {
    constructor(ctx) {
        this._pageController = new PageController();

        if (!api.hasPrivilege("posts:list")) {
            this._view = new EmptyView();
            this._view.showError("You don't have privileges to view posts.");
            return;
        }

        this._ctx = ctx;

        topNavigation.activate("posts");
        topNavigation.setTitle("Listing posts");

        this._headerView = new PostsHeaderView({
            hostNode: this._pageController.view.pageHeaderHolderNode,
            parameters: ctx.parameters,
            enableSafety: api.safetyEnabled(),
            canBulkEditTags: api.hasPrivilege("posts:bulk-edit:tags"),
            canBulkImportE621: api.hasPrivilege(
                "posts:bulk-edit:import-e621"
            ),
            canBulkEditSafety: api.hasPrivilege("posts:bulk-edit:safety"),
            canBulkDelete: api.hasPrivilege("posts:bulk-edit:delete"),
            bulkEdit: {
                tags: this._bulkEditTags,
            },
        });
        this._headerView.addEventListener("navigate", (e) =>
            this._evtNavigate(e)
        );
        this._headerView.addEventListener("bulkImportE621", () =>
            this._evtBulkImportE621()
        );

        if (this._headerView._bulkDeleteEditor) {
            this._headerView._bulkDeleteEditor.addEventListener(
                "deleteSelectedPosts",
                (e) => {
                    this._evtDeleteSelectedPosts(e);
                }
            );
        }

        this._postsMarkedForDeletion = [];
        this._bulkImportE621InProgress = false;
        this._syncPageController();
    }

    showSuccess(message) {
        this._pageController.showSuccess(message);
    }

    get _bulkEditTags() {
        return (this._ctx.parameters.tag || "").split(/\s+/).filter((s) => s);
    }

    _evtNavigate(e) {
        router.showNoDispatch(
            uri.formatClientLink("posts", e.detail.parameters)
        );
        Object.assign(this._ctx.parameters, e.detail.parameters);
        this._syncPageController();
    }

    _evtTag(e) {
        Promise.all(
            this._bulkEditTags.map((tag) => e.detail.post.tags.addByName(tag))
        )
            .then(e.detail.post.save())
            .catch((error) => window.alert(error.message));
    }

    _evtUntag(e) {
        for (let tag of this._bulkEditTags) {
            e.detail.post.tags.removeByName(tag);
        }
        e.detail.post.save().catch((error) => window.alert(error.message));
    }

    _evtChangeSafety(e) {
        e.detail.post.safety = e.detail.safety;
        e.detail.post.save().catch((error) => window.alert(error.message));
    }

    _evtMarkForDeletion(e) {
        const postId = e.detail;

        // Add or remove post from delete list
        if (e.detail.delete) {
            this._postsMarkedForDeletion.push(e.detail.post);
        } else {
            this._postsMarkedForDeletion = this._postsMarkedForDeletion.filter(
                (x) => x.id != e.detail.post.id
            );
        }
    }

    _evtDeleteSelectedPosts(e) {
        if (this._postsMarkedForDeletion.length == 0) return;

        if (
            confirm(
                `Are you sure you want to delete ${this._postsMarkedForDeletion.length} posts?`
            )
        ) {
            Promise.all(
                this._postsMarkedForDeletion.map((post) => post.delete())
            )
                .catch((error) => window.alert(error.message))
                .then(() => {
                    this._postsMarkedForDeletion = [];
                    this._headerView._navigate();
                });
        }
    }

    _getBulkImportE621Query() {
        return `sort:id ${this._ctx.parameters.query || ""}`.trim();
    }

    _loadAllPostIdsForBulkImportE621() {
        const ids = [];
        const fields = ["id"];
        const limit = 100;
        const query = this._getBulkImportE621Query();

        const loadPage = (offset) =>
            PostList.search(query, offset, limit, fields).then((response) => {
                ids.push(...response.results.map((post) => post.id));
                if (ids.length >= response.total || !response.results.length) {
                    return Promise.resolve(ids);
                }
                return loadPage(offset + response.results.length);
            });

        return loadPage(0);
    }

    _evtBulkImportE621() {
        if (this._bulkImportE621InProgress) {
            return;
        }
        this._bulkImportE621InProgress = true;
        this._pageController.view.clearMessages();

        this._loadAllPostIdsForBulkImportE621()
            .then((postIds) => {
                if (!postIds.length) {
                    this._headerView.resetBulkImportE621();
                    this.showSuccess("No posts matched the current search.");
                    return Promise.resolve();
                }

                let completed = 0;
                let updated = 0;
                let skipped = 0;
                this._headerView.setBulkImportE621Progress(0, postIds.length);

                const importNext = (index) => {
                    if (index >= postIds.length) {
                        this._syncPageController();
                        this.showSuccess(
                            `Finished e621 auto import. Updated ${updated}/${postIds.length}, skipped ${skipped}.`
                        );
                        return Promise.resolve();
                    }

                    return api
                        .post(
                            uri.formatApiLink(
                                "post",
                                postIds[index],
                                "e621-import",
                                "apply"
                            ),
                            {}
                        )
                        .then((result) => {
                            if (result.status === "updated") {
                                updated++;
                            } else {
                                skipped++;
                            }
                            completed++;
                            this._headerView.setBulkImportE621Progress(
                                completed,
                                postIds.length
                            );
                            return importNext(index + 1);
                        });
                };

                return importNext(0);
            })
            .catch((error) => {
                this._headerView.resetBulkImportE621();
                this._pageController.showError(error.message);
            })
            .then(() => {
                this._bulkImportE621InProgress = false;
            });
    }

    _syncPageController() {
        this._pageController.run({
            parameters: this._ctx.parameters,
            defaultLimit: parseInt(settings.get().postsPerPage),
            getClientUrlForPage: (offset, limit) => {
                const parameters = Object.assign({}, this._ctx.parameters, {
                    offset: offset,
                    limit: limit,
                });
                return uri.formatClientLink("posts", parameters);
            },
            requestPage: (offset, limit) => {
                return PostList.search(
                    this._ctx.parameters.query,
                    offset,
                    limit,
                    fields
                );
            },
            pageRenderer: (pageCtx) => {
                Object.assign(pageCtx, {
                    canViewPosts: api.hasPrivilege("posts:view"),
                    canBulkEditTags: api.hasPrivilege("posts:bulk-edit:tags"),
                    canBulkEditSafety: api.hasPrivilege(
                        "posts:bulk-edit:safety"
                    ),
                    canBulkDelete: api.hasPrivilege("posts:bulk-edit:delete"),
                    bulkEdit: {
                        tags: this._bulkEditTags,
                        markedForDeletion: this._postsMarkedForDeletion,
                    },
                    postFlow: settings.get().postFlow,
                });
                const view = new PostsPageView(pageCtx);
                view.addEventListener("tag", (e) => this._evtTag(e));
                view.addEventListener("untag", (e) => this._evtUntag(e));
                view.addEventListener("changeSafety", (e) =>
                    this._evtChangeSafety(e)
                );
                view.addEventListener("markForDeletion", (e) =>
                    this._evtMarkForDeletion(e)
                );
                return view;
            },
        });
    }
}

module.exports = (router) => {
    router.enter(["posts"], (ctx, next) => {
        ctx.controller = new PostListController(ctx);
    });
};

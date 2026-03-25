"""Mattermost REST API v4 MCP Server.

Wraps Mattermost REST API v4 endpoints and exposes them as MCP tools.
Bearer-token (Personal Access Token) auth only.

API docs: https://api.mattermost.com/
"""

import logging
import os
import sys
from typing import Any, Callable

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mattermost_mcp")
log.setLevel(os.environ.get("MATTERMOST_LOG_LEVEL", "WARNING").upper())
if not log.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(_handler)

# ---------------------------------------------------------------------------
# Tool registry with filtering
# ---------------------------------------------------------------------------

DEFAULT_TOOLS = {
    # users
    "get_me", "get_user", "search_users",
    # teams
    "get_my_teams",
    # channels
    "get_channels", "get_my_channels", "get_channel_by_name", "search_channels",
    "create_channel", "create_direct_channel",
    # posts
    "get_posts", "get_post_thread", "search_posts", "get_unread", "get_pinned_posts",
    "create_post",
    # reactions
    "add_reaction",
    # files
    "get_file_info", "get_file", "upload_file",
}

# Collected by @mm_tool decorator, registered to FastMCP at startup
_tool_registry: dict[str, Callable] = {}


def mm_tool(func: Callable) -> Callable:
    """Decorator that registers an async function in the tool registry."""
    _tool_registry[func.__name__] = func
    return func


# ---------------------------------------------------------------------------
# MattermostClient
# ---------------------------------------------------------------------------


class MattermostClient:
    def __init__(self, base_url: str, token: str, team_id: str):
        self.base_url = base_url.rstrip("/") + "/api/v4"
        self.token = token
        self.team_id = team_id

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_data: dict | list | None = None,
        data: bytes | None = None,
        files: dict | None = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self.token}"}
        if not files:
            headers["Accept"] = "application/json"
        log.debug("%s %s params=%s", method, url, params)
        async with httpx.AsyncClient(verify=True) as client:
            try:
                kwargs: dict[str, Any] = {
                    "headers": headers,
                    "params": params,
                    "timeout": 30.0,
                    "follow_redirects": True,
                }
                if files:
                    kwargs["files"] = files
                    if data:
                        kwargs["data"] = {"channel_id": data.decode()}
                elif json_data is not None:
                    kwargs["json"] = json_data
                elif data is not None:
                    kwargs["content"] = data

                resp = await client.request(method, url, **kwargs)
                log.debug("Response %s, %d bytes", resp.status_code, len(resp.content))
                resp.raise_for_status()
                if raw:
                    return resp.content
                if not resp.content:
                    return {"ok": True}
                return resp.json()
            except httpx.HTTPStatusError as e:
                body = e.response.text[:500] if e.response else ""
                log.warning("%s %s → HTTP %s: %s", method, path, e.response.status_code, body[:200])
                raise ValueError(f"HTTP {e.response.status_code}: {body}")
            except httpx.HTTPError as e:
                log.warning("%s %s → %s", method, path, e)
                raise ValueError(f"Error: {e}")
            except ValueError:
                raise
            except Exception as e:
                log.exception("%s %s → unexpected error", method, path)
                raise ValueError(f"Unexpected error: {e}")

    def get_team_id(self, team_id: str = "") -> str:
        """Return explicit team_id or fall back to configured default."""
        tid = team_id or self.team_id
        if not tid:
            raise ValueError("No team_id provided and MATTERMOST_TEAM_ID not set")
        return tid


# ---------------------------------------------------------------------------
# Client instance
# ---------------------------------------------------------------------------

mm = MattermostClient(
    base_url=os.environ.get("MATTERMOST_URL", ""),
    token=os.environ.get("MATTERMOST_TOKEN", ""),
    team_id=os.environ.get("MATTERMOST_TEAM_ID", ""),
)

# ===========================================================================
# Users
# ===========================================================================


@mm_tool
async def get_me() -> dict:
    """Get the current authenticated user's profile."""
    raw = await mm.request("GET", "/users/me")
    return _compact_user(raw)


@mm_tool
async def get_user(user_id: str = "", username: str = "") -> dict:
    """Get a user by ID or username. Provide one of user_id or username.

    Args:
        user_id: user ID (takes priority)
        username: username (without @)
    """
    if user_id:
        raw = await mm.request("GET", f"/users/{user_id}")
        return _compact_user(raw)
    if username:
        raw = await mm.request("GET", f"/users/username/{username}")
        return _compact_user(raw)
    return {"error": "Provide user_id or username"}


@mm_tool
async def search_users(term: str, team_id: str = "", limit: int = 100) -> list:
    """Search users by term (username, display name, email).

    Args:
        term: search term
        team_id: optional team ID to scope search
        limit: max results (default 25)
    """
    body: dict[str, Any] = {"term": term, "limit": limit}
    if team_id:
        body["team_id"] = team_id
    raw = await mm.request("POST", "/users/search", json_data=body)
    return _compact_users(raw)


@mm_tool
async def get_user_status(user_id: str) -> dict:
    """Get a user's status (online, away, dnd, offline).

    Args:
        user_id: user ID
    """
    raw = await mm.request("GET", f"/users/{user_id}/status")
    return _compact_user_status(raw)


@mm_tool
async def set_user_status(user_id: str, status: str) -> dict:
    """Set a user's status.

    Args:
        user_id: user ID
        status: one of 'online', 'away', 'dnd', 'offline'
    """
    return await mm.request("PUT", f"/users/{user_id}/status", json_data={"user_id": user_id, "status": status})


# ===========================================================================
# Teams
# ===========================================================================


@mm_tool
async def get_my_teams() -> list:
    """Get all teams the current user belongs to."""
    raw = await mm.request("GET", "/users/me/teams")
    return _compact_teams(raw)


# ===========================================================================
# Channels
# ===========================================================================


@mm_tool
async def get_channels(team_id: str = "", page: int = 0, per_page: int = 60) -> list:
    """Get public channels for a team.

    Args:
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
        page: page number (0-based)
        per_page: results per page (default 60)
    """
    tid = team_id or mm.get_team_id()
    raw = await mm.request("GET", f"/teams/{tid}/channels", params={"page": page, "per_page": per_page})
    return _compact_channels(raw)


@mm_tool
async def get_my_channels(team_id: str = "") -> list:
    """Get channels the current user is a member of for a team.

    Args:
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
    """
    tid = team_id or mm.get_team_id()
    raw = await mm.request("GET", f"/users/me/teams/{tid}/channels")
    return _compact_channels(raw)


@mm_tool
async def get_channel_by_name(channel_name: str, team_id: str = "") -> dict:
    """Get a channel by its name.

    Args:
        channel_name: channel name (URL-friendly slug)
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
    """
    tid = team_id or mm.get_team_id()
    raw = await mm.request("GET", f"/teams/{tid}/channels/name/{channel_name}")
    return _compact_channel(raw)


@mm_tool
async def search_channels(term: str, team_id: str = "") -> list:
    """Search channels by term.

    Args:
        term: search term
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
    """
    tid = team_id or mm.get_team_id()
    raw = await mm.request("POST", f"/teams/{tid}/channels/search", json_data={"term": term})
    return _compact_channels(raw)


@mm_tool
async def create_channel(
    display_name: str,
    name: str,
    type: str = "O",
    team_id: str = "",
    purpose: str = "",
    header: str = "",
) -> dict:
    """Create a new channel.

    Args:
        display_name: display name
        name: URL-friendly name
        type: 'O' for public (default), 'P' for private
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
        purpose: channel purpose
        header: channel header
    """
    tid = team_id or mm.get_team_id()
    body: dict[str, Any] = {
        "team_id": tid,
        "name": name,
        "display_name": display_name,
        "type": type,
    }
    if purpose:
        body["purpose"] = purpose
    if header:
        body["header"] = header
    return await mm.request("POST", "/channels", json_data=body)


@mm_tool
async def create_direct_channel(user_ids: list[str]) -> dict:
    """Create a direct message channel (2 users) or group message channel (3-8 users).

    Args:
        user_ids: list of 2 user IDs for DM, or 3-8 for group message
    """
    if len(user_ids) >= 3:
        return await mm.request("POST", "/channels/group", json_data=user_ids)
    return await mm.request("POST", "/channels/direct", json_data=user_ids)


# ===========================================================================
# Posts
# ===========================================================================


@mm_tool
async def get_posts(
    channel_id: str,
    page: int = 0,
    per_page: int = 60,
    since: int = 0,
    before: str = "",
    after: str = "",
) -> dict:
    """Get posts in a channel, ordered newest first.

    Args:
        channel_id: channel ID
        page: page number (0-based)
        per_page: results per page (default 60)
        since: Unix timestamp in milliseconds — return posts modified after this time. Cannot be used with before/after/page/per_page
        before: post ID — return posts before this post
        after: post ID — return posts after this post
    """
    params: dict[str, Any] = {}
    if since:
        params["since"] = since
    else:
        params["page"] = page
        params["per_page"] = per_page
        if before:
            params["before"] = before
        if after:
            params["after"] = after
    raw = await mm.request("GET", f"/channels/{channel_id}/posts", params=params)
    return _compact_post_list(raw)


@mm_tool
async def get_post_thread(post_id: str) -> dict:
    """Get a post and its replies (thread).

    Args:
        post_id: root post ID
    """
    raw = await mm.request("GET", f"/posts/{post_id}/thread")
    return _compact_thread(raw)


@mm_tool
async def search_posts(terms: str, team_id: str = "", page: int = 0, per_page: int = 60) -> dict:
    """Search posts. Supports Mattermost search syntax (from:, in:, before:, after:, etc.).

    Args:
        terms: search query
        team_id: team ID (uses MATTERMOST_TEAM_ID if empty)
        page: page number
        per_page: results per page
    """
    tid = mm.get_team_id(team_id)
    body: dict[str, Any] = {
        "terms": terms,
        "is_or_search": False,
        "page": page,
        "per_page": per_page,
    }
    raw = await mm.request("POST", f"/teams/{tid}/posts/search", json_data=body)
    return _compact_post_list(raw)


@mm_tool
async def get_unread(channel_id: str) -> dict:
    """Get unread posts for current user in a channel.

    Args:
        channel_id: channel ID
    """
    raw = await mm.request("GET", f"/users/me/channels/{channel_id}/posts/unread")
    return _compact_post_list(raw)


@mm_tool
async def get_pinned_posts(channel_id: str) -> dict:
    """Get pinned posts in a channel.

    Args:
        channel_id: channel ID
    """
    raw = await mm.request("GET", f"/channels/{channel_id}/pinned")
    return _compact_post_list(raw)


@mm_tool
async def create_post(
    channel_id: str,
    message: str,
    root_id: str = "",
    file_ids: list[str] | None = None,
    props: dict | None = None,
) -> dict:
    """Create a new post (message) in a channel.

    Args:
        channel_id: channel ID
        message: message text (supports Markdown)
        root_id: optional root post ID to reply in a thread
        file_ids: optional list of file IDs to attach
        props: optional JSON property bag. Reserved keys:
            - attachments: list of rich attachments (fallback, color, pretext, text, title, fields, image_url, actions, etc.)
            - override_username: display name override for the post author
    """
    body: dict[str, Any] = {"channel_id": channel_id, "message": message}
    if root_id:
        body["root_id"] = root_id
    if file_ids:
        body["file_ids"] = file_ids
    if props:
        body["props"] = props
    return await mm.request("POST", "/posts", json_data=body)


@mm_tool
async def update_post(
    post_id: str,
    message: str = "",
    file_ids: list[str] | None = None,
    props: dict | None = None,
) -> dict:
    """Update (patch) a post. Only provided fields are updated.

    Args:
        post_id: post ID
        message: new message text
        file_ids: updated list of attached file IDs
        props: JSON property bag (e.g. attachments)
    """
    body: dict[str, Any] = {}
    if message:
        body["message"] = message
    if file_ids is not None:
        body["file_ids"] = file_ids
    if props is not None:
        body["props"] = props
    if not body:
        return {"error": "No fields to update"}
    return await mm.request("PUT", f"/posts/{post_id}/patch", json_data=body)


@mm_tool
async def delete_post(post_id: str) -> dict:
    """Delete a post.

    Args:
        post_id: post ID
    """
    return await mm.request("DELETE", f"/posts/{post_id}")


@mm_tool
async def pin_post(post_id: str) -> dict:
    """Pin a post to its channel.

    Args:
        post_id: post ID
    """
    return await mm.request("POST", f"/posts/{post_id}/pin")


@mm_tool
async def unpin_post(post_id: str) -> dict:
    """Unpin a post from its channel.

    Args:
        post_id: post ID
    """
    return await mm.request("POST", f"/posts/{post_id}/unpin")


# ===========================================================================
# Reactions
# ===========================================================================


@mm_tool
async def get_reactions(post_id: str) -> list:
    """Get reactions on a post.

    Args:
        post_id: post ID
    """
    raw = await mm.request("GET", f"/posts/{post_id}/reactions")
    return _compact_reactions(raw)


@mm_tool
async def add_reaction(user_id: str, post_id: str, emoji_name: str) -> dict:
    """Add a reaction to a post.

    Args:
        user_id: user ID of the reactor
        post_id: post ID
        emoji_name: emoji name without colons (e.g. 'thumbsup')
    """
    return await mm.request("POST", "/reactions", json_data={
        "user_id": user_id,
        "post_id": post_id,
        "emoji_name": emoji_name,
    })


@mm_tool
async def remove_reaction(user_id: str, post_id: str, emoji_name: str) -> dict:
    """Remove a reaction from a post.

    Args:
        user_id: user ID
        post_id: post ID
        emoji_name: emoji name without colons
    """
    return await mm.request("DELETE", f"/users/{user_id}/posts/{post_id}/reactions/{emoji_name}")


# ===========================================================================
# Files
# ===========================================================================


@mm_tool
async def upload_file(channel_id: str, file_path: str, filename: str = "") -> dict:
    """Upload a file to attach to a post later.

    Args:
        channel_id: channel ID
        file_path: local path to the file
        filename: optional filename override
    """
    fname = filename or os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        raise ValueError(f"Error reading file: {e}")
    url = f"{mm.base_url}/files"
    headers = {"Authorization": f"Bearer {mm.token}"}
    async with httpx.AsyncClient(verify=True) as client:
        try:
            resp = await client.post(
                url,
                headers=headers,
                data={"channel_id": channel_id},
                files={"files": (fname, file_bytes)},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise ValueError(f"Error uploading: {e}")


@mm_tool
async def get_file_info(file_id: str) -> dict:
    """Get metadata for a file.

    Args:
        file_id: file ID
    """
    raw = await mm.request("GET", f"/files/{file_id}/info")
    return _compact_file_info(raw)


@mm_tool
async def get_file(file_id: str, save_path: str = "") -> dict:
    """Download a file. Must provide save_path for binary files.

    Args:
        file_id: file ID
        save_path: local path to save the file
    """
    result = await mm.request("GET", f"/files/{file_id}", raw=True)
    if not isinstance(result, bytes):
        return result
    if save_path:
        try:
            save_path = os.path.abspath(save_path)
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(result)
            return {"saved": save_path, "size": len(result)}
        except OSError as e:
            raise ValueError(f"Error saving: {e}")
    try:
        return {"content": result.decode("utf-8")}
    except UnicodeDecodeError:
        return {"binary": True, "size": len(result), "hint": "Use save_path to write to a file."}


# ===========================================================================
# Helpers
# ===========================================================================



_USER_FIELDS = ("id", "username", "first_name", "last_name", "nickname", "email", "position", "roles")
_TEAM_FIELDS = ("id", "display_name", "name", "description", "type")
_CHANNEL_FIELDS = ("id", "type", "display_name", "name", "header", "purpose", "team_id", "total_msg_count")
_FILE_INFO_FIELDS = ("id", "name", "extension", "size", "mime_type", "post_id")


def _pick(src: dict, fields: tuple[str, ...]) -> dict:
    """Extract fields from dict, dropping None/empty values."""
    return {k: src[k] for k in fields if src.get(k)}


def _compact_obj(data: Any, fields: tuple[str, ...]) -> dict:
    if not isinstance(data, dict):
        return data
    return _pick(data, fields)


def _compact_list(data: Any, fields: tuple[str, ...]) -> list:
    if not isinstance(data, list):
        return data
    return [_pick(item, fields) for item in data]


def _compact_user(data: Any) -> dict:
    return _compact_obj(data, _USER_FIELDS)


def _compact_users(data: Any) -> list:
    return _compact_list(data, _USER_FIELDS)


def _compact_teams(data: Any) -> list:
    return _compact_list(data, _TEAM_FIELDS)


def _compact_channel(data: Any) -> dict:
    return _compact_obj(data, _CHANNEL_FIELDS)


def _compact_channels(data: Any) -> list:
    return _compact_list(data, _CHANNEL_FIELDS)


def _compact_user_status(data: Any) -> dict:
    if not isinstance(data, dict):
        return data
    result: dict[str, Any] = {"user_id": data.get("user_id"), "status": data.get("status")}
    if data.get("status") == "dnd" and data.get("dnd_end_time"):
        result["dnd_end_time"] = data["dnd_end_time"]
    return {k: v for k, v in result.items() if v}


def _compact_reactions(data: Any) -> list:
    if not isinstance(data, list):
        return data
    return [{"user_id": r.get("user_id"), "emoji_name": r.get("emoji_name")} for r in data]


def _compact_file_info(data: Any) -> dict:
    return _compact_obj(data, _FILE_INFO_FIELDS)


def _compact_post_list(data: Any) -> dict:
    """Compact a Mattermost post list response, stripping noise."""
    if not isinstance(data, dict) or "order" not in data or "posts" not in data:
        return data

    posts = []
    for pid in data["order"]:
        p = data["posts"].get(pid, {})
        post: dict[str, Any] = {
            "id": p.get("id"),
            "user_id": p.get("user_id"),
            "channel_id": p.get("channel_id"),
            "message": p.get("message"),
            "create_at": p.get("create_at"),
            "type": p.get("type") or None,
        }
        if p.get("root_id"):
            post["root_id"] = p["root_id"]
        if p.get("reply_count"):
            post["reply_count"] = p["reply_count"]
        if p.get("is_pinned"):
            post["is_pinned"] = True
        # Compact file metadata from metadata.files (id, name, size)
        meta_files = p.get("metadata", {}).get("files")
        if meta_files:
            post["files"] = [
                {k: f[k] for k in ("id", "name", "size") if k in f}
                for f in meta_files
            ]
        elif p.get("file_ids"):
            post["file_ids"] = p["file_ids"]
        if p.get("props", {}).get("attachments"):
            post["attachments"] = p["props"]["attachments"]
        # Strip None values
        post = {k: v for k, v in post.items() if v is not None}
        posts.append(post)

    return {"posts": posts, "total": len(posts)}


def _compact_thread(data: Any) -> dict:
    """Compact a thread response: hoist channel_id/root_id to top level, strip from posts."""
    result = _compact_post_list(data)
    if not isinstance(result, dict) or not result.get("posts"):
        return result

    posts = result["posts"]
    # Extract common fields from root post
    root = posts[0]
    channel_id = root.get("channel_id")
    root_id = root.get("id")

    # Strip redundant channel_id and root_id from every post
    for p in posts:
        p.pop("channel_id", None)
        p.pop("root_id", None)

    return {"channel_id": channel_id, "root_id": root_id, "posts": posts, "total": len(posts)}


# ===========================================================================
# Server setup & entry point
# ===========================================================================


def _build_server() -> FastMCP:
    """Create FastMCP instance with filtered tools."""
    mcp = FastMCP("mattermost")

    tools_env = os.environ.get("MATTERMOST_TOOLS", "").strip()

    if tools_env.lower() == "all":
        enabled = set(_tool_registry.keys())
    elif tools_env:
        enabled = {t.strip() for t in tools_env.split(",") if t.strip()}
    else:
        enabled = DEFAULT_TOOLS

    for name, func in _tool_registry.items():
        if name in enabled:
            mcp.tool()(func)

    log.info("Registered %d tools: %s", len(enabled & set(_tool_registry.keys())),
             ", ".join(sorted(enabled & set(_tool_registry.keys()))))

    return mcp


def main():
    mcp = _build_server()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    kwargs: dict[str, Any] = {}
    if transport in ("sse", "streamable-http"):
        kwargs["host"] = os.environ.get("MCP_HOST", "0.0.0.0")
        kwargs["port"] = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    main()

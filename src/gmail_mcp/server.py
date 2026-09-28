"""MCP server for one Gmail mailbox."""

import importlib.metadata
import json
import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from .client import GmailClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    __version__ = importlib.metadata.version("gmail-second-account-mcp")
except importlib.metadata.PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"

_ICON_BASE = os.getenv("MCP_PUBLIC_URL", "").rstrip("/")
_ICON_SIZES = (48, 96, 256)
_ACCOUNT = os.getenv("GMAIL_ACCOUNT_LABEL", "Gmail")

# The SDK's 30-minute default kills sessions of chats left idle, and their next call fails.
SESSION_IDLE_TIMEOUT = 24 * 3600

mcp = FastMCP(
    "gmail",
    session_idle_timeout=SESSION_IDLE_TIMEOUT,
    icons=(
        [
            Icon(
                src=f"{_ICON_BASE}/icon.png"
                if size == 256
                else f"{_ICON_BASE}/icon-{size}.png",
                mimeType="image/png",
                sizes=[f"{size}x{size}"],
            )
            for size in _ICON_SIZES
        ]
        if _ICON_BASE
        else None
    ),
    website_url=_ICON_BASE or None,
    instructions=(
        f"Read and write the {_ACCOUNT} mailbox: search and read mail, follow "
        "threads, manage labels, archive and trash, and write drafts. This "
        "server serves one mailbox only, so nothing here can touch another "
        "account. Use gmail_search with Gmail's own query syntax, then "
        "gmail_get_message for the body."
    ),
)

mcp._mcp_server.version = __version__

_client: GmailClient | None = None


def _get_client() -> GmailClient:
    global _client
    if _client is None:
        _client = GmailClient()
    return _client


def _ok(data: dict) -> str:
    return json.dumps({"status": "success", **data}, indent=2)


def _err(e: Exception) -> str:
    return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_READ = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_WRITE = {**_READ, "readOnlyHint": False}
_DESTRUCTIVE = {**_WRITE, "destructiveHint": True}


# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def gmail_profile() -> str:
    """Which mailbox this server serves, and how much is in it.

    Worth calling first when more than one Gmail connector is set up, so a
    reply goes to the right account.
    """
    try:
        return _ok({"profile": _get_client().profile()})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def gmail_search(query: str = "", limit: int = 20, include_spam: bool = False) -> str:
    """Search the mailbox using Gmail's own query syntax.

    Takes the same operators as the Gmail search box: `from:`, `to:`,
    `subject:`, `has:attachment`, `is:unread`, `label:`, `after:`, `before:`,
    `newer_than:7d`. An empty query lists the most recent mail.

    Returns headers and a snippet per message, not bodies. Follow with
    gmail_get_message for one, or gmail_get_thread for the conversation.

    Args:
        query: Gmail search expression, e.g. "is:unread from:client.com".
        limit: Maximum messages to return, up to 100.
        include_spam: Include spam and trash, which are excluded by default.
    """
    try:
        messages = _get_client().search(query, limit, include_spam)
        return _ok({"query": query, "count": len(messages), "messages": messages})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def gmail_get_message(message_id: str) -> str:
    """One message in full, including its body text.

    Args:
        message_id: Message id from gmail_search.
    """
    try:
        return _ok({"message": _get_client().get_message(message_id)})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def gmail_get_thread(thread_id: str) -> str:
    """A whole conversation, oldest message first.

    Read this before replying: a thread's later messages often change what the
    first one asked for.

    Args:
        thread_id: Thread id from gmail_search or gmail_get_message.
    """
    try:
        return _ok(_get_client().get_thread(thread_id))
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def gmail_list_attachments(message_id: str) -> str:
    """What is attached to a message: filenames, types and sizes.

    Args:
        message_id: Message id from gmail_search.
    """
    try:
        items = _get_client().list_attachments(message_id)
        return _ok({"message_id": message_id, "count": len(items), "attachments": items})
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------
# Labels and filing
# ------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def gmail_list_labels() -> str:
    """Every label, with its id. Label ids are what the filing tools take."""
    try:
        labels = _get_client().list_labels()
        return _ok({"count": len(labels), "labels": labels})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_create_label(name: str) -> str:
    """Create a label. Use `Parent/Child` to nest it.

    Args:
        name: Label name.
    """
    try:
        return _ok({"label": _get_client().create_label(name)})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_DESTRUCTIVE)
def gmail_delete_label(label_id: str) -> str:
    """Delete a label. Messages keep their content but lose the label.

    Args:
        label_id: Label id from gmail_list_labels.
    """
    try:
        return _ok(_get_client().delete_label(label_id))
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_modify_labels(
    message_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> str:
    """Add or remove labels on a message.

    The system labels are INBOX, UNREAD, STARRED, IMPORTANT, SPAM and TRASH.
    Removing INBOX archives a message; removing UNREAD marks it read.

    Args:
        message_id: Message to file.
        add: Label ids to add.
        remove: Label ids to remove.
    """
    try:
        result = _get_client().modify_labels(message_id, add, remove)
        return _ok({"message_id": message_id, "labels": result.get("labelIds", [])})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_archive(message_id: str) -> str:
    """Archive a message by taking it out of the inbox. Nothing is deleted.

    Args:
        message_id: Message to archive.
    """
    try:
        result = _get_client().modify_labels(message_id, remove=["INBOX"])
        return _ok({"message_id": message_id, "labels": result.get("labelIds", [])})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_mark_read(message_id: str, read: bool = True) -> str:
    """Mark a message read, or unread with read=false.

    Args:
        message_id: Message to mark.
        read: True marks it read, False marks it unread.
    """
    try:
        client = _get_client()
        result = (
            client.modify_labels(message_id, remove=["UNREAD"])
            if read
            else client.modify_labels(message_id, add=["UNREAD"])
        )
        return _ok({"message_id": message_id, "labels": result.get("labelIds", [])})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_DESTRUCTIVE)
def gmail_trash(message_id: str) -> str:
    """Move a message to the trash, where Gmail keeps it for 30 days.

    Args:
        message_id: Message to trash.
    """
    try:
        result = _get_client().trash(message_id)
        return _ok({"message_id": result.get("id"), "labels": result.get("labelIds", [])})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_untrash(message_id: str) -> str:
    """Take a message back out of the trash.

    Args:
        message_id: Message to restore.
    """
    try:
        result = _get_client().untrash(message_id)
        return _ok({"message_id": result.get("id"), "labels": result.get("labelIds", [])})
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------
# Drafts
# ------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def gmail_list_drafts(limit: int = 20) -> str:
    """Drafts waiting in the mailbox.

    Args:
        limit: Maximum drafts to return, up to 100.
    """
    try:
        drafts = _get_client().list_drafts(limit)
        return _ok({"count": len(drafts), "drafts": drafts})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def gmail_get_draft(draft_id: str) -> str:
    """One draft in full, including its body.

    Args:
        draft_id: Draft id from gmail_list_drafts.
    """
    try:
        return _ok({"draft": _get_client().get_draft(draft_id)})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_create_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    reply_to_message_id: str | None = None,
) -> str:
    """Write a draft. It waits in the mailbox until a person sends it.

    Passing reply_to_message_id threads the draft onto that message and sets
    the reply headers, so it appears in the conversation rather than as a new
    one.

    Args:
        to: Recipient address, or several separated by commas.
        subject: Subject line.
        body: Plain text body.
        cc: Carbon copy addresses.
        bcc: Blind carbon copy addresses.
        reply_to_message_id: Message this replies to.
    """
    try:
        return _ok(
            _get_client().create_draft(to, subject, body, cc, bcc, reply_to_message_id)
        )
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_WRITE)
def gmail_update_draft(
    draft_id: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> str:
    """Replace a draft's contents. Gmail rewrites the whole message, so pass
    every field, not only the changed one.

    Args:
        draft_id: Draft to replace.
        to: Recipient address.
        subject: Subject line.
        body: Plain text body.
        cc: Carbon copy addresses.
        bcc: Blind carbon copy addresses.
    """
    try:
        return _ok(_get_client().update_draft(draft_id, to, subject, body, cc, bcc))
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_DESTRUCTIVE)
def gmail_delete_draft(draft_id: str) -> str:
    """Discard a draft.

    Args:
        draft_id: Draft to delete.
    """
    try:
        return _ok(_get_client().delete_draft(draft_id))
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
def gmail_send_draft(draft_id: str) -> str:
    """Send an existing draft. This reaches other people and cannot be undone.

    Show the draft and get an explicit yes before calling this. Sending is not
    a step in a plan; it is the thing the person decides.

    Args:
        draft_id: Draft to send.
    """
    try:
        return _ok(_get_client().send_draft(draft_id))
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_DESTRUCTIVE)
def gmail_send_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> str:
    """Compose and send in one step. This reaches other people immediately and
    cannot be undone.

    Prefer gmail_create_draft, then gmail_send_draft once the text has been
    read and approved. Use this only when told to send outright.

    Args:
        to: Recipient address, or several separated by commas.
        subject: Subject line.
        body: Plain text body.
        cc: Carbon copy addresses.
        bcc: Blind carbon copy addresses.
    """
    try:
        return _ok(_get_client().send_message(to, subject, body, cc, bcc))
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------


def main() -> None:
    """Run over stdin/stdout, or over HTTP behind the login server."""
    import argparse

    parser = argparse.ArgumentParser(prog="gmail-mcp")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8460)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit(
            f"refusing to listen on {args.host}: this server has no login of "
            "its own. Keep it local and put auth-server.js in front of it."
        )

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    logger.info("Listening on http://%s:%d/mcp", args.host, args.port)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()

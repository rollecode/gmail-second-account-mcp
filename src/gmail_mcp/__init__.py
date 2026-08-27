"""MCP server for one Gmail mailbox."""

from .client import GmailClient, GmailError

__all__ = ["GmailClient", "GmailError"]

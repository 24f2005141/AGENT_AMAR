"""External-system adapters. Currently: Gmail message parsing."""

from app.services.gmail_service import (
    GmailFetchNotConfigured,
    GmailService,
    decode_base64url,
    extract_attachments,
    extract_html_body,
    extract_labels,
    extract_plain_text_body,
    get_header,
    internal_date_to_datetime,
    iter_parts,
    parse_address,
    parse_address_list,
)

__all__ = [
    "GmailFetchNotConfigured",
    "GmailService",
    "decode_base64url",
    "extract_attachments",
    "extract_html_body",
    "extract_labels",
    "extract_plain_text_body",
    "get_header",
    "internal_date_to_datetime",
    "iter_parts",
    "parse_address",
    "parse_address_list",
]

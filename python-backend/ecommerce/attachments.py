from __future__ import annotations

import os
from uuid import uuid4

from chatkit.store import AttachmentStore
from chatkit.types import (
    Attachment,
    AttachmentCreateParams,
    AttachmentUploadDescriptor,
    FileAttachment,
    ImageAttachment,
)

# Where the browser uploads/fetches attachment bytes. We point these at the UI
# origin (localhost:3000), which proxies /attachments/* to this backend (see
# ui/next.config.mjs). Keeping the browser same-origin is what lets ChatKit
# render the sent image's preview_url — an absolute :8000 URL is cross-origin and
# the component won't load it. There is no cloud blob store in this demo, so the
# two-phase upload target is our own FastAPI routes behind that proxy.
PUBLIC_BASE_URL = os.environ.get("ATTACHMENT_BASE_URL", "http://localhost:3000")


class InMemoryAttachmentStore(AttachmentStore[dict]):
    """Keeps attachment bytes in memory and serves them via /attachments routes.

    Mirrors MemoryStore's ephemeral nature (bytes vanish on restart), which is
    fine for a demo. respond() reads the bytes back by id to feed the vision step.
    """

    def __init__(self) -> None:
        self._bytes: dict[str, bytes] = {}
        self._mime: dict[str, str] = {}

    def generate_attachment_id(self, mime_type: str, context: dict) -> str:
        return f"atc_{uuid4().hex[:12]}"

    async def create_attachment(
        self, input: AttachmentCreateParams, context: dict
    ) -> Attachment:
        att_id = self.generate_attachment_id(input.mime_type, context)
        self._mime[att_id] = input.mime_type
        upload = AttachmentUploadDescriptor(
            url=f"{PUBLIC_BASE_URL}/attachments/{att_id}",
            method="PUT",
        )
        if input.mime_type.startswith("image/"):
            return ImageAttachment(
                id=att_id,
                name=input.name,
                mime_type=input.mime_type,
                upload_descriptor=upload,
                preview_url=f"{PUBLIC_BASE_URL}/attachments/{att_id}",
            )
        return FileAttachment(
            id=att_id,
            name=input.name,
            mime_type=input.mime_type,
            upload_descriptor=upload,
        )

    async def delete_attachment(self, attachment_id: str, context: dict) -> None:
        self._bytes.pop(attachment_id, None)
        self._mime.pop(attachment_id, None)

    # -- byte storage; called by the PUT/GET routes in main.py -----------
    def put_bytes(self, attachment_id: str, data: bytes) -> None:
        self._bytes[attachment_id] = data

    def get_bytes(self, attachment_id: str) -> tuple[bytes, str] | None:
        if attachment_id not in self._bytes:
            return None
        return self._bytes[attachment_id], self._mime.get(
            attachment_id, "application/octet-stream"
        )

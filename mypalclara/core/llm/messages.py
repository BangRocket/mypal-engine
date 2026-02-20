"""Typed message format for Clara's internal message pipeline.

Defines MyPalClara's own message types. Everything inside the pipeline
speaks list[Message]. Providers translate at their boundary.

  Adapter -> Protocol -> [Processor/Orchestrator/MemoryManager] -> Provider -> LLM SDK
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         All speak list[Message] (typed, neutral)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from clara_core.llm.tools.response import ToolCall


class ContentPartType(str, Enum):
    """Types of content parts in a multimodal message."""

    TEXT = "text"
    IMAGE_BASE64 = "image_base64"
    IMAGE_URL = "image_url"


@dataclass(frozen=True)
class ContentPart:
    """A single part of a multimodal user message.

    Attributes:
        type: The kind of content (text, base64 image, or URL image).
        text: Text content (when type is TEXT).
        media_type: MIME type for images, e.g. "image/png".
        data: Base64-encoded image data (when type is IMAGE_BASE64).
        url: HTTP URL for the image (when type is IMAGE_URL).
    """

    type: ContentPartType
    text: str | None = None
    media_type: str | None = None
    data: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict (OpenAI content part format)."""
        if self.type == ContentPartType.TEXT:
            return {"type": "text", "text": self.text or ""}
        elif self.type == ContentPartType.IMAGE_BASE64:
            data_url = f"data:{self.media_type or 'image/jpeg'};base64,{self.data or ''}"
            return {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        elif self.type == ContentPartType.IMAGE_URL:
            return {
                "type": "image_url",
                "image_url": {"url": self.url or ""},
            }
        return {"type": "text", "text": ""}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContentPart:
        """Create from an OpenAI content part dict."""
        part_type = d.get("type", "text")

        if part_type == "text":
            return cls(type=ContentPartType.TEXT, text=d.get("text", ""))

        if part_type == "image_url":
            image_url = d.get("image_url", {}).get("url", "")
            if image_url.startswith("data:"):
                try:
                    header, base64_data = image_url.split(",", 1)
                    media_type = header.split(":")[1].split(";")[0]
                    return cls(
                        type=ContentPartType.IMAGE_BASE64,
                        media_type=media_type,
                        data=base64_data,
                    )
                except (ValueError, IndexError):
                    pass
            return cls(type=ContentPartType.IMAGE_URL, url=image_url)

        return cls(type=ContentPartType.TEXT, text=str(d))


@dataclass
class SystemMessage:
    """A system-level instruction message.

    Attributes:
        content: The system prompt text.
    """

    content: str

    def to_dict(self) -> dict[str, str]:
        """Serialize to OpenAI-format dict."""
        return {"role": "system", "content": self.content}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SystemMessage):
            return NotImplemented
        return self.content == other.content


@dataclass
class UserMessage:
    """A user-sent message, optionally multimodal.

    For plain text, set content and leave parts empty.
    For multimodal (text + images), populate parts.
    When parts is non-empty, content is the text summary.

    Attributes:
        content: Text content of the message.
        parts: Multimodal content parts (for images, etc.).
    """

    content: str
    parts: list[ContentPart] = field(default_factory=list)

    @property
    def is_multimodal(self) -> bool:
        """Whether this message contains non-text content."""
        return any(p.type != ContentPartType.TEXT for p in self.parts)

    @property
    def text(self) -> str:
        """Extract text from parts, or return content if no parts."""
        if not self.parts:
            return self.content
        text_parts = [p.text for p in self.parts if p.type == ContentPartType.TEXT and p.text]
        return " ".join(text_parts) if text_parts else self.content

    def to_dict(self) -> dict[str, Any]:
        """Serialize to OpenAI-format dict."""
        if self.parts:
            return {
                "role": "user",
                "content": [p.to_dict() for p in self.parts],
            }
        return {"role": "user", "content": self.content}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserMessage):
            return NotImplemented
        return self.content == other.content and self.parts == other.parts


@dataclass
class AssistantMessage:
    """An assistant response, optionally with tool calls.

    Attributes:
        content: Text content (may be None if only tool calls).
        tool_calls: Tool calls made by the assistant.
    """

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to OpenAI-format dict."""
        result: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
        }
        if self.tool_calls:
            result["tool_calls"] = [tc.to_openai_format() for tc in self.tool_calls]
        return result

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AssistantMessage):
            return NotImplemented
        return self.content == other.content and self.tool_calls == other.tool_calls


@dataclass
class ToolResultMessage:
    """The result of executing a tool call.

    Attributes:
        tool_call_id: The ID of the tool call this is responding to.
        content: The tool's output text.
    """

    tool_call_id: str
    content: str

    def to_dict(self) -> dict[str, str]:
        """Serialize to OpenAI-format dict."""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolResultMessage):
            return NotImplemented
        return self.tool_call_id == other.tool_call_id and self.content == other.content


# Union type for all message types
Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


def message_from_dict(d: dict[str, Any]) -> Message:
    """Create a Message from an OpenAI-format dict.

    Dispatches on the 'role' key:
    - "system" -> SystemMessage
    - "user" -> UserMessage (handles multimodal content lists)
    - "assistant" -> AssistantMessage (handles tool_calls)
    - "tool" -> ToolResultMessage

    Args:
        d: OpenAI-format message dict with 'role' and 'content'.

    Returns:
        Typed Message instance.

    Raises:
        ValueError: If the role is not recognized.
    """
    role = d.get("role", "")

    if role == "system":
        return SystemMessage(content=d.get("content", ""))

    if role == "user":
        content = d.get("content", "")
        if isinstance(content, list):
            parts = [ContentPart.from_dict(p) for p in content]
            # Extract text for the content field
            text_parts = [p.text for p in parts if p.type == ContentPartType.TEXT and p.text]
            text = " ".join(text_parts) if text_parts else ""
            return UserMessage(content=text, parts=parts)
        return UserMessage(content=content if isinstance(content, str) else str(content))

    if role == "assistant":
        tool_calls = []
        if d.get("tool_calls"):
            for tc in d["tool_calls"]:
                tool_calls.append(ToolCall.from_openai(tc))
        return AssistantMessage(content=d.get("content"), tool_calls=tool_calls)

    if role == "tool":
        return ToolResultMessage(
            tool_call_id=d.get("tool_call_id", ""),
            content=d.get("content", ""),
        )

    raise ValueError(f"Unknown message role: {role!r}")


def messages_from_dicts(ds: list[dict[str, Any]]) -> list[Message]:
    """Convert a list of OpenAI-format dicts to typed Messages.

    Args:
        ds: List of OpenAI-format message dicts.

    Returns:
        List of typed Message instances.
    """
    return [message_from_dict(d) for d in ds]

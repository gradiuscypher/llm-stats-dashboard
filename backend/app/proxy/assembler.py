"""StreamAssembler — accumulates SSE deltas into a synthetic non-stream response.

This allows `on_response` plugins (like logging) to work identically for both
stream and non-stream paths — they never touch `on_stream_chunk`.
"""

from typing import Any


class StreamAssembler:
    """Accumulate SSE choice deltas, tool-call fragments, and usage into one dict.

    Produces a synthetic non-stream-shaped response that matches OpenRouter's
    non-stream response structure, suitable for logging.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.id: str | None = None
        self.created: int | None = None
        self.finish_reason: str | None = None
        self.usage: dict | None = None

        # Accumulated message content (text)
        self._content_parts: list[str] = []

        # Accumulated reasoning (text and structured)
        self._reasoning_parts: list[str] = []
        self._reasoning_details: list[dict] = []

        # Accumulated tool calls by index
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def feed(self, chunk: dict) -> None:
        """Feed one parsed SSE chunk into the assembler."""
        # Capture top-level fields
        if "id" in chunk and chunk["id"] is not None:
            self.id = chunk["id"]
        if "created" in chunk:
            self.created = chunk["created"]

        # Capture usage (often in final chunk)
        if "usage" in chunk and chunk["usage"]:
            self.usage = chunk["usage"]

        choices = chunk.get("choices", [])
        for choice in choices:
            if "finish_reason" in choice and choice["finish_reason"] is not None:
                self.finish_reason = choice["finish_reason"]

            delta = choice.get("delta", {})
            if not delta:
                continue

            # Text content
            content = delta.get("content")
            if content is not None and content != "":
                self._content_parts.append(content)

            # Reasoning text
            reasoning = delta.get("reasoning")
            if reasoning is not None and reasoning != "":
                self._reasoning_parts.append(reasoning)

            # Reasoning details (streamed as structured fragments)
            reasoning_details = delta.get("reasoning_details", [])
            for rd in reasoning_details:
                idx = rd.get("index", 0)
                if idx >= len(self._reasoning_details):
                    self._reasoning_details.extend(
                        [{} for _ in range(idx - len(self._reasoning_details) + 1)]
                    )
                existing = self._reasoning_details[idx]
                for key in ("type", "text", "summary", "signature"):
                    val = rd.get(key)
                    if val is not None and val != "":
                        if key == "text" and key in existing:
                            existing[key] += val
                        else:
                            existing[key] = val

            # Tool calls (streamed as fragments with index)
            tool_calls = delta.get("tool_calls", [])
            for tc in tool_calls:
                idx = tc.get("index", 0)
                if idx not in self._tool_calls:
                    self._tool_calls[idx] = {
                        "id": tc.get("id"),
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }

                existing = self._tool_calls[idx]
                if tc.get("id"):
                    existing["id"] = tc["id"]
                func = tc.get("function", {})
                if func.get("name"):
                    existing["function"]["name"] += func["name"]
                if func.get("arguments"):
                    existing["function"]["arguments"] += func["arguments"]

    def assemble(self) -> dict:
        """Build the synthetic non-stream response dict."""
        content: str | None = "".join(self._content_parts) if self._content_parts else None

        tool_calls: list[dict] | None = None
        if self._tool_calls:
            tool_calls = [self._tool_calls[i] for i in sorted(self._tool_calls)]

        reasoning_str: str | None = (
            "".join(self._reasoning_parts) if self._reasoning_parts else None
        )
        reasoning_details: list[dict] | None = (
            [d for d in self._reasoning_details if d]
            if self._reasoning_details
            else None
        )

        message: dict[str, Any] = {"role": "assistant"}
        if content is not None:
            message["content"] = content
        if reasoning_str is not None:
            message["reasoning"] = reasoning_str
        if reasoning_details:
            message["reasoning_details"] = reasoning_details
        if tool_calls:
            message["tool_calls"] = tool_calls

        choice: dict[str, Any] = {
            "index": 0,
            "message": message,
        }
        if self.finish_reason:
            choice["finish_reason"] = self.finish_reason

        response: dict[str, Any] = {
            "id": self.id or "",
            "object": "chat.completion",
            "created": self.created or 0,
            "model": self.model,
            "choices": [choice],
        }
        if self.usage:
            response["usage"] = self.usage

        return response

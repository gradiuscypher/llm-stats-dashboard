"""WordCountPlugin — appends a word-count marker to the last user message (request-only).

Implements the RequestTransform protocol — pure transform, no response hooks.
The interceptor computes the diff automatically.
"""

import logging
from typing import Any, cast

from app.proxy.interceptor import TransformContext

logger = logging.getLogger(__name__)


def _count_words(content: str | list | object) -> int:
    """Count words in message content, handling str and multimodal lists."""
    if isinstance(content, str):
        return len(content.split())
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                d = cast(dict[str, Any], part)
                text_val = d.get("text", "")
                if isinstance(text_val, str):
                    total += len(text_val.split())
        return total
    return 0


def _get_text_content(content: str | list | object) -> str:
    """Extract a text string from content, or serialize."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                d = cast(dict[str, Any], part)
                t_val = d.get("text")
                if isinstance(t_val, str):
                    texts.append(t_val)
        return " ".join(texts)
    return str(content)


class WordCountPlugin:
    """Appends ``\\n\\n[word_count: N]`` to the last user message."""

    name = "word_count"

    def transform_request(
        self, messages: list[dict], ctx: TransformContext
    ) -> list[dict]:
        """Find the last user message and append the word-count marker."""
        try:
            if not messages:
                return messages

            # Find the last message with role "user"
            target_idx = None
            target_content = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    target_idx = i
                    target_content = messages[i].get("content", "")
                    break

            if target_idx is None:
                return messages

            word_count = _count_words(target_content)
            marker = f"\n\n[word_count: {word_count}]"
            text = _get_text_content(target_content)
            messages[target_idx]["content"] = text + marker

            return messages
        except Exception:
            logger.exception("WordCountPlugin.transform_request failed")
            return messages

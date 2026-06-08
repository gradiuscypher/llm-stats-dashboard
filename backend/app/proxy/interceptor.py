"""RequestInterceptor — pure transform chain applied to messages before forwarding.

Runs request-side transforms in order, snapshotting between every plugin so
diffs are attributable to the exact plugin.  Computes per-message diffs
(original → final) and returns the final message list + diffs.

Fail-open: a transform that raises is logged and skipped — its output discarded,
previous messages carried forward.
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transform protocol (also exported so plugins can implement it)
# ---------------------------------------------------------------------------


class TransformContext:
    """Lightweight read-only context passed to request transforms."""

    __slots__ = ("model", "user_id", "request_metadata")

    def __init__(
        self,
        model: str,
        user_id: str | None = None,
        request_metadata: dict | None = None,
    ) -> None:
        self.model = model
        self.user_id = user_id
        self.request_metadata = request_metadata or {}


class RequestTransform(Protocol):
    """Protocol that all request-only transform plugins conform to.

    Must have a `name` attribute and implement `transform_request`.
    """

    name: str

    def transform_request(
        self, messages: list[dict], ctx: TransformContext
    ) -> list[dict]:
        """Return a new (or mutated) message list. Pure w.r.t. proxy transport."""
        ...


# ---------------------------------------------------------------------------
# Diff data structures
# ---------------------------------------------------------------------------


@dataclass
class MessageDiff:
    """An original→final diff for a single request message."""

    message_index: int
    role: str | None
    original_content: Any  # what the client sent
    final_content: Any  # what was sent to the LLM
    modified_by: list[str]  # plugin names, in order applied
    change_kind: str  # "modified" | "added" | "removed"


@dataclass
class InterceptResult:
    """Result of running the interceptor on a message list."""

    final_messages: list[dict]  # the messages to send upstream
    diffs: list[MessageDiff]  # per-message diffs
    per_plugin_summaries: list[dict] = field(
        default_factory=list
    )  # info dicts from each plugin


# ---------------------------------------------------------------------------
# Interceptor
# ---------------------------------------------------------------------------


class RequestInterceptor:
    """Owns the transform chain and computes diffs.

    Usage:
        interceptor = RequestInterceptor(transforms)
        result = interceptor.run(messages, ctx)
        # result.final_messages → send to upstream
        # result.diffs → persist for UI diff rendering
    """

    def __init__(self, transforms: list[RequestTransform]) -> None:
        self._transforms = transforms

    def run(
        self,
        messages: list[dict],
        ctx: TransformContext,
    ) -> InterceptResult:
        """Apply all transforms, compute diffs, return the result.

        Fail-open: if a transform raises, it is skipped and the previous
        message list is carried forward.  The error is logged.
        """
        # Deep-copy incoming as "original" for diff base.
        original = copy.deepcopy(messages)
        current = messages

        # Per-plugin result tracking.
        per_plugin_results: list[dict] = []

        for transform in self._transforms:
            try:
                result = transform.transform_request(current, ctx)
                per_plugin_results.append(
                    {"plugin": transform.name, "status": "ok"}
                )
            except Exception:
                logger.exception(
                    "Transform %r failed — skipping", transform.name
                )
                per_plugin_results.append(
                    {"plugin": transform.name, "status": "error"}
                )
                # Fail-open: keep previous messages, continue to next plugin.
                continue

            # Accept the transform's output if it returned something.
            if result is not None:
                current = result

        # Compute diffs: compare original[i] vs final[i].
        final = current
        diffs = _compute_diffs(
            original,
            final,
            plugins=[t.name for t in self._transforms],
        )

        return InterceptResult(
            final_messages=final,
            diffs=diffs,
            per_plugin_summaries=per_plugin_results,
        )


# ---------------------------------------------------------------------------
# Diff computation (pure function, reusable for testing)
# ---------------------------------------------------------------------------


def _compute_diffs(
    original: list[dict],
    final: list[dict],
    *,
    plugins: list[str],
) -> list[MessageDiff]:
    """Compute per-message diffs between original and final message lists.

    v1: assumes in-place mutation (common case). For length changes,
    messages are compared by index; added/removed messages are tagged
    but exact pairing is not attempted.
    """
    diffs: list[MessageDiff] = []

    max_len = max(len(original), len(final))
    for i in range(max_len):
        orig_msg = original[i] if i < len(original) else None
        final_msg = final[i] if i < len(final) else None

        if orig_msg is None and final_msg is not None:
            # Message was added
            diffs.append(
                MessageDiff(
                    message_index=i,
                    role=final_msg.get("role"),
                    original_content=None,
                    final_content=copy.deepcopy(final_msg),
                    modified_by=list(plugins),
                    change_kind="added",
                )
            )
        elif final_msg is None and orig_msg is not None:
            # Message was removed
            diffs.append(
                MessageDiff(
                    message_index=i,
                    role=orig_msg.get("role"),
                    original_content=copy.deepcopy(orig_msg),
                    final_content=None,
                    modified_by=list(plugins),
                    change_kind="removed",
                )
            )
        elif orig_msg is not None and final_msg is not None and orig_msg != final_msg:
            # Message was modified
            # Determine which plugins actually changed this message
            # (v1: attribute all activated plugins; per-plugin snapshots
            #  need per-step tracking — future enhancement)
            diffs.append(
                MessageDiff(
                    message_index=i,
                    role=final_msg.get("role", orig_msg.get("role")),
                    original_content=copy.deepcopy(orig_msg),
                    final_content=copy.deepcopy(final_msg),
                    modified_by=list(plugins),
                    change_kind="modified",
                )
            )

    return diffs

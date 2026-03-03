"""Route messages to multiple receivers based on predicates."""

import logging
from typing import Callable

logger = logging.getLogger(__name__)

Predicate = Callable[[bytes | str], bool]


class Router:
    """Routes messages to receivers based on predicate functions.

    Each receiver is paired with an optional predicate. If the predicate
    is ``None``, the receiver gets all messages. If the predicate returns
    ``True``, the receiver gets that message. A message can match
    multiple receivers.

    The Router itself has a ``feed()`` method, so it satisfies the
    ``Receiver`` protocol and can be nested inside another Router.

    Args:
        routes: Sequence of ``(receiver, predicate)`` pairs. The receiver
            can be any object with a ``feed(message)`` method.
        passthrough: Optional callback for messages that match no
            predicate-based route. Receivers with ``None`` predicate
            (which see all messages) do not count as a match for
            passthrough purposes.
    """

    def __init__(
        self,
        routes: list[tuple[object, Predicate | None]] | None = None,
        passthrough: Callable[[bytes | str], None] | None = None,
    ) -> None:
        self._routes: list[tuple[object, Predicate | None]] = list(routes) if routes else []
        self._passthrough = passthrough

    def add(self, receiver: object, predicate: Predicate | None = None) -> None:
        """Add a receiver with an optional predicate.

        Args:
            receiver: Any object with a ``feed(message)`` method.
            predicate: If ``None``, the receiver gets all messages.
                Otherwise, it gets messages where ``predicate(msg)``
                returns ``True``.
        """
        self._routes.append((receiver, predicate))

    def feed(self, message: bytes | str) -> None:
        """Route a message to matching receivers.

        Each route is evaluated in order. A message can match multiple
        receivers. If a predicate raises an exception, the error is
        logged and that route is skipped.

        Args:
            message: A message from the device.
        """
        matched_predicate = False

        for receiver, predicate in self._routes:
            if predicate is None:
                receiver.feed(message)  # type: ignore[union-attr]
            else:
                try:
                    if predicate(message):
                        matched_predicate = True
                        receiver.feed(message)  # type: ignore[union-attr]
                except Exception:
                    logger.warning(
                        "Predicate raised for %s, skipping",
                        type(receiver).__name__,
                        exc_info=True,
                    )

        if not matched_predicate and self._passthrough is not None:
            self._passthrough(message)

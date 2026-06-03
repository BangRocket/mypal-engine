"""mypal-protocol: the WebSocket wire contract shared by the engine and its clients.

Pure Pydantic message models, zero engine/db dependencies. In Phase 2 this package
is published from the mypal-engine repo and consumed by both sides.
"""

from mypal_protocol.messages import *  # noqa: F401,F403

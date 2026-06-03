"""Back-compat shim. The protocol now lives in the top-level `mypal_protocol` package.

Import from `mypal_protocol` directly in new code. This shim keeps existing
`mypalclara.gateway.protocol` imports working during the engine extraction.
"""

from mypal_protocol.messages import *  # noqa: F401,F403

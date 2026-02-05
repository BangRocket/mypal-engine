"""Clara Gateway - WebSocket gateway for platform adapters.

The gateway provides a central message processing hub that platform adapters
connect to via WebSocket. This allows:
- Platform-agnostic message processing
- Horizontal scaling of adapters
- Stateless reconnection
- Unified tool execution
- Event-driven hooks for automation
- Built-in task scheduler (cron/interval)
"""

from mypalclara.gateway.adapter_manager import (
    AdapterConfig,
    AdapterManager,
    AdapterProcess,
    AdapterState,
    RestartPolicy,
    get_adapter_manager,
)
from mypalclara.gateway.channel_summaries import (
    ChannelMessage,
    ChannelSummaryManager,
    get_summary_manager,
)
from mypalclara.gateway.daemon import (
    DEFAULT_ADAPTER_PIDFILE_PATTERN,
    DEFAULT_GATEWAY_PIDFILE,
    check_daemon_running,
    daemonize,
    get_adapter_pidfile,
    get_daemon_status,
    stop_daemon,
)
from mypalclara.gateway.events import (
    Event,
    EventEmitter,
    EventType,
    emit,
    get_event_emitter,
    off,
    on,
)
from mypalclara.gateway.hooks import (
    Hook,
    HookManager,
    HookResult,
    HookType,
    get_hook_manager,
    hook,
)
from mypalclara.gateway.llm_orchestrator import LLMOrchestrator
from mypalclara.gateway.processor import MessageProcessor
from mypalclara.gateway.protocol import (
    GatewayMessage,
    MessageRequest,
    MessageType,
    NodeInfo,
    RegisteredMessage,
    RegisterMessage,
    ResponseChunk,
    ResponseEnd,
    ResponseStart,
    ToolResult,
    ToolStart,
)
from mypalclara.gateway.router import MessageRouter
from mypalclara.gateway.scheduler import (
    CronParser,
    ScheduledTask,
    Scheduler,
    TaskResult,
    TaskType,
    get_scheduler,
    scheduled,
)
from mypalclara.gateway.server import GatewayServer
from mypalclara.gateway.session import NodeRegistry, SessionManager
from mypalclara.gateway.tool_executor import ToolExecutor

__all__ = [
    # Events
    "Event",
    "EventEmitter",
    "EventType",
    "emit",
    "get_event_emitter",
    "on",
    "off",
    # Hooks
    "Hook",
    "HookManager",
    "HookResult",
    "HookType",
    "get_hook_manager",
    "hook",
    # Scheduler
    "CronParser",
    "ScheduledTask",
    "Scheduler",
    "TaskResult",
    "TaskType",
    "get_scheduler",
    "scheduled",
    # Protocol
    "GatewayMessage",
    "MessageType",
    "RegisterMessage",
    "RegisteredMessage",
    "MessageRequest",
    "ResponseStart",
    "ResponseChunk",
    "ToolStart",
    "ToolResult",
    "ResponseEnd",
    "NodeInfo",
    # Server
    "GatewayServer",
    # Session
    "NodeRegistry",
    "SessionManager",
    # Router
    "MessageRouter",
    # Processor
    "MessageProcessor",
    # LLM
    "LLMOrchestrator",
    # Tools
    "ToolExecutor",
    # Channel Summaries
    "ChannelSummaryManager",
    "ChannelMessage",
    "get_summary_manager",
    # Daemon
    "daemonize",
    "stop_daemon",
    "get_daemon_status",
    "check_daemon_running",
    "get_adapter_pidfile",
    "DEFAULT_GATEWAY_PIDFILE",
    "DEFAULT_ADAPTER_PIDFILE_PATTERN",
    # Adapter Manager
    "AdapterConfig",
    "AdapterManager",
    "AdapterProcess",
    "AdapterState",
    "RestartPolicy",
    "get_adapter_manager",
]

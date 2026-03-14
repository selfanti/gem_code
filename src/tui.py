"""
Gem Code TUI - A textual-based terminal user interface
Inspired by OpenCode's interface design

Performance optimized version:
- RichLog for streaming display (ultra-fast)
- Batch updates with throttling
- Markdown rendering on completion
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Container
from textual.widgets import (
    Static,
    Button,
    Label,
    Markdown,
    Footer,
    Tree,
    Rule,
    RichLog,
    TextArea,
    Collapsible,
)
from textual.reactive import reactive
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from rich.syntax import Syntax
from rich.text import Text

from .config import Config, load_config
from .models import ContextUsageSnapshot
from .session_manager import SessionManager
from .tool import formatted_tool_output

# Performance tuning constants
BATCH_SIZE: Final[int] = 10          # Update UI every N characters
BATCH_INTERVAL: Final[float] = 0.02  # Or every 20ms
MAX_LOG_LINES: Final[int] = 3000     # Keep log size manageable


@dataclass
class ChatEntry:
    """Represents a single chat message"""
    role: str
    content: str
    timestamp: datetime
    is_tool_call: bool = False
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    reasoning_content: str | None = None  # 推理/思考内容


def _format_tool_args_for_display(args: dict | None) -> str:
    """Pretty-print tool arguments for the TUI.

    Tool calls are one of the highest-signal events in an agent UI, so the
    arguments should be easy to scan. We render dictionaries as stable,
    indented JSON rather than Python's default `repr`, which is harder to read
    and easier to wrap poorly in a narrow terminal.
    """

    if not args:
        return "{}"
    return json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True)


def _format_tool_result_for_display(result: str) -> tuple[str, str]:
    """Return `(text, lexer)` for a tool result block.

    We format the stored tool output once, then attempt lightweight syntax
    detection. JSON results become formatted JSON, while everything else falls
    back to plain text so shell output still reads cleanly.
    """

    cleaned = formatted_tool_output(result)
    stripped = cleaned.strip()

    if stripped:
        try:
            parsed = json.loads(stripped)
            return (
                json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True),
                "json",
            )
        except Exception:
            pass

    return cleaned, "text"


class ThinkingIndicator(Static):
    """Animated thinking indicator"""
    
    DEFAULT_CSS = """
    ThinkingIndicator {
        height: 1;
        background: $primary-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
        display: none;
    }
    
    ThinkingIndicator.visible {
        display: block;
    }
    """
    
    dots = reactive(0)
    
    def on_mount(self) -> None:
        self.set_interval(0.5, self._animate)
    
    def _animate(self) -> None:
        self.dots = (self.dots + 1) % 4
        self.update(f"🤔 Thinking{'.' * self.dots}")


class OptimizedStreamingWidget(Static):
    """
    High-performance streaming message widget.
    Uses RichLog for streaming (fast) then converts to Markdown (pretty).
    """
    
    DEFAULT_CSS = """
    OptimizedStreamingWidget {
        width: 100%;
        height: auto;
        padding: 0 2 1 2;
        margin: 0 0 1 0;
        border-left: solid $success;
    }
    
    OptimizedStreamingWidget .header-row {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }
    
    OptimizedStreamingWidget .avatar {
        width: 3;
        content-align: center middle;
    }
    
    OptimizedStreamingWidget .header {
        width: auto;
        text-style: bold;
        color: $success;
        content-align: left middle;
    }
    
    OptimizedStreamingWidget .timestamp {
        width: auto;
        color: $text-muted;
        text-style: italic;
        content-align: right middle;
    }
    
    OptimizedStreamingWidget .content-container {
        width: 100%;
        height: auto;
        padding-left: 4;
    }
    
    OptimizedStreamingWidget RichLog {
        width: 100%;
        height: auto;
        background: transparent;
        border: none;
        padding: 0;
    }
    
    OptimizedStreamingWidget RichLog:focus {
        border: none;
    }
    
    OptimizedStreamingWidget .content-container Markdown {
        background: transparent;
        padding: 0;
    }
    """
    
    def __init__(self, **kwargs):
        self.timestamp = datetime.now()
        self._content = ""
        self._buffer = ""  # Buffer for batch updates
        self._last_update = 0.0
        super().__init__(**kwargs)
    
    def compose(self) -> ComposeResult:
        time_str = self.timestamp.strftime("%H:%M:%S")
        
        with Horizontal(classes="header-row"):
            yield Label("🤖", classes="avatar")
            yield Label("GEM", classes="header")
            yield Label(time_str, classes="timestamp")
        
        with Container(classes="content-container"):
            # Use RichLog for high-performance streaming
            # markup=False to avoid parsing [] as Rich markup (fixes MarkupError with code)
            self._log = RichLog(highlight=True, markup=False, auto_scroll=True)
            self._log.max_lines = MAX_LOG_LINES
            yield self._log
    
    def append_text(self, text: str) -> None:
        """
        Append text with batching for performance.
        Call flush() to force immediate update.
        """
        if not self.is_mounted:
            return
        self._buffer += text
        now = time.monotonic()
        
        # Batch by size or time
        buffer_len = len(self._buffer)
        time_since_update = now - self._last_update
        
        if buffer_len >= BATCH_SIZE or time_since_update >= BATCH_INTERVAL:
            self.flush()
    
    def flush(self) -> None:
        """Force immediate update of buffered content"""
        if not self.is_mounted or not self._log.is_mounted:
            return
        if self._buffer:
            try:
                self._content += self._buffer
                self._log.write(self._buffer)
                self._buffer = ""
                self._last_update = time.monotonic()
            except Exception:
                pass  # Log may have been removed
    
    def finalize(self) -> None:
        """
        Convert RichLog to Markdown for better formatting.
        This is called when streaming is complete.
        """
        try:
            self.flush()
            
            # Get the container
            container = self.query_one(".content-container", Container)
            
            # Remove RichLog
            if self._log and self._log.is_mounted:
                self._log.remove()
            
            # Add Markdown for final rendering
            container.mount(Markdown(self._content or "", classes="content"))
        except Exception as e:
            # Log error but don't crash
            print(f"Error finalizing streaming widget: {e}")


class ChatMessageWidget(Static):
    """Widget to display a completed chat message with Markdown support"""
    
    DEFAULT_CSS = """
    ChatMessageWidget {
        width: 100%;
        height: auto;
        padding: 0 2 1 2;
        margin: 0 0 1 0;
    }
    
    ChatMessageWidget.user {
        border-left: solid $primary;
    }
    
    ChatMessageWidget.assistant {
        border-left: solid $success;
    }
    
    ChatMessageWidget.tool {
        border-left: solid $warning;
    }
    
    ChatMessageWidget .header-row {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }
    
    ChatMessageWidget .avatar {
        width: 3;
        content-align: center middle;
    }
    
    ChatMessageWidget .header {
        width: auto;
        text-style: bold;
        content-align: left middle;
    }
    
    ChatMessageWidget.user .header {
        color: $primary;
    }
    
    ChatMessageWidget.assistant .header {
        color: $success;
    }
    
    ChatMessageWidget.tool .header {
        color: $warning;
    }
    
    ChatMessageWidget .timestamp {
        width: auto;
        color: $text-muted;
        text-style: italic;
        content-align: right middle;
    }
    
    ChatMessageWidget .content {
        width: 100%;
        height: auto;
        padding-left: 4;
    }
    
    ChatMessageWidget .content Markdown {
        background: transparent;
        padding: 0;
    }
    
    ChatMessageWidget .content CodeBlock {
        background: $surface-darken-2;
        border: solid $primary-darken-3;
    }
    
    ChatMessageWidget .tool-result {
        margin-top: 1;
        padding: 1;
        background: $surface-darken-2;
        border: solid $warning-darken-2;
        color: $text-muted;
    }

    ChatMessageWidget .tool-summary {
        width: 100%;
        height: auto;
        margin-left: 4;
        margin-bottom: 1;
        color: $text-muted;
        text-style: italic;
    }

    ChatMessageWidget .tool-block-title {
        width: 100%;
        height: auto;
        margin-left: 4;
        margin-top: 1;
        color: $warning;
        text-style: bold;
    }

    ChatMessageWidget .tool-code-block {
        width: 100%;
        height: auto;
        margin-left: 4;
        padding: 1;
        background: $surface-darken-2;
        border: solid $warning-darken-2;
    }
    
    ChatMessageWidget .reasoning-collapsible {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding-left: 4;
    }
    
    ChatMessageWidget .reasoning-collapsible CollapsibleTitle {
        color: $text-muted;
        text-style: italic;
    }
    
    ChatMessageWidget .reasoning-collapsible .reasoning-content {
        background: $surface-darken-2;
        padding: 1;
        color: $text-muted;
    }
    
    ChatMessageWidget .reasoning-collapsible .reasoning-content Markdown {
        background: transparent;
    }
    """
    
    def __init__(self, entry: ChatEntry, **kwargs):
        self.entry = entry
        super().__init__(**kwargs)
        self.add_class(entry.role)
    
    def compose(self) -> ComposeResult:
        time_str = self.entry.timestamp.strftime("%H:%M:%S")
        
        # Determine avatar and header text
        if self.entry.is_tool_call and self.entry.tool_name:
            avatar = "🔧"
            header_text = self.entry.tool_name.upper()
        elif self.entry.role == "user":
            avatar = "👤"
            header_text = "YOU"
        elif self.entry.role == "assistant":
            avatar = "🤖"
            header_text = "GEM"
        else:
            avatar = "💬"
            header_text = self.entry.role.upper()
        
        # Header row
        with Horizontal(classes="header-row"):
            yield Label(avatar, classes="avatar")
            yield Label(Text(header_text), classes="header")
            yield Label(time_str, classes="timestamp")
        
        # 推理内容 - 使用 Collapsible 折叠显示（仅 assistant 角色）
        if self.entry.role == "assistant" and self.entry.reasoning_content:
            with Collapsible(title="🤔 Thinking...", collapsed=True, classes="reasoning-collapsible"):
                yield Markdown(self.entry.reasoning_content, classes="reasoning-content")

        if self.entry.is_tool_call:
            # Tool call messages are rendered as structured panels instead of a
            # single Markdown blob. This makes it much easier to distinguish the
            # human-readable summary, the exact arguments, and the returned
            # output while the agent is chaining several tools in a row.
            if self.entry.content:
                yield Static(Text(self.entry.content), classes="tool-summary")

            if self.entry.tool_args is not None:
                yield Static("Arguments", classes="tool-block-title")
                yield Static(
                    Syntax(
                        _format_tool_args_for_display(self.entry.tool_args),
                        "json",
                        word_wrap=True,
                        line_numbers=False,
                        indent_guides=True,
                        theme="monokai",
                    ),
                    classes="tool-code-block",
                )

            if self.entry.tool_result:
                result_text, lexer = _format_tool_result_for_display(self.entry.tool_result)
                yield Static("Result", classes="tool-block-title")
                yield Static(
                    Syntax(
                        result_text,
                        lexer,
                        word_wrap=True,
                        line_numbers=False,
                        indent_guides=lexer == "json",
                        theme="monokai",
                    ),
                    classes="tool-code-block",
                )
            return

        # Message content with Markdown
        content = self.entry.content or ""
        yield Markdown(content, classes="content")


class ChatArea(VerticalScroll):
    """Optimized scrollable chat display area"""
    
    DEFAULT_CSS = """
    ChatArea {
        width: 100%;
        height: 1fr;
        padding: 1 0;
        border: none;
        background: $surface;
    }
    """
    
    _current_streaming: OptimizedStreamingWidget | None = None
    
    def add_message(self, entry: ChatEntry) -> ChatMessageWidget:
        """Add a new message to the chat"""
        widget = ChatMessageWidget(entry)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget
    
    def start_streaming(self) -> OptimizedStreamingWidget:
        """Start a new streaming message"""
        self._current_streaming = OptimizedStreamingWidget()
        self.mount(self._current_streaming)
        self.scroll_end(animate=False)
        return self._current_streaming
    
    def finish_streaming(self) -> None:
        """Mark current streaming message as complete and convert to Markdown"""
        if self._current_streaming:
            self._current_streaming.finalize()
            self._current_streaming = None
        self.scroll_end(animate=False)
    
    def append_streaming(self, text: str) -> None:
        """Append text to current streaming widget"""
        if self._current_streaming and self._current_streaming.is_mounted:
            try:
                self._current_streaming.append_text(text)
            except Exception:
                pass  # Widget may have been removed
    
    def flush_streaming(self) -> None:
        """Force flush the streaming buffer"""
        if self._current_streaming and self._current_streaming.is_mounted:
            try:
                self._current_streaming.flush()
            except Exception:
                pass  # Widget may have been removed
    
    def clear(self) -> None:
        """Clear all messages"""
        for child in list(self.children):
            child.remove()
        self._current_streaming = None


class ResponseMessage(Message):
    """Message for streaming response updates"""
    def __init__(self, chunk: str | None = None, done: bool = False, error: str | None = None) -> None:
        self.chunk = chunk  # None means just a flush request
        self.done = done
        self.error = error
        super().__init__()


class ToolStartMessage(Message):
    """Message for tool call start"""
    def __init__(self, tool_name: str, args: dict) -> None:
        self.tool_name = tool_name
        self.args = args
        super().__init__()


class ToolResultMessage(Message):
    """Message for tool call result"""
    def __init__(self, tool_name: str, result: str) -> None:
        self.tool_name = tool_name
        self.result = result
        super().__init__()


class InputArea(Container):
    """Input area with text input and action buttons"""
    
    DEFAULT_CSS = """
    InputArea {
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0 2 1 2;
        background: $surface-darken-1;
        border-top: solid $primary-darken-2;
    }
    
    InputArea #input-row {
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center top;
    }
    
    InputArea TextArea {
        width: 1fr;
        height: 3;
        min-height: 3;
        max-height: 10;
        margin-right: 1;
        border: solid $primary-darken-2;
        background: $surface;
    }
    
    InputArea TextArea:focus {
        border: solid $primary;
    }
    
    InputArea Button {
        width: auto;
        min-width: 8;
    }
    
    InputArea #clear-btn {
        background: $error-darken-2;
    }
    
    InputArea .button-col {
        width: auto;
        height: auto;
    }
    
    InputArea .button-col Button {
        width: auto;
        min-width: 10;
        margin-bottom: 1;
    }
    
    InputArea .hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
        text-align: center;
    }
    """
    
    class Submitted(Message):
        """Message sent when user submits input"""
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()
    
    class ClearHistory(Message):
        """Message sent when user wants to clear history"""
        pass
    
    def compose(self) -> ComposeResult:
        with Horizontal(id="input-row"):
            text_area = TextArea(
                id="message-input",
                show_line_numbers=False,
                soft_wrap=True,
                tab_behavior="indent",
            )
            text_area.cursor_blink = False
            yield text_area
            
            with Vertical(classes="button-col"):
                yield Button("Send ⏎", id="send-btn", variant="primary")
                yield Button("Clear", id="clear-btn", variant="error")
        
        yield Label(
            "[Ctrl+C] Quit  [Ctrl+L] Clear  [Send] Click button to send  [?] Help",
            classes="hint"
        )
    
    def on_mount(self) -> None:
        text_area = self.query_one("#message-input", TextArea)
        text_area.focus()
        # Set initial height
        text_area.styles.height = 3
    
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Auto-resize textarea based on content"""
        text_area = event.text_area
        lines = text_area.text.count('\n') + 1
        # Height between 3 and 10 lines
        new_height = min(max(lines, 3), 10)
        text_area.styles.height = new_height
    
    def _send_message(self) -> None:
        """Send the current message"""
        text_area = self.query_one("#message-input", TextArea)
        value = text_area.text.strip()
        if value:
            self.post_message(self.Submitted(value))
            text_area.text = ""
            text_area.styles.height = 3  # Reset height
            text_area.focus()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks"""
        if event.button.id == "send-btn":
            self._send_message()
        elif event.button.id == "clear-btn":
            self.post_message(self.ClearHistory())
    
    def set_loading(self, loading: bool) -> None:
        """Show/hide loading state"""
        btn = self.query_one("#send-btn", Button)
        btn.disabled = loading
        btn.label = "Wait..." if loading else "Send ⏎"
        
        text_area = self.query_one("#message-input", TextArea)
        text_area.disabled = loading
        if not loading:
            text_area.focus()


class Sidebar(Container):
    """Sidebar with model info and file tree"""
    
    DEFAULT_CSS = """
    Sidebar {
        width: 32;
        height: 100%;
        background: $surface-darken-2;
        border-right: solid $primary-darken-2;
        padding: 1;
    }
    
    Sidebar .logo {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
        padding-bottom: 1;
        border-bottom: solid $primary-darken-2;
    }
    
    Sidebar .section {
        margin-bottom: 2;
        padding: 0 1;
    }
    
    Sidebar .section-title {
        color: $text-muted;
        text-style: bold;
        margin-bottom: 1;
    }
    
    Sidebar .info-row {
        margin-bottom: 1;
    }
    
    Sidebar .info-label {
        color: $text-muted;
        text-style: italic;
    }
    
    Sidebar .info-value {
        color: $text;
        text-style: bold;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    
    Sidebar Tree {
        background: transparent;
        border: none;
        height: 1fr;
        padding: 0;
    }
    
    Sidebar Tree > .tree--cursor {
        background: $primary-darken-2;
    }
    
    Sidebar .stats {
        margin-top: 1;
        padding-top: 1;
        border-top: solid $primary-darken-3;
    }
    """
    
    def __init__(self, config: Config, **kwargs):
        self.config = config
        self.context_label = None
        self.context_detail_label = None
        super().__init__(**kwargs)
    
    def update_context_usage(self, snapshot: ContextUsageSnapshot) -> None:
        """Update the sidebar with the latest context estimate or server usage."""

        if self.context_label:
            percentage = snapshot.percentage
            if percentage < 60:
                color = "green"
            elif percentage < 80:
                color = "yellow"
            else:
                color = "red"

            if snapshot.used_tokens >= 1000:
                used_str = f"{snapshot.used_tokens/1000:.1f}K"
            else:
                used_str = str(snapshot.used_tokens)

            source_label = "live" if snapshot.source == "server" else "est"
            text = Text(
                f"{used_str} / {snapshot.max_tokens//1000}K ({percentage:.1f}%) [{source_label}]"
            )
            text.stylize(color)
            self.context_label.update(text)

        if self.context_detail_label:
            # Expose the estimate breakdown so users can understand whether the
            # rising number comes from prompt growth, streamed output, or the
            # static tool schemas attached to every request.
            detail = Text(
                "in "
                f"{snapshot.estimated_input_tokens/1000:.1f}K + out "
                f"{snapshot.estimated_output_tokens/1000:.1f}K + tools "
                f"{snapshot.tool_schema_tokens/1000:.1f}K"
            )
            detail.stylize("dim")
            self.context_detail_label.update(detail)
    
    def compose(self) -> ComposeResult:
        # Logo
        yield Label("⚡ Gem Code", classes="logo")
        
        # Model info
        with Vertical(classes="section"):
            yield Label("MODEL", classes="section-title")
            yield Label(Text(self.config.model), classes="info-value")
            
            with Vertical(classes="info-row"):
                yield Label("API:", classes="info-label")
                domain = self.config.base_url.replace("https://", "").replace("http://", "").split("/")[0]
                yield Label(Text(domain[:25]), classes="info-value")
        
        # Workdir info
        with Vertical(classes="section"):
            yield Label("WORKSPACE", classes="section-title")
            
            workdir = self.config.workdir
            if len(workdir) > 28:
                workdir = "..." + workdir[-25:]
            yield Label(Text(workdir), classes="info-value")

        with Vertical(classes="section"):
            yield Label("SECURITY", classes="section-title")
            sandbox_state = "on" if self.config.security.enabled else "off"
            yield Label(Text(f"sandbox: {sandbox_state}"), classes="info-value")
            yield Label(
                Text(f"network: {self.config.security.network_summary()}"),
                classes="info-value",
            )
        
        # Context usage info
        with Vertical(classes="section"):
            yield Label("CONTEXT", classes="section-title")
            self.context_label = Label(Text("0 / 200K (0%) [est]"), classes="info-value")
            yield self.context_label
            self.context_detail_label = Label(
                Text("in 0.0K + out 0.0K + tools 0.0K"),
                classes="info-value",
            )
            yield self.context_detail_label
        
        # File tree
        with Vertical(classes="section"):
            yield Label("FILES", classes="section-title")
            tree: Tree[dict] = Tree("📁 " + self._get_dir_name(self.config.workdir))
            tree.root.expand()
            yield tree
            self._populate_tree(tree)
    
    def _get_dir_name(self, path: str) -> str:
        """Get directory name from path"""
        import os
        name = os.path.basename(path) or path
        return name[:25] + "..." if len(name) > 28 else name
    
    def _populate_tree(self, tree: Tree) -> None:
        """Populate file tree with working directory contents"""
        import os
        
        # Expand ~ to full path
        workdir = os.path.expanduser(self.config.workdir)
        
        def add_directory(node, path: str, depth: int = 0) -> None:
            if depth > 2:
                return
            try:
                entries = sorted(os.listdir(path))
                dirs = []
                files = []
                
                for entry in entries:
                    if entry.startswith('.') or entry in ['node_modules', '__pycache__', '.venv', 'venv']:
                        continue
                    full_path = os.path.join(path, entry)
                    try:
                        if os.path.isdir(full_path):
                            dirs.append((entry, full_path))
                        else:
                            files.append(entry)
                    except (PermissionError, OSError):
                        pass
                
                for entry, full_path in dirs[:10]:
                    dir_node = node.add(f"📁 {entry}", expand=False)
                    add_directory(dir_node, full_path, depth + 1)
                
                if len(dirs) > 10:
                    node.add(f"... and {len(dirs) - 10} more folders")
                
                for entry in files[:20]:
                    node.add_leaf(f"📄 {entry}")
                
                if len(files) > 20:
                    node.add_leaf(f"... and {len(files) - 20} more files")
                    
            except (PermissionError, OSError):
                pass
        
        add_directory(tree.root, workdir)


class StatusBar(Static):
    """Status bar showing current state"""
    
    DEFAULT_CSS = """
    StatusBar {
        width: 100%;
        height: 1;
        background: $primary-darken-3;
        color: $text;
        content-align: left middle;
        padding: 0 2;
    }
    """
    
    status = reactive("Ready")
    
    def watch_status(self, status: str) -> None:
        self.update(Text(f" ♦ {status}"))


class HelpScreen(ModalScreen):
    """Help modal screen"""
    
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    
    HelpScreen > Container {
        width: 60;
        height: auto;
        max-height: 40;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    
    HelpScreen .title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    
    HelpScreen .key-row {
        height: auto;
        margin-bottom: 1;
    }
    
    HelpScreen .key {
        width: 20;
        text-style: bold;
        color: $text-accent;
    }
    
    HelpScreen .desc {
        width: 1fr;
    }
    
    HelpScreen Button {
        width: 100%;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape,space,q", "dismiss", "Close"),
    ]
    
    def compose(self) -> ComposeResult:
        with Container():
            yield Label("⌨️  Keyboard Shortcuts", classes="title")
            yield Rule()
            
            shortcuts = [
                ("Enter", "Insert new line"),
                ("Ctrl+Enter", "Send message"),
                ("Ctrl+C", "Quit application"),
                ("Ctrl+L", "Clear chat history"),
                ("Ctrl+S", "Toggle sidebar"),
                ("Escape", "Focus input"),
                ("?", "Show this help"),
            ]
            
            for key, desc in shortcuts:
                with Horizontal(classes="key-row"):
                    yield Label(key, classes="key")
                    yield Label(desc, classes="desc")
            
            yield Button("Close [ESC]", id="close-btn", variant="primary")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()


class GemCodeApp(App):
    """Main TUI application for Gem Code - Performance Optimized"""
    
    CSS = """
    Screen {
        align: center middle;
        background: $surface-darken-1;
    }
    
    #main-container {
        width: 100%;
        height: 100%;
    }
    
    #content-area {
        width: 1fr;
        height: 100%;
    }
    
    #chat-wrapper {
        width: 100%;
        height: 1fr;
        overflow: hidden;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=False),
        Binding("escape", "escape", "Cancel", show=False),
        Binding("question_mark", "help", "Help", show=True),
    ]
    
    def __init__(self, config: Config):
        self.config = config
        self.session_manager: SessionManager | None = None
        self._is_generating = False
        self._sidebar_visible = True
        self._current_tool_widget = None  # Track current tool widget for updates
        super().__init__()
    
    async def on_mount(self) -> None:
        """Initialize session state and surface startup failures clearly.

        Textual startup exceptions can be easy to miss because the app switches
        to an alternate screen. We therefore update the status bar immediately,
        keep the exception text visible via `notify()`, and then re-raise so the
        CLI wrapper can print a plain error message as well.
        """

        try:
            self.session_manager = SessionManager(self.config)
            await self.session_manager.init()
            self.query_one(StatusBar).status = f"Ready • {self.config.model}"
            # Poll frequently so the context meter visibly changes during
            # streaming even before the provider sends a final `usage` block.
            self.set_interval(0.25, self._update_context_display)
            self._update_context_display()
        except Exception as exc:
            self.query_one(StatusBar).status = "Startup error"
            self.notify(
                f"TUI startup failed: {exc}",
                title="Startup Error",
                severity="error",
            )
            raise
    
    def _update_context_display(self) -> None:
        """Update context usage display in sidebar and status bar"""
        if self.session_manager and self.session_manager.session:
            snapshot = self.session_manager.session.get_context_usage_snapshot()
            sidebar = self.query_one("#sidebar", Sidebar)
            sidebar.update_context_usage(snapshot)

            status_bar = self.query_one(StatusBar)
            phase = "Generating" if self._is_generating else "Ready"
            marker = "~" if snapshot.source == "estimated" else ""
            status_bar.status = (
                f"{phase} • {self.config.model} • Context: "
                f"{marker}{snapshot.percentage:.1f}%"
            )
    
    def compose(self) -> ComposeResult:
        with Horizontal(id="main-container"):
            # Sidebar
            yield Sidebar(self.config, id="sidebar")
            
            # Main content area
            with Vertical(id="content-area"):
                # Chat wrapper with chat area and thinking indicator
                with Vertical(id="chat-wrapper"):
                    yield ChatArea(id="chat-area")
                    yield ThinkingIndicator(id="thinking-indicator")
                
                # Status bar
                yield StatusBar(id="status-bar")
                
                # Input area
                yield InputArea(id="input-area")
        
        yield Footer()
    
    async def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        """Handle user message submission"""
        if not self.session_manager or self._is_generating:
            return
        
        user_message = event.value.strip()
        if not user_message:
            return
        
        chat_area = self.query_one("#chat-area", ChatArea)
        
        # Add user message
        chat_area.add_message(ChatEntry(
            role="user",
            content=user_message,
            timestamp=datetime.now()
        ))
        
        # Set loading state
        self._is_generating = True
        self.query_one("#input-area", InputArea).set_loading(True)
        self.query_one("#thinking-indicator", ThinkingIndicator).add_class("visible")
        self.query_one(StatusBar).status = "Generating..."
        
        # Start streaming message
        streaming_widget = chat_area.start_streaming()
        
        # Generate response
        asyncio.create_task(self._generate_response(user_message))
    
    async def _generate_response(self, user_message: str) -> None:
        """Generate response with optimized streaming"""
        chat_area = self.query_one("#chat-area", ChatArea)
        
        # 每轮的状态（每次 API 调用会重置）
        turn_state = {
            'content': "",
            'reasoning': "",
            'pending_messages': 0,
        }
        
        try:
            def on_reasoning(chunk: str) -> None:
                """Handle reasoning content (thinking process)"""
                turn_state['reasoning'] += chunk
                self._update_context_display()
            
            def on_content(chunk: str) -> None:
                """Handle formal content output with batching"""
                turn_state['content'] += chunk
                turn_state['pending_messages'] += 1
                
                # 直接更新 streaming widget
                if chat_area._current_streaming:
                    try:
                        chat_area.append_streaming(chunk)
                    except Exception as e:
                        # Widget may have been removed
                        print(f"Error appending streaming content: {e}")
                self._update_context_display()
            
            def on_turn_end(content: str, reasoning: str, has_more: bool) -> None:
                """每次 API 调用结束时调用"""
                # 完成当前 streaming widget，转换为 ChatMessageWidget
                if chat_area._current_streaming:
                    try:
                        chat_area.flush_streaming()
                        if chat_area._current_streaming.is_mounted:
                            chat_area._current_streaming.remove()
                    except Exception:
                        pass  # Widget may already be removed
                    finally:
                        chat_area._current_streaming = None
                
                # 创建最终的 assistant 消息（包含 content 和 reasoning）
                entry = ChatEntry(
                    role="assistant",
                    content=content,
                    timestamp=datetime.now(),
                    reasoning_content=reasoning if reasoning else None
                )
                chat_area.add_message(entry)
                
                # 如果有 tool 调用，显示 "Thinking..." 继续下一轮
                if has_more:
                    self.query_one(StatusBar).status = "Processing tools..."
                    # 重置状态准备下一轮
                    turn_state['content'] = ""
                    turn_state['reasoning'] = ""
                    # 创建新的 streaming widget 给下一轮使用
                    chat_area.start_streaming()
                else:
                    # 全部完成
                    self._is_generating = False
                    self.query_one("#input-area", InputArea).set_loading(False)
                    self.query_one("#thinking-indicator", ThinkingIndicator).remove_class("visible")
                    # Update context display
                    self._update_context_display()
            
            def on_tool_start(tool_name: str, args: dict) -> None:
                """Handle tool call start"""
                self.post_message(ToolStartMessage(tool_name, args))
                self._update_context_display()
            
            def on_tool_result(tool_name: str, result: str) -> None:
                """Handle tool call result"""
                self.post_message(ToolResultMessage(tool_name, result))
                self._update_context_display()
            
            # Run the chat
            await self.session_manager.session.chat(         #type: ignore
                user_message,
                on_reasoning=on_reasoning,
                on_content=on_content,
                on_turn_end=on_turn_end,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result
            ) 
            
        except Exception as e:
            # Replace [ with \[ to prevent Rich markup parsing
            error_msg = f"\n\n❌ Error: {str(e)}".replace("[", r"\[")
            if chat_area._current_streaming:
                chat_area.append_streaming(error_msg)
                chat_area.flush_streaming()
            self._is_generating = False
            self.query_one("#input-area", InputArea).set_loading(False)
            self.query_one("#thinking-indicator", ThinkingIndicator).remove_class("visible")
            self.query_one(StatusBar).status = "Error"
    
    def on_response_message(self, message: ResponseMessage) -> None:
        """Handle response message - runs in main thread (for error display only)"""
        # Note: 正常的流式更新现在直接在 on_content 回调中处理
        # 这个 handler 保留用于显示错误信息
        if message.error:
            chat_area = self.query_one("#chat-area", ChatArea)
            if chat_area._current_streaming:
                # Replace [ with \[ to prevent Rich markup parsing
                error_msg = f"\n\n❌ Error: {message.error}".replace("[", r"\[")
                chat_area.append_streaming(error_msg)
                chat_area.flush_streaming()
    
    def on_tool_start_message(self, message: ToolStartMessage) -> None:
        """Handle tool call start - display tool call in chat"""
        chat_area = self.query_one("#chat-area", ChatArea)

        # Keep the summary short because the detailed arguments are rendered in
        # a dedicated syntax-highlighted block by `ChatMessageWidget`.
        arg_count = len(message.args) if message.args else 0
        content = f"Dispatching structured tool call with {arg_count} argument field(s)."

        entry = ChatEntry(
            role="tool",
            content=content,
            timestamp=datetime.now(),
            is_tool_call=True,
            tool_name=message.tool_name,
            tool_args=message.args,
            tool_result=None  # Will be updated when result arrives
        )
        
        self._current_tool_widget = chat_area.add_message(entry)
        
        # Show thinking indicator
        self.query_one("#thinking-indicator", ThinkingIndicator).add_class("visible")
        self.query_one(StatusBar).status = f"Running {message.tool_name}..."
    
    def on_tool_result_message(self, message: ToolResultMessage) -> None:
        """Handle tool call result - update tool widget with result"""
        chat_area = self.query_one("#chat-area", ChatArea)
        
        # Update the existing tool widget if we have one
        if self._current_tool_widget:
            try:
                # Save entry reference before removing widget
                entry = self._current_tool_widget.entry
                # Update the entry with result
                entry.tool_result = message.result
                
                # Refresh the widget to show result
                # Remove and re-add to refresh content
                if self._current_tool_widget.is_mounted:
                    self._current_tool_widget.remove()
                new_widget = ChatMessageWidget(entry)
                chat_area.mount(new_widget)
                chat_area.scroll_end(animate=False)
            except Exception as e:
                # Log error but don't crash
                print(f"Error updating tool widget: {e}")
            finally:
                self._current_tool_widget = None
        
        self.query_one(StatusBar).status = "Processing..."
        # Update context display after tool result
        self._update_context_display()
    
    def on_input_area_clear_history(self) -> None:
        """Handle clear history request"""
        self.action_clear()
    
    async def action_quit(self) -> None:
        """Quit application with cleanup"""
        if self.session_manager and self.session_manager.session:
            try:
                await self.session_manager.session.cleanup()
            except Exception:
                pass  # 忽略清理错误
        self.exit()
    
    def action_clear(self) -> None:
        """Clear chat history"""
        chat_area = self.query_one("#chat-area", ChatArea)
        chat_area.clear()
        
        if self.session_manager:
            self.session_manager.session.clear_history()
            # Reset context display
            sidebar = self.query_one("#sidebar", Sidebar)
            sidebar.update_context_usage(
                self.session_manager.session.get_context_usage_snapshot()
            )
        
        self.notify("Chat history cleared", title="Info", severity="information")
    
    def action_escape(self) -> None:
        """Handle escape key"""
        if self._is_generating:
            self.notify("Cannot cancel while generating", title="Info")
        else:
            self.query_one("#input-area", InputArea).focus()
    
    def action_help(self) -> None:
        """Show help screen"""
        self.push_screen(HelpScreen())
    
    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility"""
        sidebar = self.query_one("#sidebar", Sidebar)
        self._sidebar_visible = not self._sidebar_visible
        sidebar.display = self._sidebar_visible
    
    @property
    def session(self):
        """Shortcut to access session from session_manager"""
        return self.session_manager.session if self.session_manager else None
    
    async def on_unmount(self) -> None:
        """Cleanup when app unmounts"""
        if self.session_manager and self.session_manager.session:
            try:
                await self.session_manager.session.cleanup()
            except Exception:
                pass  # 忽略清理错误，确保应用正常退出


def run_tui():
    """Entry point for TUI"""
    try:
        config = load_config()
        app = GemCodeApp(config)
        app.run()
    except Exception as e:
        print(f"Error starting TUI: {e}")
        raise


if __name__ == "__main__":
    run_tui()

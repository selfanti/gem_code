"""
Gem Code TUI - A textual-based terminal user interface
Inspired by OpenCode's interface design
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Container
from textual.widgets import (
    Static,
    Input,
    Button,
    Label,
    Markdown,
    Footer,
    Tree,
    Rule,
)
from textual.reactive import reactive
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen


from .config import Config, load_config
from .session import Session


@dataclass
class ChatEntry:
    """Represents a single chat message"""
    role: str
    content: str
    timestamp: datetime
    is_tool_call: bool = False
    tool_name: str | None = None
    tool_result: str | None = None


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


class StreamingMessageWidget(Static):
    """Widget to display a streaming message that updates in real-time"""
    
    DEFAULT_CSS = """
    StreamingMessageWidget {
        width: 100%;
        height: auto;
        padding: 0 2 1 2;
        margin: 0 0 1 0;
        border-left: solid $success;
    }
    
    StreamingMessageWidget .header-row {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }
    
    StreamingMessageWidget .avatar {
        width: 3;
        content-align: center middle;
    }
    
    StreamingMessageWidget .header {
        width: auto;
        text-style: bold;
        color: $success;
        content-align: left middle;
    }
    
    StreamingMessageWidget .timestamp {
        width: auto;
        color: $text-muted;
        text-style: italic;
        content-align: right middle;
    }
    
    StreamingMessageWidget .content {
        width: 100%;
        height: auto;
        padding-left: 4;
    }
    
    StreamingMessageWidget .content Markdown {
        background: transparent;
        padding: 0;
    }
    """
    
    def __init__(self, **kwargs):
        self.timestamp = datetime.now()
        self._content = ""
        super().__init__(**kwargs)
    
    def compose(self) -> ComposeResult:
        time_str = self.timestamp.strftime("%H:%M:%S")
        
        with Horizontal(classes="header-row"):
            yield Label("🤖", classes="avatar")
            yield Label("GEM", classes="header")
            yield Label(time_str, classes="timestamp")
        
        self._markdown = Markdown(self._content, classes="content")
        yield self._markdown
    
    def update_content(self, content: str) -> None:
        """Update the content of this message"""
        self._content = content
        if hasattr(self, '_markdown'):
            self._markdown.update(content)


class ChatMessageWidget(Static):
    """Widget to display a single chat message with Markdown support"""
    
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
            yield Label(header_text, classes="header")
            yield Label(time_str, classes="timestamp")
        
        # Message content with Markdown
        content = self.entry.content or ""
        yield Markdown(content, classes="content")
        
        # Tool result if any
        if self.entry.tool_result:
            result_text = self.entry.tool_result
            if len(result_text) > 500:
                result_text = result_text[:250] + "\n... [truncated] ...\n" + result_text[-200:]
            yield Static(f"Result:\n{result_text}", classes="tool-result")


class ChatArea(VerticalScroll):
    """Scrollable chat display area"""
    
    DEFAULT_CSS = """
    ChatArea {
        width: 100%;
        height: 1fr;
        padding: 1 0;
        border: none;
        background: $surface;
    }
    """
    
    _current_streaming: StreamingMessageWidget | None = None
    
    def add_message(self, entry: ChatEntry) -> ChatMessageWidget:
        """Add a new message to the chat"""
        widget = ChatMessageWidget(entry)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget
    
    def start_streaming(self) -> StreamingMessageWidget:
        """Start a new streaming message"""
        self._current_streaming = StreamingMessageWidget()
        self.mount(self._current_streaming)
        self.scroll_end(animate=False)
        return self._current_streaming
    
    def finish_streaming(self) -> None:
        """Mark current streaming message as complete"""
        self._current_streaming = None
    
    def clear(self) -> None:
        """Clear all messages"""
        for child in list(self.children):
            child.remove()
        self._current_streaming = None


class ResponseMessage(Message):
    """Message for streaming response updates"""
    def __init__(self, content: str, done: bool = False, error: str | None = None) -> None:
        self.content = content
        self.done = done
        self.error = error
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
    }
    
    InputArea Input {
        width: 1fr;
        margin-right: 1;
    }
    
    InputArea Button {
        width: auto;
        min-width: 8;
    }
    
    InputArea #clear-btn {
        background: $error-darken-2;
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
            yield Input(
                placeholder="Send a message...",
                id="message-input"
            )
            yield Button("Send ⏎", id="send-btn", variant="primary")
            yield Button("Clear", id="clear-btn", variant="error")
        
        yield Label(
            "[^C] Quit  [^L] Clear Chat  [Enter] Send  [Shift+Enter] New Line  [?] Help",
            classes="hint"
        )
    
    def on_mount(self) -> None:
        self.query_one("#message-input", Input).focus()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input"""
        if event.value.strip():
            self.post_message(self.Submitted(event.value))
            event.input.value = ""
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks"""
        if event.button.id == "send-btn":
            input_widget = self.query_one("#message-input", Input)
            value = input_widget.value.strip()
            if value:
                self.post_message(self.Submitted(value))
                input_widget.value = ""
                input_widget.focus()
        elif event.button.id == "clear-btn":
            self.post_message(self.ClearHistory())
    
    def set_loading(self, loading: bool) -> None:
        """Show/hide loading state"""
        btn = self.query_one("#send-btn", Button)
        btn.disabled = loading
        btn.label = "Wait..." if loading else "Send ⏎"
        
        input_widget = self.query_one("#message-input", Input)
        input_widget.disabled = loading
        if not loading:
            input_widget.focus()


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
        super().__init__(**kwargs)
    
    def compose(self) -> ComposeResult:
        # Logo
        yield Label("⚡ Gem Code", classes="logo")
        
        # Model info
        with Vertical(classes="section"):
            yield Label("MODEL", classes="section-title")
            
            with Vertical(classes="info-row"):
                yield Label("Name:", classes="info-label")
                yield Label(self.config.model, classes="info-value")
            
            with Vertical(classes="info-row"):
                yield Label("API:", classes="info-label")
                domain = self.config.base_url.replace("https://", "").replace("http://", "").split("/")[0]
                yield Label(domain[:25], classes="info-value")
        
        # Workdir info
        with Vertical(classes="section"):
            yield Label("WORKSPACE", classes="section-title")
            
            workdir = self.config.workdir
            if len(workdir) > 28:
                workdir = "..." + workdir[-25:]
            yield Label(workdir, classes="info-value")
        
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
        self.update(f" ♦ {status}")


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
                ("Enter", "Send message"),
                ("Shift+Enter", "Insert new line"),
                ("Ctrl+C", "Quit application"),
                ("Ctrl+L", "Clear chat history"),
                ("Ctrl+S", "Toggle sidebar"),
                ("Escape", "Cancel / Focus input"),
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
    """Main TUI application for Gem Code"""
    
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
        self.session: Session | None = None
        self._current_response = ""
        self._is_generating = False
        self._sidebar_visible = True
        super().__init__()
    
    async def on_mount(self) -> None:
        """Initialize session when app mounts"""
        self.session = Session(self.config)
        await self.session.init()
        self.query_one(StatusBar).status = f"Ready • {self.config.model}"
    
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
        if not self.session or self._is_generating:
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
        self._current_response = ""
        self.query_one("#input-area", InputArea).set_loading(True)
        self.query_one("#thinking-indicator", ThinkingIndicator).add_class("visible")
        self.query_one(StatusBar).status = "Generating..."
        
        # Start streaming message
        streaming_widget = chat_area.start_streaming()
        
        # Generate response
        asyncio.create_task(self._generate_response(user_message, streaming_widget))
    
    async def _generate_response(self, user_message: str, streaming_widget: StreamingMessageWidget) -> None:
        """Generate response with streaming updates"""
        try:
            def on_chunk(chunk: str) -> None:
                """Handle streaming chunk"""
                self._current_response += chunk
                # Post message to update UI from main thread
                self.post_message(ResponseMessage(self._current_response))
            
            # Run the chat
            await self.session.chat(user_message, on_chunk=on_chunk)
            
            # Signal completion
            self.post_message(ResponseMessage(self._current_response, done=True))
            
        except Exception as e:
            self._current_response += f"\n\n❌ Error: {str(e)}"
            self.post_message(ResponseMessage(self._current_response, done=True, error=str(e)))
    
    def on_response_message(self, message: ResponseMessage) -> None:
        """Handle response message - runs in main thread"""
        # Update streaming widget
        chat_area = self.query_one("#chat-area", ChatArea)
        if chat_area._current_streaming:
            chat_area._current_streaming.update_content(message.content)
        
        if message.done:
            self._is_generating = False
            chat_area.finish_streaming()
            self.query_one("#input-area", InputArea).set_loading(False)
            self.query_one("#thinking-indicator", ThinkingIndicator).remove_class("visible")
            self.query_one(StatusBar).status = "Ready"
    
    def on_input_area_clear_history(self) -> None:
        """Handle clear history request"""
        self.action_clear()
    
    def action_clear(self) -> None:
        """Clear chat history"""
        chat_area = self.query_one("#chat-area", ChatArea)
        chat_area.clear()
        
        if self.session:
            self.session.clear_history()
        
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

from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Label, Markdown, Static

class MyApp(App):
    CSS = """
    Collapsible {
        width: 100%;
        margin: 1 0;
    }
    Collapsible > .collapsible-title {
        text-style: bold;
        color: $accent;
    }
    """

    def compose(self) -> ComposeResult:
        # 默认展开
        with Collapsible(title="🧠 思考过程 (Reasoning)", collapsed=False):
            yield Markdown("""
1. 用户询问的是 Python TUI 框架
2. Textual 提供了 Collapsible 组件
3. 适合展示大模型的思考链
            """)
        
        # 默认折叠（适合节省空间）
        with Collapsible(title="📊 技术详情", collapsed=True):
            yield Static("Model: GPT-4\nTokens: 1500\nLatency: 2.3s")
        
        yield Label("下方是主要内容...")

if __name__ == "__main__":
    app = MyApp()
    app.run()
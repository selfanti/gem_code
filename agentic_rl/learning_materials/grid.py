import pygame
import sys
from typing import Tuple, Optional, Callable,Literal
ACTIONS = Literal['up', 'down', 'left', 'right','stay']



class InteractiveGrid:
    def __init__(self, rows: int, cols: int, cell_size: int = 100, 
                 margin: int = 3, caption: str = "Interactive Grid"):
        """
        初始化交互式网格
        
        Args:
            rows: 行数
            cols: 列数  
            cell_size: 每个格子的像素大小
            margin: 格子间距
            caption: 窗口标题
        """
        pygame.init()
        self.rows = rows
        self.cols = cols
        self.cell_size = cell_size
        self.margin = margin
        
        # 计算窗口尺寸
        self.width = cols * (cell_size + margin) + margin
        self.height = rows * (cell_size + margin) + margin
        
        # 创建窗口
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption(caption)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont('arial', cell_size // 2)
        
        # 网格数据存储
        self.colors = [[(255, 255, 255) for _ in range(cols)] for _ in range(rows)]  # 默认白色
        self.texts = [["" for _ in range(cols)] for _ in range(rows)]
        self.data = [[None for _ in range(cols)] for _ in range(rows)]  # 用户自定义数据
        
        # 点击回调函数 (row, col, button) -> None, button: 1左键 2中键 3右键
        self.on_click: Optional[Callable[[int, int, int], None]] = None
        
    def set_cell_color(self, row: int, col: int, color: Tuple[int, int, int]):
        """设置指定格子的颜色 (RGB)"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.colors[row][col] = color
            
    def set_cell_text(self, row: int, col: int, text: str):
        """设置指定格子的文本"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.texts[row][col] = str(text) if text is not None else ""
            
    def set_cell_data(self, row: int, col: int, data):
        """设置格子的自定义数据（用于存储状态等）"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.data[row][col] = data
            
    def get_cell_data(self, row: int, col: int):
        """获取格子的自定义数据"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.data[row][col]
        return None
            
    def get_cell_from_pos(self, pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """将鼠标坐标转换为网格坐标"""
        x, y = pos
        col = x // (self.cell_size + self.margin)
        row = y // (self.cell_size + self.margin)
        
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return (row, col)
        return None
    
    def draw(self):
        """绘制网格"""
        self.screen.fill((40, 40, 40))  # 背景深灰色
        
        for row in range(self.rows):
            for col in range(self.cols):
                # 计算格子位置
                x = col * (self.cell_size + self.margin) + self.margin
                y = row * (self.cell_size + self.margin) + self.margin
                
                # 绘制填充色
                color = self.colors[row][col]
                pygame.draw.rect(self.screen, color, 
                               (x, y, self.cell_size, self.cell_size))
                
                # 如果有文本，绘制数字
                text = self.texts[row][col]
                if text:
                    # 根据背景亮度选择文字颜色
                    brightness = sum(color) / 3
                    text_color = (0, 0, 0) if brightness > 128 else (255, 255, 255)
                    
                    text_surface = self.font.render(text, True, text_color)
                    text_rect = text_surface.get_rect(center=(
                        x + self.cell_size // 2,
                        y + self.cell_size // 2
                    ))
                    self.screen.blit(text_surface, text_rect)
                
                # 绘制边框（可选）
                pygame.draw.rect(self.screen, (100, 100, 100), 
                               (x, y, self.cell_size, self.cell_size), 1)
        
        pygame.display.flip()
    
    def handle_events(self):
        """处理事件"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            
            # 鼠标点击事件
            if event.type == pygame.MOUSEBUTTONDOWN:
                cell = self.get_cell_from_pos(event.pos)
                if cell and self.on_click:
                    row, col = cell
                    self.on_click(row, col, event.button)  # 1=左键, 2=中键, 3=右键
            
            # 键盘事件（示例：按 R 重置）
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r:
                    self.reset()
                    
        return True
    
    def reset(self):
        """重置网格"""
        self.colors = [[(255, 255, 255) for _ in range(self.cols)] for _ in range(self.rows)]
        self.texts = [["" for _ in range(self.cols)] for _ in range(self.rows)]
        self.data = [[None for _ in range(self.cols)] for _ in range(self.rows)]
    
    def run(self, fps: int = 60):
        """
        运行主循环（阻塞式）
        
        如果你需要在后台训练 RL 模型同时更新网格，应该在单独线程调用此方法
        """
        running = True
        while running:
            running = self.handle_events()
            self.draw()
            self.clock.tick(fps)
        
        pygame.quit()

# ==================== 使用示例 ====================

def demo():
    """演示：创建 10x10 网格，左键切换颜色，右键增加数字"""
    
    # 颜色循环
    color_palette = [
        (255, 255, 255),  # 白
        (231, 76, 60),    # 红
        (46, 204, 113),   # 绿
        (52, 152, 219),   # 蓝
        (241, 196, 15),   # 黄
        (155, 89, 182),   # 紫
    ]
    
    grid = InteractiveGrid(rows=10, cols=10, cell_size=80)
    
    # 存储每个格子的颜色索引
    color_indices = [[0 for _ in range(10)] for _ in range(10)]
    numbers = [[0 for _ in range(10)] for _ in range(10)]
    
    def on_cell_click(row: int, col: int, button: int):
        if button == 1:  # 左键：切换颜色
            color_indices[row][col] = (color_indices[row][col] + 1) % len(color_palette)
            grid.set_cell_color(row, col, color_palette[color_indices[row][col]])
            
        elif button == 3:  # 右键：数字 +1
            numbers[row][col] = (numbers[row][col] + 1) % 100
            grid.set_cell_text(row, col, str(numbers[row][col]))
            
        elif button == 2:  # 中键：清除
            grid.set_cell_color(row, col, (255, 255, 255))
            grid.set_cell_text(row, col, "")
            color_indices[row][col] = 0
            numbers[row][col] = 0
    
    grid.on_click = on_cell_click
    
    print("操作说明：")
    print("- 左键：切换颜色")
    print("- 右键：数字 +1")
    print("- 中键：清除")
    print("- 键盘 R：重置")
    
    grid.run()

if __name__ == "__main__":
    demo()
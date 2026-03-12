# UI 测试配置

# 目标 URL
BASE_URL = "http://localhost:5001/"

# 登录凭据
USERNAME = "admin"
PASSWORD = "admin123"

# 浏览器配置
VIEWPORT_SIZE = {'width': 1400, 'height': 900}
HEADLESS = False  # 设为 True 则不显示浏览器窗口

# 超时设置（毫秒）
DEFAULT_TIMEOUT = 10000

# 截图输出目录
OUTPUT_DIR = "./screenshots"
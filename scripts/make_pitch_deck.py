#!/usr/bin/env python3
"""
生成 Open ACE 向客户介绍的产品 PPT。
输出：docs/marketing/Open-ACE-产品介绍.pptx

基于 origin/main 最新代码（v1.2.0 + Unreleased）：
- 版本徽章 v1.2.0
- 新增「Kubernetes / OpenShift 云原生集成」页（平台 K8s 高可用部署、
  AI 自主开发 OpenSandbox 沙箱隔离、多用户工作区资源隔离）
- 竞品对比表新增「K8s 原生 & AI 沙箱隔离」维度
"""

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ----------------------------------------------------------------------------
# 主题色板
# ----------------------------------------------------------------------------
NAVY = RGBColor(0x0B, 0x1F, 0x3A)  # 深海军蓝（主背景/标题）
BLUE = RGBColor(0x1E, 0x6F, 0xF5)  # 科技蓝（强调）
CYAN = RGBColor(0x22, 0xD3, 0xEE)  # 亮青（点缀）
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)  # 浅灰底
CARD = RGBColor(0xFF, 0xFF, 0xFF)  # 卡片白
INK = RGBColor(0x1E, 0x29, 0x3B)  # 正文深色
GRAY = RGBColor(0x64, 0x74, 0x8B)  # 次要文字
GREEN = RGBColor(0x16, 0xA3, 0x4A)  # 成功/优势
AMBER = RGBColor(0xF5, 0x9E, 0x0B)  # 警示/差异
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARKCARD = RGBColor(0x14, 0x2A, 0x4A)  # 深色卡片
DARKER = RGBColor(0x10, 0x2A, 0x4D)  # 更深卡片
LIGHTBLUE_TEXT = RGBColor(0xD8, 0xE2, 0xF2)  # 深底正文

# ----------------------------------------------------------------------------
# 16:9 演示文稿
# ----------------------------------------------------------------------------
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]

# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------


def add_slide():
    return prs.slides.add_slide(BLANK)


def fill(shape, color):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def rect(slide, x, y, w, h, color, shape=MSO_SHAPE.RECTANGLE):
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    fill(sp, color)
    sp.shadow.inherit = False
    return sp


def round_rect(slide, x, y, w, h, color):
    sp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    fill(sp, color)
    sp.shadow.inherit = False
    try:
        sp.adjustments[0] = 0.08
    except Exception:
        pass
    return sp


def text(
    slide, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, wrap=True, line_spacing=1.0
):
    """runs: str 或 [(text, size, color, bold[, line_spacing]), ...] 列表。
    每段可选第 5 元素覆盖行距；否则用函数级 line_spacing。"""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    if isinstance(runs, str):
        runs = [(runs, 18, INK, False)]
    first = True
    for item in runs:
        t = item[0]
        size = item[1]
        color = item[2] if len(item) > 2 else INK
        bold = item[3] if len(item) > 3 else False
        ls = item[4] if len(item) > 4 else line_spacing
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        para.alignment = align
        para.line_spacing = ls
        r = para.add_run()
        r.text = t
        r.font.size = Pt(size)
        r.font.color.rgb = color
        r.font.bold = bold
        r.font.name = "Microsoft YaHei"
        first = False
    return tb


def bg(slide, color):
    rect(slide, 0, 0, SW, SH, color)


def card(slide, x, y, w, h, fill_color=CARD):
    """带细边、轻阴影的卡片"""
    sp = round_rect(slide, x, y, w, h, fill_color)
    sp.line.color.rgb = RGBColor(0xE2, 0xE8, 0xF0)
    sp.line.width = Pt(0.75)
    return sp


def page_header(slide, kicker, title, accent=BLUE):
    """深色页统一页眉"""
    rect(slide, 0, 0, SW, SH, NAVY)
    rect(slide, Inches(0.7), Inches(0.55), Inches(0.12), Inches(0.42), accent)
    text(slide, Inches(0.95), Inches(0.5), Inches(10), Inches(0.35), [(kicker, 13, CYAN, True)])
    text(slide, Inches(0.95), Inches(0.82), Inches(11), Inches(0.6), [(title, 28, WHITE, True)])


def page_number(slide, n, total):
    text(
        slide,
        Inches(12.0),
        Inches(7.0),
        Inches(1.1),
        Inches(0.3),
        [(f"{n} / {total}", 10, RGBColor(0x8A, 0x9A, 0xB8), False)],
        align=PP_ALIGN.RIGHT,
    )


def light_page_header(slide, kicker, title):
    """浅色页统一页眉"""
    bg(slide, LIGHT)
    rect(slide, 0, 0, Inches(0.16), SH, BLUE)
    text(slide, Inches(0.7), Inches(0.5), Inches(10), Inches(0.32), [(kicker, 13, BLUE, True)])
    text(slide, Inches(0.7), Inches(0.82), Inches(11), Inches(0.6), [(title, 28, INK, True)])


def bullet(tf, t, size=14, color=INK, bold=False, space_before=4, icon="•  "):
    para = tf.add_paragraph()
    para.space_before = Pt(space_before)
    r = para.add_run()
    r.text = icon + t
    r.font.size = Pt(size)
    r.font.color.rgb = color
    r.font.bold = bold
    r.font.name = "Microsoft YaHei"
    return para


def chip(slide, x, y, w, label, color=BLUE):
    sp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, Inches(0.38))
    fill(sp, color)
    sp.shadow.inherit = False
    try:
        sp.adjustments[0] = 0.5
    except Exception:
        pass
    tf = sp.text_frame
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size = Pt(11)
    r.font.bold = True
    r.font.color.rgb = WHITE
    r.font.name = "Microsoft YaHei"
    return sp


TOTAL = 13  # 总页数

# ============================================================================
# 第 1 页：封面
# ============================================================================
s = add_slide()
bg(s, NAVY)
rect(s, Inches(9.8), Inches(0), Inches(3.6), SH, DARKER)
rect(s, Inches(10.1), Inches(1.2), Inches(2.8), Inches(0.10), CYAN)
rect(s, Inches(10.1), Inches(1.5), Inches(2.8), Inches(0.04), BLUE)
rect(s, Inches(0.9), Inches(2.0), Inches(0.14), Inches(2.4), CYAN)
text(s, Inches(1.2), Inches(2.0), Inches(9), Inches(0.5), [("Open ACE", 20, CYAN, True)])
tb = s.shapes.add_textbox(Inches(1.2), Inches(2.5), Inches(10.5), Inches(1.8))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
r = p.add_run()
r.text = "自托管 AI Coding Agent"
r.font.size = Pt(50)
r.font.bold = True
r.font.color.rgb = WHITE
r.font.name = "Microsoft YaHei"
p2 = tf.add_paragraph()
r2 = p2.add_run()
r2.text = "工作台 与 治理控制面"
r2.font.size = Pt(50)
r2.font.bold = True
r2.font.color.rgb = WHITE
r2.font.name = "Microsoft YaHei"

text(
    s,
    Inches(1.2),
    Inches(4.7),
    Inches(10),
    Inches(0.5),
    [
        (
            "把团队已在用的多个 AI 编码工具接进来，把密钥、成本和风险统一管起来。",
            17,
            RGBColor(0xC7, 0xD6, 0xEA),
            False,
        )
    ],
)
chip(s, Inches(1.2), Inches(5.5), Inches(1.9), "Apache 2.0 开源", BLUE)
chip(s, Inches(3.25), Inches(5.5), Inches(1.6), "v1.2.0", CYAN)
chip(s, Inches(5.0), Inches(5.5), Inches(2.3), "支持私有化部署", RGBColor(0x12, 0x7A, 0x4E))
text(
    s,
    Inches(1.2),
    Inches(6.8),
    Inches(11),
    Inches(0.3),
    [("产品介绍  ·  Product Overview for Customers", 12, RGBColor(0x7E, 0x93, 0xB6), False)],
)

# ============================================================================
# 第 2 页：市场痛点
# ============================================================================
s = add_slide()
page_header(s, "行业现状  ·  THE PROBLEM", "当 AI 编码工具进入团队，问题才刚刚开始")
page_number(s, 2, TOTAL)

pains = [
    ("🔑", "密钥失控", "API Key 散落在个人电脑和远程机器，泄漏风险高、轮换难、无法追溯"),
    ("🖥️", "远程难跑", "AI CLI 需要跑在内网开发机、测试机、GPU 机器，反复传 SSH 凭据很笨重"),
    ("🧩", "工具分散", "Claude Code、Qwen Code、Codex、OpenClaw 各自为战，无统一入口与历史"),
    ("💸", "成本盲区", "团队花了多少 Token、多少钱、谁在用、是否超支——基本是黑盒"),
    ("🛡️", "审计缺位", "合规、审计、配额、敏感内容检测缺失，上不了生产环境"),
    ("📊", "价值难证", "AI 投入产出比说不清，效率提升无法量化，决策无依据"),
]
cw = Inches(3.95)
ch = Inches(2.55)
gx = Inches(0.25)
gy = Inches(0.25)
x0 = Inches(0.7)
y0 = Inches(1.75)
for i, (icon, t, d) in enumerate(pains):
    col = i % 3
    row = i // 3
    x = x0 + col * (cw + gx)
    y = y0 + row * (ch + gy)
    card(s, x, y, cw, ch, DARKCARD)
    text(s, x + Inches(0.35), y + Inches(0.3), Inches(1), Inches(0.6), [(icon, 30)])
    text(
        s, x + Inches(0.35), y + Inches(0.95), cw - Inches(0.7), Inches(0.4), [(t, 18, WHITE, True)]
    )
    text(
        s,
        x + Inches(0.35),
        y + Inches(1.4),
        cw - Inches(0.7),
        Inches(1.1),
        [(d, 12.5, RGBColor(0xB8, 0xC6, 0xDE), False)],
    )

# ============================================================================
# 第 3 页：产品定位 / 一句话价值
# ============================================================================
s = add_slide()
bg(s, LIGHT)
rect(s, 0, 0, Inches(0.16), SH, BLUE)
text(
    s,
    Inches(0.7),
    Inches(0.5),
    Inches(10),
    Inches(0.32),
    [("产品定位  ·  WHAT IS IT", 13, BLUE, True)],
)
text(s, Inches(0.7), Inches(0.82), Inches(12), Inches(0.6), [("一个平台，双重价值", 28, INK, True)])

card(s, Inches(0.9), Inches(1.8), Inches(11.5), Inches(1.9), WHITE)
text(
    s,
    Inches(1.3),
    Inches(2.05),
    Inches(10.8),
    Inches(0.5),
    [("Open ACE = AI 编码工作台  +  AI 治理控制面", 24, NAVY, True)],
)
text(
    s,
    Inches(1.3),
    Inches(2.7),
    Inches(10.8),
    Inches(0.9),
    [
        (
            "开发者在浏览器里统一使用 Claude Code、Qwen Code、Codex、OpenClaw 等 AI 编码工具，并把它们跑在团队自己的远程机器上；",
            15,
            GRAY,
            False,
        ),
        (
            "管理员则集中管理 API Key、权限、成本、配额、审计和合规——一切自托管、数据不出企业网络。",
            15,
            GRAY,
            False,
        ),
    ],
    line_spacing=1.3,
)

quad = [
    ("🌐", "统一入口", "多个 AI CLI 一个浏览器工作台，统一会话、历史与治理"),
    ("🖥️", "远程执行", "Remote Agent 让 AI CLI 直接在目标机器运行，免反复传凭据"),
    ("🔑", "密钥安全", "API Key 加密留服务端，只下发短期代理令牌给会话"),
    ("📊", "治理可视", "Token、成本、配额、审计、合规、ROI 一屏掌握"),
]
qw = Inches(2.75)
qh = Inches(2.5)
qx0 = Inches(0.9)
qy0 = Inches(4.05)
gqx = Inches(0.18)
for i, (icon, t, d) in enumerate(quad):
    x = qx0 + i * (qw + gqx)
    card(s, x, qy0, qw, qh, WHITE)
    rect(s, x, qy0, qw, Inches(0.1), BLUE)
    text(s, x + Inches(0.3), qy0 + Inches(0.35), Inches(1), Inches(0.6), [(icon, 28)])
    text(
        s, x + Inches(0.3), qy0 + Inches(1.0), qw - Inches(0.6), Inches(0.4), [(t, 17, NAVY, True)]
    )
    text(
        s,
        x + Inches(0.3),
        qy0 + Inches(1.45),
        qw - Inches(0.6),
        Inches(1.0),
        [(d, 12.5, GRAY, False)],
    )

# ============================================================================
# 第 4 页：两种模式
# ============================================================================
s = add_slide()
page_header(s, "产品形态  ·  TWO MODES", "两种模式，服务两类用户")
page_number(s, 4, TOTAL)

rect(s, Inches(0.7), Inches(1.8), Inches(5.85), Inches(4.7), DARKCARD)
rect(s, Inches(0.7), Inches(1.8), Inches(5.85), Inches(0.7), BLUE)
text(
    s,
    Inches(1.0),
    Inches(1.92),
    Inches(5.5),
    Inches(0.45),
    [("🚀  Work 模式 — 让 AI 成为超级助手", 17, WHITE, True)],
)
text(
    s,
    Inches(1.0),
    Inches(2.7),
    Inches(5.3),
    Inches(0.4),
    [("面向每一位开发者，提供流畅的 AI 交互体验", 12.5, CYAN, False)],
)
tfw = text(s, Inches(1.0), Inches(3.25), Inches(5.3), Inches(3), []).text_frame
tfw.word_wrap = True
for t in [
    "多 AI 工具集成：Claude Code / Qwen Code / Codex / OpenClaw / ZCode 统一入口",
    "远程工作区：浏览器里选远程机器，启动 AI 编码会话、终端、目录浏览",
    "智能会话管理：历史记录、会话恢复、上下文记忆、跨工具同步",
    "提示词库：团队共享优质提示词，最佳实践一键复用",
    "快速检索：跨会话搜索历史对话，知识沉淀不丢失",
]:
    bullet(tfw, t, size=13, color=LIGHTBLUE_TEXT)

rect(s, Inches(6.78), Inches(1.8), Inches(5.85), Inches(4.7), DARKCARD)
rect(s, Inches(6.78), Inches(1.8), Inches(5.85), Inches(0.7), CYAN)
text(
    s,
    Inches(7.08),
    Inches(1.92),
    Inches(5.5),
    Inches(0.45),
    [("📊  Manage 模式 — 让 AI 治理有据可依", 17, NAVY, True)],
)
text(
    s,
    Inches(7.08),
    Inches(2.7),
    Inches(5.3),
    Inches(0.4),
    [("面向管理者，提供全方位的 AI 使用洞察与管控", 12.5, BLUE, False)],
)
tfm = text(s, Inches(7.08), Inches(3.25), Inches(5.3), Inches(3), []).text_frame
tfm.word_wrap = True
for t in [
    "用量可视化：Token 消耗趋势、成本分析、使用热力图一目了然",
    "API Key 治理：加密存储密钥，通过代理令牌调用，避免密钥下发",
    "智能告警：配额预警、异常检测、超支提醒，风险早知道",
    "合规审计：敏感内容检测、对话追溯、合规报告与 CSV 下载",
    "多租户 + ROI：部门隔离、权限控制、投入产出比量化",
]:
    bullet(tfm, t, size=13, color=LIGHTBLUE_TEXT)

# ============================================================================
# 第 5 页：六大核心能力
# ============================================================================
s = add_slide()
light_page_header(s, "核心能力  ·  CAPABILITIES", "八大核心能力，覆盖 AI 编码全生命周期")
page_number(s, 5, TOTAL)

caps = [
    ("🎛️", "Agent 控制面", "统一管理工具、用户、机器、密钥、配额、成本和审计，而非替代每个 AI CLI"),
    (
        "🤖",
        "多 CLI 适配器",
        "原生适配 Claude Code、Qwen Code、Codex、OpenClaw、ZCode，含会话恢复与权限模式",
    ),
    (
        "🖥️",
        "Remote Agent",
        "AI CLI 直接在 Linux/macOS/Windows 远程机器运行，支持浏览器终端与 VSCode 代理",
    ),
    ("🔑", "API Key 代理", "真实密钥加密存服务端，远程 Agent 只拿短期代理令牌，统一配额与用量统计"),
    (
        "🤖",
        "AI 自主开发",
        "端到端工作流：规划→开发→PR 审查→报告，支持重试/Fork/里程碑，可跑在 gVisor/Kata 沙箱",
    ),
    (
        "🔌",
        "Model Gateway",
        "兼容 LiteLLM 模型网关，厂商中立，可对接集中式模型网关并保留配额与归因",
    ),
    (
        "☁️",
        "云原生部署",
        "Kubernetes Kustomize 一键部署，3 副本 + HPA + Prometheus 指标，兼容 OpenShift",
    ),
    ("🏢", "企业集成", "SSO / 飞书 / 钉钉、多租户权限模型、Kubernetes、反向代理"),
]
cw = Inches(2.95)
ch = Inches(2.15)
gx = Inches(0.12)
gy = Inches(0.18)
x0 = Inches(0.7)
y0 = Inches(1.7)
for i, (icon, t, d) in enumerate(caps):
    col = i % 4
    row = i // 4
    x = x0 + col * (cw + gx)
    y = y0 + row * (ch + gy)
    card(s, x, y, cw, ch, WHITE)
    text(s, x + Inches(0.28), y + Inches(0.25), Inches(1), Inches(0.55), [(icon, 26)])
    text(
        s,
        x + Inches(0.28),
        y + Inches(0.85),
        cw - Inches(0.5),
        Inches(0.35),
        [(t, 14.5, NAVY, True)],
    )
    text(
        s,
        x + Inches(0.28),
        y + Inches(1.25),
        cw - Inches(0.5),
        Inches(0.85),
        [(d, 11.5, GRAY, False)],
    )

# ============================================================================
# 第 6 页：核心架构（差异化卖点）
# ============================================================================
s = add_slide()
page_header(s, "核心架构  ·  HOW IT WORKS", "Remote Agent + API Key 代理：安全与效率兼得")
page_number(s, 6, TOTAL)

layers = [
    ("浏览器 (React SPA)", "Work 模式（全员）  ·  Manage 模式（管理员）", BLUE, Inches(1.55)),
    (
        "Flask API 服务（自托管）",
        "23 Blueprints · 密钥加密存储 · 配额/审计/合规 · API Key 代理签发短期令牌",
        CYAN,
        Inches(3.25),
    ),
    (
        "Remote Agent（远程机器守护进程）",
        "HTTP 轮询 · CLI 子进程 · WebSocket PTY 终端 · 会话同步（Claude/Qwen/Codex/OpenClaw）",
        RGBColor(0x12, 0x7A, 0x4E),
        Inches(4.95),
    ),
]
for title_, sub, color, y in layers:
    rect(s, Inches(0.7), y, Inches(8.2), Inches(1.4), DARKCARD)
    rect(s, Inches(0.7), y, Inches(0.12), Inches(1.4), color)
    text(s, Inches(1.0), y + Inches(0.18), Inches(7.8), Inches(0.4), [(title_, 17, WHITE, True)])
    text(
        s,
        Inches(1.0),
        y + Inches(0.62),
        Inches(7.8),
        Inches(0.7),
        [(sub, 12.5, RGBColor(0xC0, 0xCF, 0xE6), False)],
    )

for ay in [Inches(2.99), Inches(4.69)]:
    ar = s.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(4.6), ay, Inches(0.4), Inches(0.22))
    fill(ar, CYAN)
    ar.shadow.inherit = False

card(s, Inches(9.2), Inches(1.7), Inches(3.45), Inches(4.65), DARKER)
text(s, Inches(9.5), Inches(1.95), Inches(2.9), Inches(0.4), [("为什么这样设计？", 16, CYAN, True)])
tfd = text(s, Inches(9.5), Inches(2.45), Inches(2.9), Inches(3.3), []).text_frame
tfd.word_wrap = True
for t in [
    "真实 API Key 永不出服务端",
    "远程机器只持有短期代理令牌",
    "令牌可配额、可回收、可追溯",
    "支持内网 / 私有网络机器",
    "终端断线可恢复屏幕",
    "厂商中立，可接 LiteLLM",
]:
    bullet(tfd, t, size=12.5, color=LIGHTBLUE_TEXT)

text(
    s,
    Inches(0.7),
    Inches(6.6),
    Inches(12),
    Inches(0.4),
    [
        (
            "✦  适合需要私有化部署、内网远程机器、统一密钥代理、团队配额和可追溯审计的研发与 IT 团队",
            13,
            AMBER,
            True,
        )
    ],
)

# ============================================================================
# 第 7 页：Kubernetes / OpenShift 云原生集成（新增）
# ============================================================================
s = add_slide()
page_header(s, "企业部署  ·  CLOUD-NATIVE", "Kubernetes / OpenShift 云原生集成")
page_number(s, 7, TOTAL)

k8s_cols = [
    (
        "☁️",
        "平台部署：K8s 高可用",
        BLUE,
        [
            "Kustomize 一键部署（kubectl apply -k k8s/）",
            "3 副本 + HPA 自动扩缩容（3→10 Pod）",
            "PostgreSQL / Redis StatefulSet 持久化",
            "/livez /readyz 探针 + Prometheus /metrics",
            "PDB + Pod 反亲和 + 优雅滚动更新",
            "NetworkPolicy 最小权限 + 非 root 运行",
        ],
    ),
    (
        "🛡️",
        "AI 自主开发：沙箱隔离",
        RGBColor(0x12, 0x7A, 0x4E),
        [
            "三后端按租户/项目选择：POSIX / 远程机 / 沙箱",
            "gVisor / Kata 双档安全容器运行时",
            "非 root + 只读根文件系统 + seccomp 强化",
            "CPU / 内存 / PIDs / 存储限额 + TTL",
            "默认拒绝出站 + FQDN 白名单",
            "Fail-closed：隔离不达标即拒绝并审计",
        ],
    ),
    (
        "👥",
        "多用户工作区：资源隔离",
        CYAN,
        [
            "每用户 WebUI 独立沙箱 Pod，Token 身份",
            "无宿主 OS 账号 / sudo 链 / 宿主凭据",
            "租户级 OS 隔离：跨租户访问阻断、可回收",
            "UID 跨容器重建固定，配额 60 秒巡检执行",
            "会话历史快照随 Pod 回收与恢复",
        ],
    ),
]
cw = Inches(3.85)
x0 = Inches(0.7)
y0 = Inches(1.7)
for i, (icon, t, color, items) in enumerate(k8s_cols):
    x = x0 + i * (cw + Inches(0.19))
    card(s, x, y0, cw, Inches(4.55), DARKCARD)
    rect(s, x, y0, cw, Inches(0.08), color)
    text(s, x + Inches(0.3), y0 + Inches(0.3), Inches(1), Inches(0.5), [(icon, 24)])
    text(
        s,
        x + Inches(0.95),
        y0 + Inches(0.36),
        cw - Inches(1.2),
        Inches(0.4),
        [(t, 15, WHITE, True)],
    )
    tfk = text(s, x + Inches(0.3), y0 + Inches(1.0), cw - Inches(0.6), Inches(3.4), []).text_frame
    tfk.word_wrap = True
    for it in items:
        bullet(tfk, it, size=11, color=LIGHTBLUE_TEXT, space_before=7)

# OpenShift 兼容条
rect(s, Inches(0.7), Inches(6.4), Inches(12.63), Inches(0.55), DARKER)
rect(s, Inches(0.7), Inches(6.4), Inches(0.1), Inches(0.55), AMBER)
text(
    s,
    Inches(1.0),
    Inches(6.47),
    Inches(12.2),
    Inches(0.45),
    [
        (
            "🖥️  OpenShift / OKD 兼容：标准 Kustomize 清单，安全上下文符合 restricted SCC 约束，SSRF 防护覆盖 OpenShift 内部 DNS",
            12,
            RGBColor(0xE8, 0xD5, 0xA8),
            False,
        )
    ],
)

# ============================================================================
# 第 8 页：核心价值（ROI）
# ============================================================================
s = add_slide()
light_page_header(s, "核心价值  ·  WHY IT MATTERS", "对开发者、管理者、企业三个层面创造价值")
page_number(s, 8, TOTAL)

cols = [
    (
        "💻",
        "对开发者",
        BLUE,
        [
            "一个入口用遍所有 AI 编码工具",
            "远程机器随时可用，免配环境",
            "会话历史不丢，知识可沉淀",
            "专注编码，密钥与环境由平台托管",
        ],
    ),
    (
        "👔",
        "对管理者",
        CYAN,
        [
            "Token / 成本 / 配额一屏可视",
            "告警与异常检测，风险早知道",
            "合规审计与报告导出，上得了生产",
            "ROI 量化，AI 投入产出说得清",
        ],
    ),
    (
        "🏢",
        "对企业",
        RGBColor(0x12, 0x7A, 0x4E),
        [
            "数据与密钥不出企业网络",
            "私有化部署，满足合规要求",
            "多租户隔离，部门独立核算",
            "开源可控，无厂商锁定",
        ],
    ),
]
cw = Inches(3.95)
x0 = Inches(0.7)
y0 = Inches(1.75)
for i, (icon, t, color, items) in enumerate(cols):
    x = x0 + i * (cw + Inches(0.18))
    card(s, x, y0, cw, Inches(4.7), WHITE)
    rect(s, x, y0, cw, Inches(0.95), color)
    text(s, x + Inches(0.35), y0 + Inches(0.22), Inches(1), Inches(0.5), [(icon, 26)])
    text(
        s, x + Inches(1.1), y0 + Inches(0.28), cw - Inches(1.3), Inches(0.5), [(t, 19, WHITE, True)]
    )
    tfc = text(s, x + Inches(0.35), y0 + Inches(1.25), cw - Inches(0.7), Inches(3.2), []).text_frame
    tfc.word_wrap = True
    for it in items:
        bullet(tfc, it, size=13.5, color=INK, space_before=8)

# ============================================================================
# 第 9 页：竞品对比
# ============================================================================
s = add_slide()
light_page_header(s, "竞品对比  ·  COMPARISON", "与市场主流方案的差异化定位")
page_number(s, 9, TOTAL)

rows = [
    (
        "能力维度",
        "Open ACE",
        "通用 LLM 网关\n(LiteLLM等)",
        "AI IDE/插件\n(Cursor等)",
        "企业自建脚本",
    ),
    ("定位", "工作台+治理控制面", "模型路由/计费", "单人编码体验", "临时拼装"),
    ("自托管/私有化", "✅ 默认自托管", "✅", "❌ 多为云服务", "✅"),
    ("多 AI CLI 统一入口", "✅ Claude/Qwen/Codex 等", "❌ 仅模型层", "⚠️ 各自封闭", "❌"),
    ("Remote Agent 远程执行", "✅ 浏览器内远程机器", "❌", "❌", "⚠️ 需自建"),
    ("API Key 不下发", "✅ 短期代理令牌", "⚠️ 依赖网关", "❌ 密钥在本机", "❌"),
    ("成本/配额/审计/合规", "✅ 一体化治理", "⚠️ 仅计费", "❌", "❌"),
    ("多租户 / SSO / 飞书", "✅", "⚠️ 部分", "❌", "❌"),
    ("K8s 原生 & AI 沙箱隔离", "✅ HPA+gVisor/Kata 沙箱", "⚠️ 可容器化", "❌", "❌ 需自建"),
    ("开源协议", "Apache 2.0", "多协议", "闭源", "—"),
]
ncol = len(rows[0])
nrow = len(rows)
tx = Inches(0.5)
ty = Inches(1.5)
col_w = [Inches(2.85), Inches(2.95), Inches(2.6), Inches(2.4), Inches(1.53)]
row_h = Inches(0.5)
# 表头
xx = tx
for c in range(ncol):
    cw_ = col_w[c]
    hdr_color = NAVY if c <= 1 else RGBColor(0x33, 0x4E, 0x73)
    sp = rect(s, xx, ty, cw_, Inches(0.8), hdr_color)
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = Emu(20000)
    tf.margin_bottom = Emu(20000)
    for li, line in enumerate(rows[0][c].split("\n")):
        p = tf.paragraphs[0] if li == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = line
        r.font.size = Pt(11.5 if c == 0 else 11)
        r.font.bold = True
        r.font.color.rgb = WHITE
        r.font.name = "Microsoft YaHei"
    xx += cw_
# 数据行
for ri in range(1, nrow):
    yy = ty + Inches(0.8) + (ri - 1) * row_h
    xx = tx
    for c in range(ncol):
        cw_ = col_w[c]
        if c == 1:
            cell_color = RGBColor(0xE8, 0xF1, 0xFF)
        elif ri % 2 == 1:
            cell_color = WHITE
        else:
            cell_color = RGBColor(0xF5, 0xF7, 0xFA)
        sp = rect(s, xx, yy, cw_, row_h, cell_color)
        sp.line.color.rgb = RGBColor(0xDD, 0xE3, 0xEC)
        sp.line.width = Pt(0.5)
        val = rows[ri][c]
        is_check = val.startswith("✅")
        tf = sp.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_top = Emu(20000)
        tf.margin_bottom = Emu(20000)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = val
        r.font.size = Pt(10.5)
        r.font.bold = (c == 1) or (c == 0)
        if c == 0:
            r.font.color.rgb = INK
        elif is_check:
            r.font.color.rgb = GREEN
        elif val.startswith("⚠️"):
            r.font.color.rgb = AMBER
        elif val.startswith("❌"):
            r.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
        else:
            r.font.color.rgb = INK
        r.font.name = "Microsoft YaHei"
        xx += cw_

rect(s, tx + col_w[0], ty - Inches(0.12), col_w[1], Inches(0.1), CYAN)

# ============================================================================
# 第 10 页：差异化卖点总结
# ============================================================================
s = add_slide()
page_header(s, "差异化卖点  ·  KEY DIFFERENTIATORS", "为什么是 Open ACE？")
page_number(s, 10, TOTAL)

diffs = [
    (
        "🔒",
        "默认自托管，数据边界可控",
        "密钥与会话数据留在企业自己的网络与数据库，满足私有化与合规要求，无云依赖。",
    ),
    (
        "🎛️",
        "控制面而非又一个 CLI",
        "不替代任何 AI 编码工具，而是把团队已在用的工具纳入统一的入口、权限、成本与审计体系。",
    ),
    (
        "🔑",
        "短期代理令牌架构",
        "真实 LLM API Key 加密留服务端，远程会话仅持有时效令牌，可配额、可回收、可追溯。",
    ),
    (
        "🖥️",
        "Remote Agent 真实远程执行",
        "AI CLI 直接在开发/测试/GPU 机器运行，浏览器内终端 + VSCode 代理，免反复传 SSH。",
    ),
    (
        "🛡️",
        "云原生 + 双层沙箱隔离",
        "K8s 高可用部署；AI 自主开发与多用户工作区运行在 gVisor/Kata 沙箱，故障关闭式安全。",
    ),
    ("🌐", "厂商中立 + 多租户", "兼容 LiteLLM 模型网关，多 CLI 统一治理，部门隔离与独立核算。"),
]
cw = Inches(3.95)
ch = Inches(2.3)
gx = Inches(0.25)
x0 = Inches(0.7)
y0 = Inches(1.7)
for i, (icon, t, d) in enumerate(diffs):
    col = i % 3
    row = i // 3
    x = x0 + col * (cw + gx)
    y = y0 + row * (ch + Inches(0.25))
    card(s, x, y, cw, ch, DARKCARD)
    rect(s, x, y, Inches(0.1), ch, CYAN)
    text(s, x + Inches(0.32), y + Inches(0.28), Inches(1), Inches(0.55), [(icon, 26)])
    text(
        s,
        x + Inches(0.32),
        y + Inches(0.92),
        cw - Inches(0.6),
        Inches(0.4),
        [(t, 15.5, WHITE, True)],
    )
    text(
        s,
        x + Inches(0.32),
        y + Inches(1.38),
        cw - Inches(0.6),
        Inches(0.85),
        [(d, 12, RGBColor(0xC0, 0xCF, 0xE6), False)],
    )

# ============================================================================
# 第 11 页：典型场景 / 目标客户
# ============================================================================
s = add_slide()
light_page_header(s, "适用场景  ·  USE CASES", "谁最需要 Open ACE？")
page_number(s, 11, TOTAL)

scenes = [
    ("研发团队", "已在用 Claude Code / Qwen Code / Codex，需要统一入口、共享提示词与会话沉淀。"),
    ("AI 平台团队", "构建内部 AI 工具平台，需要密钥代理、远程执行与统一治理控制面。"),
    ("DevOps / IT 团队", "负责自托管基础设施、审计与访问控制，需要私有化部署与多租户。"),
    ("安全合规团队", "不能用公共 Demo、不能让密钥散落，需要审计追踪与合规报告导出。"),
    ("GPU / 算力团队", "AI CLI 需跑在 GPU 机器或测试环境，需要浏览器内远程工作区与终端。"),
    ("多部门企业", "需要部门隔离、独立配额核算、SSO 与飞书/钉钉集成。"),
]
cw = Inches(3.95)
ch = Inches(1.45)
gx = Inches(0.25)
gy = Inches(0.2)
x0 = Inches(0.7)
y0 = Inches(1.7)
for i, (t, d) in enumerate(scenes):
    col = i % 3
    row = i // 3
    x = x0 + col * (cw + gx)
    y = y0 + row * (ch + gy)
    card(s, x, y, cw, ch, WHITE)
    rect(s, x, y, Inches(0.1), ch, BLUE)
    text(s, x + Inches(0.3), y + Inches(0.2), cw - Inches(0.5), Inches(0.4), [(t, 16, NAVY, True)])
    text(
        s,
        x + Inches(0.3),
        y + Inches(0.62),
        cw - Inches(0.5),
        Inches(0.8),
        [(d, 12.5, GRAY, False)],
    )

# ============================================================================
# 第 12 页：快速开始 / 部署
# ============================================================================
s = add_slide()
page_header(s, "落地路径  ·  GET STARTED", "5 分钟启动，渐进式落地")
page_number(s, 12, TOTAL)

card(s, Inches(0.7), Inches(1.8), Inches(6.0), Inches(4.6), DARKER)
text(
    s, Inches(1.0), Inches(2.0), Inches(5.5), Inches(0.4), [("🚀 一键部署（推荐）", 17, CYAN, True)]
)
code_sp = rect(s, Inches(1.0), Inches(2.55), Inches(5.4), Inches(1.5), RGBColor(0x07, 0x16, 0x29))
code_tf = code_sp.text_frame
code_tf.word_wrap = True
code_tf.margin_left = Inches(0.2)
code_tf.margin_top = Inches(0.15)
for li, line in enumerate(
    [
        "git clone https://github.com/open-ace/open-ace.git",
        "cd open-ace",
        "docker compose up -d --build",
        "",
        "# 访问 http://localhost:19888",
    ]
):
    p = code_tf.paragraphs[0] if li == 0 else code_tf.add_paragraph()
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run()
    r.text = line if line else " "
    r.font.size = Pt(11)
    r.font.color.rgb = GREEN if line.startswith("#") else RGBColor(0xA7, 0xF3, 0xD0)
    r.font.name = "Consolas"
text(s, Inches(1.0), Inches(4.25), Inches(5.5), Inches(0.4), [("📦 其他部署方式", 14, CYAN, True)])
tfdep = text(s, Inches(1.0), Inches(4.65), Inches(5.4), Inches(1.6), []).text_frame
tfdep.word_wrap = True
for t in [
    "源码安装（本地开发）",
    "Kubernetes Kustomize 部署（3 副本 + HPA + Prometheus）",
    "Linux systemd / macOS launchd 远程 Agent",
    "支持 SQLite（单机）与 PostgreSQL（生产）",
]:
    bullet(tfdep, t, size=12.5, color=LIGHTBLUE_TEXT)

card(s, Inches(6.9), Inches(1.8), Inches(5.75), Inches(4.6), DARKER)
text(s, Inches(7.2), Inches(2.0), Inches(5.2), Inches(0.4), [("🗺️ 渐进式落地路线", 17, CYAN, True)])
steps = [
    ("Step 1", "本地 Docker 试用，评估 Work / Manage 两模式"),
    ("Step 2", "接入团队 API Key，开启 API Key 代理"),
    ("Step 3", "部署 Remote Agent 到开发/测试/GPU 机器"),
    ("Step 4", "上 K8s 高可用部署，开启 AI 沙箱隔离"),
    ("Step 5", "配置配额、审计、SSO，产出合规与 ROI 报告"),
]
for i, (k, v) in enumerate(steps):
    y = Inches(2.55 + i * 0.7)
    rect(s, Inches(7.2), y + Inches(0.08), Inches(0.85), Inches(0.42), BLUE)
    tfk = s.shapes[-1].text_frame
    p = tfk.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = k
    r.font.size = Pt(11)
    r.font.bold = True
    r.font.color.rgb = WHITE
    r.font.name = "Microsoft YaHei"
    text(
        s,
        Inches(8.2),
        y + Inches(0.08),
        Inches(4.3),
        Inches(0.5),
        [(v, 13, LIGHTBLUE_TEXT, False)],
        anchor=MSO_ANCHOR.MIDDLE,
    )

# ============================================================================
# 第 13 页：结尾 / Call to Action
# ============================================================================
s = add_slide()
bg(s, NAVY)
rect(s, 0, Inches(6.7), SW, Inches(0.8), RGBColor(0x07, 0x16, 0x29))
rect(s, Inches(0.9), Inches(2.2), Inches(0.14), Inches(2.6), CYAN)
text(
    s,
    Inches(1.2),
    Inches(2.2),
    Inches(11),
    Inches(0.5),
    [("把 AI Coding Agent 接进来，", 40, WHITE, True)],
)
text(
    s,
    Inches(1.2),
    Inches(2.95),
    Inches(11),
    Inches(0.5),
    [("把密钥、成本和风险管起来。", 40, CYAN, True)],
)
text(
    s,
    Inches(1.2),
    Inches(3.95),
    Inches(10.5),
    Inches(0.5),
    [
        (
            "Open ACE — self-hosted workspace and control plane for AI coding agents.",
            15,
            RGBColor(0xC7, 0xD6, 0xEA),
            False,
        )
    ],
)

cta = [
    ("🌐", "官网", "www.open-ace.com"),
    ("💻", "代码仓库", "github.com/open-ace/open-ace"),
    ("📚", "文档", "open-ace.github.io/open-ace-docs"),
    ("💬", "反馈", "GitHub Discussions"),
]
cw = Inches(2.8)
x0 = Inches(0.9)
y0 = Inches(4.7)
for i, (icon, t, v) in enumerate(cta):
    x = x0 + i * (cw + Inches(0.18))
    card(s, x, y0, cw, Inches(1.5), DARKER)
    text(s, x + Inches(0.25), y0 + Inches(0.2), Inches(1), Inches(0.5), [(icon, 22)])
    text(
        s,
        x + Inches(0.25),
        y0 + Inches(0.72),
        cw - Inches(0.5),
        Inches(0.35),
        [(t, 13, CYAN, True)],
    )
    text(
        s,
        x + Inches(0.25),
        y0 + Inches(1.05),
        cw - Inches(0.5),
        Inches(0.35),
        [(v, 11.5, WHITE, False)],
    )

text(
    s,
    Inches(0.9),
    Inches(6.9),
    Inches(11),
    Inches(0.4),
    [
        (
            "Apache 2.0 开源  ·  自托管优先  ·  厂商中立  ·  面向真实研发流程",
            13,
            RGBColor(0x8A, 0x9A, 0xB8),
            False,
        )
    ],
)

# ----------------------------------------------------------------------------
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "marketing")
out_dir = os.path.abspath(out_dir)
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "Open-ACE-产品介绍.pptx")
prs.save(out_path)
print("已生成:", out_path)
print("总页数:", len(prs.slides._sldIdLst))

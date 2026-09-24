#!/usr/bin/env python3
"""
Generate the English customer-facing product deck for Open ACE.
Output: docs/marketing/Open-ACE-Product-Overview.pptx

Mirrors scripts/make_pitch_deck.py (v1.2.0 content, 13 pages) with English
copy sized for each layout: Arial for text, shortened card titles, and all
layout fixes from the Chinese deck (left-aligned code block, single-codepoint
emoji, header-clear architecture page) applied.
"""

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ----------------------------------------------------------------------------
# Palette (identical to the Chinese deck)
# ----------------------------------------------------------------------------
NAVY = RGBColor(0x0B, 0x1F, 0x3A)
BLUE = RGBColor(0x1E, 0x6F, 0xF5)
CYAN = RGBColor(0x22, 0xD3, 0xEE)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x1E, 0x29, 0x3B)
GRAY = RGBColor(0x64, 0x74, 0x8B)
GREEN = RGBColor(0x16, 0xA3, 0x4A)
AMBER = RGBColor(0xF5, 0x9E, 0x0B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARKCARD = RGBColor(0x14, 0x2A, 0x4A)
DARKER = RGBColor(0x10, 0x2A, 0x4D)
LIGHTBLUE_TEXT = RGBColor(0xD8, 0xE2, 0xF2)

FONT = "Arial"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]

# ----------------------------------------------------------------------------
# Helpers
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
        r.font.name = FONT
        first = False
    return tb


def bg(slide, color):
    rect(slide, 0, 0, SW, SH, color)


def card(slide, x, y, w, h, fill_color=CARD):
    sp = round_rect(slide, x, y, w, h, fill_color)
    sp.line.color.rgb = RGBColor(0xE2, 0xE8, 0xF0)
    sp.line.width = Pt(0.75)
    return sp


def page_header(slide, kicker, title, accent=BLUE):
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
    r.font.name = FONT
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
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.word_wrap = False
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size = Pt(11)
    r.font.bold = True
    r.font.color.rgb = WHITE
    r.font.name = FONT
    return sp


TOTAL = 13

# ============================================================================
# Page 1: Cover
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
r.text = "Self-Hosted AI Coding Agent"
r.font.size = Pt(34)
r.font.bold = True
r.font.color.rgb = WHITE
r.font.name = FONT
p2 = tf.add_paragraph()
r2 = p2.add_run()
r2.text = "Workspace & Governance Control Plane"
r2.font.size = Pt(30)
r2.font.bold = True
r2.font.color.rgb = WHITE
r2.font.name = FONT

text(
    s,
    Inches(1.2),
    Inches(4.7),
    Inches(10),
    Inches(0.5),
    [
        (
            "One place for every AI coding tool your team uses — keys, cost, and risk under control.",
            15,
            RGBColor(0xC7, 0xD6, 0xEA),
            False,
        )
    ],
)
chip(s, Inches(1.2), Inches(5.5), Inches(1.7), "Apache 2.0", BLUE)
chip(s, Inches(3.05), Inches(5.5), Inches(1.3), "v1.2.0", CYAN)
chip(s, Inches(4.5), Inches(5.5), Inches(2.1), "Self-Hosted First", RGBColor(0x12, 0x7A, 0x4E))
text(
    s,
    Inches(1.2),
    Inches(6.8),
    Inches(11),
    Inches(0.3),
    [("Product Overview for Customers", 12, RGBColor(0x7E, 0x93, 0xB6), False)],
)

# ============================================================================
# Page 2: The problem
# ============================================================================
s = add_slide()
page_header(s, "THE PROBLEM", "When AI Coding Tools Meet Teams, the Real Work Begins")
page_number(s, 2, TOTAL)

pains = [
    (
        "🔑",
        "Keys Everywhere",
        "API keys scattered across laptops and remote machines — high leak risk, hard to rotate, no traceability",
    ),
    (
        "🖥️",
        "Hard to Run Remotely",
        "AI CLIs belong on intranet dev, staging, or GPU machines; repeatedly sharing SSH credentials is clumsy",
    ),
    (
        "🧩",
        "Fragmented Tools",
        "Claude Code, Qwen Code, Codex, OpenClaw each on their own — no unified entry point or history",
    ),
    (
        "💸",
        "Cost Blind Spot",
        "How many tokens, how much money, who is using what, any overspend — largely a black box",
    ),
    (
        "🛡️",
        "No Audit Trail",
        "Compliance, audit, quotas, and sensitive-content screening are missing — production is out of reach",
    ),
    (
        "📊",
        "Unproven ROI",
        "AI payback is unclear, efficiency gains are unquantified, and decisions lack evidence",
    ),
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
        [(d, 12, RGBColor(0xB8, 0xC6, 0xDE), False)],
    )

# ============================================================================
# Page 3: Positioning
# ============================================================================
s = add_slide()
bg(s, LIGHT)
rect(s, 0, 0, Inches(0.16), SH, BLUE)
text(s, Inches(0.7), Inches(0.5), Inches(10), Inches(0.32), [("WHAT IS IT", 13, BLUE, True)])
text(
    s,
    Inches(0.7),
    Inches(0.82),
    Inches(12),
    Inches(0.6),
    [("One Platform, Double Value", 28, INK, True)],
)

card(s, Inches(0.9), Inches(1.8), Inches(11.5), Inches(1.9), WHITE)
text(
    s,
    Inches(1.3),
    Inches(2.05),
    Inches(10.8),
    Inches(0.5),
    [("Open ACE = AI Coding Workspace + Governance Control Plane", 22, NAVY, True)],
)
text(
    s,
    Inches(1.3),
    Inches(2.65),
    Inches(10.8),
    Inches(1.0),
    [
        (
            "Developers use Claude Code, Qwen Code, Codex, OpenClaw and more from one browser workspace, running them on the team's own remote machines;",
            13.5,
            GRAY,
            False,
        ),
        (
            "administrators centrally manage API keys, access, cost, quotas, audit, and compliance — fully self-hosted, data never leaves your network.",
            13.5,
            GRAY,
            False,
        ),
    ],
    line_spacing=1.25,
)

quad = [
    (
        "🌐",
        "Unified Entry",
        "Many AI CLIs, one browser workspace, one history, one governance layer",
    ),
    (
        "🖥️",
        "Remote Execution",
        "Remote Agent runs CLIs on target machines — no credential handoffs",
    ),
    (
        "🔑",
        "Key Security",
        "Keys stay encrypted server-side; sessions get short-lived proxy tokens",
    ),
    ("📊", "Governance Visibility", "Tokens, cost, quotas, audit, compliance, ROI — at a glance"),
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
        s, x + Inches(0.3), qy0 + Inches(1.0), qw - Inches(0.6), Inches(0.4), [(t, 15, NAVY, True)]
    )
    text(
        s,
        x + Inches(0.3),
        qy0 + Inches(1.4),
        qw - Inches(0.6),
        Inches(1.05),
        [(d, 11.5, GRAY, False)],
    )

# ============================================================================
# Page 4: Two modes
# ============================================================================
s = add_slide()
page_header(s, "TWO MODES", "Two Modes, Two Audiences")
page_number(s, 4, TOTAL)

rect(s, Inches(0.7), Inches(1.8), Inches(5.85), Inches(4.7), DARKCARD)
rect(s, Inches(0.7), Inches(1.8), Inches(5.85), Inches(0.7), BLUE)
text(
    s,
    Inches(1.0),
    Inches(1.95),
    Inches(5.5),
    Inches(0.45),
    [("🚀  Work Mode — Your AI Super Assistant", 16, WHITE, True)],
)
text(
    s,
    Inches(1.0),
    Inches(2.7),
    Inches(5.3),
    Inches(0.4),
    [("For every developer: a smooth AI experience", 12, CYAN, False)],
)
tfw = text(s, Inches(1.0), Inches(3.2), Inches(5.3), Inches(3.1), []).text_frame
tfw.word_wrap = True
for t in [
    "Multi-tool: Claude Code / Qwen Code / Codex / OpenClaw / ZCode in one place",
    "Remote workspace: pick a machine in the browser, start AI sessions, terminal, file browsing",
    "Smart sessions: history, recovery, context memory, cross-tool sync",
    "Prompt library: share best prompts across the team, reuse in one click",
    "Quick search: find any past conversation, knowledge never lost",
]:
    bullet(tfw, t, size=12, color=LIGHTBLUE_TEXT)

rect(s, Inches(6.78), Inches(1.8), Inches(5.85), Inches(4.7), DARKCARD)
rect(s, Inches(6.78), Inches(1.8), Inches(5.85), Inches(0.7), CYAN)
text(
    s,
    Inches(7.08),
    Inches(1.95),
    Inches(5.5),
    Inches(0.45),
    [("📊  Manage Mode — Data-Driven AI Governance", 16, NAVY, True)],
)
text(
    s,
    Inches(7.08),
    Inches(2.7),
    Inches(5.3),
    Inches(0.4),
    [("For administrators: full insight and control", 12, BLUE, False)],
)
tfm = text(s, Inches(7.08), Inches(3.2), Inches(5.3), Inches(3.1), []).text_frame
tfm.word_wrap = True
for t in [
    "Usage visibility: token trends, cost analysis, usage heatmaps",
    "API key governance: encrypted storage, proxy-token calls, no key distribution",
    "Smart alerts: quota warnings, anomaly detection, overspend notices",
    "Compliance audit: sensitive-content detection, traceable history, CSV reports",
    "Multi-tenant + ROI: department isolation and quantified payback",
]:
    bullet(tfm, t, size=12, color=LIGHTBLUE_TEXT)

# ============================================================================
# Page 5: Capabilities
# ============================================================================
s = add_slide()
light_page_header(s, "CAPABILITIES", "Eight Core Capabilities, Full AI Coding Lifecycle")
page_number(s, 5, TOTAL)

caps = [
    (
        "🎛️",
        "Agent Control Plane",
        "Manage tools, users, machines, keys, quotas, cost and audit in one place — without replacing any CLI",
    ),
    (
        "🤖",
        "Multi-CLI Adapters",
        "Native adapters for Claude Code, Qwen Code, Codex, OpenClaw, ZCode — with session recovery and permission modes",
    ),
    (
        "🖥️",
        "Remote Agent",
        "AI CLIs run on Linux/macOS/Windows machines with in-browser terminal and VSCode proxy",
    ),
    (
        "🔑",
        "API Key Proxy",
        "Real keys encrypted server-side; remote agents hold short-lived tokens with unified quotas",
    ),
    (
        "🤖",
        "Autonomous Dev",
        "End-to-end workflow: plan → code → PR review → report; retry/fork/milestones, gVisor/Kata sandbox",
    ),
    (
        "🔌",
        "Model Gateway",
        "LiteLLM-compatible gateway, vendor-neutral, keeps quotas and attribution intact",
    ),
    (
        "☁️",
        "Cloud-Native",
        "One-command Kustomize deploy, 3 replicas + HPA + Prometheus metrics, OpenShift-compatible",
    ),
    (
        "🏢",
        "Enterprise Integration",
        "SSO / Feishu / DingTalk, multi-tenant permissions, Kubernetes, reverse proxy",
    ),
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
        s, x + Inches(0.28), y + Inches(0.85), cw - Inches(0.5), Inches(0.35), [(t, 14, NAVY, True)]
    )
    text(
        s,
        x + Inches(0.28),
        y + Inches(1.22),
        cw - Inches(0.5),
        Inches(0.88),
        [(d, 10.5, GRAY, False)],
    )

# ============================================================================
# Page 6: Architecture
# ============================================================================
s = add_slide()
page_header(s, "HOW IT WORKS", "Remote Agent + API Key Proxy: Secure and Efficient")
page_number(s, 6, TOTAL)

layers = [
    ("Browser (React SPA)", "Work Mode (everyone)  ·  Manage Mode (admins)", BLUE, Inches(1.55)),
    (
        "Flask API Server (self-hosted)",
        "23 blueprints · encrypted key storage · quotas/audit/compliance · issues short-lived proxy tokens",
        CYAN,
        Inches(3.25),
    ),
    (
        "Remote Agent (daemon on your machines)",
        "HTTP polling · CLI subprocesses · WebSocket PTY terminal · session sync (Claude/Qwen/Codex/OpenClaw)",
        RGBColor(0x12, 0x7A, 0x4E),
        Inches(4.95),
    ),
]
for title_, sub, color, y in layers:
    rect(s, Inches(0.7), y, Inches(8.2), Inches(1.4), DARKCARD)
    rect(s, Inches(0.7), y, Inches(0.12), Inches(1.4), color)
    text(s, Inches(1.0), y + Inches(0.18), Inches(7.8), Inches(0.4), [(title_, 16, WHITE, True)])
    text(
        s,
        Inches(1.0),
        y + Inches(0.62),
        Inches(7.8),
        Inches(0.7),
        [(sub, 12, RGBColor(0xC0, 0xCF, 0xE6), False)],
    )

for ay in [Inches(2.99), Inches(4.69)]:
    ar = s.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(4.6), ay, Inches(0.4), Inches(0.22))
    fill(ar, CYAN)
    ar.shadow.inherit = False

card(s, Inches(9.2), Inches(1.7), Inches(3.45), Inches(4.65), DARKER)
text(s, Inches(9.5), Inches(1.95), Inches(2.9), Inches(0.4), [("Why this design?", 16, CYAN, True)])
tfd = text(s, Inches(9.5), Inches(2.45), Inches(2.9), Inches(3.4), []).text_frame
tfd.word_wrap = True
for t in [
    "Real API keys never leave the server",
    "Remote machines hold only short-lived tokens",
    "Tokens are quota-able, revocable, traceable",
    "Works on intranet / private-network machines",
    "Terminal screens recover after disconnects",
    "Vendor-neutral, LiteLLM-ready",
]:
    bullet(tfd, t, size=12, color=LIGHTBLUE_TEXT)

text(
    s,
    Inches(0.7),
    Inches(6.6),
    Inches(12),
    Inches(0.4),
    [
        (
            "✦  Built for teams that need self-hosting, intranet machines, centralized key proxying, team quotas, and traceable audit",
            13,
            AMBER,
            True,
        )
    ],
)

# ============================================================================
# Page 7: Kubernetes / OpenShift
# ============================================================================
s = add_slide()
page_header(s, "CLOUD-NATIVE", "Kubernetes / OpenShift Cloud-Native Integration")
page_number(s, 7, TOTAL)

k8s_cols = [
    (
        "☁️",
        "HA Deployment on K8s",
        BLUE,
        [
            "One-command Kustomize deploy (kubectl apply -k k8s/)",
            "3 replicas + HPA autoscaling (3→10 pods)",
            "PostgreSQL / Redis StatefulSets, persistent",
            "/livez /readyz probes + Prometheus /metrics",
            "PDB + anti-affinity + graceful rolling updates",
            "Least-privilege NetworkPolicy + non-root",
        ],
    ),
    (
        "🛡️",
        "AI Sandbox Isolation",
        RGBColor(0x12, 0x7A, 0x4E),
        [
            "Backends per tenant/project: POSIX / remote / sandbox",
            "gVisor / Kata dual-tier secure runtimes",
            "Non-root + read-only rootfs + seccomp",
            "CPU / memory / PIDs / storage limits + TTL",
            "Default-deny egress + FQDN allowlist",
            "Fail-closed: refuse and audit on weak isolation",
        ],
    ),
    (
        "👥",
        "Multi-User Isolation",
        CYAN,
        [
            "Per-user WebUI in its own sandbox pod, token identity",
            "No host OS accounts / sudo chain / host credentials",
            "Tenant-scoped OS isolation, revocable",
            "Stable UIDs across rebuilds; 60s quota sweeps",
            "Session snapshots survive pod recycling",
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
        [(t, 14, WHITE, True)],
    )
    tfk = text(s, x + Inches(0.3), y0 + Inches(1.0), cw - Inches(0.6), Inches(3.4), []).text_frame
    tfk.word_wrap = True
    for it in items:
        bullet(tfk, it, size=10.5, color=LIGHTBLUE_TEXT, space_before=7)

rect(s, Inches(0.7), Inches(6.4), Inches(12.63), Inches(0.55), DARKER)
rect(s, Inches(0.7), Inches(6.4), Inches(0.1), Inches(0.55), AMBER)
text(
    s,
    Inches(1.0),
    Inches(6.5),
    Inches(12.2),
    Inches(0.4),
    [
        (
            "🖥️  OpenShift / OKD compatible — standard Kustomize manifests; non-root, read-only rootfs and seccomp satisfy restricted SCC; SSRF guard covers OpenShift internal DNS",
            10.5,
            RGBColor(0xE8, 0xD5, 0xA8),
            False,
        )
    ],
)

# ============================================================================
# Page 8: Value
# ============================================================================
s = add_slide()
light_page_header(s, "WHY IT MATTERS", "Value for Developers, Managers, and the Enterprise")
page_number(s, 8, TOTAL)

cols = [
    (
        "💻",
        "For Developers",
        BLUE,
        [
            "Every AI coding tool in one entry",
            "Remote machines ready to use, no setup",
            "Session history preserved, knowledge retained",
            "Focus on code — keys and envs handled by the platform",
        ],
    ),
    (
        "👔",
        "For Managers",
        CYAN,
        [
            "Tokens / cost / quotas on one screen",
            "Alerts and anomaly detection, early warnings",
            "Audit and report exports, production-ready",
            "Quantified ROI — AI payback made clear",
        ],
    ),
    (
        "🏢",
        "For the Enterprise",
        RGBColor(0x12, 0x7A, 0x4E),
        [
            "Data and keys never leave your network",
            "Self-hosted deployment, compliance-ready",
            "Multi-tenant isolation, per-department accounting",
            "Open source control, no vendor lock-in",
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
        s, x + Inches(1.1), y0 + Inches(0.28), cw - Inches(1.3), Inches(0.5), [(t, 17, WHITE, True)]
    )
    tfc = text(s, x + Inches(0.35), y0 + Inches(1.25), cw - Inches(0.7), Inches(3.2), []).text_frame
    tfc.word_wrap = True
    for it in items:
        bullet(tfc, it, size=12.5, color=INK, space_before=8)

# ============================================================================
# Page 9: Comparison
# ============================================================================
s = add_slide()
light_page_header(s, "COMPARISON", "Positioning vs. Market Alternatives")
page_number(s, 9, TOTAL)

rows = [
    (
        "Capability",
        "Open ACE",
        "LLM Gateways\n(LiteLLM etc.)",
        "AI IDEs/Plugins\n(Cursor etc.)",
        "DIY Scripts",
    ),
    ("Role", "Workspace + control plane", "Routing & billing", "Solo coding UX", "Ad hoc"),
    ("Self-hosting", "✅ Default self-hosted", "✅", "❌ Mostly cloud", "✅"),
    (
        "Multi-CLI unified entry",
        "✅ Claude/Qwen/Codex etc.",
        "❌ Model layer only",
        "⚠️ Walled gardens",
        "❌",
    ),
    ("Remote Agent execution", "✅ In-browser remote machines", "❌", "❌", "⚠️ DIY"),
    (
        "Keys never leave server",
        "✅ Short-lived proxy tokens",
        "⚠️ Via gateway",
        "❌ Local keys",
        "❌",
    ),
    ("Cost/quota/audit/compliance", "✅ Unified governance", "⚠️ Billing only", "❌", "❌"),
    ("Multi-tenant / SSO / Feishu", "✅", "⚠️ Partial", "❌", "❌"),
    ("K8s-native & AI sandboxing", "✅ HPA + gVisor/Kata", "⚠️ Container only", "❌", "❌ DIY"),
    ("License", "Apache 2.0", "Varies", "Closed", "—"),
]
ncol = len(rows[0])
nrow = len(rows)
tx = Inches(0.5)
ty = Inches(1.5)
col_w = [Inches(2.85), Inches(2.95), Inches(2.6), Inches(2.4), Inches(1.53)]
row_h = Inches(0.5)
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
        r.font.name = FONT
    xx += cw_
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
        r.font.size = Pt(10)
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
        r.font.name = FONT
        xx += cw_

rect(s, tx + col_w[0], ty - Inches(0.12), col_w[1], Inches(0.1), CYAN)

# ============================================================================
# Page 10: Differentiators
# ============================================================================
s = add_slide()
page_header(s, "KEY DIFFERENTIATORS", "Why Open ACE?")
page_number(s, 10, TOTAL)

diffs = [
    (
        "🔒",
        "Self-Hosted by Default",
        "Keys and session data stay in your own network and database — private, compliant, no cloud dependency.",
    ),
    (
        "🎛️",
        "Control Plane, Not a CLI",
        "Replaces none of your tools: brings the CLIs your team already uses under unified access, cost, and audit.",
    ),
    (
        "🔑",
        "Short-Lived Proxy Tokens",
        "Real LLM keys stay encrypted server-side; sessions hold expiring tokens — quota-able, revocable, traceable.",
    ),
    (
        "🖥️",
        "Real Remote Execution",
        "AI CLIs run on dev/staging/GPU machines; in-browser terminal + VSCode proxy, no repeated SSH handoffs.",
    ),
    (
        "🛡️",
        "Cloud-Native, Sandboxed",
        "HA Kubernetes deploy; autonomous dev and multi-user workspaces run in gVisor/Kata sandboxes, fail-closed.",
    ),
    (
        "🌐",
        "Vendor-Neutral + Tenancy",
        "LiteLLM-compatible gateway, unified multi-CLI governance, tenant isolation and accounting.",
    ),
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
        s, x + Inches(0.32), y + Inches(0.92), cw - Inches(0.6), Inches(0.4), [(t, 15, WHITE, True)]
    )
    text(
        s,
        x + Inches(0.32),
        y + Inches(1.35),
        cw - Inches(0.6),
        Inches(0.88),
        [(d, 11, RGBColor(0xC0, 0xCF, 0xE6), False)],
    )

# ============================================================================
# Page 11: Use cases
# ============================================================================
s = add_slide()
light_page_header(s, "USE CASES", "Who Needs Open ACE Most?")
page_number(s, 11, TOTAL)

scenes = [
    (
        "Engineering Teams",
        "Already on Claude Code / Qwen Code / Codex and want one entry, shared prompts, and session continuity.",
    ),
    (
        "AI Platform Teams",
        "Building internal AI tooling that needs key proxying, remote execution, and one governance plane.",
    ),
    (
        "DevOps / IT Teams",
        "Own self-hosted infrastructure, audit, and access control; need private deployment and multi-tenancy.",
    ),
    (
        "Security & Compliance",
        "No shared public demos, no scattered keys — needs audit trails and exportable compliance reports.",
    ),
    (
        "GPU / Compute Teams",
        "AI CLIs must run on GPU or staging machines; browser remote workspaces and terminals.",
    ),
    (
        "Multi-Department Enterprises",
        "Need department isolation, independent quota accounting, SSO, and Feishu/DingTalk integration.",
    ),
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
    text(
        s, x + Inches(0.3), y + Inches(0.18), cw - Inches(0.5), Inches(0.4), [(t, 14.5, NAVY, True)]
    )
    text(
        s, x + Inches(0.3), y + Inches(0.58), cw - Inches(0.5), Inches(0.85), [(d, 11, GRAY, False)]
    )

# ============================================================================
# Page 12: Get started
# ============================================================================
s = add_slide()
page_header(s, "GET STARTED", "Start in 5 Minutes, Roll Out Incrementally")
page_number(s, 12, TOTAL)

card(s, Inches(0.7), Inches(1.8), Inches(6.0), Inches(4.6), DARKER)
text(
    s,
    Inches(1.0),
    Inches(2.0),
    Inches(5.5),
    Inches(0.4),
    [("🚀 One-Command Deploy (Recommended)", 16, CYAN, True)],
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
        "# open http://localhost:19888",
    ]
):
    p = code_tf.paragraphs[0] if li == 0 else code_tf.add_paragraph()
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run()
    r.text = line if line else " "
    r.font.size = Pt(11)
    r.font.color.rgb = GREEN if line.startswith("#") else RGBColor(0xA7, 0xF3, 0xD0)
    r.font.name = "Consolas"
text(
    s,
    Inches(1.0),
    Inches(4.25),
    Inches(5.5),
    Inches(0.4),
    [("📦 Other deployment options", 14, CYAN, True)],
)
tfdep = text(s, Inches(1.0), Inches(4.65), Inches(5.4), Inches(1.6), []).text_frame
tfdep.word_wrap = True
for t in [
    "Source install (local development)",
    "Kubernetes Kustomize (3 replicas + HPA + Prometheus)",
    "Linux systemd / macOS launchd remote agents",
    "SQLite (single box) or PostgreSQL (production)",
]:
    bullet(tfdep, t, size=12, color=LIGHTBLUE_TEXT)

card(s, Inches(6.9), Inches(1.8), Inches(5.75), Inches(4.6), DARKER)
text(
    s,
    Inches(7.2),
    Inches(2.0),
    Inches(5.2),
    Inches(0.4),
    [("🗺️ Incremental Rollout", 16, CYAN, True)],
)
steps = [
    ("Step 1", "Try locally with Docker; evaluate both modes"),
    ("Step 2", "Onboard team keys; enable the API Key proxy"),
    ("Step 3", "Deploy Remote Agents to dev/staging/GPU hosts"),
    ("Step 4", "Move to HA Kubernetes; enable AI sandboxing"),
    ("Step 5", "Quotas, audit, SSO + compliance & ROI reports"),
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
    r.font.name = FONT
    text(
        s,
        Inches(8.2),
        y + Inches(0.08),
        Inches(4.3),
        Inches(0.5),
        [(v, 12, LIGHTBLUE_TEXT, False)],
        anchor=MSO_ANCHOR.MIDDLE,
    )

# ============================================================================
# Page 13: CTA
# ============================================================================
s = add_slide()
bg(s, NAVY)
rect(s, 0, Inches(6.7), SW, Inches(0.8), RGBColor(0x07, 0x16, 0x29))
rect(s, Inches(0.9), Inches(2.2), Inches(0.14), Inches(2.6), CYAN)
text(
    s,
    Inches(1.2),
    Inches(2.2),
    Inches(11.5),
    Inches(0.6),
    [("Bring AI coding agents in.", 38, WHITE, True)],
)
text(
    s,
    Inches(1.2),
    Inches(2.95),
    Inches(11.5),
    Inches(0.6),
    [("Keep keys, cost, and risk under control.", 38, CYAN, True)],
)
text(
    s,
    Inches(1.2),
    Inches(3.95),
    Inches(10.5),
    Inches(0.5),
    [
        (
            "Open ACE — the self-hosted workspace and control plane for AI coding agents.",
            15,
            RGBColor(0xC7, 0xD6, 0xEA),
            False,
        )
    ],
)

cta = [
    ("🌐", "Website", "www.open-ace.com"),
    ("💻", "Repository", "github.com/open-ace/open-ace"),
    ("📚", "Docs", "open-ace.github.io/open-ace-docs"),
    ("💬", "Feedback", "GitHub Discussions"),
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
        [(v, 10.5, WHITE, False)],
    )

text(
    s,
    Inches(0.9),
    Inches(6.9),
    Inches(11),
    Inches(0.4),
    [
        (
            "Apache 2.0 open source  ·  Self-hosted first  ·  Vendor-neutral  ·  Built for real engineering workflows",
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
out_path = os.path.join(out_dir, "Open-ACE-Product-Overview.pptx")
prs.save(out_path)
print("Generated:", out_path)
print("Slides:", len(prs.slides._sldIdLst))

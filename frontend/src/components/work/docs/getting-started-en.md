# 🚀 Getting Started — Quick Start Guide

> Welcome to Open ACE! This guide is designed for **first-time users** to help you get started quickly and avoid common pitfalls.

---

## 1. What is Open ACE?

**Open ACE** is an open-source **enterprise AI work platform** that helps you solve two key problems:

| Problem | Solution |
|---------|----------|
| 🤔 **Is AI being used effectively?** | **Work Mode** — Use AI efficiently to boost productivity |
| 😰 **Is AI being managed properly?** | **Manage Mode** — Give managers full control over AI usage, reducing risks |

**Core Capabilities:**
- 🤖 **Multi-AI Tool Integration** — Supports Qwen Code, Claude Code, OpenClaw and other mainstream AI tools, all in one platform
- 💬 **Smart Conversation Management** — History records, session recovery, context memory, conversations never interrupted
- 📝 **Prompt Library** — Team-shared quality prompts, best practices reused with one click
- 🌐 **Remote Workspace** — Operate remote machines in browser, AI runs directly on remote servers
- 📊 **Usage Analytics** — Token consumption, cost analysis, efficiency improvement, data-driven decisions

**Who should use it?**
- Developers who frequently use AI coding assistants
- Teams that want to centrally manage AI tools and costs
- Organizations that want private deployment to ensure data security

---

## 2. Preparation Before First Use

### 1. Basic Environment

Before starting, make sure:

- ✅ **Open ACE is deployed** — Your team has deployed Open ACE platform (local or remote)
- ✅ **Account registered** — You have a login account (contact admin or use default account)
- ✅ **Browser support** — Chrome, Edge, Firefox or other modern browsers recommended

### 2. Login to Platform

1. Open browser, access Open ACE address (e.g., `http://localhost:5000` or your deployment address)
2. Enter **username** and **password**, click login

> 💡 **Default admin account**: `admin` / `admin123` (Please change default password in production!)

### 3. Key Configuration (Admin Operations)

If you are an **admin**, you need to configure before first use:

#### Add API Key

**Why configure?** Open ACE doesn't provide AI models itself, it helps you **centrally manage** API Keys for multiple AI tools, so team members don't need to configure individually.

**Configuration steps:**
1. After login, switch to **Manage Mode** (click top mode switcher)
2. Left sidebar → **Remote Workspaces** → **API Keys**
3. Click **"Add API Key"** button
4. Select Provider (e.g., OpenAI, Anthropic, Google), enter API Key and optional Base URL
5. Click save

> ⚠️ **Important**: Remote workspace feature **requires** adding API Key in management page, environment variable Keys won't work automatically.

---

## 3. Quick Start for Core Features

### Scenario 1: Create a New Conversation Session

1. After login, you enter **Work Mode** by default
2. In **left sidebar**, find **"Sessions"** area
3. Click **"New Session"** button (usually a `+` icon)
4. In the dialog:
   - Select **workspace type** (Local Workspace or Remote Workspace)
   - If selecting Remote Workspace, choose an **online remote machine** from the list
   - Enter **project path** (working directory on remote machine, auto-filled by default)
   - Enter **session title** (optional, for easier searching later)
5. Click **"Create"**, new workspace tab will open

### Scenario 2: Select Different AI Models

1. In workspace page, find top **tool selector**
2. Click dropdown menu, you can see available AI tools:
   - **Qwen** — Qwen series (e.g., qwen3-coder-plus)
   - **Claude** — Anthropic Claude series
   - **OpenClaw** — Other OpenAI compatible models
3. After selecting your tool, system will automatically load corresponding model
4. Some tools support **specifying model name**, enter when creating session

> 💡 **Tip**: Available AI tools depend on API Keys configured by admin. If a tool doesn't work, contact admin to check if API Key is valid.

### Scenario 3: Use Prompt Templates

**What are prompt templates?** Pre-written quality prompts to help you start quickly, no need to write prompts from scratch every time.

**How to use:**

1. In **right sidebar**, click **"Prompts"** tab
2. You'll see a prompt list, click any prompt to view details
3. Click **copy icon** next to prompt to copy content to clipboard
4. Paste copied content to workspace input box, modify as needed and send

**Browse more prompts:**
1. Click **"Prompts"** in left sidebar
2. Enter prompts management page to browse, search, filter all prompts
3. Click any prompt card to view full content and usage instructions

---

## 4. Interface Function Description

Open ACE provides two modes: **Work Mode** (available to all users) and **Manage Mode** (admin only).

### 🔵 Work Mode — Available to All Users

Work Mode is the daily work interface for regular users, using **three-column layout**:

```
┌─────────────────────────────────────────────────────────────┐
│  [Logo] Open ACE        [Mode Switcher]        [User Info]  │  ← Top Navigation
├──────────┬──────────────────────────────┬──────────────────┤
│          │                              │                  │
│  Session │                              │  Prompts         │
│  List    │      Main Workspace          │  Tools           │  ← Right Toolbar
│          │   (Conversation/Code)        │  Docs            │
│          │                              │                  │
│  Nav     │                              │                  │
│  Menu    │                              │                  │
├──────────┴──────────────────────────────┴──────────────────┤
│  [Status Bar: Token Usage | Request Usage]                  │  ← Bottom Status Bar
└─────────────────────────────────────────────────────────────┘
   ↑ Left Sidebar           ↑ Center Main Area
```

#### Left Sidebar Navigation

| Navigation Item | Description |
|-----------------|-------------|
| **Workspace** | Default AI conversation interface, interact with AI, write code |
| **Sessions** | View all history sessions, search and filter, restore previous conversations |
| **Prompts** | Browse and manage prompt library, quickly reuse quality prompts |
| **My Usage** | View personal Token usage and trend analysis |
| **Insights** | Get AI usage efficiency analysis and optimization suggestions |

#### Center Main Area

- **Conversation Area**: Display conversation history with AI, including messages, code blocks, thinking process
- **Input Box**: Enter questions or commands at bottom, press Enter to send
- **Toolbar**: Above or beside input box, provides send, pause, stop buttons
- **Fullscreen Mode**: Click fullscreen button to hide side panels, focus on conversation (press ESC to exit)

#### Right Toolbar

| Tab | Description |
|-----|-------------|
| **Prompts** | Quickly browse common prompts, one-click copy |
| **Tools** | Quick entry to switch different AI tools (Qwen, Claude, OpenClaw) |
| **Docs** | Help documentation entry (Getting Started, FAQ, etc.) |

> 💡 **Tip**: Right panel can be collapsed/expanded, click arrow next to panel title.

---

### 🟠 Manage Mode — Admin Only

Manage Mode is the admin-only backend management interface. **Regular users cannot access this mode**, accessing `/manage/*` will be redirected to Work Mode.

#### Navigation Groups

| Group | Navigation Item | Description |
|-------|-----------------|-------------|
| **Overview** | Dashboard | System overview, key metrics at a glance |
| **Analysis** | Token Trend | Token consumption trend analysis charts |
| | Request Statistics | API request statistics and visualization |
| | Anomaly Detection | Abnormal usage behavior detection and alerts |
| | ROI Analysis | Return on investment analysis reports |
| | Conversation History | All users' conversation history records |
| | Messages | Message statistics and management |
| **Governance** | Audit Center | Operation audit logs, compliance tracking |
| | Quota & Alerts | User quota settings and alert rules |
| | Compliance Management | Compliance policy configuration and management |
| | Security Center | Security settings and risk monitoring |
| **Users** | User Management | User account management, permission assignment |
| | Tenant Management | Multi-tenant management, tenant isolation |
| **Projects** | Project Management | Project creation and management |
| **Remote Workspaces** | Remote Machines | Remote machine registration and management |
| | API Keys | API Key centralized management |
| **Settings** | SSO Settings | Single sign-on configuration |

> 💡 **Tip**: All features in Manage Mode are admin-exclusive, regular users cannot access.

---

## 5. FAQ — Common Questions and Troubleshooting

### Q1: Why no response when sending message?

**Possible causes and solutions:**

1. **API Key not configured or invalid**
   - Contact admin to confirm API Key is correctly configured
   - Check if API Key is expired or disabled

2. **Network connection issue**
   - Check browser network is normal
   - Try refreshing page or re-login

3. **AI tool not selected**
   - Confirm valid AI tool is selected in top tool selector
   - If tool list is empty, backend hasn't configured available API Keys

### Q2: Model cannot be selected or shows error?

**Common situations:**

- **Tool shows but doesn't work** → API Key may be invalid, contact admin to check
- **Model name error** → Confirm model name is correct (e.g., `qwen3-coder-plus`)
- **"No available API Key" prompt** → Admin needs to add corresponding API Key in management page

### Q3: How to switch between different modes?

Open ACE has two modes:

| Mode | Entry | Target Users |
|------|-------|--------------|
| **Work Mode** | Default mode, available to all users | Regular users, developers |
| **Manage Mode** | Click top mode switcher → select "Manage" | Admin only visible |

**How to switch:**
1. At page top, find **Mode Switcher** (usually shows "Work" or "Manage")
2. Click switcher, select your desired mode
3. Page will automatically redirect to corresponding mode homepage

### Q4: How to find previous conversations?

1. Click **"Sessions"** in left sidebar
2. Use **search box** to enter keywords, filter history sessions
3. Click any session card to view full conversation
4. To continue that session, click **"Restore Session"** button

### Q5: Remote workspace cannot see machines?

**Troubleshooting steps:**

1. **Confirm machine registered** → Contact admin to confirm remote machine is registered and online
2. **Confirm permission assigned** → Admin needs to assign machine to you in management page
3. **Check network connectivity** → Remote machine needs to access Open ACE server
4. **Check machine status** → If machine shows "offline", check if remote Agent is running normally

### Q6: How to copy prompts?

1. In right panel "Prompts" tab, find your desired prompt
2. Click **copy icon** (clipboard icon) on right side of prompt
3. System will show "Copied", then paste to workspace input box

### Q7: Where to view Token usage?

- **Personal usage**: Left sidebar → **My Usage**
- **Detailed report**: Left sidebar → **Insights**
- **Real-time usage**: Bottom status bar shows current session Token consumption

### Q8: How to exit fullscreen mode?

- Press **ESC** key on keyboard
- Or click fullscreen button (if visible)

---

## 6. Next Steps — Advanced Usage Suggestions

Congratulations! After completing above content, you can proficiently use Open ACE. Next you can try:

### 🎯 Custom Prompts

- In prompts management page, click **"New Prompt"**
- Create your own prompt templates, set categories and tags
- Share your best practices with team

### 🌐 Remote Workspace Advanced

- Learn how to register remote machines, install Agent
- Configure multiple AI tool API Keys for different scenarios
- Use pause/resume function to manage long-running tasks

### 📊 Data Analysis (Admin)

- Switch to Manage Mode, view team overall usage
- Set quota alerts to avoid over-consumption
- Generate compliance reports for audit requirements

### 🔍 Search and Filter

- Use keyword search in session list to quickly locate history conversations
- Filter by date, tool, user for precise finding

### 🤝 Join Community

- Have ideas or bugs? [Join discussion](https://github.com/open-ace/open-ace)

---

> **Maximize AI value, Minimize AI risk**
>
> *Open ACE — Your AI Governance Expert*

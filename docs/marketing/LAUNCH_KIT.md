# Open ACE Launch Kit

Use this kit to introduce Open ACE to early users, developer communities, and teams adopting AI coding tools. The goal is not broad traffic at any cost. The goal is to reach people who already feel the pain of managing Claude Code, Qwen Code, Codex, OpenClaw, API keys, remote machines, cost, quotas, and audit trails across a team.

## Core Message

### One-liner

Open ACE is a self-hosted AI workspace and governance platform for teams using Claude Code, Qwen Code, Codex, OpenClaw, and other AI coding tools.

### Problem

AI coding tools often start as individual experiments. Once a team adopts them, the operational questions become messy:

- Who has access to which tool?
- Where are API keys stored?
- How do we run AI CLIs on development, staging, or GPU machines?
- How much are teams spending?
- Which sessions, prompts, and outputs need audit trails?
- How do administrators set quotas without blocking useful work?

### Solution

Open ACE gives teams one self-hosted place for AI work and AI governance:

- Work Mode for sessions, prompts, history, remote workspaces, terminals, and directory browsing.
- Manage Mode for token usage, cost, quotas, anomalies, audit trails, compliance reports, and ROI.
- Remote Agent for running AI CLIs on remote machines from the browser.
- API Key proxy so real LLM keys stay encrypted on the server while sessions receive short-lived proxy tokens.

### Audience

Primary:

- Engineering teams adopting Claude Code, Qwen Code, Codex, or OpenClaw.
- AI platform teams building internal AI tooling.
- DevOps and IT teams responsible for self-hosted infrastructure, audit, and access control.

Secondary:

- Open source developers interested in LLMOps and self-hosted developer tools.
- Security-conscious teams that cannot use shared public demos or unmanaged API keys.

## Launch Channels

### High Fit

| Channel | Goal | Suggested Format |
|---------|------|------------------|
| GitHub Discussions | Recruit early adopters and collect feedback | Early adopter post |
| Hacker News Show HN | Reach builders and self-hosted tool evaluators | Short, technical launch |
| V2EX | Reach Chinese developers using AI coding tools | Personal, feedback-seeking post |
| Juejin / SegmentFault / OSChina | Explain the product and architecture | Longer Chinese launch article |
| LinkedIn / X | Reach engineering leaders and AI platform teams | Short positioning post |

### Medium Fit

| Channel | Goal | Suggested Format |
|---------|------|------------------|
| Reddit selfhosted | Reach self-hosted users | Only post if it follows subreddit rules |
| Reddit devtools / local AI communities | Reach tool builders | Architecture-first post |
| Product Hunt | Later, when public demo/video is ready | Polished launch with visuals |

## Copy-Ready Posts

### Hacker News: Show HN

Title:

```text
Show HN: Open ACE - self-hosted workspace and governance for AI coding tools
```

Body:

```text
Hi HN,

We just released Open ACE v1.0.0, an Apache 2.0 self-hosted AI workspace and governance platform for teams using Claude Code, Qwen Code, Codex, OpenClaw, and similar AI coding tools.

The problem we are trying to solve: AI coding tools often start as individual experiments, but team adoption quickly raises operational questions:

- Where should API keys live?
- How do people run AI CLIs on development, staging, or GPU machines?
- How do administrators see token usage, cost, quotas, anomalies, and audit trails?
- How do teams keep sessions, prompts, and remote workspaces from becoming scattered?

Open ACE has two modes:

- Work Mode: AI sessions, prompt library, history, remote workspaces, browser terminal, and code-server/VSCode proxy.
- Manage Mode: token and cost dashboards, quotas, alerts, audit trails, compliance reports, and ROI visibility.

One design detail we care about: Remote Agent lets AI CLIs run on remote machines, while real LLM API keys stay encrypted on the Open ACE server. Remote sessions only receive short-lived proxy tokens.

Repo: https://github.com/open-ace/open-ace
Website: https://www.open-ace.com
Release: https://github.com/open-ace/open-ace/releases/tag/v1.0.0
Early adopter discussion: https://github.com/open-ace/open-ace/discussions/658

We are looking for early feedback from teams already using AI coding tools internally. I would especially love feedback on the Remote Agent/API Key proxy design, first-run Docker experience, and what governance data is actually useful.
```

### Reddit / Self-Hosted Communities

Use only where allowed by community rules. Avoid posting as an advertisement. Ask for architecture and self-hosting feedback.

```text
I am working on Open ACE, an Apache 2.0 self-hosted AI workspace and governance platform for teams adopting AI coding tools such as Claude Code, Qwen Code, Codex, and OpenClaw.

It is meant for the point where AI coding tools move from personal experiments to team infrastructure:

- AI sessions, prompts, history, and remote workspaces in one place
- Remote Agent to run AI CLIs on development/staging/GPU machines
- API Key proxy so real LLM keys stay encrypted on the server
- Usage, cost, quota, audit, compliance, and ROI dashboards for administrators
- Docker Compose quick start and PostgreSQL-backed deployment

Repo: https://github.com/open-ace/open-ace
Website: https://www.open-ace.com
Release: https://github.com/open-ace/open-ace/releases/tag/v1.0.0
Early adopter discussion: https://github.com/open-ace/open-ace/discussions/658

I am looking for feedback from people running internal developer tools or self-hosted AI infrastructure. What would make you trust or reject this kind of system?
```

### LinkedIn / X

```text
We released Open ACE v1.0.0.

Open ACE is an Apache 2.0 self-hosted AI workspace and governance platform for teams adopting Claude Code, Qwen Code, Codex, OpenClaw, and other AI coding tools.

The goal: give developers one place to work with AI sessions, prompts, history, remote machines, terminals, and code-server/VSCode access, while giving administrators visibility into token usage, cost, quotas, audit trails, compliance reports, and ROI.

One design choice we care about: real LLM API keys stay encrypted on the server. Remote agents receive short-lived proxy tokens instead of long-lived API keys.

Repo: https://github.com/open-ace/open-ace
Website: https://www.open-ace.com
Release: https://github.com/open-ace/open-ace/releases/tag/v1.0.0
Early adopter discussion: https://github.com/open-ace/open-ace/discussions/658

We are looking for early adopters using AI coding tools in teams. Feedback on first-run experience, Remote Agent, and governance workflows would be extremely useful.
```

### V2EX

Title:

```text
开源了一个自托管 AI Coding 工作台：统一 Claude Code / Qwen Code / Codex 的入口、远程机器、成本和审计
```

Body:

```text
大家好，我们刚发布了 Open ACE v1.0.0。

它是一个 Apache 2.0 的自托管 AI 工作台和治理平台，主要面向已经在团队里使用 Claude Code、Qwen Code、Codex、OpenClaw 等 AI coding 工具的人。

我们想解决的不是“再做一个聊天 UI”，而是团队真正开始用 AI coding tools 之后会遇到的问题：

- API Key 放在哪里？要不要下发到每台开发机？
- AI CLI 怎么跑在开发机、测试机、GPU 机器上？
- 管理员怎么看 Token、成本、配额、异常和审计？
- 会话历史、提示词、远程工作区怎么沉淀？
- 合规报告和使用数据怎么导出？

Open ACE 现在有两个模式：

- Work 模式：AI 会话、提示词库、历史记录、远程工作区、浏览器终端、目录浏览、code-server/VSCode 代理。
- Manage 模式：Token/成本、配额、告警、异常、审计、合规报告、ROI。

比较核心的设计是 Remote Agent + API Key proxy：AI CLI 可以跑在远程机器上，但真实 LLM API Key 加密保存在 Open ACE 服务端，远程 Agent 只拿短期代理令牌。

项目地址：https://github.com/open-ace/open-ace
官网：https://www.open-ace.com
Release：https://github.com/open-ace/open-ace/releases/tag/v1.0.0
早期用户反馈：https://github.com/open-ace/open-ace/discussions/658

想找早期试用者和架构反馈，尤其是：

1. 你们团队现在用哪些 AI coding 工具？
2. 最大痛点是 API Key、远程机器、成本、权限还是审计？
3. Docker Compose 首次启动体验有没有卡点？
4. Manage 模式里哪些治理数据对你真的有用？

欢迎拍砖。我们更想听真实使用场景，不追求只拿 star。
```

### Juejin / SegmentFault / OSChina

Title:

```text
Open ACE v1.0.0：一个自托管 AI Coding 工作台，统一工具入口、远程执行、成本和审计
```

Lead:

```text
AI coding tools 从个人尝鲜进入团队生产后，问题会从“哪个模型更强”变成“怎么统一入口、怎么管 API Key、怎么跑远程机器、怎么统计成本、怎么审计和设配额”。Open ACE 尝试把这些问题收进一个自托管平台。
```

Suggested outline:

```text
1. 为什么做 Open ACE
2. 团队使用 AI coding tools 后出现的治理问题
3. Work 模式：会话、提示词、历史记录、远程工作区
4. Manage 模式：Token、成本、配额、告警、审计、ROI
5. Remote Agent + API Key proxy 架构
6. Docker Compose 快速开始
7. v1.0.0 目前能做什么，不能做什么
8. 想找什么样的早期用户反馈
```

## 60-90 Second Demo Script

### Goal

Show that Open ACE is a real product with a workspace, governance dashboard, release, docs, and a clear self-hosted story.

### Recording Plan

1. GitHub repository, 5 seconds
   - Show README top, v1.0.0 release, Apache 2.0 license.
   - Voiceover: "Open ACE is an Apache 2.0 self-hosted AI workspace and governance platform."

2. Website, 8 seconds
   - Show Work/Manage screenshots.
   - Voiceover: "It gives teams one entry point for AI coding tools, and gives administrators visibility into usage, cost, quotas, and audit trails."

3. Docker quick start, 10 seconds
   - Show the quick start command, not necessarily a full terminal wait.
   - Voiceover: "You can start locally with Docker Compose and evaluate the core workflow."

4. Work Mode, 20 seconds
   - Show sessions, prompt library, history, remote workspace concept.
   - Voiceover: "Work Mode is for developers: sessions, prompts, history, remote machines, terminal access, and code-server/VSCode workflows."

5. Manage Mode, 20 seconds
   - Show usage, cost, quotas, audit/compliance views.
   - Voiceover: "Manage Mode is for operators: token usage, cost, quotas, alerts, compliance reports, and ROI."

6. Remote Agent/API Key proxy, 15 seconds
   - Use a simple architecture slide from the docs or the diagram in `REMOTE-WORKSPACE.md`.
   - Voiceover: "Remote Agent can run AI CLIs on target machines, while real API keys stay encrypted on the Open ACE server. Agents receive short-lived proxy tokens."

7. Call to action, 5 seconds
   - Show the early adopter discussion.
   - Voiceover: "We are looking for teams using AI coding tools internally. Try it, open an issue, or tell us what would make this useful in your environment."

## Outreach Templates

### Direct Message to Engineering Leaders

```text
Hi <name>, I saw your team is exploring AI coding tools. We just released Open ACE v1.0.0, an Apache 2.0 self-hosted workspace and governance platform for teams using Claude Code, Qwen Code, Codex, and similar tools.

The angle is not another chat UI. It is about operationalizing AI coding tools: API key governance, remote machine execution, token/cost visibility, quotas, audit trails, and team workflows.

Repo: https://github.com/open-ace/open-ace

Would you be open to giving feedback on whether this maps to your team's problems?
```

### Reply When Someone Says "How Is This Different?"

```text
Open ACE is focused on team adoption of AI coding tools, not just individual model access.

The differentiators are:

- Self-hosted workspace and governance in one product.
- Remote Agent for running AI CLIs on remote machines.
- API Key proxy so real keys stay on the server.
- Manage Mode for cost, quota, audit, compliance, and ROI.
- Multi-tool support for Claude Code, Qwen Code, Codex, OpenClaw, and more.
```

### Reply When Someone Says "Can I Try It?"

```text
Yes. The fastest path is Docker Compose:

git clone https://github.com/open-ace/open-ace.git
cd open-ace
docker compose up -d --build

Then open http://localhost:5000.

The default local account is documented in the README. For production, change the default password and configure SECRET_KEY before exposing the service.
```

## 14-Day Execution Plan

### Day 1

- Publish the GitHub early adopter discussion.
- Link the docs/site CTA to that discussion.
- Share the launch post on personal/company LinkedIn or X.

### Day 2

- Post the Chinese V2EX version.
- Reply to every serious comment with a specific answer and a link to the relevant doc.

### Day 3

- Post the technical article about Remote Agent/API Key proxy.
- Share it in engineering/AI platform circles.

### Day 4-5

- Record and publish the 60-90 second demo.
- Add the demo link to README, the docs site, and the early adopter discussion.

### Day 6-7

- Post Show HN if the demo and first-run path are ready.
- Monitor comments for repeated objections and convert them into docs/issues.

### Week 2

- Follow up with early users.
- Turn repeated questions into FAQ updates.
- Create 3-5 new `good first issue` tasks from feedback.
- Publish one "what we learned from early feedback" update.

## Success Signals

Do not optimize for one metric. Look for a cluster:

- More GitHub visitors and clone uniques after posts.
- New stars or forks from outside the maintainer network.
- Release asset downloads.
- New issues about first-run, deployment, Remote Agent, or API keys.
- Discussion replies from people describing real team workflows.
- External PRs or comments on `good first issue`.

The strongest signal is not a star. The strongest signal is someone saying: "We are using Claude Code/Qwen Code/Codex in a team, and this maps to a real problem."

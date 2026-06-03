# Looking for early adopters using Claude Code, Qwen Code, Codex, or OpenClaw in teams

We just published Open ACE v1.0.0:

- Repository: https://github.com/open-ace/open-ace
- Website: https://www.open-ace.com
- Release: https://github.com/open-ace/open-ace/releases/tag/v1.0.0

Open ACE is an Apache 2.0 self-hosted AI workspace and governance platform for teams adopting AI coding tools such as Claude Code, Qwen Code, Codex, OpenClaw, and similar tools.

The goal is not to build another chat UI. The goal is to help teams operationalize AI coding tools:

- Give developers one place for AI sessions, prompts, history, remote workspaces, terminals, and code-server/VSCode workflows.
- Give administrators visibility into token usage, cost, quotas, anomalies, audit trails, compliance reports, and ROI.
- Let AI CLIs run on remote machines through Remote Agent.
- Keep real LLM API keys encrypted on the Open ACE server and issue short-lived proxy tokens to local or remote sessions.

We are looking for early feedback from teams that already use AI coding tools internally.

## Questions

1. Which AI coding tools are you using today?
2. Are they used by individuals, a small team, or a broader engineering org?
3. Where do API keys live today?
4. Do you need AI tools to run on remote development, staging, or GPU machines?
5. What do you need to see before trusting a self-hosted AI governance platform?
6. Which governance data is actually useful: token cost, quotas, audit trails, compliance reports, ROI, or something else?
7. Did Docker Compose first-run work for you? If not, where did it fail?

## Helpful Feedback

The most useful replies are concrete:

- "We use <tool> with <N> developers, and our main issue is <problem>."
- "Remote Agent would/would not help because <reason>."
- "The first-run experience failed at <step>."
- "The governance dashboard should show <metric>."
- "We cannot adopt this unless <security/deployment/integration requirement>."

If you prefer issues instead of discussion replies:

- Bugs: https://github.com/open-ace/open-ace/issues
- Roadmap: https://github.com/open-ace/open-ace/blob/main/ROADMAP.md
- Contributing: https://github.com/open-ace/open-ace/blob/main/CONTRIBUTING.md

Thanks for taking a look. We care more about real deployment feedback than vanity metrics.

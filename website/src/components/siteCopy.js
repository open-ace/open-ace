export function getLocaleKey(locale) {
  return locale === 'zh-Hans' ? 'zh-Hans' : 'en';
}

export const homeCopy = {
  en: {
    featureCards: [
      {
        title: 'Unified AI coding workspace',
        description:
          'Run Claude Code, Qwen Code, Codex, and OpenClaw behind one self-hosted control plane.',
      },
      {
        title: 'Remote Agent execution',
        description:
          'Push coding sessions onto remote Linux, macOS, and Windows machines without handing out raw SSH access.',
      },
      {
        title: 'Governance and observability',
        description:
          'Track API key routing, permissions, quotas, cost, audit, and compliance from the same platform.',
      },
    ],
    audienceCards: [
      {
        eyebrow: 'For builders',
        title: 'Give developers one place to work with AI agents',
        body: 'Open ACE combines multi-tool sessions, remote workspaces, prompt reuse, and browser-based access.',
      },
      {
        eyebrow: 'For platform teams',
        title: 'Keep deployment and secrets inside your boundary',
        body: 'Remote Agent and API Key Proxy are designed for self-hosted environments, internal machines, and controlled access.',
      },
      {
        eyebrow: 'For governance',
        title: 'See usage, risk, and ROI without stitching tools together',
        body: 'Manage mode is built around cost visibility, quotas, audit trails, compliance reporting, and policy controls.',
      },
    ],
    signalItems: [
      {
        label: 'Work mode',
        value: 'Sessions, prompts, remote machines, browser workspace',
      },
      {
        label: 'Manage mode',
        value: 'API keys, quotas, audit, compliance, ROI',
      },
      {
        label: 'Best next read',
        value: 'Remote Agent, Deployment, Permission Model',
      },
    ],
    hero: {
      kicker: 'Open ACE',
      title: 'Self-hosted AI coding workspace for real engineering teams.',
      text:
        'Open ACE gives teams a browser-based workspace for AI coding agents plus the governance plane needed to run them on internal machines, controlled API keys, and auditable workflows.',
      docsCta: 'Read the docs',
      deployCta: 'Start deployment',
      phaseBadge: 'Phase 2',
      phaseLabel: 'Docs, product site, and project transparency',
    },
    sections: {
      core: {
        kicker: 'Core value',
        title: 'Three layers, one platform',
      },
      audience: {
        kicker: 'Why teams adopt it',
        title: 'Designed for self-hosted, multi-agent, governed AI engineering',
      },
      visuals: {
        kicker: 'Product in action',
        title: 'Two surfaces, one operating model',
        text:
          'The screenshots below anchor the public site in real product surfaces instead of abstract claims. Work mode is where developers interact with AI agents. Manage mode is where platform and security teams keep usage, policy, and audit in view.',
      },
      architecture: {
        kicker: 'How it fits together',
        title: 'From browser workspace to governed execution',
        text:
          'Open ACE is intentionally opinionated about where prompts run, where keys live, and where audit visibility accumulates.',
      },
      start: {
        kicker: 'Start here',
        title: 'Product, docs, and project visibility',
      },
    },
    screenshots: [
      {
        eyebrow: 'Work mode',
        title: 'Browser-based AI workspace for daily engineering',
        body: 'Sessions, remote workspaces, prompts, and project context live in the same workflow surface.',
        image: '/img/work-mode-en.png',
      },
      {
        eyebrow: 'Manage mode',
        title: 'Governance plane for keys, quotas, audit, and ROI',
        body: 'Platform teams can track spend, enforce boundaries, and review operational signals without stitching tools together.',
        image: '/img/manage-mode-en.png',
      },
    ],
    architectureCards: [
      {
        title: 'Browser workspace',
        body: 'Developers open one browser interface for sessions, prompts, remote terminals, and project context.',
      },
      {
        title: 'Remote execution',
        body: 'Remote Agent runs the chosen AI CLI on internal Linux, macOS, or Windows machines.',
      },
      {
        title: 'Policy and observability',
        body: 'API key routing, quotas, approval boundaries, and audit trails stay centralized in the control plane.',
      },
    ],
    linkCards: [
      {
        label: 'Documentation Home',
        title: 'Browse the structured docs entry point',
        to: '/docs/intro',
      },
      {
        label: 'Product Intro',
        title: 'Understand positioning, features, and architecture at a glance',
        to: '/docs/reference/intro',
      },
      {
        label: 'Deployment Guide',
        title: 'Go from local startup to production deployment decisions',
        to: '/docs/reference/deployment',
      },
      {
        label: 'Remote Agent',
        title: 'Learn how CLI agents are executed on controlled remote machines',
        to: '/docs/reference/remote-agent',
      },
      {
        label: 'Project Status',
        title: 'View roadmap, release history, and contribution entry points from the site',
        to: '/project',
      },
    ],
  },
  'zh-Hans': {
    featureCards: [
      {
        title: '统一的 AI Coding 工作台',
        description: '把 Claude Code、Qwen Code、Codex、OpenClaw 统一纳入一个自托管控制面。',
      },
      {
        title: 'Remote Agent 远程执行',
        description: '把 AI 编码会话推到 Linux、macOS、Windows 远程机器上运行，而不是下发原始 SSH 访问。',
      },
      {
        title: '治理与可观测性',
        description: '在同一平台里查看 API Key 路由、权限、配额、成本、审计和合规信号。',
      },
    ],
    audienceCards: [
      {
        eyebrow: '面向开发者',
        title: '给研发团队一个统一使用 AI Agent 的入口',
        body: 'Open ACE 把多工具会话、远程工作区、提示词复用和浏览器访问组织成一个完整工作流。',
      },
      {
        eyebrow: '面向平台团队',
        title: '把部署边界和密钥留在你自己的环境里',
        body: 'Remote Agent 和 API Key Proxy 都围绕自托管、内网机器和受控访问设计。',
      },
      {
        eyebrow: '面向治理',
        title: '不用拼接多套工具，也能看见成本、风险和 ROI',
        body: 'Manage 模式围绕成本可见性、配额、审计轨迹、合规报告和策略控制构建。',
      },
    ],
    signalItems: [
      {
        label: 'Work 模式',
        value: '会话、提示词、远程机器、浏览器工作台',
      },
      {
        label: 'Manage 模式',
        value: 'API Key、配额、审计、合规、ROI',
      },
      {
        label: '优先阅读',
        value: 'Remote Agent、Deployment、Permission Model',
      },
    ],
    hero: {
      kicker: 'Open ACE',
      title: '为真实工程团队设计的自托管 AI Coding 工作台。',
      text:
        'Open ACE 给团队提供浏览器里的 AI Coding Agent 工作空间，以及把 Agent 运行在内网机器、受控 API Key 和可审计流程上的治理控制面。',
      docsCta: '阅读文档',
      deployCta: '开始部署',
      phaseBadge: '第二期',
      phaseLabel: '文档站、产品站与项目透明页',
    },
    sections: {
      core: {
        kicker: '核心价值',
        title: '三层能力，一个平台',
      },
      audience: {
        kicker: '团队为什么采用',
        title: '围绕自托管、多 Agent、受治理的 AI 工程场景设计',
      },
      visuals: {
        kicker: '真实产品界面',
        title: '两种界面，一套运行模型',
        text:
          '下面的截图把公开站点锚定在真实产品界面上，而不是抽象描述。Work 模式面向开发者，Manage 模式面向平台和安全团队。',
      },
      architecture: {
        kicker: '整体机制',
        title: '从浏览器工作台到受治理的远程执行',
        text:
          'Open ACE 对“提示词在哪运行、密钥放在哪里、审计信息沉淀在哪里”给出了明确的产品答案。',
      },
      start: {
        kicker: '从这里开始',
        title: '产品说明、文档入口与项目透明度',
      },
    },
    screenshots: [
      {
        eyebrow: 'Work 模式',
        title: '面向日常研发的浏览器 AI 工作台',
        body: '会话、远程工作区、提示词和项目上下文都放在同一个工作流界面里。',
        image: '/img/work-mode-zh.png',
      },
      {
        eyebrow: 'Manage 模式',
        title: '面向密钥、配额、审计和 ROI 的治理控制面',
        body: '平台团队可以在不拼装多套工具的情况下查看支出、策略边界和运行信号。',
        image: '/img/manage-mode-zh.png',
      },
    ],
    architectureCards: [
      {
        title: '浏览器工作台',
        body: '开发者在一个浏览器界面里打开会话、提示词、远程终端和项目上下文。',
      },
      {
        title: '远程执行层',
        body: 'Remote Agent 把 выбран的 AI CLI 真正运行在内网 Linux、macOS 或 Windows 机器上。',
      },
      {
        title: '治理与可观测层',
        body: 'API Key 路由、配额、审批边界和审计轨迹统一沉淀在控制面里。',
      },
    ],
    linkCards: [
      {
        label: '文档首页',
        title: '从结构化文档入口开始理解产品和部署方式',
        to: '/docs/intro',
      },
      {
        label: '产品介绍',
        title: '快速理解定位、能力和整体架构',
        to: '/docs/reference/intro',
      },
      {
        label: '部署指南',
        title: '从本地启动走向生产部署决策',
        to: '/docs/reference/deployment',
      },
      {
        label: 'Remote Agent',
        title: '了解 CLI Agent 如何在受控远程机器上运行',
        to: '/docs/reference/remote-agent',
      },
      {
        label: '项目状态',
        title: '在站点内查看路线图、版本演进和协作入口',
        to: '/project',
      },
    ],
  },
};

export const projectCopy = {
  en: {
    chrome: {
      navItems: [
        {key: 'overview', to: '/project', eyebrow: 'Project', title: 'Overview and metrics'},
        {key: 'roadmap', to: '/project/roadmap', eyebrow: 'Roadmap', title: 'Now, next, and success signals'},
        {key: 'releases', to: '/project/releases', eyebrow: 'Releases', title: 'Latest release and changelog view'},
        {key: 'community', to: '/project/community', eyebrow: 'Community', title: 'Contribution entry points and governance'},
      ],
      heroMetrics: [
        {label: 'Latest release', value: null},
        {label: 'Roadmap lanes', value: null},
        {label: 'Project links', value: 'Docs, issues, PRs, discussions, security'},
      ],
      panelTitle: 'Build-time generated project view',
    },
    common: {
      liveGithub: 'Live in GitHub',
      inProgress: 'In progress',
    },
    overview: {
      title: 'Project visibility without leaving the docs site.',
      description:
        'This section turns repository state, roadmap planning, changelog history, and community entry points into a single public surface. The content is generated during the site build, so the docs site stays synchronized with the repository instead of hand-maintained screenshots.',
      eyebrow: 'Project',
      sections: {
        metricsTitle: 'Live repository signals',
        metricsText:
          'The site reads repository metadata at build time when GitHub API access is available, then falls back to committed project files so Pages builds remain deterministic.',
        phaseTitle: 'What this phase adds',
        phaseText:
          'Phase 1 established the product homepage and bilingual docs shell. This phase adds project transparency pages so visitors can inspect momentum, release maturity, and contribution paths from the same domain.',
        nextTitle: 'Where to go next',
        nextText: 'Use these pages as the default project surface for roadmap, release, and contribution status.',
      },
      metrics: [
        {
          label: 'Stars',
          hint: 'Public adoption signal pulled from repository metadata when available.',
        },
        {
          label: 'Open issues',
          hint: 'Current backlog volume for public product and engineering work.',
        },
        {
          label: 'Good first issues',
          hint: 'Visible starter work for new external contributors.',
        },
      ],
      phaseCards: [
        {
          title: 'Roadmap page',
          items: [
            'Structured rendering of roadmap lanes from the repository source file',
            'Clear separation between current work, upcoming work, and success criteria',
            'Internal links from the landing site instead of raw GitHub Markdown links',
          ],
        },
        {
          title: 'Release page',
          items: [
            'Latest release summary plus changelog sections extracted during build',
            'Fallback to committed changelog data when live release API data is unavailable',
            'Better signal for evaluators deciding whether the project is actively maintained',
          ],
        },
        {
          title: 'Community page',
          items: [
            'Contribution, security, issues, PRs, and discussions in one place',
            'Starter focus areas for new contributors',
            'Build-time counters for open PRs and good-first-issue inventory when possible',
          ],
        },
      ],
      nextCards: [
        {
          label: 'Roadmap',
          title: 'Read current priorities, next-stage work, and success signals.',
          to: '/project/roadmap',
        },
        {
          label: 'Releases',
          title: 'Inspect shipped capabilities, latest release metadata, and changelog structure.',
          to: '/project/releases',
        },
        {
          label: 'Community',
          title: 'Find contribution links, public issue paths, and security reporting entry points.',
          to: '/project/community',
        },
        {
          label: 'GitHub issues',
          title: 'Jump into the live backlog when you need the canonical queue.',
          to: null,
        },
      ],
    },
    roadmap: {
      title: 'A practical roadmap, not a vague wishlist.',
      description:
        'This view is generated from the repository roadmap file. It keeps the public roadmap inside the docs site while still preserving Git-based review and editing workflows for maintainers.',
      eyebrow: 'Roadmap',
      sectionTitle: 'Tracked roadmap lanes',
      sectionText:
        'The source of truth remains ROADMAP.md in the repository. During each site build, the content is parsed into stable sections that can be rendered with stronger visual structure.',
    },
    releases: {
      title: 'Release history that reads like product progress.',
      description:
        'This page combines committed changelog data with live release metadata when the GitHub API is available during the build. It gives evaluators one place to inspect shipping cadence and scope.',
      eyebrow: 'Releases',
      latestTitle: 'Latest published release',
      latestText:
        'The hero release card is sourced from GitHub releases when available and falls back to the committed changelog if the API cannot be reached during the build.',
      latestFallbackTitle: 'Pending first tagged release',
      latestFallbackDate: 'No published release metadata available yet',
      latestPublishedPrefix: 'Published',
      cta: 'View release on GitHub',
      changelogTitle: 'Changelog highlights',
      changelogText:
        'The content below is parsed from CHANGELOG.md. Each release section stays close to the repository source while becoming easier to scan on the public site.',
    },
    community: {
      title: 'Clear entry points for contributors and evaluators.',
      description:
        'Community pages should do more than dump repository links. This surface explains where to start, what kinds of help are wanted, and which governance documents matter before contributing or deploying.',
      eyebrow: 'Community',
      snapshotTitle: 'Contribution snapshot',
      snapshotText:
        'These counters are fetched during the site build when GitHub API access is available. They stay optional so local and CI builds remain reliable even without network metadata.',
      focusTitle: 'Where help is most useful',
      focusText:
        'These focus areas come from the generated project data file and are meant to keep public contribution expectations visible from the documentation site.',
      linksTitle: 'Primary project links',
      linksText:
        'Keep the contribution, discussion, and governance paths visible without making users search the repo sidebar.',
      metrics: [
        {
          label: 'Good first issues',
          hint: 'Starter issues that help new contributors find bounded work quickly.',
        },
        {
          label: 'Open pull requests',
          hint: 'Current review load visible to contributors and maintainers.',
        },
        {
          label: 'Starter focus',
          hint: 'Contribution themes maintained as part of the generated project data.',
          derivedFromFocusCount: true,
        },
      ],
      linkGroups: [
        {
          title: 'Contribute',
          links: ['Contributing guide', 'Good first issues', 'Open pull requests'],
        },
        {
          title: 'Discuss',
          links: ['GitHub Discussions', 'Issue tracker', 'Project docs'],
        },
        {
          title: 'Governance',
          links: ['Security policy', 'Code of conduct', 'Repository home'],
        },
      ],
    },
  },
  'zh-Hans': {
    chrome: {
      navItems: [
        {key: 'overview', to: '/project', eyebrow: '项目', title: '总览与指标'},
        {key: 'roadmap', to: '/project/roadmap', eyebrow: '路线图', title: '当前、下一步与成功信号'},
        {key: 'releases', to: '/project/releases', eyebrow: '版本', title: '最新发布与变更历史'},
        {key: 'community', to: '/project/community', eyebrow: '社区', title: '协作入口与治理信息'},
      ],
      heroMetrics: [
        {label: '最新版本', value: null},
        {label: '路线图分区', value: null},
        {label: '项目入口', value: '文档、Issue、PR、Discussion、安全策略'},
      ],
      panelTitle: '构建期生成的项目视图',
    },
    common: {
      liveGithub: '以 GitHub 实时数据为准',
      inProgress: '进行中',
    },
    overview: {
      title: '不离开文档站，也能看见项目状态。',
      description:
        '这个部分把仓库状态、路线图规划、变更历史和社区入口收拢成同一个公开界面。内容在站点构建时自动生成，避免文档站和仓库状态逐渐脱节。',
      eyebrow: '项目',
      sections: {
        metricsTitle: '实时仓库信号',
        metricsText:
          '当 GitHub API 在构建时可用，站点会读取仓库元数据；如果不可用，则回退到已提交的项目文件，保证 Pages 构建依然稳定。',
        phaseTitle: '这一阶段新增了什么',
        phaseText:
          '第一期完成了产品首页和双语文档骨架；这一期把路线图、版本演进和社区入口放进站点内部，形成更完整的公开透明面。',
        nextTitle: '下一步去哪里看',
        nextText: '以后可以把这些页面当成路线图、版本和社区状态的默认入口。',
      },
      metrics: [
        {
          label: 'Stars',
          hint: '当构建时可读取到仓库元数据，会显示公开采纳信号。',
        },
        {
          label: 'Open issues',
          hint: '公开产品与工程待办规模的直观指标。',
        },
        {
          label: 'Good first issues',
          hint: '方便新贡献者快速找到边界清晰的起步任务。',
        },
      ],
      phaseCards: [
        {
          title: '路线图页',
          items: [
            '把仓库里的路线图分区结构化展示出来',
            '清晰区分当前工作、下一步工作和成功信号',
            '从站点内部直接访问，而不是跳回 GitHub 原始 Markdown',
          ],
        },
        {
          title: '版本页',
          items: [
            '在构建时提取最新发布摘要和 changelog 结构',
            '当 GitHub Release API 不可用时回退到仓库里的 changelog',
            '让评估者更快判断项目是否在持续维护',
          ],
        },
        {
          title: '社区页',
          items: [
            '把贡献、安全、Issue、PR 和 Discussions 放到一个页面里',
            '明确展示新贡献者最适合切入的方向',
            '在可用时构建期统计 open PR 和 good first issue 数量',
          ],
        },
      ],
      nextCards: [
        {
          label: '路线图',
          title: '查看当前优先级、下一阶段工作和成功信号。',
          to: '/project/roadmap',
        },
        {
          label: '版本',
          title: '查看已发布能力、最新版本元数据和 changelog 结构。',
          to: '/project/releases',
        },
        {
          label: '社区',
          title: '查看贡献入口、公开协作路径和安全治理入口。',
          to: '/project/community',
        },
        {
          label: 'GitHub Issues',
          title: '需要查看权威待办队列时，直接跳到 live backlog。',
          to: null,
        },
      ],
    },
    roadmap: {
      title: '这是一份可执行路线图，而不是模糊愿景。',
      description:
        '这个页面直接从仓库里的路线图文件生成。它把公开路线图放在文档站里，同时保留 Git 驱动的评审和编辑流程。',
      eyebrow: '路线图',
      sectionTitle: '当前追踪的路线图分区',
      sectionText:
        '真实来源仍然是仓库中的 ROADMAP.md。站点每次构建时都会把它解析成稳定分区，让公开展示更清晰。',
    },
    releases: {
      title: '让版本历史更像产品进展，而不是原始日志。',
      description:
        '这个页面把仓库中的 changelog 和可用时的 GitHub Release 元数据组合起来，让评估者能在一个地方看到发布节奏和交付范围。',
      eyebrow: '版本',
      latestTitle: '最新发布版本',
      latestText:
        '顶部版本卡片优先使用 GitHub Releases 数据；如果构建时拿不到 API，则自动回退到仓库里的 changelog 信息。',
      latestFallbackTitle: '尚未检测到已发布版本',
      latestFallbackDate: '当前没有可用的发布元数据',
      latestPublishedPrefix: '发布时间',
      cta: '在 GitHub 查看版本',
      changelogTitle: 'Changelog 摘要',
      changelogText:
        '下面的内容从 CHANGELOG.md 解析而来，既保留仓库中的原始来源，又让公开站点更容易扫描阅读。',
    },
    community: {
      title: '给贡献者和评估者一个清晰的协作入口。',
      description:
        '社区页不应该只是堆 GitHub 链接。这里会说明从哪里开始、当前最需要什么类型的帮助，以及在贡献或部署前应该读哪些治理文档。',
      eyebrow: '社区',
      snapshotTitle: '协作快照',
      snapshotText:
        '这些计数在构建时尝试从 GitHub API 获取；如果不可用，也不会影响本地和 CI 构建稳定性。',
      focusTitle: '现在最值得帮忙的方向',
      focusText:
        '这些关注点来自构建期生成的项目数据文件，目的是把公共协作预期直接展示在文档站里。',
      linksTitle: '核心项目入口',
      linksText:
        '把贡献、讨论和治理入口放在一个页面里，不用再让用户去仓库侧边栏里自己翻。',
      metrics: [
        {
          label: 'Good first issues',
          hint: '帮助新贡献者快速找到边界清晰的起步任务。',
        },
        {
          label: 'Open pull requests',
          hint: '让贡献者和维护者都能直观看到当前评审负载。',
        },
        {
          label: 'Starter focus',
          hint: '从项目数据里维护的重点协作方向数量。',
          derivedFromFocusCount: true,
        },
      ],
      linkGroups: [
        {
          title: '参与贡献',
          links: ['贡献指南', 'Good first issues', 'Open pull requests'],
        },
        {
          title: '参与讨论',
          links: ['GitHub Discussions', 'Issue 列表', '项目文档'],
        },
        {
          title: '治理信息',
          links: ['安全策略', '行为准则', '仓库主页'],
        },
      ],
    },
  },
};

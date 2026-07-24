# Open ACE Documentation

[English](#english) | [中文](#中文)

---

## English

Documentation files are in the [en/](en/) directory.

### Quick Navigation

| Document | Description |
|----------|-------------|
| [**INTRO**](en/INTRO.md) | Product introduction, core capabilities, and quick start guide |
| [**ARCHITECTURE**](en/ARCHITECTURE.md) | System architecture overview — backend, frontend, and remote agent layers |
| [**AUTONOMOUS-DEVELOPMENT**](en/AUTONOMOUS-DEVELOPMENT.md) | AI autonomous development — lifecycle, three-session design, CI repair, isolation, and maintenance |
| [**API**](en/API.md) | Complete REST API reference for all endpoints |
| [**DATABASE-SCHEMA**](en/DATABASE-SCHEMA.md) | Database tables, columns, foreign keys, and indexes |
| [**DATABASE-CONVENTIONS**](en/DATABASE-CONVENTIONS.md) | Naming conventions for database fields and migrations |
| [**PERMISSION-MODEL**](en/PERMISSION-MODEL.md) | Role-based access control, authentication, and authorization |
| [**FRONTEND-GUIDE**](en/FRONTEND-GUIDE.md) | React/TypeScript frontend development guide |
| [**REMOTE-AGENT**](en/REMOTE-AGENT.md) | Remote agent client — installation, configuration, CLI tools |
| [**REMOTE-WORKSPACE**](en/REMOTE-WORKSPACE.md) | Remote workspace from server perspective — deployment, management UI, API |
| [**DEPLOYMENT**](en/DEPLOYMENT.md) | Docker deployment and multi-user workspace setup |
| [**KUBERNETES**](en/KUBERNETES.md) | Single-instance Kubernetes deployment guide with manifests reference |
| [**NGINX**](en/NGINX.md) | Nginx reverse proxy configuration for HTTPS and WebSocket |
| [**DEVELOPMENT**](en/DEVELOPMENT.md) | Development environment setup, project structure, and testing |
| [**FEISHU-CONFIG**](en/FEISHU_CONFIG.md) | Feishu/Lark integration configuration guide |
| [**DINGTALK-CONFIG**](en/DINGTALK_CONFIG.md) | DingTalk integration configuration guide |
| [**SAML-CONFIG**](en/SAML_CONFIG.md) | SAML 2.0 SSO provider configuration guide |
| [**CONCEPTS**](en/CONCEPTS.md) | Core concept definitions — Request, Message, Session, Conversation |
| [**TOKEN-ACCOUNTING**](en/token-accounting.md) | Deep dive into Claude / Codex / ZCode / Qwen token collection, computation, storage, and downstream usage |
| [**REPOSITORY-SETUP**](REPOSITORY_SETUP.md) | GitHub repository topics, labels, releases, and demo checklist |
| [**MARKETING**](marketing/README.md) | Launch kit, early adopter discussion, and technical article drafts |

### Reading Guide by Role

| Role | Recommended Reading |
|------|---------------------|
| New to Open ACE | INTRO → ARCHITECTURE → DEVELOPMENT |
| Autonomous development maintainer | AUTONOMOUS-DEVELOPMENT → ARCHITECTURE → DEVELOPMENT |
| Frontend developer | FRONTEND-GUIDE → DEVELOPMENT |
| DevOps / Deployment | DEPLOYMENT → KUBERNETES → NGINX |
| API integrator | API → PERMISSION-MODEL → CONCEPTS |
| Managing remote machines | REMOTE-WORKSPACE → REMOTE-AGENT |
| Growing the project | MARKETING → REPOSITORY-SETUP |

---

## 中文

文档文件位于 [cn/](cn/) 目录。

### 文档导航

| 文档 | 说明 |
|------|------|
| [**INTRO**](cn/INTRO.md) | 产品介绍、核心功能和快速入门指南 |
| [**ARCHITECTURE**](cn/ARCHITECTURE.md) | 系统架构总览 — 后端、前端和远程代理层 |
| [**AUTONOMOUS-DEVELOPMENT**](cn/AUTONOMOUS-DEVELOPMENT.md) | AI 自主开发 — 生命周期、三会话设计、CI 修复、隔离执行和维护指南 |
| [**API**](cn/API.md) | 完整的 REST API 端点参考文档 |
| [**DATABASE-SCHEMA**](cn/DATABASE-SCHEMA.md) | 数据库表、列、外键和索引 |
| [**DATABASE-CONVENTIONS**](cn/DATABASE-CONVENTIONS.md) | 数据库字段和迁移的命名规范 |
| [**PERMISSION-MODEL**](cn/PERMISSION-MODEL.md) | 基于角色的访问控制、认证和授权 |
| [**FRONTEND-GUIDE**](cn/FRONTEND-GUIDE.md) | React/TypeScript 前端开发指南 |
| [**REMOTE-AGENT**](cn/REMOTE-AGENT.md) | 远程代理客户端 — 安装、配置、CLI 工具 |
| [**REMOTE-WORKSPACE**](cn/REMOTE-WORKSPACE.md) | 服务端视角的远程工作区 — 部署、管理界面、API |
| [**DEPLOYMENT**](cn/DEPLOYMENT.md) | Docker 部署和多用户工作空间配置 |
| [**KUBERNETES**](cn/KUBERNETES.md) | 单实例 Kubernetes 部署指南及 manifests 参考 |
| [**NGINX**](cn/NGINX.md) | Nginx 反向代理配置（HTTPS 和 WebSocket） |
| [**DEVELOPMENT**](cn/DEVELOPMENT.md) | 开发环境搭建、项目结构和测试 |
| [**FEISHU-CONFIG**](cn/FEISHU_CONFIG.md) | 飞书集成配置指南 |
| [**DINGTALK-CONFIG**](cn/DINGTALK_CONFIG.md) | 钉钉集成配置指南 |
| [**SAML-CONFIG**](cn/SAML_CONFIG.md) | SAML 2.0 SSO Provider 配置指南 |
| [**CONCEPTS**](cn/CONCEPTS.md) | 核心概念定义 — Request、Message、Session、Conversation |
| [**TOKEN-ACCOUNTING**](cn/token-accounting.md) | Claude / Codex / ZCode / Qwen token 抓取、计算、落库和下游消费链路说明 |
| [**REPOSITORY-SETUP**](REPOSITORY_SETUP.md) | GitHub 仓库 topics、labels、releases 和 Demo 检查清单 |
| [**MARKETING**](marketing/README.md) | 发布传播材料、早期用户讨论帖和技术文章草稿 |

### 按角色阅读指南

| 角色 | 推荐阅读顺序 |
|------|--------------|
| 初次了解 Open ACE | INTRO → ARCHITECTURE → DEVELOPMENT |
| 自主开发维护者 | AUTONOMOUS-DEVELOPMENT → ARCHITECTURE → DEVELOPMENT |
| 前端开发者 | FRONTEND-GUIDE → DEVELOPMENT |
| 运维 / 部署 | DEPLOYMENT → KUBERNETES → NGINX |
| API 集成 | API → PERMISSION-MODEL → CONCEPTS |
| 管理远程机器 | REMOTE-WORKSPACE → REMOTE-AGENT |
| 推广项目 | MARKETING → REPOSITORY-SETUP |

---

## Directory Structure / 目录结构

```
docs/
├── README.md          ← You are here / 你在这里
├── en/                # English documentation
├── cn/                # 中文文档
├── marketing/         # Launch and outreach materials / 发布传播材料
└── images/            # Documentation images / 文档图片
```

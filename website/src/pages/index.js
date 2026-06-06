import React from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import clsx from 'clsx';
import styles from './index.module.css';

const featureCards = [
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
];

const audienceCards = [
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
];

const signalItems = [
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
];

export default function Home() {
  return (
    <Layout
      title="Open ACE"
      description="Self-hosted AI coding agent workspace and governance plane"
    >
      <header className={styles.hero}>
        <div className={styles.heroBackdrop} />
        <div className={styles.heroInner}>
          <div className={styles.heroCopy}>
            <p className={styles.kicker}>Open ACE</p>
            <h1>Self-hosted AI coding workspace for real engineering teams.</h1>
            <p className={styles.heroText}>
              Open ACE gives teams a browser-based workspace for AI coding agents plus the
              governance plane needed to run them on internal machines, controlled API keys,
              and auditable workflows.
            </p>
            <div className={styles.heroActions}>
              <Link className="button button--primary button--lg" to="/docs/intro">
                Read the docs
              </Link>
              <Link className="button button--secondary button--lg" to="/docs/reference/DEPLOYMENT">
                Start deployment
              </Link>
            </div>
          </div>
          <div className={styles.heroPanel}>
            <div className={styles.heroPanelTop}>
              <span className={styles.panelBadge}>Phase 1</span>
              <span className={styles.panelBadgeMuted}>Docs and product site</span>
            </div>
            {signalItems.map((item) => (
              <div key={item.label} className={styles.signalBlock}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>
        </div>
      </header>

      <main className={styles.main}>
        <section className={styles.section}>
          <div className={styles.sectionHeader}>
            <p className={styles.kicker}>Core value</p>
            <h2>Three layers, one platform</h2>
          </div>
          <div className={styles.featureGrid}>
            {featureCards.map((card) => (
              <article key={card.title} className={styles.featureCard}>
                <h3>{card.title}</h3>
                <p>{card.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className={clsx(styles.section, styles.audienceSection)}>
          <div className={styles.sectionHeader}>
            <p className={styles.kicker}>Why teams adopt it</p>
            <h2>Designed for self-hosted, multi-agent, governed AI engineering</h2>
          </div>
          <div className={styles.audienceGrid}>
            {audienceCards.map((card) => (
              <article key={card.title} className={styles.audienceCard}>
                <p className={styles.audienceEyebrow}>{card.eyebrow}</p>
                <h3>{card.title}</h3>
                <p>{card.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className={styles.section}>
          <div className={styles.sectionHeader}>
            <p className={styles.kicker}>Start here</p>
            <h2>Phase 1 site scope</h2>
          </div>
          <div className={styles.linkGrid}>
            <Link className={styles.linkCard} to="/docs/intro">
              <span>Documentation Home</span>
              <strong>Browse the structured docs entry point</strong>
            </Link>
            <Link className={styles.linkCard} to="/docs/reference/INTRO">
              <span>Product Intro</span>
              <strong>Understand positioning, features, and architecture at a glance</strong>
            </Link>
            <Link className={styles.linkCard} to="/docs/reference/DEPLOYMENT">
              <span>Deployment Guide</span>
              <strong>Go from local startup to production deployment decisions</strong>
            </Link>
            <Link className={styles.linkCard} to="/docs/reference/REMOTE-AGENT">
              <span>Remote Agent</span>
              <strong>Learn how CLI agents are executed on controlled remote machines</strong>
            </Link>
            <Link className={styles.linkCard} to="/project">
              <span>Project Status</span>
              <strong>View roadmap, release history, and contribution entry points from the site</strong>
            </Link>
          </div>
        </section>
      </main>
    </Layout>
  );
}

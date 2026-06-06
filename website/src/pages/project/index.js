import React from 'react';
import Link from '@docusaurus/Link';
import Translate from '@docusaurus/Translate';
import {ProjectChrome, formatNumber, projectData, styles} from '../../components/project/ProjectLayout';

export default function ProjectOverviewPage() {
  const metrics = [
    {
      label: 'Stars',
      value: formatNumber(projectData.repository.stars),
      hint: 'Public adoption signal pulled from repository metadata when available.',
    },
    {
      label: 'Open issues',
      value: formatNumber(projectData.repository.openIssues),
      hint: 'Current backlog volume for public product and engineering work.',
    },
    {
      label: 'Good first issues',
      value: formatNumber(projectData.community.goodFirstIssueCount),
      hint: 'Visible starter work for new external contributors.',
    },
  ];

  return (
    <ProjectChrome
      title="Project visibility without leaving the docs site."
      description="This section turns repository state, roadmap planning, changelog history, and community entry points into a single public surface. The content is generated during the site build, so the docs site stays synchronized with the repository instead of hand-maintained screenshots."
      eyebrow="Project"
      active="overview"
    >
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Live repository signals</h2>
          <p>
            The site reads repository metadata at build time when GitHub API access is available, then
            falls back to committed project files so Pages builds remain deterministic.
          </p>
        </div>
        <div className={styles.cardGrid}>
          {metrics.map((metric) => (
            <article key={metric.label} className={styles.metricCard}>
              <p className={styles.metricLabel}>{metric.label}</p>
              <p className={styles.metricValue}>{metric.value}</p>
              <p className={styles.metricHint}>{metric.hint}</p>
            </article>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>What this phase adds</h2>
          <p>
            Phase 1 established the product homepage and bilingual docs shell. This phase adds project
            transparency pages so visitors can inspect momentum, release maturity, and contribution paths
            from the same domain.
          </p>
        </div>
        <div className={styles.cardGrid}>
          <article className={styles.sectionCard}>
            <h3>Roadmap page</h3>
            <ul className={styles.bulletList}>
              <li>Structured rendering of roadmap lanes from the repository source file</li>
              <li>Clear separation between current work, upcoming work, and success criteria</li>
              <li>Internal links from the landing site instead of raw GitHub Markdown links</li>
            </ul>
          </article>
          <article className={styles.sectionCard}>
            <h3>Release page</h3>
            <ul className={styles.bulletList}>
              <li>Latest release summary plus changelog sections extracted during build</li>
              <li>Fallback to committed changelog data when live release API data is unavailable</li>
              <li>Better signal for evaluators deciding whether the project is actively maintained</li>
            </ul>
          </article>
          <article className={styles.sectionCard}>
            <h3>Community page</h3>
            <ul className={styles.bulletList}>
              <li>Contribution, security, issues, PRs, and discussions in one place</li>
              <li>Starter focus areas for new contributors</li>
              <li>Build-time counters for open PRs and good-first-issue inventory when possible</li>
            </ul>
          </article>
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Where to go next</h2>
          <p>Use these pages as the default project surface for roadmap, release, and contribution status.</p>
        </div>
        <div className={styles.linkGrid}>
          <Link className={styles.linkCard} to="/project/roadmap">
            <span>Roadmap</span>
            <strong>Read current priorities, next-stage work, and success signals.</strong>
          </Link>
          <Link className={styles.linkCard} to="/project/releases">
            <span>Releases</span>
            <strong>Inspect shipped capabilities, latest release metadata, and changelog structure.</strong>
          </Link>
          <Link className={styles.linkCard} to="/project/community">
            <span>Community</span>
            <strong>Find contribution links, public issue paths, and security reporting entry points.</strong>
          </Link>
          <Link className={styles.linkCard} to={projectData.repository.issuesUrl}>
            <span>
              <Translate id="project.overview.issueLinkLabel">GitHub issues</Translate>
            </span>
            <strong>Jump into the live backlog when you need the canonical queue.</strong>
          </Link>
        </div>
      </section>
    </ProjectChrome>
  );
}

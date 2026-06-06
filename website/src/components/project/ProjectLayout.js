import React from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import Translate from '@docusaurus/Translate';
import projectData from '../../data/generated/project-data.json';
import styles from './project.module.css';

export function formatNumber(value) {
  if (typeof value !== 'number') {
    return 'Live in GitHub';
  }

  return new Intl.NumberFormat('en-US').format(value);
}

export function formatDate(value) {
  if (!value) {
    return 'In progress';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function ProjectChrome({
  title,
  description,
  eyebrow,
  active,
  children,
}) {
  const navItems = [
    {
      key: 'overview',
      to: '/project',
      eyebrow: 'Project',
      title: 'Overview and metrics',
      active: active === 'overview',
    },
    {
      key: 'roadmap',
      to: '/project/roadmap',
      eyebrow: 'Roadmap',
      title: 'Now, next, and success signals',
      active: active === 'roadmap',
    },
    {
      key: 'releases',
      to: '/project/releases',
      eyebrow: 'Releases',
      title: 'Latest release and changelog view',
      active: active === 'releases',
    },
    {
      key: 'community',
      to: '/project/community',
      eyebrow: 'Community',
      title: 'Contribution entry points and governance',
      active: active === 'community',
    },
  ];

  const heroMetrics = [
    {
      label: 'Latest release',
      value: projectData.releases.latest?.tagName || 'Unreleased',
    },
    {
      label: 'Roadmap lanes',
      value: `${projectData.roadmap.sections.length} tracked sections`,
    },
    {
      label: 'Project links',
      value: 'Docs, issues, PRs, discussions, security',
    },
  ];

  return (
    <Layout title={title} description={description}>
      <main className={styles.page}>
        <section className={styles.hero}>
          <p className={styles.eyebrow}>{eyebrow}</p>
          <div className={styles.heroGrid}>
            <div className={styles.heroCopy}>
              <h1>{title}</h1>
              <p>{description}</p>
            </div>
            <aside className={styles.heroPanel}>
              <p className={styles.heroPanelTitle}>
                <Translate id="project.hero.panelTitle">Build-time generated project view</Translate>
              </p>
              {heroMetrics.map((metric) => (
                <div key={metric.label} className={styles.heroMetric}>
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                </div>
              ))}
            </aside>
          </div>
        </section>

        <div className={styles.body}>
          <div className={styles.navGrid}>
            {navItems.map((item) => (
              <Link
                key={item.key}
                className={styles.navCard}
                style={item.active ? {borderColor: 'rgba(181, 107, 46, 0.34)'} : undefined}
                to={item.to}
              >
                <span>{item.eyebrow}</span>
                <strong>{item.title}</strong>
              </Link>
            ))}
          </div>
          {children}
        </div>
      </main>
    </Layout>
  );
}

export {projectData, styles};

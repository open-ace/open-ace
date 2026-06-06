import React from 'react';
import Link from '@docusaurus/Link';
import {ProjectChrome, formatDate, projectData, styles} from '../../components/project/ProjectLayout';

export default function ReleasesPage() {
  const releasedEntries = projectData.releases.entries.filter((entry) => entry.version !== 'Unreleased');

  return (
    <ProjectChrome
      title="Release history that reads like product progress."
      description="This page combines committed changelog data with live release metadata when the GitHub API is available during the build. It gives evaluators one place to inspect shipping cadence and scope."
      eyebrow="Releases"
      active="releases"
    >
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Latest published release</h2>
          <p>
            The hero release card is sourced from GitHub releases when available and falls back to the
            committed changelog if the API cannot be reached during the build.
          </p>
        </div>
        <article className={styles.releaseCard}>
          <p className={styles.releaseVersion}>{projectData.releases.latest?.tagName || 'Unreleased'}</p>
          <h3>{projectData.releases.latest?.name || 'Pending first tagged release'}</h3>
          <p className={styles.releaseDate}>
            {projectData.releases.latest?.publishedAt
              ? `Published ${formatDate(projectData.releases.latest.publishedAt)}`
              : 'No published release metadata available yet'}
          </p>
          <Link className="button button--primary" to={projectData.releases.latest?.url || projectData.repository.releasesUrl}>
            View release on GitHub
          </Link>
        </article>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Changelog highlights</h2>
          <p>
            The content below is parsed from <code>CHANGELOG.md</code>. Each release section stays close to
            the repository source while becoming easier to scan on the public site.
          </p>
        </div>
        <div className={styles.timelineGrid}>
          {releasedEntries.map((entry) => (
            <article key={`${entry.version}-${entry.date}`} className={styles.releaseCard}>
              <p className={styles.releaseVersion}>v{entry.version}</p>
              <p className={styles.releaseDate}>{formatDate(entry.date)}</p>
              {entry.sections.map((section) => (
                <div key={section.title}>
                  <p className={styles.sectionTag}>{section.title}</p>
                  <ul className={styles.bulletList}>
                    {section.items.slice(0, 5).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </article>
          ))}
        </div>
      </section>
    </ProjectChrome>
  );
}

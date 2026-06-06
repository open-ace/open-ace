import React from 'react';
import Link from '@docusaurus/Link';
import {ProjectChrome, formatNumber, projectData, styles} from '../../components/project/ProjectLayout';

export default function CommunityPage() {
  const linkGroups = [
    {
      title: 'Contribute',
      links: [
        ['Contributing guide', projectData.community.contributingUrl],
        ['Good first issues', `${projectData.repository.issuesUrl}?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22`],
        ['Open pull requests', projectData.community.pullsUrl],
      ],
    },
    {
      title: 'Discuss',
      links: [
        ['GitHub Discussions', projectData.community.discussionsUrl],
        ['Issue tracker', projectData.community.issuesUrl],
        ['Project docs', projectData.repository.docsUrl],
      ],
    },
    {
      title: 'Governance',
      links: [
        ['Security policy', projectData.community.securityUrl],
        ['Code of conduct', projectData.community.codeOfConductUrl],
        ['Repository home', projectData.repository.url],
      ],
    },
  ];

  return (
    <ProjectChrome
      title="Clear entry points for contributors and evaluators."
      description="Community pages should do more than dump repository links. This surface explains where to start, what kinds of help are wanted, and which governance documents matter before contributing or deploying."
      eyebrow="Community"
      active="community"
    >
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Contribution snapshot</h2>
          <p>
            These counters are fetched during the site build when GitHub API access is available. They stay
            optional so local and CI builds remain reliable even without network metadata.
          </p>
        </div>
        <div className={styles.cardGrid}>
          <article className={styles.metricCard}>
            <p className={styles.metricLabel}>Good first issues</p>
            <p className={styles.metricValue}>{formatNumber(projectData.community.goodFirstIssueCount)}</p>
            <p className={styles.metricHint}>Starter issues that help new contributors find bounded work quickly.</p>
          </article>
          <article className={styles.metricCard}>
            <p className={styles.metricLabel}>Open pull requests</p>
            <p className={styles.metricValue}>{formatNumber(projectData.community.openPullRequestCount)}</p>
            <p className={styles.metricHint}>Current review load visible to contributors and maintainers.</p>
          </article>
          <article className={styles.metricCard}>
            <p className={styles.metricLabel}>Starter focus</p>
            <p className={styles.metricValue}>{projectData.community.starterFocus.length}</p>
            <p className={styles.metricHint}>Contribution themes maintained as part of the generated project data.</p>
          </article>
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Where help is most useful</h2>
          <p>
            These focus areas come from the generated project data file and are meant to keep public
            contribution expectations visible from the documentation site.
          </p>
        </div>
        <article className={styles.communityCard}>
          <ul className={styles.bulletList}>
            {projectData.community.starterFocus.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </article>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Primary project links</h2>
          <p>Keep the contribution, discussion, and governance paths visible without making users search the repo sidebar.</p>
        </div>
        <div className={styles.metaGrid}>
          {linkGroups.map((group) => (
            <article key={group.title} className={styles.metaBlock}>
              <h3>{group.title}</h3>
              <ul className={styles.linkList}>
                {group.links.map(([label, href]) => (
                  <li key={label}>
                    <Link to={href}>{label}</Link>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </ProjectChrome>
  );
}

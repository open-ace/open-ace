import React from 'react';
import {ProjectChrome, projectData, styles} from '../../components/project/ProjectLayout';

export default function RoadmapPage() {
  return (
    <ProjectChrome
      title="A practical roadmap, not a vague wishlist."
      description="This view is generated from the repository roadmap file. It keeps the public roadmap inside the docs site while still preserving Git-based review and editing workflows for maintainers."
      eyebrow="Roadmap"
      active="roadmap"
    >
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2>Tracked roadmap lanes</h2>
          <p>
            The source of truth remains <code>ROADMAP.md</code> in the repository. During each site build,
            the content is parsed into stable sections that can be rendered with stronger visual structure.
          </p>
        </div>
        <div className={styles.timelineGrid}>
          {projectData.roadmap.sections.map((section) => (
            <article key={section.id} className={styles.sectionCard}>
              <h3>{section.title}</h3>
              <ul className={styles.bulletList}>
                {section.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </ProjectChrome>
  );
}

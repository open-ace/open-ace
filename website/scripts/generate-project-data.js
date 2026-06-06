const fs = require('fs');
const path = require('path');
const https = require('https');

const repoRoot = path.resolve(__dirname, '..', '..');
const websiteRoot = path.resolve(__dirname, '..');
const outputDir = path.join(websiteRoot, 'src', 'data', 'generated');
const outputFile = path.join(outputDir, 'project-data.json');

const repoMeta = {
  owner: 'open-ace',
  repo: 'open-ace',
  url: 'https://github.com/open-ace/open-ace',
  docsUrl: 'https://open-ace.github.io/open-ace/docs/intro',
  discussionsUrl: 'https://github.com/open-ace/open-ace/discussions',
  issuesUrl: 'https://github.com/open-ace/open-ace/issues',
  pullsUrl: 'https://github.com/open-ace/open-ace/pulls',
  releasesUrl: 'https://github.com/open-ace/open-ace/releases',
  roadmapUrl: 'https://github.com/open-ace/open-ace/blob/main/ROADMAP.md',
  changelogUrl: 'https://github.com/open-ace/open-ace/blob/main/CHANGELOG.md',
  contributingUrl: 'https://github.com/open-ace/open-ace/blob/main/CONTRIBUTING.md',
  codeOfConductUrl: 'https://github.com/open-ace/open-ace/blob/main/CODE_OF_CONDUCT.md',
  securityUrl: 'https://github.com/open-ace/open-ace/security/policy',
};

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, {recursive: true});
}

function readFile(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function parseRoadmap(markdown) {
  const lines = markdown.split(/\r?\n/);
  const sections = [];
  let currentSection = null;

  for (const line of lines) {
    const headingMatch = line.match(/^##\s+(.+)$/);
    if (headingMatch) {
      currentSection = {
        id: slugify(headingMatch[1]),
        title: headingMatch[1],
        items: [],
      };
      sections.push(currentSection);
      continue;
    }

    const bulletMatch = line.match(/^- (.+)$/);
    if (bulletMatch && currentSection) {
      currentSection.items.push(bulletMatch[1].trim());
    }
  }

  return sections;
}

function parseChangelog(markdown) {
  const lines = markdown.split(/\r?\n/);
  const releases = [];
  let currentRelease = null;
  let currentSection = null;

  for (const line of lines) {
    const releaseMatch = line.match(/^## \[(.+?)\](?: - (\d{4}-\d{2}-\d{2}))?$/);
    if (releaseMatch) {
      currentRelease = {
        version: releaseMatch[1],
        date: releaseMatch[2] || null,
        sections: [],
      };
      releases.push(currentRelease);
      currentSection = null;
      continue;
    }

    const sectionMatch = line.match(/^###\s+(.+)$/);
    if (sectionMatch && currentRelease) {
      currentSection = {
        title: sectionMatch[1],
        items: [],
      };
      currentRelease.sections.push(currentSection);
      continue;
    }

    const bulletMatch = line.match(/^- (.+)$/);
    if (bulletMatch && currentSection) {
      currentSection.items.push(bulletMatch[1].trim());
    }
  }

  return releases;
}

function requestJson(url) {
  const token = process.env.OPEN_ACE_GITHUB_TOKEN || process.env.GITHUB_TOKEN || process.env.GH_TOKEN;

  return new Promise((resolve) => {
    const request = https.get(
      url,
      {
        headers: {
          Accept: 'application/vnd.github+json',
          'User-Agent': 'open-ace-website-build',
          ...(token ? {Authorization: `Bearer ${token}`} : {}),
        },
        timeout: 8000,
      },
      (response) => {
        let data = '';

        response.on('data', (chunk) => {
          data += chunk;
        });

        response.on('end', () => {
          if (response.statusCode && response.statusCode >= 400) {
            resolve(null);
            return;
          }

          try {
            resolve(JSON.parse(data));
          } catch (_error) {
            resolve(null);
          }
        });
      }
    );

    request.on('error', () => resolve(null));
    request.on('timeout', () => {
      request.destroy();
      resolve(null);
    });
  });
}

async function fetchGithubData() {
  const {owner, repo} = repoMeta;
  const base = `https://api.github.com/repos/${owner}/${repo}`;

  const [repository, releases, openGoodFirstIssues, openPullRequests] = await Promise.all([
    requestJson(base),
    requestJson(`${base}/releases?per_page=5`),
    requestJson(
      `https://api.github.com/search/issues?q=${encodeURIComponent(
        `repo:${owner}/${repo} is:issue is:open label:"good first issue"`
      )}`
    ),
    requestJson(
      `https://api.github.com/search/issues?q=${encodeURIComponent(`repo:${owner}/${repo} is:pr is:open`)}`
    ),
  ]);

  return {
    repository,
    releases,
    openGoodFirstIssues,
    openPullRequests,
  };
}

async function main() {
  const roadmapSections = parseRoadmap(readFile('ROADMAP.md'));
  const changelogReleases = parseChangelog(readFile('CHANGELOG.md'));
  const githubData = await fetchGithubData();

  const releaseEntries = changelogReleases
    .filter((entry) => entry.version !== 'Unreleased')
    .slice(0, 4);

  const latestRelease = githubData.releases?.[0]
    ? {
        tagName: githubData.releases[0].tag_name,
        name: githubData.releases[0].name || githubData.releases[0].tag_name,
        publishedAt: githubData.releases[0].published_at,
        url: githubData.releases[0].html_url,
      }
    : releaseEntries[0]
      ? {
          tagName: releaseEntries[0].version,
          name: releaseEntries[0].version,
          publishedAt: releaseEntries[0].date,
          url: repoMeta.releasesUrl,
        }
      : null;

  const output = {
    generatedAt: new Date().toISOString(),
    repository: {
      ...repoMeta,
      stars: githubData.repository?.stargazers_count ?? null,
      forks: githubData.repository?.forks_count ?? null,
      openIssues: githubData.repository?.open_issues_count ?? null,
      watchers: githubData.repository?.subscribers_count ?? null,
      defaultBranch: githubData.repository?.default_branch ?? 'main',
      license: githubData.repository?.license?.spdx_id ?? 'Apache-2.0',
    },
    roadmap: {
      sections: roadmapSections,
    },
    releases: {
      latest: latestRelease,
      entries: changelogReleases,
      githubRecent:
        githubData.releases?.map((release) => ({
          tagName: release.tag_name,
          name: release.name || release.tag_name,
          publishedAt: release.published_at,
          url: release.html_url,
          draft: release.draft,
          prerelease: release.prerelease,
        })) ?? [],
    },
    community: {
      contributingUrl: repoMeta.contributingUrl,
      codeOfConductUrl: repoMeta.codeOfConductUrl,
      securityUrl: repoMeta.securityUrl,
      discussionsUrl: repoMeta.discussionsUrl,
      issuesUrl: repoMeta.issuesUrl,
      pullsUrl: repoMeta.pullsUrl,
      goodFirstIssueCount: githubData.openGoodFirstIssues?.total_count ?? null,
      openPullRequestCount: githubData.openPullRequests?.total_count ?? null,
      starterFocus: [
        'First-run setup and deployment fixes',
        'Docs parity between Chinese and English',
        'Remote Agent hardening and observability',
        'Governance dashboards and connector examples',
      ],
    },
  };

  ensureDir(outputDir);
  fs.writeFileSync(outputFile, `${JSON.stringify(output, null, 2)}\n`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

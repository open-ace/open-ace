const fs = require('fs');
const path = require('path');

const websiteRoot = path.resolve(__dirname, '..');
const buildRoot = path.join(websiteRoot, 'build');

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function readOneOf(candidates) {
  for (const candidate of candidates) {
    const fullPath = path.join(buildRoot, candidate);
    if (fs.existsSync(fullPath)) {
      return fs.readFileSync(fullPath, 'utf8');
    }
  }

  throw new Error(`Missing expected build output. Checked: ${candidates.join(', ')}`);
}

function main() {
  assert(fs.existsSync(buildRoot), 'Missing build directory');

  const homepage = readOneOf(['index.html']);
  const docsIntro = readOneOf(['docs/intro.html']);
  const projectOverview = readOneOf(['project.html', 'project/index.html']);
  const projectRoadmap = readOneOf(['project/roadmap.html', 'project/roadmap/index.html']);
  const zhHomepage = readOneOf(['zh-Hans/index.html']);
  const zhProject = readOneOf(['zh-Hans/project.html', 'zh-Hans/project/index.html']);

  assert(homepage.includes('Self-hosted AI coding workspace'), 'Homepage is missing the main product headline');
  assert(homepage.includes('Product in action') || homepage.includes('真实产品界面'), 'Homepage is missing the screenshot section');
  assert(docsIntro.includes('Open ACE Documentation') || docsIntro.includes('Open ACE 文档'), 'Docs intro page is missing expected copy');
  assert(projectOverview.includes('Project visibility') || projectOverview.includes('项目透明度'), 'Project overview page is missing expected content');
  assert(projectRoadmap.includes('Tracked roadmap lanes') || projectRoadmap.includes('路线图'), 'Roadmap page is missing expected content');
  assert(zhHomepage.includes('Open ACE'), 'Chinese homepage did not render');
  assert(zhProject.includes('项目') || zhProject.includes('Project'), 'Chinese project page did not render');
}

main();

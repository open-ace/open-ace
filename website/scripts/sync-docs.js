const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..', '..');
const websiteRoot = path.resolve(__dirname, '..');
const docsRoot = path.join(repoRoot, 'docs');
const staticImgRoot = path.join(websiteRoot, 'static', 'img');
const englishSource = path.join(docsRoot, 'en');
const chineseSource = path.join(docsRoot, 'cn');
const imagesSource = path.join(docsRoot, 'images');
const englishTarget = path.join(websiteRoot, 'docs', 'reference');
const chineseTarget = path.join(
  websiteRoot,
  'i18n',
  'zh-Hans',
  'docusaurus-plugin-content-docs',
  'current',
  'reference'
);
const englishCategoryFile = path.join(englishTarget, '_category_.json');
const chineseCategoryFile = path.join(chineseTarget, '_category_.json');

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, {recursive: true});
}

function resetDir(dirPath) {
  fs.rmSync(dirPath, {recursive: true, force: true});
  ensureDir(dirPath);
}

function copyDir(source, target) {
  ensureDir(target);
  for (const entry of fs.readdirSync(source, {withFileTypes: true})) {
    const sourcePath = path.join(source, entry.name);
    const targetPath = path.join(target, entry.name);
    if (entry.isDirectory()) {
      copyDir(sourcePath, targetPath);
    } else {
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
}

function writeCategoryJson(filePath, label) {
  fs.writeFileSync(
    filePath,
    `${JSON.stringify(
      {
        label,
        position: 1,
        collapsed: false,
      },
      null,
      2
    )}\n`
  );
}

function main() {
  resetDir(englishTarget);
  resetDir(chineseTarget);

  copyDir(englishSource, englishTarget);
  copyDir(chineseSource, chineseTarget);

  writeCategoryJson(englishCategoryFile, 'Reference');
  writeCategoryJson(chineseCategoryFile, '参考文档');

  ensureDir(staticImgRoot);
  fs.copyFileSync(path.join(imagesSource, 'logo.svg'), path.join(staticImgRoot, 'logo.svg'));
  fs.copyFileSync(path.join(imagesSource, 'logo.png'), path.join(staticImgRoot, 'social-card.png'));
}

main();

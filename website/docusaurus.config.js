// @ts-check

const {themes} = require('prism-react-renderer');

const lightCodeTheme = themes.github;
const darkCodeTheme = themes.dracula;

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Open ACE',
  tagline: 'Self-hosted AI coding agent workspace and governance plane',
  favicon: 'img/logo.svg',
  url: 'https://open-ace.github.io',
  baseUrl: '/open-ace/',
  organizationName: 'open-ace',
  projectName: 'open-ace',
  trailingSlash: false,
  onBrokenLinks: 'throw',
  onBrokenAnchors: 'warn',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },
  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-Hans'],
    localeConfigs: {
      en: {
        label: 'English',
        htmlLang: 'en',
      },
      'zh-Hans': {
        label: '中文',
        htmlLang: 'zh-CN',
      },
    },
  },
  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: 'docs',
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: 'https://github.com/open-ace/open-ace/tree/main/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      },
    ],
  ],
  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/social-card.png',
      navbar: {
        title: 'Open ACE',
        logo: {
          alt: 'Open ACE logo',
          src: 'img/logo.svg',
        },
        items: [
          {to: '/', label: 'Home', position: 'left'},
          {to: '/docs/intro', label: 'Docs', position: 'left'},
          {to: '/docs/reference/INTRO', label: 'Product Guide', position: 'left'},
          {to: '/docs/reference/REMOTE-AGENT', label: 'Remote Agent', position: 'left'},
          {
            href: 'https://github.com/open-ace/open-ace',
            label: 'GitHub',
            position: 'right',
          },
          {
            href: 'https://github.com/open-ace/open-ace/discussions',
            label: 'Discussions',
            position: 'right',
          },
          {
            type: 'localeDropdown',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              {label: 'Getting Started', to: '/docs/intro'},
              {label: 'Deployment', to: '/docs/reference/DEPLOYMENT'},
              {label: 'Remote Agent', to: '/docs/reference/REMOTE-AGENT'},
            ],
          },
          {
            title: 'Project',
            items: [
              {
                label: 'Roadmap',
                href: 'https://github.com/open-ace/open-ace/blob/main/ROADMAP.md',
              },
              {
                label: 'Changelog',
                href: 'https://github.com/open-ace/open-ace/blob/main/CHANGELOG.md',
              },
              {
                label: 'Security',
                href: 'https://github.com/open-ace/open-ace/security/policy',
              },
            ],
          },
          {
            title: 'Community',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/open-ace/open-ace',
              },
              {
                label: 'Issues',
                href: 'https://github.com/open-ace/open-ace/issues',
              },
              {
                label: 'Discussions',
                href: 'https://github.com/open-ace/open-ace/discussions',
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Open ACE.`,
      },
      colorMode: {
        defaultMode: 'light',
        disableSwitch: false,
        respectPrefersColorScheme: true,
      },
      prism: {
        theme: lightCodeTheme,
        darkTheme: darkCodeTheme,
      },
    }),
};

module.exports = config;

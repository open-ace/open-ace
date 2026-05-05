/**
 * Vite Plugin: Auto Preload Critical Resources
 *
 * Automatically injects preload tags for critical resources:
 * - Fonts (woff2)
 * - Critical CSS
 * - Critical JS chunks (react-vendor, router)
 */

import type { Plugin, HtmlTagDescriptor } from 'vite';

interface PreloadOptions {
  /** Preload font files */
  fonts?: boolean;
  /** Preload critical JS chunks */
  criticalJs?: string[];
  /** Preload CSS */
  css?: boolean;
}

const defaultOptions: PreloadOptions = {
  fonts: true,
  criticalJs: ['react-vendor', 'router', 'query'],
  css: true,
};

export function vitePluginPreload(options: PreloadOptions = {}): Plugin {
  const opts = { ...defaultOptions, ...options };
  const preloadTags: HtmlTagDescriptor[] = [];

  return {
    name: 'vite-plugin-preload',
    enforce: 'post',

    generateBundle(_, bundle) {
      // Find and collect font files
      if (opts.fonts) {
        for (const [fileName, file] of Object.entries(bundle)) {
          if (fileName.endsWith('.woff2') && file.type === 'asset') {
            preloadTags.push({
              tag: 'link',
              attrs: {
                rel: 'preload',
                href: `/static/js/dist/${fileName}`,
                as: 'font',
                type: 'font/woff2',
                crossorigin: 'anonymous',
              },
              injectTo: 'head-prepend',
            });
          }
        }
      }

      // Find and collect critical JS chunks
      if (opts.criticalJs && opts.criticalJs.length > 0) {
        for (const [fileName, file] of Object.entries(bundle)) {
          if (file.type === 'chunk') {
            // Check if this chunk matches any critical JS pattern
            const isCritical = opts.criticalJs.some((pattern) =>
              fileName.startsWith(pattern) || fileName.includes(pattern)
            );
            if (isCritical) {
              preloadTags.push({
                tag: 'link',
                attrs: {
                  rel: 'modulepreload',
                  href: `/static/js/dist/${fileName}`,
                },
                injectTo: 'head-prepend',
              });
            }
          }
        }
      }

      // Find and collect CSS files
      if (opts.css) {
        for (const [fileName, file] of Object.entries(bundle)) {
          if (fileName.endsWith('.css') && file.type === 'asset') {
            preloadTags.push({
              tag: 'link',
              attrs: {
                rel: 'preload',
                href: `/static/js/dist/${fileName}`,
                as: 'style',
              },
              injectTo: 'head-prepend',
            });
          }
        }
      }
    },

    transformIndexHtml(html) {
      // Inject preload tags into HTML
      return {
        html,
        tags: preloadTags,
      };
    },
  };
}

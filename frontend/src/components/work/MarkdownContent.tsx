import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/utils';
import './DocumentViewer.css';

// Helper function to generate heading id from children
const generateHeadingId = (children: React.ReactNode): string => {
  const text = React.Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string') return child;
      if (typeof child === 'number') return String(child);
      return '';
    })
    .join('')
    .trim();

  // Generate slug: lowercase for English, keep original for CJK
  return (
    text
      .toLowerCase()
      .replace(/\s+/g, '-')
      .replace(/[^\w一-鿿぀-ゟ゠-ヿ가-힯-]/g, '') || text
  );
};

interface MarkdownContentProps {
  content: string;
  className?: string;
}

/**
 * Shared markdown renderer (plan/review full text, docs, etc.).
 *
 * react-markdown v10 is safe by default — it does NOT render raw HTML unless a
 * rehype-raw plugin is added. Keep it that way: milestone content is untrusted,
 * so never add rehype-raw here.
 */
export const MarkdownContent: React.FC<MarkdownContentProps> = ({ content, className }) => (
  <div className={cn('markdown-content', className)}>
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => (
          <h1 className="doc-heading-1" id={generateHeadingId(children)}>
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="doc-heading-2" id={generateHeadingId(children)}>
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="doc-heading-3" id={generateHeadingId(children)}>
            {children}
          </h3>
        ),
        p: ({ children }) => <p className="doc-paragraph">{children}</p>,
        ul: ({ children }) => <ul className="doc-list">{children}</ul>,
        ol: ({ children }) => <ol className="doc-list">{children}</ol>,
        li: ({ children }) => <li className="doc-list-item">{children}</li>,
        table: ({ children }) => (
          <div className="doc-table-wrapper">
            <table className="doc-table">{children}</table>
          </div>
        ),
        th: ({ children }) => <th className="doc-table-header">{children}</th>,
        td: ({ children }) => <td className="doc-table-cell">{children}</td>,
        blockquote: ({ children }) => <blockquote className="doc-quote">{children}</blockquote>,
        code: ({ className: codeClassName, children, ...props }) =>
          !codeClassName ? (
            <code className="doc-code-inline" {...props}>
              {children}
            </code>
          ) : (
            <code className={cn('doc-code-block', codeClassName)} {...props}>
              {children}
            </code>
          ),
        pre: ({ children }) => <pre className="doc-pre">{children}</pre>,
        a: ({ href, children }) => {
          if (href?.startsWith('#')) {
            const handleClick = (e: React.MouseEvent) => {
              e.preventDefault();
              const targetId = href.slice(1);
              const element = document.getElementById(targetId);
              if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }
            };
            return (
              <a className="doc-link doc-anchor-link" href={href} onClick={handleClick}>
                {children}
              </a>
            );
          }
          return (
            <a className="doc-link" href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        },
        hr: () => <hr className="doc-divider" />,
        strong: ({ children }) => <strong className="doc-strong">{children}</strong>,
      }}
    >
      {content}
    </ReactMarkdown>
  </div>
);

export default MarkdownContent;

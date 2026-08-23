import { describe, it, expect } from 'vitest';
import { buildProjectsPathSegment, injectProjectsPath, isWorkspaceRoute } from './urlUtils';

describe('buildProjectsPathSegment', () => {
  it('encodes simple path', () => {
    expect(buildProjectsPathSegment('/home/user/my-project')).toBe('projects/home/user/my-project');
  });

  it('handles path with spaces', () => {
    expect(buildProjectsPathSegment('/home/user/my project')).toBe(
      'projects/home/user/my%20project'
    );
  });

  it('handles path with Chinese characters', () => {
    expect(buildProjectsPathSegment('/home/user/测试项目')).toBe(
      'projects/home/user/%E6%B5%8B%E8%AF%95%E9%A1%B9%E7%9B%AE'
    );
  });

  it('handles path with special characters', () => {
    expect(buildProjectsPathSegment('/home/user/test@proj')).toBe('projects/home/user/test%40proj');
  });

  it('handles empty segments (double slashes)', () => {
    // Empty string segment encodes to empty string, preserving slash
    expect(buildProjectsPathSegment('/home//user')).toBe('projects/home/user');
  });

  it('handles root path', () => {
    expect(buildProjectsPathSegment('/')).toBe('projects');
  });
});

describe('injectProjectsPath', () => {
  it('injects path into base URL without query', () => {
    expect(injectProjectsPath('http://h:3100', 'projects/home/user')).toBe(
      'http://h:3100/projects/home/user'
    );
  });

  it('injects path into base URL with query string', () => {
    expect(injectProjectsPath('http://h:3100?token=abc', 'projects/home/user')).toBe(
      'http://h:3100/projects/home/user?token=abc'
    );
  });

  it('handles base URL ending with slash', () => {
    expect(injectProjectsPath('http://h:3100/', 'projects/home/user')).toBe(
      'http://h:3100/projects/home/user'
    );
  });

  it('handles base URL with existing path', () => {
    expect(injectProjectsPath('http://h:3100/app', 'projects/home/user')).toBe(
      'http://h:3100/app/projects/home/user'
    );
  });

  it('returns base unchanged when path segment is empty', () => {
    expect(injectProjectsPath('http://h:3100', '')).toBe('http://h:3100');
  });

  it('preserves complex query strings', () => {
    expect(
      injectProjectsPath('http://h:3100?token=abc&lang=en&theme=dark', 'projects/home/user')
    ).toBe('http://h:3100/projects/home/user?token=abc&lang=en&theme=dark');
  });
});

describe('isWorkspaceRoute', () => {
  it('matches the work root', () => {
    expect(isWorkspaceRoute('/work')).toBe(true);
  });

  it('matches the work root with trailing slash', () => {
    expect(isWorkspaceRoute('/work/')).toBe(true);
  });

  it('matches the explicit workspace route', () => {
    expect(isWorkspaceRoute('/work/workspace')).toBe(true);
  });

  it('ignores query strings', () => {
    expect(isWorkspaceRoute('/work/workspace?sessionId=abc&toolName=qwen')).toBe(true);
  });

  it('does not match the workspace route with trailing slash (historical behavior)', () => {
    expect(isWorkspaceRoute('/work/workspace/')).toBe(false);
  });

  it('does not match other work sub-routes', () => {
    expect(isWorkspaceRoute('/work/sessions')).toBe(false);
    expect(isWorkspaceRoute('/work/prompts')).toBe(false);
    expect(isWorkspaceRoute('/manage/dashboard')).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';

import { parseDiffFiles, parseDiffStats } from './WorkflowTimeline.utils';

describe('WorkflowTimeline.utils', () => {
  it('parses diff stats json', () => {
    expect(parseDiffStats('{"additions":100,"deletions":25,"files":3,"commits":2}')).toEqual({
      additions: 100,
      deletions: 25,
      files: 3,
      commits: 2,
    });
    expect(parseDiffStats('')).toBeNull();
    expect(parseDiffStats('{bad json')).toBeNull();
  });

  it('parses per-file diff details across commits and statuses', () => {
    const diff = [
      '--- Commit: abc12345 ---',
      'diff --git a/src/new.ts b/src/new.ts',
      'new file mode 100644',
      '+++ b/src/new.ts',
      '+const answer = 42;',
      'diff --git a/src/old.ts b/src/old.ts',
      'deleted file mode 100644',
      '--- a/src/old.ts',
      '-legacy();',
      '--- Commit: def67890 ---',
      'diff --git a/src/rename.ts b/src/rename-next.ts',
      'rename from src/rename.ts',
      'rename to src/rename-next.ts',
      '@@ -1 +1 @@',
      '-before();',
      '+after();',
    ].join('\n');

    const files = parseDiffFiles(diff);

    expect(files).toHaveLength(3);
    expect(files[0]).toMatchObject({
      commitLabel: 'abc12345',
      path: 'src/new.ts',
      status: 'added',
      additions: 1,
      deletions: 0,
    });
    expect(files[1]).toMatchObject({
      commitLabel: 'abc12345',
      path: 'src/old.ts',
      status: 'deleted',
      additions: 0,
      deletions: 1,
    });
    expect(files[2]).toMatchObject({
      commitLabel: 'def67890',
      path: 'src/rename-next.ts',
      status: 'modified',
      additions: 1,
      deletions: 1,
    });
  });
});

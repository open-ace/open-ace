import { formatTokens } from '@/utils';

export type ParsedDiffFileStatus = 'added' | 'modified' | 'deleted';

export interface ParsedDiffFile {
  id: string;
  path: string;
  status: ParsedDiffFileStatus;
  additions: number;
  deletions: number;
  patch: string;
  commitLabel?: string;
}

export function parseDiffStats(
  statsJson: string
): { additions: number; deletions: number; files: number; commits: number } | null {
  try {
    return statsJson ? JSON.parse(statsJson) : null;
  } catch {
    return null;
  }
}

export function parseDiffFiles(diffText: string): ParsedDiffFile[] {
  if (!diffText.trim()) return [];

  const files: ParsedDiffFile[] = [];
  const lines = diffText.split('\n');
  let commitLabel = '';
  let current: {
    path: string;
    status: ParsedDiffFileStatus;
    additions: number;
    deletions: number;
    patchLines: string[];
    commitLabel?: string;
  } | null = null;

  const pushCurrent = () => {
    if (!current) return;
    files.push({
      id: `${current.commitLabel ?? 'no-commit'}:${current.path}:${files.length}`,
      path: current.path,
      status: current.status,
      additions: current.additions,
      deletions: current.deletions,
      patch: current.patchLines.join('\n').trim(),
      commitLabel: current.commitLabel,
    });
    current = null;
  };

  for (const line of lines) {
    if (line.startsWith('--- Commit: ')) {
      pushCurrent();
      commitLabel = line.replace(/^--- Commit:\s*/, '').replace(/\s*---$/, '').trim();
      continue;
    }

    if (line.startsWith('diff --git ')) {
      pushCurrent();
      const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
      const nextPath = match?.[2] || match?.[1] || line.replace('diff --git ', '').trim();
      current = {
        path: nextPath,
        status: 'modified',
        additions: 0,
        deletions: 0,
        patchLines: [line],
        commitLabel,
      };
      continue;
    }

    if (!current) {
      continue;
    }

    current.patchLines.push(line);

    if (line.startsWith('new file mode ')) {
      current.status = 'added';
    } else if (line.startsWith('deleted file mode ')) {
      current.status = 'deleted';
    } else if (line.startsWith('rename to ')) {
      current.path = line.replace('rename to ', '').trim();
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      current.additions += 1;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      current.deletions += 1;
    }
  }

  pushCurrent();
  return files;
}

export { formatTokens };

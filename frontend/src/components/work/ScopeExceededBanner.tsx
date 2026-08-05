import { useState } from 'react';
import { Button } from '@/components/common';
import { t, type Language } from '@/i18n';

interface ScopeExceededBannerProps {
  fileCount: number;
  currentLimit: number;
  language: Language;
  isPending: boolean;
  /** Retry after persisting a higher per-workflow cap. */
  onRetryWithLimit: (limit: number) => void;
  /** Retry with the existing cap (no override). */
  onPlainRetry: () => void;
}

const roundUp = (n: number, step: number) => Math.ceil(n / step) * step;

/**
 * Shown at the top of a workflow timeline when a round failed ONLY because it
 * exceeded the changed-files cap (#2309). Offers smart preset caps (the
 * smallest ≥ fileCount rounded to 50 is marked suggested) plus a custom input,
 * then retries with the chosen cap persisted on the workflow — without
 * weakening the global cap for other workflows.
 */
export function ScopeExceededBanner({
  fileCount,
  currentLimit,
  language,
  isPending,
  onRetryWithLimit,
  onPlainRetry,
}: ScopeExceededBannerProps) {
  // Deduped preset suggestions anchored to the actual file count, all above the
  // current cap so the retry actually unblocks.
  const presets = Array.from(
    new Set([
      roundUp(fileCount, 50),
      roundUp(fileCount, 100),
      roundUp(Math.max(fileCount, 100) * 2, 50),
    ])
  ).filter((v) => v > 0 && v > currentLimit);
  const suggested = presets[0];

  const [choice, setChoice] = useState<number | 'custom'>(suggested ?? 'custom');
  const [custom, setCustom] = useState('');
  const resolved = choice === 'custom' ? parseInt(custom, 10) : choice;
  const canRetry = Number.isInteger(resolved) && resolved > 0;

  return (
    <div
      className="timeline-state-banner timeline-state-banner--error"
      role="alert"
      data-testid="scope-exceeded-banner"
    >
      <div className="timeline-state-banner__copy">
        <div className="timeline-state-banner__title">{t('autoScopeExceededTitle', language)}</div>
        <div className="timeline-state-banner__message">
          {t('autoScopeExceededBody', language, { count: fileCount, limit: currentLimit })}
        </div>
        <div className="mt-2 d-flex flex-wrap align-items-center gap-3">
          {presets.map((p, i) => (
            <label
              key={p}
              className="d-inline-flex align-items-center gap-1"
              style={{ cursor: isPending ? 'not-allowed' : 'pointer' }}
            >
              <input
                type="radio"
                name="scope-limit"
                checked={choice === p}
                onChange={() => setChoice(p)}
                disabled={isPending}
              />
              <span>
                {p}
                {i === 0 ? ` · ${t('autoScopeSuggested', language)}` : ''}
              </span>
            </label>
          ))}
          <label
            className="d-inline-flex align-items-center gap-1"
            style={{ cursor: isPending ? 'not-allowed' : 'pointer' }}
          >
            <input
              type="radio"
              name="scope-limit"
              checked={choice === 'custom'}
              onChange={() => setChoice('custom')}
              disabled={isPending}
            />
            <span>{t('autoScopeCustom', language)}</span>
          </label>
          {choice === 'custom' && (
            <input
              type="number"
              min={1}
              className="form-control form-control-sm"
              style={{ width: 110 }}
              value={custom}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCustom(e.target.value)}
              disabled={isPending}
              aria-label={t('autoScopeCustom', language)}
            />
          )}
        </div>
      </div>
      <div className="timeline-state-banner__actions d-flex flex-column gap-2 mt-2">
        <Button
          size="sm"
          variant="primary"
          disabled={!canRetry || isPending}
          onClick={() => canRetry && onRetryWithLimit(resolved)}
        >
          {t('autoRetryWithHigherLimit', language)}
        </Button>
        <Button size="sm" variant="outline-secondary" disabled={isPending} onClick={onPlainRetry}>
          {t('autoPlainRetry', language)}
        </Button>
      </div>
    </div>
  );
}

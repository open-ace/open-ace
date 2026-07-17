/* eslint-disable @typescript-eslint/no-explicit-any */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigateMock = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

vi.mock('./LocalDirectoryBrowser', () => ({
  LocalDirectoryBrowser: ({ initialPath, onSelectPath, listMaxHeight }: any) => (
    <div data-testid="local-directory-browser">
      <span data-testid="initial-path">{initialPath}</span>
      <span data-testid="list-max-height">{listMaxHeight}</span>
      <button onClick={() => onSelectPath('/home/alice/project')} data-testid="select-path">
        Select
      </button>
    </div>
  ),
}));

import { PersonalFiles } from './PersonalFiles';

describe('PersonalFiles', () => {
  beforeEach(() => {
    navigateMock.mockClear();
  });

  it('renders the home directory browser', () => {
    render(<PersonalFiles />);

    expect(screen.getByText('personalFiles')).toBeInTheDocument();
    expect(screen.getByTestId('local-directory-browser')).toBeInTheDocument();
    expect(screen.getByTestId('initial-path')).toHaveTextContent('home');
  });

  it('opens a local workspace tab for the selected path', () => {
    render(<PersonalFiles />);

    fireEvent.click(screen.getByTestId('select-path'));

    expect(navigateMock).toHaveBeenCalledTimes(1);
    const target = navigateMock.mock.calls[0][0] as string;
    expect(target).toContain('/work?newTab=true');
    expect(target).toContain('workspaceType=local');
    expect(target).toContain('projectPath=%2Fhome%2Falice%2Fproject');
  });
});

/**
 * TerminalTab connect-race regression test (follow-up to PR #2984).
 *
 * initTerminal completes asynchronously (dynamic xterm chunk import) and
 * only writes xtermRef. A ref write does not re-run effects, so a
 * wsUrl/token delivered before the import finishes was silently dropped —
 * the connect effect never fired again and no WebSocket was ever opened.
 * The component must connect once xterm becomes ready.
 */
import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: Record<string, unknown> = {};
    open = vi.fn();
    onData = vi.fn();
    onResize = vi.fn();
    loadAddon = vi.fn();
    dispose = vi.fn();
    writeln = vi.fn();
    write = vi.fn();
    focus = vi.fn();
  },
}));
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit = vi.fn();
    dispose = vi.fn();
    proposeDimensions = vi.fn(() => ({ cols: 80, rows: 24 }));
  },
}));
vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: class {},
}));

const wsInstances: Array<{ url: string }> = [];
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  binaryType = '';
  url: string;
  close = vi.fn();
  send = vi.fn();
  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
  }
}

import { TerminalTab } from './TerminalTab';

describe('TerminalTab', () => {
  beforeEach(() => {
    wsInstances.length = 0;
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  it('connects when xterm finishes initializing after wsUrl already arrived', async () => {
    // wsUrl/token are present from the first render; xterm's dynamic chunk
    // import resolves only in a microtask after the mount effects ran.
    render(<TerminalTab wsUrl="/ws/terminal/abc" token="tok123" isActive={true} />);

    // At mount the connect effect ran before initTerminal completed.
    expect(wsInstances.length).toBe(0);

    // Flush the dynamic-import microtasks: xterm becomes ready and the
    // pending connect must fire instead of being dropped.
    await act(async () => {});
    expect(wsInstances.length).toBe(1);
    expect(wsInstances[0].url).toContain('/ws/terminal/abc');
    expect(wsInstances[0].url).toContain('token=tok123');

    // No duplicate connection on subsequent effect runs.
    await act(async () => {});
    expect(wsInstances.length).toBe(1);
  });
});

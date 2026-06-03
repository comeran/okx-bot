import { describe, expect, it } from 'vitest';

import config from './vite.config';

describe('vite proxy configuration', () => {
  it('routes API and WebSocket traffic to the local OKX bot backend', () => {
    const proxy = config.server?.proxy as Record<string, { target?: string }>;

    expect(proxy['/api']?.target).toBe('http://127.0.0.1:8080');
    expect(proxy['/ws']?.target).toBe('ws://127.0.0.1:8080');
  });
});

import { describe, expect, it } from 'vitest';

import { configureMonacoEnvironment, createMonacoEnvironment } from './monaco';

describe('monaco worker environment', () => {
  it('creates workers through the provided factory', () => {
    const worker = {} as Worker;
    const environment = createMonacoEnvironment(() => worker);

    expect(environment.getWorker('module-id', 'yaml')).toBe(worker);
  });

  it('assigns MonacoEnvironment on the provided target', () => {
    const worker = {} as Worker;
    const target: { MonacoEnvironment?: ReturnType<typeof createMonacoEnvironment> } = {};

    configureMonacoEnvironment(target, () => worker);

    expect(target.MonacoEnvironment?.getWorker('module-id', 'editor')).toBe(worker);
  });
});

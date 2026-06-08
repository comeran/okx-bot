import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';

export interface MonacoWorkerEnvironment {
  getWorker: (moduleId: string, label: string) => Worker;
}

export interface MonacoEnvironmentTarget {
  MonacoEnvironment?: MonacoWorkerEnvironment;
}

export type MonacoWorkerFactory = () => Worker;

export function createMonacoEnvironment(
  createWorker: MonacoWorkerFactory = () => new EditorWorker(),
): MonacoWorkerEnvironment {
  return {
    getWorker: () => createWorker(),
  };
}

export function configureMonacoEnvironment(
  target: MonacoEnvironmentTarget = globalThis as MonacoEnvironmentTarget,
  createWorker?: MonacoWorkerFactory,
): void {
  target.MonacoEnvironment = createMonacoEnvironment(createWorker);
}

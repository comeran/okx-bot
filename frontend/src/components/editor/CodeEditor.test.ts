import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mount } from '@/test-utils/mount';
import type { StrategyValidationIssue } from '@/types/strategy';
import CodeEditor from './CodeEditor.vue';

interface MockInput {
  attributes: Record<string, string>;
  setAttribute: ReturnType<typeof vi.fn>;
  removeAttribute: ReturnType<typeof vi.fn>;
}

interface MockModel {
  uri: { value: string; toString: () => string };
  value: string;
  disposed: boolean;
  listeners: Set<() => void>;
  getValue: () => string;
  setValue: (next: string) => void;
  onDidChangeContent: (listener: () => void) => { dispose: () => void };
  dispose: () => void;
}

const monaco = vi.hoisted(() => {
  const models = new Map<string, MockModel>();
  const editors: Array<Record<string, any>> = [];
  const markerCalls: Array<{ model: MockModel; owner: string; markers: unknown[] }> = [];
  const languageCalls: Array<{ model: MockModel; language: string }> = [];

  function input(): MockInput {
    const attributes: Record<string, string> = {};
    return {
      attributes,
      setAttribute: vi.fn((name: string, value: string) => { attributes[name] = value; }),
      removeAttribute: vi.fn((name: string) => { delete attributes[name]; }),
    };
  }

  function model(value: string, uri: { value: string; toString: () => string }): MockModel {
    const listeners = new Set<() => void>();
    const result = {
      uri,
      value,
      disposed: false,
      listeners,
      getValue: vi.fn(() => result.value),
      setValue: vi.fn((next: string) => {
        result.value = next;
        for (const listener of listeners) listener();
      }),
      onDidChangeContent: vi.fn((listener: () => void) => {
        listeners.add(listener);
        return { dispose: vi.fn(() => listeners.delete(listener)) };
      }),
      dispose: vi.fn(() => {
        result.disposed = true;
        models.delete(uri.value);
      }),
    };
    return result;
  }

  return {
    models,
    editors,
    markerCalls,
    languageCalls,
    input,
    model,
    Uri: {
      parse: vi.fn((value: string) => ({ value, toString: () => value })),
    },
    editor: {
      create: vi.fn((_surface: unknown, options: Record<string, unknown>) => {
        const focusedInput = input();
        const instance = {
          options,
          focusedInput,
          currentModel: null as MockModel | null,
          disposed: false,
          setModel: vi.fn((next: MockModel | null) => { instance.currentModel = next; }),
          updateOptions: vi.fn((next: Record<string, unknown>) => Object.assign(instance.options, next)),
          getDomNode: vi.fn(() => ({ querySelector: vi.fn(() => focusedInput) })),
          dispose: vi.fn(() => { instance.disposed = true; }),
        };
        editors.push(instance);
        return instance;
      }),
      getModel: vi.fn((uri: { value: string }) => models.get(uri.value) ?? null),
      createModel: vi.fn((value: string, _language: string, uri: { value: string; toString: () => string }) => {
        const created = model(value, uri);
        models.set(uri.value, created);
        return created;
      }),
      setModelMarkers: vi.fn((target: MockModel, owner: string, markers: unknown[]) => {
        markerCalls.push({ model: target, owner, markers });
      }),
      setModelLanguage: vi.fn((target: MockModel, language: string) => {
        languageCalls.push({ model: target, language });
      }),
    },
  };
});

vi.mock('monaco-editor', () => ({
  Uri: monaco.Uri,
  editor: monaco.editor,
}));

const mountedWrappers: Array<Awaited<ReturnType<typeof mount>>> = [];

async function mountEditor(options: {
  value?: string;
  modelUri?: string;
  label?: string;
  description?: string;
  readonly?: boolean;
  issues?: StrategyValidationIssue[];
} = {}) {
  let value = options.value ?? 'name: first';
  const updates: string[] = [];
  let wrapper: Awaited<ReturnType<typeof mount>> | undefined;
  wrapper = await mount(CodeEditor, {
    props: {
      modelValue: value,
      modelUri: options.modelUri ?? 'inmemory://strategy/first.yaml',
      label: options.label ?? 'Localized YAML label',
      description: options.description ?? 'Localized YAML description',
      readonly: options.readonly ?? false,
      issues: options.issues ?? [],
      'onUpdate:modelValue': (next: string) => {
        value = next;
        updates.push(next);
        void wrapper?.updateProps({ modelValue: next });
      },
    },
  });
  if (!wrapper) throw new Error('CodeEditor did not mount');
  mountedWrappers.push(wrapper);
  return { wrapper, updates, value: () => value };
}

describe('CodeEditor', () => {
  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
  });

  beforeEach(() => {
    monaco.models.clear();
    monaco.editors.length = 0;
    monaco.markerCalls.length = 0;
    monaco.languageCalls.length = 0;
    vi.clearAllMocks();
  });

  it('synchronizes v-model and readonly options through a mounted editor', async () => {
    const { wrapper, updates } = await mountEditor({ readonly: true });
    const editor = monaco.editors[0];
    const model = editor.currentModel as MockModel;

    expect(editor.options).toMatchObject({ readOnly: true, ariaLabel: 'Localized YAML label' });
    model.setValue('name: changed');
    await wrapper.flush();
    expect(updates).toEqual(['name: changed']);

    await wrapper.updateProps({ modelValue: 'name: parent', readonly: false });
    expect(model.getValue()).toBe('name: parent');
    expect(editor.updateOptions).toHaveBeenCalledWith({ readOnly: false });
  });

  it('labels only Monaco’s focused input and keeps its description association current', async () => {
    const { wrapper } = await mountEditor();
    const editor = monaco.editors[0];
    const description = wrapper.find((node) => node.type === 'p');
    const surface = wrapper.find((node) => node.props.class === 'code-editor__surface');

    expect(surface.props).not.toHaveProperty('role');
    expect(surface.props).not.toHaveProperty('aria-label');
    expect(surface.props).not.toHaveProperty('aria-describedby');
    expect(surface.props).not.toHaveProperty('aria-readonly');
    expect(editor.options.ariaLabel).toBe('Localized YAML label');
    expect(editor.focusedInput.attributes['aria-label']).toBe('Localized YAML label');
    expect(editor.focusedInput.attributes['aria-describedby']).toBe(description.props.id);

    await wrapper.updateProps({ label: 'Updated label', description: '' });
    expect(editor.updateOptions).toHaveBeenCalledWith({ ariaLabel: 'Updated label' });
    expect(editor.focusedInput.attributes['aria-label']).toBe('Updated label');
    expect(editor.focusedInput.attributes['aria-describedby']).toBeUndefined();
  });

  it('applies positioned markers and clears them after diagnostics are removed', async () => {
    const issue: StrategyValidationIssue = {
      path: 'params.fast_window', code: 'minimum', message: 'Too small', line: 8, column: 5,
    };
    const { wrapper } = await mountEditor({ issues: [issue] });
    const model = monaco.editors[0].currentModel as MockModel;

    expect(monaco.markerCalls.at(-1)).toMatchObject({
      model,
      owner: 'strategy-validation',
      markers: [{ message: 'Too small', startLineNumber: 8, startColumn: 5 }],
    });

    await wrapper.updateProps({ issues: [] });
    expect(monaco.markerCalls.at(-1)).toEqual({ model, owner: 'strategy-validation', markers: [] });
  });

  it('switches URI models and cleans subscriptions and module-owned models', async () => {
    const { wrapper } = await mountEditor();
    const editor = monaco.editors[0];
    const first = editor.currentModel as MockModel;

    await wrapper.updateProps({ modelUri: 'inmemory://strategy/second.yaml', modelValue: 'name: second' });
    const second = editor.currentModel as MockModel;
    expect(second).not.toBe(first);
    expect(first.dispose).toHaveBeenCalledTimes(1);
    expect(first.listeners.size).toBe(0);

    wrapper.unmount();
    expect(second.dispose).toHaveBeenCalledTimes(1);
    expect(second.listeners.size).toBe(0);
    expect(editor.dispose).toHaveBeenCalledTimes(1);
  });

  it('switches URI and parent content together without writing B into a shared A model', async () => {
    const holder = await mountEditor({ value: 'name: a', modelUri: 'inmemory://strategy/a.yaml' });
    const sharedA = monaco.editors[0].currentModel as MockModel;
    const switching = await mountEditor({ value: 'name: a', modelUri: 'inmemory://strategy/a.yaml' });

    await switching.wrapper.updateProps({
      modelUri: 'inmemory://strategy/b.yaml',
      modelValue: 'name: b',
    });

    expect(sharedA.getValue()).toBe('name: a');
    expect(monaco.editors[0].currentModel).toBe(sharedA);
    expect(monaco.editors[1].currentModel).not.toBe(sharedA);
    expect((monaco.editors[1].currentModel as MockModel).getValue()).toBe('name: b');
    holder.wrapper.unmount();
  });

  it('keeps a shared module-owned model alive until its final editor unmounts', async () => {
    const first = await mountEditor({ value: 'shared', modelUri: 'inmemory://strategy/shared.yaml' });
    const model = monaco.editors[0].currentModel as MockModel;
    const second = await mountEditor({ value: 'shared', modelUri: 'inmemory://strategy/shared.yaml' });

    expect(monaco.editors[1].currentModel).toBe(model);
    expect(monaco.editor.createModel).toHaveBeenCalledTimes(1);
    first.wrapper.unmount();
    expect(model.dispose).not.toHaveBeenCalled();
    expect(monaco.editors[1].currentModel).toBe(model);

    second.wrapper.unmount();
    expect(model.dispose).toHaveBeenCalledTimes(1);
  });

  it('never disposes an externally owned Monaco model', async () => {
    const uri = monaco.Uri.parse('inmemory://strategy/external.yaml');
    const external = monaco.model('external value', uri);
    monaco.models.set(uri.value, external);

    const { wrapper, updates } = await mountEditor({
      value: 'different parent value',
      modelUri: uri.value,
    });

    expect(monaco.editors[0].currentModel).toBe(external);
    expect(external.getValue()).toBe('different parent value');
    expect(updates).toEqual([]);
    wrapper.unmount();
    expect(external.dispose).not.toHaveBeenCalled();
    expect(monaco.models.get(uri.value)).toBe(external);
  });
});

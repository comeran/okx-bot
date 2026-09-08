import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import type { StrategyConfigPayload, StrategyDefinition } from '@/types/strategy';
import StrategyEditorPanel from './StrategyEditorPanel.vue';

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => (params?.name ? `${key}:${params.name}` : key),
  }),
}));

vi.mock('@/components/StrategyForm.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'StrategyForm',
      inheritAttrs: false,
      props: ['modelValue', 'definitions', 'mode', 'issues', 'readonly', 'dirty'],
      emits: ['update:modelValue'],
      setup(props, { attrs, emit }) {
        return () => h('strategy-form-stub', {
          ...attrs,
          ...props,
          'onUpdate:modelValue': (value: StrategyConfigPayload) => emit('update:modelValue', value),
        });
      },
    }),
  };
});

vi.mock('@/components/editor/CodeEditor.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'CodeEditor',
      inheritAttrs: false,
      props: ['modelValue', 'modelUri', 'label', 'description', 'issues', 'readonly', 'height'],
      emits: ['update:modelValue'],
      setup(props, { attrs, emit }) {
        return () => h('code-editor-stub', {
          ...attrs,
          ...props,
          'onUpdate:modelValue': (value: string) => emit('update:modelValue', value),
        });
      },
    }),
  };
});

const ElButton = defineHostComponent('el-button');
const strategyEditorPanelSource = readFileSync(fileURLToPath(new URL('./StrategyEditorPanel.vue', import.meta.url)), 'utf8');

const definitions: StrategyDefinition[] = [
  {
    strategy_type: 'ma_cross',
    label: 'MA Cross',
    description: 'Cross',
    params: [],
  },
];

function draft(overrides: Partial<StrategyConfigPayload> = {}): StrategyConfigPayload {
  return {
    name: 'desk:btc',
    strategy_type: 'ma_cross',
    symbol: 'BTC-USDT-SWAP',
    timeframe: '5m',
    enabled: true,
    params: { fast_window: 10 },
    ...overrides,
  };
}

function button(wrapper: Awaited<ReturnType<typeof mount>>, label: string) {
  return wrapper.find((node) => node.type === 'el-button' && textContent(node) === label);
}

describe('StrategyEditorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the sticky mobile action bar safe-area contract in the SFC stylesheet', () => {
    expect(strategyEditorPanelSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.strategy-editor-panel__actions \{[\s\S]*?position: sticky;[\s\S]*?bottom: 0;[\s\S]*?padding-bottom: calc\(var\(--ui-space-12\) \+ env\(safe-area-inset-bottom, 0px\)\);[\s\S]*?\}/);
  });

  it('renders metadata, dirty state, and structured controls', async () => {
    const wrapper = await mount(StrategyEditorPanel, {
      components: { ElButton },
      props: {
        modelValue: draft(),
        yaml: 'name: desk:btc',
        title: 'strategies.editor.edit',
        mode: 'edit',
        definitions,
        advanced: false,
        readonly: true,
        dirty: true,
        busy: false,
        saveLoading: false,
        modelUri: 'inmemory://strategy/edit.yaml',
        validationSummary: ['Validation issue'],
        selectedName: 'desk:btc',
        cloneSourceName: '',
        issues: [],
        'onUpdate:modelValue': vi.fn(),
        'onUpdate:yaml': vi.fn(),
      },
    });

    expect(wrapper.text()).toContain('strategies.editor.edit');
    expect(wrapper.text()).toContain('strategies.editor.selectedMetadata:desk:btc');
    expect(wrapper.text()).toContain('strategies.editor.unsavedChanges');
    expect(wrapper.text()).toContain('Validation issue');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub')).toBeTruthy();
    expect(wrapper.findAll((node) => node.type === 'code-editor-stub')).toHaveLength(0);
    expect(button(wrapper, 'strategies.editor.structured')).toBeTruthy();
    expect(button(wrapper, 'strategies.editor.advanced')).toBeTruthy();
    expect(button(wrapper, 'common.save')).toBeTruthy();
    expect(button(wrapper, 'common.cancel')).toBeTruthy();
    expect(button(wrapper, 'common.close')).toBeTruthy();

    wrapper.unmount();
  });

  it('emits mode toggles and preserves the bound payloads when switching views', async () => {
    const updates: Array<{ model?: StrategyConfigPayload; yaml?: string }> = [];
    let wrapper: Awaited<ReturnType<typeof mount>> | undefined;
    wrapper = await mount(StrategyEditorPanel, {
      components: { ElButton },
      props: {
        modelValue: draft({ timeframe: '15m' }),
        yaml: 'name: desk:btc\ntimeframe: 15m',
        title: 'strategies.editor.clone',
        mode: 'clone',
        definitions,
        advanced: false,
        readonly: false,
        dirty: false,
        busy: false,
        saveLoading: false,
        modelUri: 'inmemory://strategy/clone.yaml',
        validationSummary: [],
        selectedName: 'desk:btc',
        cloneSourceName: 'desk:btc',
        issues: [],
        'onUpdate:modelValue': (value: StrategyConfigPayload) => {
          updates.push({ model: value });
          void wrapper?.updateProps({ modelValue: value });
        },
        'onUpdate:yaml': (value: string) => {
          updates.push({ yaml: value });
          void wrapper?.updateProps({ yaml: value });
        },
      },
    });

    await wrapper.trigger(button(wrapper, 'strategies.editor.advanced'), 'click');
    expect(updates).toEqual([]);
    await wrapper.updateProps({ advanced: true });
    expect(wrapper.find((node) => node.type === 'code-editor-stub')).toBeTruthy();
    expect(wrapper.find((node) => node.type === 'code-editor-stub').props.modelValue).toBe('name: desk:btc\ntimeframe: 15m');

    await wrapper.trigger(button(wrapper, 'common.save'), 'click');
    await wrapper.trigger(button(wrapper, 'common.cancel'), 'click');
    await wrapper.trigger(button(wrapper, 'common.close'), 'click');

    expect(wrapper.find((node) => node.type === 'code-editor-stub').props.modelValue).toBe('name: desk:btc\ntimeframe: 15m');
    expect(wrapper.find((node) => node.type === 'code-editor-stub').props.modelUri).toBe('inmemory://strategy/clone.yaml');

    await wrapper.trigger(button(wrapper, 'strategies.editor.structured'), 'click');
    await wrapper.updateProps({ advanced: false });
    expect(updates).toEqual([]);
    expect(wrapper.find((node) => node.type === 'strategy-form-stub')).toBeTruthy();
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.modelValue).toMatchObject({ timeframe: '15m' });

    wrapper.unmount();
  });
});

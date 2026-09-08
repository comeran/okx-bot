import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent, type TestHostNode } from '@/test-utils/mount';
import StrategyList, { type StrategyListRow } from './StrategyList.vue';
import StrategyListSource from './StrategyList.vue?raw';

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

const ElButton = defineHostComponent('el-button');

function row(overrides: Partial<StrategyListRow>): StrategyListRow {
  return {
    name: 'desk:btc',
    strategyType: 'ma_cross',
    symbol: 'BTC-USDT-SWAP',
    timeframe: '5m',
    enabled: true,
    statusLabel: 'Stopped',
    statusTone: 'info',
    runtimeError: '',
    safetyText: 'strategies.list.stoppedSafety',
    selected: false,
    canEdit: true,
    canDelete: true,
    canStart: true,
    canStop: false,
    isDeleting: false,
    isStarting: false,
    isStopping: false,
    actionLabels: {
      select: 'strategies.actions.select:desk:btc',
      edit: 'strategies.actions.edit:desk:btc',
      clone: 'strategies.actions.clone:desk:btc',
      delete: 'strategies.actions.delete:desk:btc',
      start: 'strategies.actions.start:desk:btc',
      stop: 'strategies.actions.stop:desk:btc',
    },
    ...overrides,
  };
}

function descendantOf(node: TestHostNode, ancestor: TestHostNode): boolean {
  let current: TestHostNode | null = node.parent;
  while (current) {
    if (current === ancestor) return true;
    current = current.parent;
  }
  return false;
}

function actionLabels(
  wrapper: Awaited<ReturnType<typeof mount>>,
  ancestor: TestHostNode,
  includeSelect = false,
): string[] {
  return wrapper.findAll((node) => node.type === 'el-button' && descendantOf(node, ancestor))
    .map((node) => String(node.props['aria-label']))
    .filter((label) => includeSelect || !label.startsWith('strategies.actions.select:'));
}

describe('StrategyList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('switches to cards at tablet widths before the app shell overflows', () => {
    expect(StrategyListSource).toContain('@media (max-width: 1023px)');
    expect(StrategyListSource).toContain('.strategy-list {\n    display: none;\n  }');
    expect(StrategyListSource).toContain('.strategy-list__cards {\n    display: block;\n  }');
  });

  it('keeps desktop strategy actions and labels inside a fixed table layout', () => {
    expect(StrategyListSource).toContain('table-layout: fixed;');
    expect(StrategyListSource).toContain('overflow-wrap: anywhere;');
    expect(StrategyListSource).toContain('word-break: break-word;');
    expect(StrategyListSource).toContain('min-width: 0;');
    expect(StrategyListSource).toContain('max-width: 100%;');
    expect(StrategyListSource).toContain('flex-wrap: wrap;');
    expect(StrategyListSource).toContain('white-space: normal;');
    expect(StrategyListSource).toContain('box-sizing: border-box;');
  });

  it('keeps the same row action model across desktop and mobile layouts', async () => {
    const onSelect = vi.fn();
    const onEdit = vi.fn();
    const onClone = vi.fn();
    const onDelete = vi.fn();
    const onStart = vi.fn();
    const onStop = vi.fn();

    const rows = [
      row({
        name: 'desk:btc',
        selected: false,
        statusLabel: 'Running',
        statusTone: 'success',
        canEdit: false,
        canDelete: false,
        canStart: false,
        canStop: true,
        safetyText: 'strategies.list.runningSafety',
        actionLabels: {
          select: 'strategies.actions.select:desk:btc',
          edit: 'strategies.actions.edit:desk:btc',
          clone: 'strategies.actions.clone:desk:btc',
          delete: 'strategies.actions.delete:desk:btc',
          start: 'strategies.actions.start:desk:btc',
          stop: 'strategies.actions.stop:desk:btc',
        },
      }),
      row({
        name: 'desk:eth',
        selected: true,
        statusLabel: 'Stopped',
        statusTone: 'info',
        runtimeError: 'Exchange unavailable',
        safetyText: 'strategies.list.stoppedSafety',
        actionLabels: {
          select: 'strategies.actions.select:desk:eth',
          edit: 'strategies.actions.edit:desk:eth',
          clone: 'strategies.actions.clone:desk:eth',
          delete: 'strategies.actions.delete:desk:eth',
          start: 'strategies.actions.start:desk:eth',
          stop: 'strategies.actions.stop:desk:eth',
        },
      }),
    ];

    const wrapper = await mount(StrategyList, {
      components: { ElButton },
      props: {
        title: 'strategies.list.title',
        description: 'strategies.list.description',
        rows,
        onSelect,
        onEdit,
        onClone,
        onDelete,
        onStart,
        onStop,
      },
    });

    const desktop = wrapper.getByTestId('strategy-desktop-table');
    const mobile = wrapper.getByTestId('strategy-mobile-cards');
    const desktopRunning = wrapper.find((node) => node.type === 'tr' && textContent(node).includes('desk:btc'));
    const mobileRunning = wrapper.find((node) => node.type === 'article' && String(node.props.class).includes('strategy-list__card') && textContent(node).includes('desk:btc'));
    const desktopStopped = wrapper.find((node) => node.type === 'tr' && textContent(node).includes('desk:eth'));
    const mobileStopped = wrapper.find((node) => node.type === 'article' && String(node.props.class).includes('strategy-list__card') && textContent(node).includes('desk:eth'));

    expect(textContent(desktop)).toContain('Running');
    expect(textContent(mobile)).toContain('Exchange unavailable');
    expect(wrapper.find((node) => node.type === 'span' && String(node.props.class).includes('status-badge') && node.props['aria-label'] === 'Running')).toBeTruthy();
    expect(wrapper.find((node) => node.type === 'span' && String(node.props.class).includes('status-badge') && node.props['aria-label'] === 'Stopped')).toBeTruthy();

    expect(actionLabels(wrapper, desktopRunning)).toEqual(actionLabels(wrapper, mobileRunning));
    expect(actionLabels(wrapper, desktopStopped)).toEqual(actionLabels(wrapper, mobileStopped));
    expect(actionLabels(wrapper, desktopRunning)).toEqual([
      'strategies.actions.stop:desk:btc',
    ]);
    expect(actionLabels(wrapper, desktopStopped)).toEqual([
      'strategies.actions.edit:desk:eth',
      'strategies.actions.clone:desk:eth',
      'strategies.actions.delete:desk:eth',
      'strategies.actions.start:desk:eth',
    ]);
    expect(buttons(wrapper, 'strategies.actions.start:desk:btc')).toHaveLength(0);
    expect(buttons(wrapper, 'strategies.actions.stop:desk:btc')).toHaveLength(2);
    expect(buttons(wrapper, 'strategies.actions.stop:desk:eth')).toHaveLength(0);

    await wrapper.trigger(wrapper.find((node) => node.type === 'button' && node.props['aria-label'] === 'strategies.actions.select:desk:eth'), 'click');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:eth')[0], 'click');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.start:desk:eth')[0], 'click');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.stop:desk:btc')[0], 'click');

    expect(onSelect).toHaveBeenCalledWith(rows[1]);
    expect(onEdit).toHaveBeenCalledWith(rows[1]);
    expect(onStart).toHaveBeenCalledWith(rows[1]);
    expect(onStop).toHaveBeenCalledWith(rows[0]);

    wrapper.unmount();
  });
});

function buttons(wrapper: Awaited<ReturnType<typeof mount>>, label: string): TestHostNode[] {
  return wrapper.findAll((node) => node.type === 'el-button' && node.props['aria-label'] === label);
}

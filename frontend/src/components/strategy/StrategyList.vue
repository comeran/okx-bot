<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import DataState from '@/components/ui/DataState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';

export type StrategyListStatusTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';

export interface StrategyListRowActions {
  select: string;
  edit: string;
  clone: string;
  delete: string;
  start: string;
  stop: string;
}

export interface StrategyListRow {
  name: string;
  strategyType: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  statusLabel: string;
  statusTone: StrategyListStatusTone;
  runtimeError: string;
  safetyText: string;
  selected: boolean;
  canEdit: boolean;
  canDelete: boolean;
  canStart: boolean;
  canStop: boolean;
  isDeleting: boolean;
  isStarting: boolean;
  isStopping: boolean;
  actionLabels: StrategyListRowActions;
}

interface Props {
  title: string;
  description?: string;
  rows: StrategyListRow[];
  loading?: boolean;
  emptyDescription?: string;
  onSelect?: (row: StrategyListRow) => void;
  onEdit?: (row: StrategyListRow) => void;
  onClone?: (row: StrategyListRow) => void;
  onDelete?: (row: StrategyListRow) => void;
  onStart?: (row: StrategyListRow) => void;
  onStop?: (row: StrategyListRow) => void;
}

const props = withDefaults(defineProps<Props>(), {
  description: '',
  loading: false,
  emptyDescription: '',
});

const { t } = useI18n();

const hasRows = computed(() => props.rows.length > 0);

function invoke(handler: ((row: StrategyListRow) => void) | undefined, row: StrategyListRow): void {
  handler?.(row);
}

function visibleActions(row: StrategyListRow) {
  return [
    row.canEdit ? { key: 'edit', label: row.actionLabels.edit, type: 'default' as const, handler: props.onEdit } : null,
    row.canEdit ? { key: 'clone', label: row.actionLabels.clone, type: 'default' as const, handler: props.onClone } : null,
    row.canDelete ? {
      key: 'delete',
      label: row.actionLabels.delete,
      type: 'danger' as const,
      handler: props.onDelete,
      loading: row.isDeleting,
    } : null,
    row.canStart ? {
      key: 'start',
      label: row.actionLabels.start,
      type: 'success' as const,
      handler: props.onStart,
      loading: row.isStarting,
    } : null,
    row.canStop ? {
      key: 'stop',
      label: row.actionLabels.stop,
      type: 'warning' as const,
      handler: props.onStop,
      loading: row.isStopping,
    } : null,
  ].filter(Boolean) as Array<{
    key: string;
    label: string;
    type: 'default' | 'danger' | 'success' | 'warning';
    handler?: (row: StrategyListRow) => void;
    loading?: boolean;
  }>;
}
</script>

<template>
  <SectionCard :title="props.title" :description="props.description" class="strategy-list-card">
    <DataState :loading="props.loading" :empty="!hasRows" :empty-description="props.emptyDescription">
      <template #default>
        <div class="strategy-list" data-testid="strategy-desktop-table">
          <table class="strategy-list__table">
            <colgroup>
              <col class="strategy-list__col-name">
              <col class="strategy-list__col-type">
              <col class="strategy-list__col-market">
              <col class="strategy-list__col-enabled">
              <col class="strategy-list__col-status">
              <col class="strategy-list__col-actions">
            </colgroup>
            <thead>
              <tr>
                <th>{{ t('common.name') }}</th>
                <th>{{ t('strategies.form.strategyType') }}</th>
                <th>{{ t('strategies.market') }}</th>
                <th>{{ t('strategies.form.enabled') }}</th>
                <th>{{ t('common.status') }}</th>
                <th>{{ t('common.actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in props.rows"
                :key="row.name"
                :class="{ 'strategy-list__row--selected': row.selected }"
                :aria-selected="row.selected ? 'true' : 'false'"
                tabindex="0"
                @click="invoke(props.onSelect, row)"
                @keydown.enter.prevent="invoke(props.onSelect, row)"
              >
                <td>
                  <button
                    type="button"
                    class="strategy-list__name-button"
                    :aria-label="row.actionLabels.select"
                    :aria-pressed="row.selected ? 'true' : 'false'"
                    @click.stop="invoke(props.onSelect, row)"
                  >
                    <strong>{{ row.name }}</strong>
                  </button>
                </td>
                <td>{{ row.strategyType }}</td>
                <td>{{ row.symbol }} / {{ row.timeframe }}</td>
                <td>{{ row.enabled ? t('common.yes') : t('common.no') }}</td>
                <td>
                  <div class="strategy-list__status-cell">
                    <StatusBadge :status="row.statusLabel" :tone="row.statusTone" />
                    <p v-if="row.safetyText" class="strategy-list__meta">{{ row.safetyText }}</p>
                    <p v-if="row.runtimeError" class="strategy-list__error">{{ row.runtimeError }}</p>
                  </div>
                </td>
                <td class="strategy-list__actions-cell">
                  <div class="strategy-list__actions" @click.stop @keydown.stop>
                    <el-button
                      v-for="action in visibleActions(row)"
                      :key="action.key"
                      size="small"
                      :type="action.type === 'default' ? undefined : action.type"
                      :loading="action.loading"
                      :aria-label="action.label"
                      @click="invoke(action.handler, row)"
                    >
                      {{ action.label }}
                    </el-button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="strategy-list__cards" data-testid="strategy-mobile-cards">
          <article
            v-for="row in props.rows"
            :key="row.name"
            class="strategy-list__card"
            :class="{ 'strategy-list__card--selected': row.selected }"
          >
            <div class="strategy-list__card-header">
              <button
                type="button"
                class="strategy-list__name-button"
                :aria-label="row.actionLabels.select"
                :aria-pressed="row.selected ? 'true' : 'false'"
                @click="invoke(props.onSelect, row)"
              >
                <strong>{{ row.name }}</strong>
              </button>
              <StatusBadge :status="row.statusLabel" :tone="row.statusTone" />
            </div>
            <dl>
              <dt>{{ t('strategies.form.strategyType') }}</dt>
              <dd>{{ row.strategyType }}</dd>
              <dt>{{ t('strategies.market') }}</dt>
              <dd>{{ row.symbol }} / {{ row.timeframe }}</dd>
              <dt>{{ t('strategies.form.enabled') }}</dt>
              <dd>{{ row.enabled ? t('common.yes') : t('common.no') }}</dd>
            </dl>
            <p v-if="row.safetyText" class="strategy-list__meta">{{ row.safetyText }}</p>
            <p v-if="row.runtimeError" class="strategy-list__error">{{ row.runtimeError }}</p>
            <div class="strategy-list__actions">
              <el-button
                v-for="action in visibleActions(row)"
                :key="action.key"
                size="small"
                :type="action.type === 'default' ? undefined : action.type"
                :loading="action.loading"
                :aria-label="action.label"
                @click="invoke(action.handler, row)"
              >
                {{ action.label }}
              </el-button>
            </div>
          </article>
        </div>
      </template>
    </DataState>
  </SectionCard>
</template>

<style scoped>
.strategy-list-card {
  min-width: 0;
}

.strategy-list {
  min-width: 0;
}

.strategy-list__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.strategy-list__col-name {
  width: 17%;
}

.strategy-list__col-type {
  width: 18%;
}

.strategy-list__col-market {
  width: 21%;
}

.strategy-list__col-enabled {
  width: 10%;
}

.strategy-list__col-status {
  width: 18%;
}

.strategy-list__col-actions {
  width: 16%;
}

.strategy-list__table th,
.strategy-list__table td {
  padding: var(--ui-space-12) var(--ui-space-10);
  border-bottom: var(--ui-border-width-thin) solid var(--ui-color-border);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.strategy-list__row--selected {
  background: var(--ui-color-primary-soft);
}

.strategy-list__row--selected .strategy-list__name-button {
  color: var(--ui-color-primary);
}

.strategy-list__name-button {
  appearance: none;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.strategy-list__name-button:focus-visible {
  outline: 2px solid var(--ui-color-primary);
  outline-offset: 2px;
}

.strategy-list__status-cell {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-6);
  min-width: 0;
}

.strategy-list__actions-cell {
  min-width: 0;
  max-width: 100%;
}

.strategy-list__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-8);
  min-width: 0;
  max-width: 100%;
}

.strategy-list__actions :deep(.el-button) {
  max-width: 100%;
  white-space: normal;
  overflow-wrap: anywhere;
  box-sizing: border-box;
  height: auto;
  text-align: left;
}

.strategy-list__meta,
.strategy-list__error {
  margin: 0;
  font-size: var(--ui-font-size-12);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.strategy-list__meta {
  color: var(--ui-color-text-secondary);
}

.strategy-list__error {
  color: var(--ui-color-danger);
}

.strategy-list__cards {
  display: none;
}

.strategy-list__card {
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
}

.strategy-list__card--selected {
  border-color: color-mix(in srgb, var(--ui-color-primary) 34%, var(--ui-color-border));
  background: var(--ui-color-primary-soft);
}

.strategy-list__card + .strategy-list__card {
  margin-top: var(--ui-space-12);
}

.strategy-list__card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ui-space-12);
  margin-bottom: var(--ui-space-12);
}

.strategy-list__card dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--ui-space-6) var(--ui-space-12);
  margin: 0;
}

.strategy-list__card dt {
  color: var(--ui-color-text-secondary);
}

.strategy-list__card dd {
  margin: 0;
  overflow-wrap: anywhere;
}

@media (max-width: 1023px) {
  .strategy-list {
    display: none;
  }

  .strategy-list__cards {
    display: block;
  }
}
</style>

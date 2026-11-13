<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import SectionCard from '@/components/ui/SectionCard.vue';
import StrategyAllocationReadiness from './StrategyAllocationReadiness.vue';
import { matchesExecutionContext, useAccountContextStore } from '@/stores/accountContext';
import {
  activateStrategyAllocation,
  deleteStrategyAllocation,
  getStrategyAllocation,
  releaseStrategyAllocation,
  saveStrategyAllocation,
} from '@/services/strategyAllocations';
import type { StrategyAllocationEnvelope } from '@/types/strategyAllocation';

const props = defineProps<{ strategyName: string; status?: string; strategySymbol?: string }>();
const { t } = useI18n();
const accountContext = useAccountContextStore();
const allocation = ref<StrategyAllocationEnvelope | null>(null);
const budget = ref('');
const loading = ref(false);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const needsRefresh = ref(false);
let requestSequence = 0;

const stopped = computed(() => (
  (props.status ?? allocation.value?.lifecycle_status) === 'stopped'
));
const reservation = computed(() => allocation.value?.reservation ?? null);
const localPaper = computed(() => allocation.value?.context.execution_mode === 'paper');
const baseEditable = computed(() => Boolean(
  allocation.value?.context.available
  && allocation.value.context.scope_id
  && allocation.value.can_edit
  && matchesExecutionContext(accountContext.context, allocation.value.execution_context)
  && stopped.value
  && !loading.value && !busy.value && !needsRefresh.value
  && !allocation.value?.other_scope_reservation,
));
const canEdit = computed(() => baseEditable.value && reservation.value == null);
const canActivate = computed(() => Boolean(
  localPaper.value && canEdit.value && allocation.value?.draft,
));
const canRelease = computed(() => Boolean(
  localPaper.value && reservation.value && stopped.value
  && !loading.value && !busy.value && !needsRefresh.value,
));
const executionLabel = computed(() => {
  const mode = allocation.value?.context.execution_mode;
  if (mode === 'paper') return t('strategies.allocation.localPaper');
  if (mode === 'demo') return t('strategies.allocation.sandbox');
  if (mode === 'live') return t('strategies.allocation.exchangeRouting');
  return t('strategies.allocation.backtest');
});
const unavailableMessage = computed(() => (
  allocation.value?.context.reason === 'unsupported_market'
    ? t('strategies.allocation.unsupportedMarket')
    : t('strategies.allocation.credentialsUnavailable')
));

function applyEnvelope(envelope: StrategyAllocationEnvelope): void {
  if (envelope.strategy !== props.strategyName) throw new Error(t('strategies.allocation.loadFailed'));
  const context = accountContext.context;
  const unavailableMatch = context && !context.available && !envelope.execution_context?.available
    && !envelope.context.available && envelope.execution_context?.account_scope_id === null
    && envelope.execution_context.environment === context.environment
    && envelope.execution_context.market_type === context.market_type;
  if (!matchesExecutionContext(context, envelope.execution_context) && !unavailableMatch) {
    throw new Error(t('strategies.allocation.loadFailed'));
  }
  allocation.value = envelope;
  budget.value = envelope.draft === null ? '' : String(envelope.draft.budget);
}

function showFailure(cause: unknown, fallback: string): void {
  const response = (cause as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  const detail = response?.data?.detail;
  error.value = typeof detail === 'string'
    ? detail
    : typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string'
      ? detail.message
      : fallback;
  if (response?.status === 409) needsRefresh.value = true;
}

async function load(): Promise<void> {
  const sequence = ++requestSequence;
  const strategy = props.strategyName;
  const epoch = accountContext.epoch;
  allocation.value = null;
  budget.value = '';
  loading.value = true;
  busy.value = false;
  error.value = '';
  notice.value = '';
  needsRefresh.value = false;
  if (!accountContext.context) { loading.value = false; return; }
  try {
    const result = await getStrategyAllocation(strategy);
    if (sequence === requestSequence && epoch === accountContext.epoch) applyEnvelope(result);
  } catch (cause) {
    if (sequence === requestSequence && epoch === accountContext.epoch) showFailure(cause, t('strategies.allocation.loadFailed'));
  } finally {
    if (sequence === requestSequence && epoch === accountContext.epoch) loading.value = false;
  }
}

async function save(): Promise<void> {
  if (!canEdit.value || !allocation.value?.context.scope_id) return;
  error.value = '';
  notice.value = '';
  const amount = Number(budget.value);
  if (!budget.value.trim() || !Number.isFinite(amount) || amount <= 0) {
    error.value = t('strategies.allocation.invalidBudget');
    return;
  }
  const sequence = ++requestSequence;
  const strategy = props.strategyName;
  const epoch = accountContext.epoch;
  const revision = allocation.value?.draft?.revision ?? 0;
  const scopeId = allocation.value.context.scope_id;
  const symbol = allocation.value.symbol;
  busy.value = true;
  try {
    const result = await saveStrategyAllocation(strategy, {
      budget: amount,
      expected_revision: revision,
      expected_scope_id: scopeId,
      expected_symbol: symbol,
    });
    if (sequence !== requestSequence || epoch !== accountContext.epoch) return;
    applyEnvelope(result);
    notice.value = t('strategies.allocation.saved');
  } catch (cause) {
    if (sequence === requestSequence && epoch === accountContext.epoch) showFailure(cause, t('strategies.allocation.saveFailed'));
  } finally {
    if (sequence === requestSequence && epoch === accountContext.epoch) busy.value = false;
  }
}

async function remove(): Promise<void> {
  if (!canEdit.value || !allocation.value?.draft || !allocation.value.context.scope_id) return;
  const sequence = ++requestSequence;
  const strategy = props.strategyName;
  const epoch = accountContext.epoch;
  const revision = allocation.value.draft.revision;
  const scopeId = allocation.value.context.scope_id;
  const symbol = allocation.value.symbol;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await deleteStrategyAllocation(strategy, revision, scopeId, symbol);
    if (sequence !== requestSequence || epoch !== accountContext.epoch) return;
    applyEnvelope(result);
    notice.value = t('strategies.allocation.removed');
  } catch (cause) {
    if (sequence === requestSequence && epoch === accountContext.epoch) showFailure(cause, t('strategies.allocation.removeFailed'));
  } finally {
    if (sequence === requestSequence && epoch === accountContext.epoch) busy.value = false;
  }
}

async function activate(): Promise<void> {
  if (!canActivate.value || !allocation.value?.draft || !allocation.value.context.scope_id) return;
  const sequence = ++requestSequence;
  const strategy = props.strategyName;
  const revision = allocation.value.draft.revision;
  const scopeId = allocation.value.context.scope_id;
  const symbol = allocation.value.symbol;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await activateStrategyAllocation(strategy, {
      expected_revision: revision,
      expected_scope_id: scopeId,
      expected_symbol: symbol,
    });
    if (sequence !== requestSequence) return;
    applyEnvelope(result);
    notice.value = t('strategies.allocation.activated');
  } catch (cause) {
    if (sequence === requestSequence) showFailure(cause, t('strategies.allocation.activateFailed'));
  } finally {
    if (sequence === requestSequence) busy.value = false;
  }
}

async function release(): Promise<void> {
  if (!canRelease.value || !reservation.value || !allocation.value?.context.scope_id) return;
  const sequence = ++requestSequence;
  const strategy = props.strategyName;
  const revision = reservation.value.revision;
  const scopeId = allocation.value.context.scope_id;
  const symbol = allocation.value.symbol;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await releaseStrategyAllocation(strategy, {
      expected_revision: revision,
      expected_scope_id: scopeId,
      expected_symbol: symbol,
    });
    if (sequence !== requestSequence) return;
    applyEnvelope(result);
    notice.value = t('strategies.allocation.released');
  } catch (cause) {
    if (sequence === requestSequence) showFailure(cause, t('strategies.allocation.releaseFailed'));
  } finally {
    if (sequence === requestSequence) busy.value = false;
  }
}

watch(() => [props.strategyName, props.status, props.strategySymbol, accountContext.epoch, accountContext.context], () => {
  void load();
}, { immediate: true, flush: 'sync' });
onMounted(() => { void accountContext.ensure(); });
onBeforeUnmount(() => { requestSequence += 1; });
</script>

<template>
  <SectionCard :title="t('strategies.allocation.title')" :description="strategyName" class="allocation-panel">
    <p class="allocation-panel__warning">{{ t('strategies.allocation.draftOnly') }}</p>
    <p class="allocation-panel__warning">{{ t('strategies.allocation.pending') }}</p>
    <p v-if="allocation?.context.execution_mode === 'live'" class="allocation-panel__warning">
      {{ t('strategies.allocation.liveStillClosed') }}
    </p>
    <p v-if="reservation" class="allocation-panel__warning">{{ t('strategies.allocation.reservedHint') }}</p>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-if="error" role="alert" class="allocation-panel__error">{{ error }}</p>
    <p v-if="needsRefresh" role="status">{{ t('strategies.allocation.conflict') }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="allocation">
      <dl class="allocation-panel__context">
        <dt>{{ t('strategies.allocation.environment') }}</dt>
        <dd>{{ t(`strategies.allocation.${allocation.context.environment}`) }}</dd>
        <dt>{{ t('strategies.allocation.executionMode') }}</dt>
        <dd>{{ executionLabel }}</dd>
        <dt>{{ t('strategies.allocation.account') }}</dt>
        <dd>{{ allocation.context.account_fingerprint ?? '—' }} · {{ t('strategies.allocation.unverified') }}</dd>
        <dt>{{ t('strategies.allocation.market') }}</dt>
        <dd>{{ allocation.context.market_type }} · {{ allocation.symbol }} · {{ allocation.currency }}</dd>
        <dt>{{ t('common.status') }}</dt>
        <dd>{{ reservation ? t('strategies.allocation.reserved') : allocation.draft ? t('strategies.allocation.draft') : t('strategies.allocation.noDraft') }}</dd>
        <dt>{{ t('strategies.allocation.revision') }}</dt>
        <dd>{{ allocation.draft?.revision ?? '—' }}</dd>
        <dt>{{ t('strategies.allocation.realizedPnl') }}</dt>
        <dd data-testid="allocation-realized">{{ allocation.realized_pnl ?? '—' }}</dd>
        <template v-if="reservation">
          <dt>{{ t('strategies.allocation.availableCash') }}</dt>
          <dd>{{ reservation.cash }}</dd>
          <dt>{{ t('strategies.allocation.holdings') }}</dt>
          <dd>{{ reservation.holdings }}</dd>
        </template>
        <template v-if="allocation.funded_book">
          <dt>{{ t('strategies.allocation.fundedCash') }}</dt>
          <dd data-testid="allocation-funded-cash">{{ allocation.funded_book.cash }}</dd>
          <dt>{{ t('strategies.allocation.fundedPosition') }}</dt>
          <dd data-testid="allocation-funded-position">{{ allocation.funded_book.base_position }}</dd>
          <dt>{{ t('strategies.allocation.fundedPnl') }}</dt>
          <dd data-testid="allocation-funded-pnl">{{ allocation.funded_book.realized_pnl }}</dd>
          <dt>{{ t('strategies.allocation.fundedReserved') }}</dt>
          <dd>{{ allocation.funded_book.reserved_quote }}</dd>
        </template>
      </dl>
      <p v-if="allocation.funded_book" role="status">{{ t('strategies.allocation.fundedClosed') }}</p>
      <p v-if="allocation.other_scope_reservation" role="status">{{ t('strategies.allocation.otherScope') }}</p>
      <p v-else-if="!allocation.context.available" role="status">{{ unavailableMessage }}</p>
      <p v-else-if="!stopped || !allocation.can_edit" role="status">{{ t('strategies.allocation.stoppedOnly') }}</p>
      <form data-testid="allocation-form" @submit.prevent="save">
        <label class="allocation-panel__budget-label">
          {{ t('strategies.allocation.budget') }} ({{ allocation.currency }})
          <input
            data-testid="allocation-budget"
            type="text"
            inputmode="decimal"
            :value="budget"
            :disabled="!canEdit"
            @input="budget = ($event.target as HTMLInputElement).value"
          >
        </label>
        <div class="allocation-panel__actions">
          <button type="submit" data-testid="allocation-save" :disabled="!canEdit">{{ t('strategies.allocation.save') }}</button>
          <button type="button" data-testid="allocation-delete" :disabled="!canEdit || !allocation.draft" @click="remove">{{ t('strategies.allocation.remove') }}</button>
          <button v-if="localPaper && allocation.draft && !reservation" type="button" data-testid="allocation-activate" :disabled="!canActivate" @click="activate">{{ t('strategies.allocation.activate') }}</button>
          <button v-if="localPaper && reservation" type="button" data-testid="allocation-release" :disabled="!canRelease" @click="release">{{ t('strategies.allocation.release') }}</button>
        </div>
      </form>
      <StrategyAllocationReadiness
        :allocation="allocation"
        :status="status"
        :suspended="loading || busy || needsRefresh"
        @conflict="needsRefresh = true"
      />
    </template>
    <button type="button" data-testid="allocation-refresh" :disabled="loading || busy" @click="load">{{ t('common.refresh') }}</button>
  </SectionCard>
</template>

<style scoped>
.allocation-panel { margin-top: var(--ui-space-16); }
.allocation-panel__warning { color: var(--ui-color-text-secondary); }
.allocation-panel__error { color: var(--ui-color-danger); }
.allocation-panel__context { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: var(--ui-space-8); }
.allocation-panel__context dd { margin: 0; overflow-wrap: anywhere; }
.allocation-panel__context dt { color: var(--ui-color-text-secondary); }
.allocation-panel__budget-label { display: grid; gap: var(--ui-space-8); }
.allocation-panel__budget-label input { min-width: 0; padding: var(--ui-space-8); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-8); }
.allocation-panel__actions { display: flex; flex-wrap: wrap; gap: var(--ui-space-8); margin: var(--ui-space-12) 0; }
button { cursor: pointer; padding: var(--ui-space-8) var(--ui-space-12); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-8); background: var(--ui-color-surface); color: var(--ui-color-text); }
button:disabled { cursor: default; opacity: 0.5; }
</style>

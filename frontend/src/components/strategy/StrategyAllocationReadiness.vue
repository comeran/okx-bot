<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getStrategyAllocationReadiness } from '@/services/strategyAllocations';
import { matchesExecutionContext, useAccountContextStore } from '@/stores/accountContext';
import type { StrategyAllocationEnvelope, StrategyAllocationReadiness } from '@/types/strategyAllocation';

const props = defineProps<{
  allocation: StrategyAllocationEnvelope | null;
  status?: string;
  suspended?: boolean;
}>();
const emit = defineEmits<{ conflict: [] }>();
const { t } = useI18n();
const accountContext = useAccountContextStore();
const readiness = ref<StrategyAllocationReadiness | null>(null);
const loading = ref(false);
const error = ref('');
let sequence = 0;

const lifecycle = computed(() => props.status ?? props.allocation?.lifecycle_status);
const request = computed(() => {
  const envelope = props.allocation;
  const draft = envelope?.draft;
  const context = accountContext.context;
  if (props.suspended || lifecycle.value !== 'stopped' || !draft || !envelope.context.available
    || !context?.available || !context.account_scope_id
    || !matchesExecutionContext(context, envelope.execution_context)
    || context.environment !== envelope.context.environment
    || context.market_type !== envelope.context.market_type
    || draft.symbol !== envelope.symbol || draft.scope_id !== envelope.context.scope_id) return null;
  return {
    strategy: envelope.strategy,
    expected_account_scope_id: context.account_scope_id,
    expected_scope_id: draft.scope_id,
    expected_revision: draft.revision,
    expected_symbol: draft.symbol,
  };
});

async function load(): Promise<void> {
  const currentSequence = ++sequence;
  const epoch = accountContext.epoch;
  const captured = request.value;
  readiness.value = null;
  error.value = '';
  loading.value = false;
  if (!captured) return;
  const current = () => currentSequence === sequence && epoch === accountContext.epoch;
  loading.value = true;
  try {
    const { strategy, ...preconditions } = captured;
    const result = await getStrategyAllocationReadiness(strategy, preconditions);
    if (!current()) return;
    if (!matchesExecutionContext(accountContext.context, result.execution_context)
      || result.strategy !== strategy || result.symbol !== captured.expected_symbol
      || result.allocation_scope_id !== captured.expected_scope_id
      || result.draft_revision !== captured.expected_revision
      || result.currency !== props.allocation?.currency
      || result.can_activate !== false || result.execution_ready !== false) {
      throw new Error('Readiness binding changed');
    }
    readiness.value = result;
  } catch (cause) {
    if (!current()) return;
    const status = (cause as { response?: { status?: number } })?.response?.status;
    error.value = t('strategies.allocation.readiness.loadFailed');
    if (status === 409) emit('conflict');
  } finally {
    if (current()) loading.value = false;
  }
}

watch(() => [accountContext.epoch, request.value], () => { void load(); }, { immediate: true, flush: 'sync' });
onBeforeUnmount(() => { sequence += 1; });
</script>

<template>
  <section class="allocation-readiness" data-testid="allocation-readiness" :aria-label="t('strategies.allocation.readiness.title')">
    <h3>{{ t('strategies.allocation.readiness.title') }}</h3>
    <p>{{ t('strategies.allocation.readiness.savedOnly') }}</p>
    <p class="allocation-readiness__note">{{ t('strategies.allocation.readiness.closed') }}</p>
    <p v-if="!allocation?.draft" role="status">{{ t('strategies.allocation.readiness.saveFirst') }}</p>
    <p v-else-if="lifecycle !== 'stopped'" role="status">{{ t('strategies.allocation.stoppedOnly') }}</p>
    <p v-else-if="!request" role="status">{{ t('strategies.allocation.readiness.waiting') }}</p>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-if="error" role="alert" class="allocation-readiness__error">{{ error }}</p>
    <template v-if="readiness">
      <p>{{ t('strategies.allocation.readiness.checkedAt') }}: {{ new Date(readiness.evaluated_at).toLocaleString() }}</p>
      <dl class="allocation-readiness__values">
        <dt>{{ t('strategies.allocation.readiness.savedBudget') }}</dt><dd>{{ readiness.budget }} {{ readiness.currency }}</dd>
        <dt>{{ t('strategies.allocation.readiness.proposedTotal') }}</dt><dd>{{ readiness.capacity.proposed_total }} {{ readiness.currency }}</dd>
        <dt>{{ t('strategies.allocation.readiness.observedAvailable') }}</dt><dd>{{ readiness.capacity.observed_quote_available ?? '—' }} {{ readiness.currency }}</dd>
        <dt>{{ t('strategies.allocation.readiness.comparison') }}</dt><dd>{{ t(`strategies.allocation.readiness.${readiness.capacity.comparison}`) }}</dd>
      </dl>
      <p>{{ t('strategies.allocation.readiness.indicative') }}</p>
      <p v-if="!readiness.ledger">{{ t('strategies.allocation.readiness.uninitialized') }}</p>
      <template v-else>
        <h4>{{ t('strategies.allocation.readiness.prepared') }}</h4>
        <dl class="allocation-readiness__values">
          <dt>{{ t('strategies.allocation.readiness.quoteBalance') }}</dt><dd>{{ readiness.ledger.quote_balance }} {{ readiness.currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.quoteReserved') }}</dt><dd>{{ readiness.ledger.quote_reserved }} {{ readiness.currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.quoteAvailable') }}</dt><dd>{{ readiness.ledger.quote_available }} {{ readiness.currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.baseBalance') }}</dt><dd>{{ readiness.ledger.base_balance }} {{ readiness.ledger.base_currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.baseReserved') }}</dt><dd>{{ readiness.ledger.base_reserved }} {{ readiness.ledger.base_currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.baseAvailable') }}</dt><dd>{{ readiness.ledger.base_available }} {{ readiness.ledger.base_currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.costBasis') }}</dt><dd>{{ readiness.ledger.cost_basis_quote }} {{ readiness.currency }}</dd>
          <dt>{{ t('strategies.allocation.readiness.realizedPnl') }}</dt><dd>{{ readiness.ledger.realized_pnl_quote }} {{ readiness.currency }}</dd>
          <template v-for="(amount, currency) in readiness.ledger.fees" :key="currency">
            <dt>{{ t('strategies.allocation.readiness.fees') }} · {{ currency }}</dt><dd>{{ amount }} {{ currency }}</dd>
          </template>
        </dl>
      </template>
      <ul class="allocation-readiness__checks">
        <li v-for="check in readiness.checks" :key="check.code">
          <span>{{ t(`strategies.allocation.readiness.checks.${check.code}`) }}</span>
          <strong :class="`allocation-readiness__${check.status}`">{{ t(`strategies.allocation.readiness.${check.status}`) }}</strong>
        </li>
      </ul>
    </template>
    <button type="button" data-testid="readiness-refresh" :disabled="!request || loading" @click="load">{{ t('common.refresh') }}</button>
  </section>
</template>

<style scoped>
.allocation-readiness { margin-top: var(--ui-space-16); border-top: 1px solid var(--ui-color-border); padding-top: var(--ui-space-12); }
.allocation-readiness__values { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: var(--ui-space-8); }
.allocation-readiness__values dd { margin: 0; overflow-wrap: anywhere; }
.allocation-readiness__values dt, .allocation-readiness__note { color: var(--ui-color-text-secondary); }
.allocation-readiness__checks { padding: 0; list-style: none; }
.allocation-readiness__checks li { display: flex; justify-content: space-between; gap: var(--ui-space-12); padding: var(--ui-space-4) 0; }
.allocation-readiness__error, .allocation-readiness__blocked { color: var(--ui-color-danger); }
.allocation-readiness__unknown { color: var(--ui-color-text-secondary); }
button { padding: var(--ui-space-8) var(--ui-space-12); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-8); background: var(--ui-color-surface); color: var(--ui-color-text); cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
</style>

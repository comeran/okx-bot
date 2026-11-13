<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import StatusBadge from '@/components/ui/StatusBadge.vue';
import type { ExchangeAuthentication, ExecutionContext } from '@/types/executionContext';

const props = defineProps<{
  context: Pick<ExecutionContext, 'available'> | null;
  authentication: ExchangeAuthentication | null;
  checking?: boolean;
}>();
const { t } = useI18n();
const status = computed(() => {
  if (props.checking) return 'checking';
  if (!props.context?.available) return 'unconfigured';
  return props.authentication?.status ?? 'unchecked';
});
const tone = computed(() => {
  if (status.value === 'authenticated') return 'success';
  if (status.value === 'failed') return 'danger';
  if (status.value === 'unavailable') return 'warning';
  return 'neutral';
});
</script>

<template>
  <StatusBadge :status="t(`exchangeAuth.status.${status}`)" :tone="tone" :title="t('exchangeAuth.observation')" />
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';

import type { ExecutionContext } from '@/types/executionContext';

defineProps<{
  context: ExecutionContext | null;
  loading?: boolean;
  error?: string | null;
  missingSnapshot?: boolean;
}>();
const { t } = useI18n();
</script>
<template>
  <div class="account-context" role="status">
    <p v-if="loading">{{ t('accountContext.loading') }}</p>
    <p v-else-if="error">{{ t('accountContext.loadError') }}</p>
    <template v-else-if="context">
      <p>
        {{ t(`accountContext.${context.environment}`) }}
        <span v-if="context.available"> · {{ t('accountContext.unverified') }}</span>
      </p>
      <p v-if="!context.available">
        {{ t(context.reason === 'unsupported_market' ? 'accountContext.unsupported' : 'accountContext.unavailable') }}
      </p>
      <p v-else-if="missingSnapshot">{{ t('accountContext.missingSnapshot') }}</p>
    </template>
  </div>
</template>
<style scoped>
.account-context {
  color: var(--ui-color-text-secondary);
  overflow-wrap: anywhere;
}
</style>

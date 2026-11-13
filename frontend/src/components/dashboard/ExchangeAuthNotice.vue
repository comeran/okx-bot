<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { RouterLink } from 'vue-router';
import { ArrowRight, WarningFilled } from '@element-plus/icons-vue';

import { exchangeAuthenticationProviderCodes, exchangeAuthenticationReasons, type ExchangeAuthentication } from '@/types/executionContext';

const props = defineProps<{ authentication: ExchangeAuthentication | null }>();
const { t } = useI18n();
const visible = computed(() => ['failed', 'unavailable'].includes(props.authentication?.status ?? ''));
const reason = computed(() => {
  const value = props.authentication?.reason;
  return value && exchangeAuthenticationReasons.includes(value) ? value
    : props.authentication?.status === 'failed' ? 'authentication_failed' : 'provider_unavailable';
});
const providerCode = computed(() => {
  const value = props.authentication?.provider_code;
  return typeof value === 'string' && exchangeAuthenticationProviderCodes.includes(value) ? value : null;
});
</script>

<template>
  <div v-if="visible" class="exchange-auth-notice" :class="`exchange-auth-notice--${authentication?.status}`" role="alert">
    <WarningFilled class="exchange-auth-notice__icon" aria-hidden="true" />
    <div class="exchange-auth-notice__copy">
      <div class="exchange-auth-notice__heading">
        <strong>{{ t(`exchangeAuth.status.${authentication?.status}`) }}</strong>
        <span class="exchange-auth-notice__environment">{{ t(authentication?.environment === 'live' ? 'accountContext.live' : 'accountContext.sandbox') }}</span>
        <span v-if="providerCode" class="exchange-auth-notice__code">{{ t('exchangeAuth.providerCode', { code: providerCode }) }}</span>
      </div>
      <p>{{ t(`exchangeAuth.reasons.${reason}`) }}</p>
    </div>
    <RouterLink to="/settings" class="exchange-auth-notice__action">
      {{ t('exchangeAuth.openSettings') }}
      <ArrowRight aria-hidden="true" />
    </RouterLink>
  </div>
</template>

<style scoped>
.exchange-auth-notice {
  display: flex;
  align-items: center;
  gap: var(--ui-space-12);
  margin-bottom: var(--ui-space-16);
  padding: var(--ui-space-12) var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-left: 3px solid var(--ui-color-warning);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
  color: var(--ui-color-text);
  overflow-wrap: anywhere;
}

.exchange-auth-notice--failed {
  border-left-color: var(--ui-color-danger);
}

.exchange-auth-notice__icon {
  align-self: flex-start;
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
  margin-top: 2px;
  color: var(--ui-color-warning);
}

.exchange-auth-notice--failed .exchange-auth-notice__icon {
  color: var(--ui-color-danger);
}

.exchange-auth-notice__copy {
  flex: 1;
  min-width: 0;
}

.exchange-auth-notice__heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--ui-space-4) var(--ui-space-10);
  font-size: var(--ui-font-size-12);
}

.exchange-auth-notice__heading strong {
  font-size: var(--ui-font-size-13);
  font-weight: 600;
}

.exchange-auth-notice__environment {
  color: var(--ui-color-text-secondary);
}

.exchange-auth-notice__code {
  padding: 1px var(--ui-space-6);
  border-radius: var(--ui-radius-4);
  background: var(--ui-color-info-soft);
  font-variant-numeric: tabular-nums;
}

.exchange-auth-notice p {
  margin: var(--ui-space-4) 0 0;
  font-size: var(--ui-font-size-12);
  line-height: 1.6;
  color: var(--ui-color-text-secondary);
}

.exchange-auth-notice__action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  gap: var(--ui-space-8);
  min-height: 36px;
  padding: var(--ui-space-6) var(--ui-space-10);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: 6px;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-12);
  font-weight: 600;
  text-decoration: none;
  transition: color 160ms ease, border-color 160ms ease, background-color 160ms ease;
}

.exchange-auth-notice__action:hover {
  border-color: var(--ui-color-primary);
  color: var(--ui-color-primary);
  background: var(--ui-color-primary-soft);
}

.exchange-auth-notice__action svg {
  width: 12px;
  height: 12px;
}

@media (max-width: 640px) {
  .exchange-auth-notice {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr);
    gap: var(--ui-space-8) var(--ui-space-10);
    padding: var(--ui-space-12);
  }

  .exchange-auth-notice__action {
    grid-column: 2;
    justify-self: start;
    min-height: 40px;
  }
}
</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';

import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import DataState from '@/components/ui/DataState.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SecretField from '@/components/settings/SecretField.vue';
import { useDirtyGuard } from '@/composables/useDirtyGuard';
import { getSettings, updateSettings } from '@/services/settings';
import type { AppSettingsUpdate, AppSettingsView } from '@/types/settings';

const { t, locale } = useI18n();

const loading = ref(false);
const saving = ref(false);
const hasLoadedSettings = ref(false);
const loadError = ref<string | null>(null);
const settingsView = ref<AppSettingsView | null>(null);

const form = reactive<AppSettingsUpdate>({
  mode: 'backtest',
  exchange: {
    api_key: '',
    secret: '',
    passphrase: '',
    market_type: 'spot',
    demo: true,
  },
  backtest: {
    initial_capital: 100000,
    fee_rate: 0.0005,
    slippage: 0.001,
    data_cache_dir: './data',
  },
  risk: {
    max_daily_loss_pct: 0.05,
    max_drawdown_pct: 0.15,
    max_total_position_pct: 0.8,
    allow_live_open_orders: false,
    live_max_order_notional: 0,
  },
  notify: {
    telegram_bot_token: '',
    telegram_chat_id: '',
  },
  web: {
    host: '0.0.0.0',
    port: 8080,
  },
});

function buildUpdatePayload(): AppSettingsUpdate {
  return {
    mode: form.mode,
    exchange: {
      api_key: form.exchange.api_key,
      secret: form.exchange.secret,
      passphrase: form.exchange.passphrase,
      market_type: form.exchange.market_type,
      demo: form.exchange.demo,
    },
    backtest: {
      initial_capital: form.backtest.initial_capital,
      fee_rate: form.backtest.fee_rate,
      slippage: form.backtest.slippage,
      data_cache_dir: form.backtest.data_cache_dir,
    },
    risk: {
      max_daily_loss_pct: form.risk.max_daily_loss_pct,
      max_drawdown_pct: form.risk.max_drawdown_pct,
      max_total_position_pct: form.risk.max_total_position_pct,
      allow_live_open_orders: form.risk.allow_live_open_orders,
      live_max_order_notional: form.risk.live_max_order_notional,
    },
    notify: {
      telegram_bot_token: form.notify.telegram_bot_token,
      telegram_chat_id: form.notify.telegram_chat_id,
    },
    web: {
      host: form.web.host,
      port: form.web.port,
    },
  };
}

function cloneSettings(settings: AppSettingsView): AppSettingsUpdate {
  return {
    mode: settings.mode,
    exchange: {
      api_key: '',
      secret: '',
      passphrase: '',
      market_type: settings.exchange.market_type,
      demo: settings.exchange.demo,
    },
    backtest: {
      initial_capital: settings.backtest.initial_capital,
      fee_rate: settings.backtest.fee_rate,
      slippage: settings.backtest.slippage,
      data_cache_dir: settings.backtest.data_cache_dir,
    },
    risk: {
      max_daily_loss_pct: settings.risk.max_daily_loss_pct,
      max_drawdown_pct: settings.risk.max_drawdown_pct,
      max_total_position_pct: settings.risk.max_total_position_pct,
      allow_live_open_orders: settings.risk.allow_live_open_orders,
      live_max_order_notional: settings.risk.live_max_order_notional,
    },
    notify: {
      telegram_bot_token: '',
      telegram_chat_id: settings.notify.telegram_chat_id,
    },
    web: {
      host: settings.web.host,
      port: settings.web.port,
    },
  };
}

const baselineSnapshot = ref(JSON.stringify(buildUpdatePayload()));
let settingsRequestToken = 0;

const isDirty = computed(() => JSON.stringify(buildUpdatePayload()) !== baselineSnapshot.value);
const initialLoading = computed(() => loading.value && !hasLoadedSettings.value);
const staleData = computed(() => hasLoadedSettings.value && Boolean(loadError.value) && !loading.value);
const actionDisabled = computed(() => loading.value || saving.value);
const allowedModes = ['backtest', 'paper', 'live'] as const;
const allowedMarketTypes = ['spot', 'swap', 'future', 'option'] as const;

type SettingsSectionKey = 'runtime' | 'exchange' | 'backtest' | 'risk' | 'notifications' | 'web';

type SettingsValidationState = Record<SettingsSectionKey, string[]>;

function createValidationState(): SettingsValidationState {
  return {
    runtime: [],
    exchange: [],
    backtest: [],
    risk: [],
    notifications: [],
    web: [],
  };
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function addValidationError(
  state: SettingsValidationState,
  section: SettingsSectionKey,
  message: string,
): void {
  state[section].push(message);
}

function validateSettings(settings: AppSettingsUpdate): SettingsValidationState {
  const state = createValidationState();

  if (!allowedModes.includes(settings.mode as typeof allowedModes[number])) {
    addValidationError(state, 'runtime', t('settings.validation.requiredField', { field: t('settings.mode') }));
  }

  if (!allowedMarketTypes.includes(settings.exchange.market_type as typeof allowedMarketTypes[number])) {
    addValidationError(
      state,
      'exchange',
      t('settings.validation.requiredField', { field: t('settings.marketType') }),
    );
  }

  if (!isFiniteNumber(settings.backtest.initial_capital) || settings.backtest.initial_capital < 0) {
    addValidationError(
      state,
      'backtest',
      t('settings.validation.nonNegative', { field: t('settings.initialCapital') }),
    );
  }

  if (!isFiniteNumber(settings.backtest.fee_rate) || settings.backtest.fee_rate < 0) {
    addValidationError(
      state,
      'backtest',
      t('settings.validation.nonNegative', { field: t('settings.feeRate') }),
    );
  }

  if (!isFiniteNumber(settings.backtest.slippage) || settings.backtest.slippage < 0) {
    addValidationError(
      state,
      'backtest',
      t('settings.validation.nonNegative', { field: t('settings.slippage') }),
    );
  }

  if (!isFiniteNumber(settings.risk.max_daily_loss_pct) || settings.risk.max_daily_loss_pct < 0 || settings.risk.max_daily_loss_pct > 1) {
    addValidationError(
      state,
      'risk',
      t('settings.validation.percentageRange', { field: t('settings.maxDailyLossPct') }),
    );
  }

  if (!isFiniteNumber(settings.risk.max_drawdown_pct) || settings.risk.max_drawdown_pct < 0 || settings.risk.max_drawdown_pct > 1) {
    addValidationError(
      state,
      'risk',
      t('settings.validation.percentageRange', { field: t('settings.maxDrawdownPct') }),
    );
  }

  if (!isFiniteNumber(settings.risk.max_total_position_pct) || settings.risk.max_total_position_pct < 0 || settings.risk.max_total_position_pct > 1) {
    addValidationError(
      state,
      'risk',
      t('settings.validation.percentageRange', { field: t('settings.maxTotalPositionPct') }),
    );
  }

  if (!isFiniteNumber(settings.risk.live_max_order_notional) || settings.risk.live_max_order_notional < 0) {
    addValidationError(
      state,
      'risk',
      t('settings.validation.nonNegative', { field: t('settings.liveMaxOrderNotional') }),
    );
  }

  if (!Number.isInteger(settings.web.port) || settings.web.port < 1 || settings.web.port > 65535) {
    addValidationError(
      state,
      'web',
      t('settings.validation.portRange', { field: t('settings.port') }),
    );
  }

  return state;
}

const validationState = computed(() => {
  void locale.value;
  return validateSettings(buildUpdatePayload());
});

const hasValidationErrors = computed(() => Object.values(validationState.value).some((errors) => errors.length > 0));
const mobileActionsStyle = {
  position: 'fixed',
  left: 'var(--ui-space-16)',
  right: 'var(--ui-space-16)',
  bottom: '0',
  paddingBottom: 'calc(var(--ui-space-12) + env(safe-area-inset-bottom))',
} as const;

function applySettings(settings: AppSettingsView): void {
  settingsView.value = settings;
  form.mode = settings.mode;
  form.exchange.api_key = '';
  form.exchange.secret = '';
  form.exchange.passphrase = '';
  form.exchange.market_type = settings.exchange.market_type;
  form.exchange.demo = settings.exchange.demo;
  form.backtest = { ...settings.backtest };
  form.risk = { ...settings.risk };
  form.notify.telegram_bot_token = '';
  form.notify.telegram_chat_id = settings.notify.telegram_chat_id;
  form.web = { ...settings.web };
  baselineSnapshot.value = JSON.stringify(cloneSettings(settings));
  hasLoadedSettings.value = true;
}

async function confirmDiscard(): Promise<boolean> {
  if (!isDirty.value) return true;

  try {
    await ElMessageBox.confirm(
      t('settings.confirm.discardChanges'),
      t('settings.confirm.discardTitle'),
      {
        type: 'warning',
        confirmButtonText: t('common.discard'),
        cancelButtonText: t('common.cancel'),
      },
    );
    return true;
  } catch {
    return false;
  }
}

const { confirmIfDirty } = useDirtyGuard(() => isDirty.value, confirmDiscard);

async function loadSettings(): Promise<void> {
  if (!(await confirmIfDirty())) return;

  const requestToken = ++settingsRequestToken;
  loading.value = true;
  loadError.value = null;
  try {
    const nextSettings = await getSettings();
    if (requestToken !== settingsRequestToken) {
      return;
    }
    applySettings(nextSettings);
  } catch {
    if (requestToken === settingsRequestToken) {
      loadError.value = t('settings.loadError');
      ElMessage.error(t('settings.loadError'));
    }
  } finally {
    if (requestToken === settingsRequestToken) {
      loading.value = false;
    }
  }
}

async function saveSettings(): Promise<void> {
  if (saving.value || loading.value || hasValidationErrors.value) return;

  saving.value = true;
  try {
    applySettings(await updateSettings(buildUpdatePayload()));
    loadError.value = null;
    ElMessage.success(t('settings.saveSuccess'));
  } catch {
    ElMessage.error(t('settings.saveError'));
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void loadSettings();
});

onBeforeRouteLeave(async () => confirmIfDirty());
</script>

<template>
  <section class="settings-page">
    <AppPageHeader
      class="settings-page__header"
      :title="t('settings.title')"
      :description="t('settings.description')"
    >
      <template #actions>
        <div class="settings-page__header-actions">
          <el-button
            :loading="loading"
            :disabled="actionDisabled"
            :aria-label="t('common.reload')"
            @click="loadSettings"
          >
            {{ t('common.reload') }}
          </el-button>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="actionDisabled || hasValidationErrors"
            :aria-label="t('settings.saveSettings')"
            @click="saveSettings"
          >
            {{ t('settings.saveSettings') }}
          </el-button>
        </div>
      </template>
    </AppPageHeader>

    <DataState
      :loading="initialLoading"
      :error="loadError"
      :stale="staleData"
      @retry="loadSettings"
    >
      <form class="settings-page__content" :aria-label="t('settings.title')" @submit.prevent="saveSettings">
        <el-form :model="form" label-position="top" class="settings-form" :disabled="actionDisabled">
          <SettingsSection :title="t('settings.runtime')" :description="t('settings.runtimeDescription')">
            <template v-if="validationState.runtime.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.runtime[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.runtime.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.runtime" :key="`runtime-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>
              <el-row :gutter="16">
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.mode')">
                    <el-select v-model="form.mode" class="settings-form__control">
                      <el-option :label="t('settings.modes.backtest')" value="backtest" />
                      <el-option :label="t('settings.modes.paper')" value="paper" />
                      <el-option :label="t('settings.modes.live')" value="live" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
            </template>
          </SettingsSection>

          <SettingsSection :title="t('settings.okxExchange')" :description="t('settings.exchangeDescription')">
            <template v-if="validationState.exchange.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.exchange[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.exchange.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.exchange" :key="`exchange-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>

              <el-row :gutter="16">
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.marketType')">
                    <el-select v-model="form.exchange.market_type" class="settings-form__control">
                      <el-option :label="t('settings.marketTypes.spot')" value="spot" />
                      <el-option :label="t('settings.marketTypes.swap')" value="swap" />
                      <el-option :label="t('settings.marketTypes.future')" value="future" />
                      <el-option :label="t('settings.marketTypes.option')" value="option" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.okxDemo')">
                    <el-switch v-model="form.exchange.demo" />
                  </el-form-item>
                </el-col>
              </el-row>

              <el-row :gutter="16">
                <el-col :xs="24" :md="8">
                  <SecretField
                    v-model="form.exchange.api_key"
                    :configured="Boolean(settingsView?.exchange.api_key_set)"
                    :label="t('settings.apiKey')"
                    :hint="t('settings.keepExisting')"
                    :disabled="actionDisabled"
                  />
                </el-col>
                <el-col :xs="24" :md="8">
                  <SecretField
                    v-model="form.exchange.secret"
                    :configured="Boolean(settingsView?.exchange.secret_set)"
                    :label="t('settings.secret')"
                    :hint="t('settings.keepExisting')"
                    :disabled="actionDisabled"
                  />
                </el-col>
                <el-col :xs="24" :md="8">
                  <SecretField
                    v-model="form.exchange.passphrase"
                    :configured="Boolean(settingsView?.exchange.passphrase_set)"
                    :label="t('settings.passphrase')"
                    :hint="t('settings.keepExisting')"
                    :disabled="actionDisabled"
                  />
                </el-col>
              </el-row>
            </template>
          </SettingsSection>

          <SettingsSection :title="t('settings.backtestDefaults')" :description="t('settings.backtestDescription')">
            <template v-if="validationState.backtest.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.backtest[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.backtest.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.backtest" :key="`backtest-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>
              <el-row :gutter="16">
                <el-col :xs="24" :md="6">
                  <el-form-item :label="t('settings.initialCapital')">
                    <el-input-number v-model="form.backtest.initial_capital" :min="0" :step="1000" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="6">
                  <el-form-item :label="t('settings.feeRate')">
                    <el-input-number v-model="form.backtest.fee_rate" :min="0" :step="0.0001" :precision="6" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="6">
                  <el-form-item :label="t('settings.slippage')">
                    <el-input-number v-model="form.backtest.slippage" :min="0" :step="0.0001" :precision="6" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="6">
                  <el-form-item :label="t('settings.dataCacheDir')">
                    <el-input v-model="form.backtest.data_cache_dir" />
                  </el-form-item>
                </el-col>
              </el-row>
            </template>
          </SettingsSection>

          <SettingsSection :title="t('settings.riskLimits')" :description="t('settings.riskDescription')">
            <template v-if="validationState.risk.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.risk[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.risk.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.risk" :key="`risk-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>
              <el-row :gutter="16">
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.maxDailyLossPct')">
                    <el-input-number v-model="form.risk.max_daily_loss_pct" :min="0" :max="1" :step="0.01" :precision="4" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.maxDrawdownPct')">
                    <el-input-number v-model="form.risk.max_drawdown_pct" :min="0" :max="1" :step="0.01" :precision="4" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.maxTotalPositionPct')">
                    <el-input-number v-model="form.risk.max_total_position_pct" :min="0" :max="1" :step="0.01" :precision="4" class="settings-form__control" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.allowLiveOpenOrders')">
                    <el-switch v-model="form.risk.allow_live_open_orders" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="8">
                  <el-form-item :label="t('settings.liveMaxOrderNotional')">
                    <el-input-number v-model="form.risk.live_max_order_notional" :min="0" :step="100" class="settings-form__control" />
                  </el-form-item>
                </el-col>
              </el-row>
            </template>
          </SettingsSection>

          <SettingsSection :title="t('settings.notifications')" :description="t('settings.notificationsDescription')">
            <template v-if="validationState.notifications.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.notifications[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.notifications.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.notifications" :key="`notifications-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>
              <el-row :gutter="16">
                <el-col :xs="24" :md="12">
                  <SecretField
                    v-model="form.notify.telegram_bot_token"
                    :configured="Boolean(settingsView?.notify.telegram_bot_token_set)"
                    :label="t('settings.telegramBotToken')"
                    :hint="t('settings.keepExisting')"
                    :disabled="actionDisabled"
                  />
                </el-col>
                <el-col :xs="24" :md="12">
                  <el-form-item :label="t('settings.telegramChatId')">
                    <el-input v-model="form.notify.telegram_chat_id" />
                  </el-form-item>
                </el-col>
              </el-row>
            </template>
          </SettingsSection>

          <SettingsSection :title="t('settings.webServer')" :description="t('settings.webDescription')">
            <template v-if="validationState.web.length" #status>
              <span class="settings-page__validation-status">
                {{ validationState.web[0] }}
              </span>
            </template>
            <template #content>
              <div v-if="validationState.web.length" class="settings-page__validation" role="alert" aria-live="polite">
                <p v-for="(error, index) in validationState.web" :key="`web-${index}-${error}`" class="settings-page__validation-item">
                  {{ error }}
                </p>
              </div>
              <el-row :gutter="16">
                <el-col :xs="24" :md="12">
                  <el-form-item :label="t('settings.host')">
                    <el-input v-model="form.web.host" />
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :md="12">
                  <el-form-item :label="t('settings.port')">
                    <el-input-number v-model="form.web.port" :min="1" :max="65535" class="settings-form__control" />
                  </el-form-item>
                </el-col>
              </el-row>
            </template>
          </SettingsSection>
        </el-form>

        <div
          class="settings-page__mobile-actions"
          :style="mobileActionsStyle"
          :aria-label="t('settings.mobileActions')"
        >
          <el-button
            :loading="loading"
            :disabled="actionDisabled"
            :aria-label="t('common.reload')"
            @click="loadSettings"
          >
            {{ t('common.reload') }}
          </el-button>
          <el-button
            type="primary"
            native-type="submit"
            :loading="saving"
            :disabled="actionDisabled || hasValidationErrors"
            :aria-label="t('settings.saveSettings')"
          >
            {{ t('settings.saveSettings') }}
          </el-button>
        </div>
      </form>
    </DataState>
  </section>
</template>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.settings-page__header {
  position: sticky;
  top: var(--ui-space-16);
  z-index: 20;
  padding: var(--ui-space-8) 0;
  background: linear-gradient(180deg, color-mix(in srgb, var(--ui-color-surface) 96%, transparent), color-mix(in srgb, var(--ui-color-surface) 86%, transparent));
  backdrop-filter: blur(var(--ui-blur-lg));
}

.settings-page__header-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--ui-space-12);
}

.settings-page__content {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
}

.settings-form__control,
.settings-form :deep(.el-select),
.settings-form :deep(.el-input-number) {
  width: 100%;
}

.settings-page__validation {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-4);
  margin-bottom: var(--ui-space-12);
  padding: var(--ui-space-12);
  border: var(--ui-border-width-thin) solid var(--el-color-danger-light-5);
  border-radius: var(--ui-radius-8);
  background: color-mix(in srgb, var(--el-color-danger-light-9) 72%, transparent);
}

.settings-page__validation-status,
.settings-page__validation-item {
  color: var(--el-color-danger);
}

.settings-page__validation-status {
  font-weight: 600;
}

.settings-page__validation-item {
  margin: 0;
  font-size: var(--ui-font-size-13);
  line-height: 1.45;
}

.settings-page__mobile-actions {
  display: none;
}

@media (max-width: 767px) {
  .settings-page {
    padding-bottom: calc(96px + var(--ui-space-16) + env(safe-area-inset-bottom));
  }

  .settings-page__header {
    top: 0;
  }

  .settings-page__header :deep(.app-page-header__actions) {
    display: none;
  }

  .settings-page__header-actions {
    display: none;
  }

  .settings-page__mobile-actions {
    position: fixed;
    left: var(--ui-space-16);
    right: var(--ui-space-16);
    bottom: 0;
    z-index: 30;
    display: flex;
    gap: var(--ui-space-12);
    padding: var(--ui-space-12) var(--ui-space-16) calc(var(--ui-space-12) + env(safe-area-inset-bottom));
    border: var(--ui-border-width-thin) solid var(--ui-color-border);
    border-bottom: 0;
    border-radius: var(--ui-radius-10) var(--ui-radius-10) 0 0;
    background: var(--ui-color-surface);
    box-shadow: 0 -10px 24px rgba(15, 23, 42, 0.08);
  }

  .settings-page__mobile-actions .el-button {
    flex: 1 1 0;
  }
}
</style>

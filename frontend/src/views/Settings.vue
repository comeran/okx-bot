<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import { getSettings, updateSettings } from '@/services/settings';
import type { AppSettingsUpdate, AppSettingsView } from '@/types/settings';

const { t } = useI18n();

const loading = ref(false);
const saving = ref(false);
const settingsView = ref<AppSettingsView | null>(null);

const form = reactive<AppSettingsUpdate>({
  mode: 'backtest',
  exchange: {
    api_key: '',
    secret: '',
    passphrase: '',
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

function applySettings(settings: AppSettingsView): void {
  settingsView.value = settings;
  form.mode = settings.mode;
  form.exchange.api_key = '';
  form.exchange.secret = '';
  form.exchange.passphrase = '';
  form.backtest = { ...settings.backtest };
  form.risk = { ...settings.risk };
  form.notify.telegram_bot_token = '';
  form.notify.telegram_chat_id = settings.notify.telegram_chat_id;
  form.web = { ...settings.web };
}

async function loadSettings(): Promise<void> {
  loading.value = true;
  try {
    applySettings(await getSettings());
  } catch {
    ElMessage.error(t('settings.loadError'));
  } finally {
    loading.value = false;
  }
}

async function saveSettings(): Promise<void> {
  saving.value = true;
  try {
    applySettings(await updateSettings({ ...form }));
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
</script>

<template>
  <section class="settings-page">
    <div class="settings-page__header">
      <div>
        <h2>{{ t('settings.title') }}</h2>
        <p>{{ t('settings.description') }}</p>
      </div>
      <div class="settings-page__actions">
        <el-button :loading="loading" @click="loadSettings">{{ t('common.reload') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveSettings">{{ t('settings.saveSettings') }}</el-button>
      </div>
    </div>

    <el-form v-loading="loading" :model="form" label-position="top" class="settings-form">
      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.runtime') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.mode')">
              <el-select v-model="form.mode">
                <el-option :label="t('settings.modes.backtest')" value="backtest" />
                <el-option :label="t('settings.modes.paper')" value="paper" />
                <el-option :label="t('settings.modes.live')" value="live" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.okxExchange') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.apiKey')">
              <el-input v-model="form.exchange.api_key" type="password" show-password :placeholder="t('settings.keepExisting')" />
              <div v-if="settingsView?.exchange.api_key_set" class="secret-status">
                {{ t('common.current') }}: {{ settingsView.exchange.api_key }}
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.secret')">
              <el-input v-model="form.exchange.secret" type="password" show-password :placeholder="t('settings.keepExisting')" />
              <div v-if="settingsView?.exchange.secret_set" class="secret-status">
                {{ t('common.current') }}: {{ settingsView.exchange.secret }}
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.passphrase')">
              <el-input v-model="form.exchange.passphrase" type="password" show-password :placeholder="t('settings.keepExisting')" />
              <div v-if="settingsView?.exchange.passphrase_set" class="secret-status">
                {{ t('common.current') }}: {{ settingsView.exchange.passphrase }}
              </div>
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.backtestDefaults') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="6">
            <el-form-item :label="t('settings.initialCapital')">
              <el-input-number v-model="form.backtest.initial_capital" :min="0" :step="1000" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="6">
            <el-form-item :label="t('settings.feeRate')">
              <el-input-number v-model="form.backtest.fee_rate" :min="0" :step="0.0001" :precision="6" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="6">
            <el-form-item :label="t('settings.slippage')">
              <el-input-number v-model="form.backtest.slippage" :min="0" :step="0.0001" :precision="6" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="6">
            <el-form-item :label="t('settings.dataCacheDir')">
              <el-input v-model="form.backtest.data_cache_dir" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.riskLimits') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.maxDailyLossPct')">
              <el-input-number v-model="form.risk.max_daily_loss_pct" :min="0" :max="1" :step="0.01" :precision="4" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.maxDrawdownPct')">
              <el-input-number v-model="form.risk.max_drawdown_pct" :min="0" :max="1" :step="0.01" :precision="4" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('settings.maxTotalPositionPct')">
              <el-input-number v-model="form.risk.max_total_position_pct" :min="0" :max="1" :step="0.01" :precision="4" class="full-width" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.notifications') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="12">
            <el-form-item :label="t('settings.telegramBotToken')">
              <el-input v-model="form.notify.telegram_bot_token" type="password" show-password :placeholder="t('settings.keepExisting')" />
              <div v-if="settingsView?.notify.telegram_bot_token_set" class="secret-status">
                {{ t('common.current') }}: {{ settingsView.notify.telegram_bot_token }}
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item :label="t('settings.telegramChatId')">
              <el-input v-model="form.notify.telegram_chat_id" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>

      <el-card shadow="never" class="settings-card">
        <template #header>{{ t('settings.webServer') }}</template>
        <el-row :gutter="16">
          <el-col :xs="24" :md="12">
            <el-form-item :label="t('settings.host')">
              <el-input v-model="form.web.host" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="12">
            <el-form-item :label="t('settings.port')">
              <el-input-number v-model="form.web.port" :min="1" :max="65535" class="full-width" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-card>
    </el-form>
  </section>
</template>

<style scoped>
h2 {
  margin: 0 0 8px;
}

.settings-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.settings-page__header p {
  margin: 0;
  color: #606266;
}

.settings-page__actions {
  display: flex;
  gap: 8px;
}

.settings-card {
  margin-bottom: 16px;
}

.settings-form :deep(.el-select),
.full-width {
  width: 100%;
}

.secret-status {
  margin-top: 6px;
  color: #909399;
  font-size: 12px;
}
</style>

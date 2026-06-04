<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import CodeEditor from '@/components/editor/CodeEditor.vue';
import StrategyForm from '@/components/StrategyForm.vue';
import { listStrategies, startStrategy, stopStrategy } from '@/services/strategies';
import type { StrategySummary, StrategyYamlForm } from '@/types/strategy';
import { getStrategyActionState, getStrategyStatusTagType } from '@/utils/strategy';

const { t } = useI18n();

const strategies = ref<StrategySummary[]>([]);
const selectedName = ref('');
const loading = ref(false);
const actionName = ref('');

const form = reactive<StrategyYamlForm>({
  name: 'ma_cross',
  symbol: 'BTC-USDT-SWAP',
  timeframe: '1m',
  capitalPct: 0.1,
  maxPositionPct: 0.1,
  stopLossPct: 0.02,
  takeProfitPct: 0.04,
});

const code = ref('');

const selectedStrategy = computed(() =>
  strategies.value.find((strategy) => strategy.name === selectedName.value),
);

function buildYaml(): string {
  return [
    `name: ${form.name}`,
    `symbol: ${form.symbol}`,
    `timeframe: ${form.timeframe}`,
    `capital_pct: ${form.capitalPct}`,
    'risk:',
    `  max_position_pct: ${form.maxPositionPct}`,
    `  stop_loss_pct: ${form.stopLossPct}`,
    `  take_profit_pct: ${form.takeProfitPct}`,
    'params:',
    '  fast: 10',
    '  slow: 30',
    'indicators:',
    '  fast_ma: "sma(close, {{ fast }})"',
    '  slow_ma: "sma(close, {{ slow }})"',
    'conditions:',
    '  buy:',
    '    - "fast_ma > slow_ma"',
    '  sell:',
    '    - "fast_ma < slow_ma"',
  ].join('\n');
}

function applyFormToEditor(): void {
  code.value = `${buildYaml()}\n`;
}

function selectStrategy(strategy: StrategySummary): void {
  selectedName.value = strategy.name;
  form.name = strategy.name;
  applyFormToEditor();
}

async function refreshStrategies(): Promise<void> {
  loading.value = true;
  try {
    strategies.value = await listStrategies();

    if (!selectedName.value && strategies.value.length > 0) {
      selectStrategy(strategies.value[0]);
    } else if (selectedName.value && !selectedStrategy.value) {
      selectedName.value = '';
    }
  } catch {
    ElMessage.error(t('strategies.loadError'));
  } finally {
    loading.value = false;
  }
}

async function runAction(strategy: StrategySummary, action: 'start' | 'stop'): Promise<void> {
  actionName.value = strategy.name;
  try {
    if (action === 'start') {
      await startStrategy(strategy.name);
      ElMessage.success(t('strategies.started', { name: strategy.name }));
    } else {
      await stopStrategy(strategy.name);
      ElMessage.success(t('strategies.stopped', { name: strategy.name }));
    }

    await refreshStrategies();
  } catch {
    ElMessage.error(t('strategies.actionError', { action, name: strategy.name }));
  } finally {
    actionName.value = '';
  }
}

function actionState(strategy: StrategySummary) {
  return getStrategyActionState(strategy, actionName.value);
}

onMounted(() => {
  applyFormToEditor();
  void refreshStrategies();
});
</script>

<template>
  <section class="strategy-page">
    <div class="strategy-page__header">
      <div>
        <h2>{{ t('strategies.title') }}</h2>
        <p>{{ t('strategies.description') }}</p>
      </div>
      <el-button :loading="loading" @click="refreshStrategies">{{ t('strategies.refresh') }}</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="9">
        <el-card shadow="never">
          <template #header>{{ t('strategies.strategyList') }}</template>

          <el-empty v-if="!loading && strategies.length === 0" :description="t('strategies.noStrategiesFound')" />
          <el-table
            v-else
            v-loading="loading"
            :data="strategies"
            highlight-current-row
            @row-click="selectStrategy"
          >
            <el-table-column prop="name" :label="t('common.name')" min-width="140" />
            <el-table-column :label="t('common.status')" width="110">
              <template #default="{ row }: { row: StrategySummary }">
                <el-tag :type="getStrategyStatusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="170" fixed="right">
              <template #default="{ row }: { row: StrategySummary }">
                <el-button
                  size="small"
                  type="success"
                  :disabled="actionState(row).startDisabled"
                  :loading="actionState(row).actionLoading"
                  @click.stop="runAction(row, 'start')"
                >
                  {{ t('strategies.start') }}
                </el-button>
                <el-button
                  size="small"
                  :disabled="actionState(row).stopDisabled"
                  :loading="actionState(row).actionLoading"
                  @click.stop="runAction(row, 'stop')"
                >
                  {{ t('strategies.stop') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="15">
        <el-card shadow="never">
          <template #header>
            <span>{{ t('strategies.yamlForm') }}</span>
          </template>

          <p class="strategy-page__hint">{{ t('strategies.yamlDraftHint') }}</p>
          <StrategyForm v-model="form" @regenerate="applyFormToEditor" />
          <CodeEditor v-model="code" :label="t('strategies.strategyYaml')" language="yaml" />
        </el-card>
      </el-col>
    </el-row>
  </section>
</template>

<style scoped>
h2 {
  margin: 0 0 8px;
}

.strategy-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.strategy-page__header p,
.strategy-page__hint {
  margin: 0;
  color: #606266;
}

.strategy-page__hint {
  margin-bottom: 16px;
}
</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';

import CodeEditor from '@/components/editor/CodeEditor.vue';
import StrategyForm from '@/components/StrategyForm.vue';
import { listStrategies, startStrategy, stopStrategy } from '@/services/strategies';
import type { StrategySummary, StrategyYamlForm } from '@/types/strategy';

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
    ElMessage.error('Failed to load strategies');
  } finally {
    loading.value = false;
  }
}

async function runAction(strategy: StrategySummary, action: 'start' | 'stop'): Promise<void> {
  actionName.value = strategy.name;
  try {
    if (action === 'start') {
      await startStrategy(strategy.name);
      ElMessage.success(`Started ${strategy.name}`);
    } else {
      await stopStrategy(strategy.name);
      ElMessage.success(`Stopped ${strategy.name}`);
    }

    await refreshStrategies();
  } catch {
    ElMessage.error(`Failed to ${action} ${strategy.name}`);
  } finally {
    actionName.value = '';
  }
}

function statusType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'info';
  if (status === 'error') return 'danger';
  return 'warning';
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
        <h2>Strategies</h2>
        <p>Manage strategy runtime status and draft YAML configuration.</p>
      </div>
      <el-button :loading="loading" @click="refreshStrategies">Refresh</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="9">
        <el-card shadow="never">
          <template #header>Strategy List</template>

          <el-empty v-if="!loading && strategies.length === 0" description="No strategies found" />
          <el-table
            v-else
            v-loading="loading"
            :data="strategies"
            highlight-current-row
            @row-click="selectStrategy"
          >
            <el-table-column prop="name" label="Name" min-width="140" />
            <el-table-column label="Status" width="110">
              <template #default="{ row }: { row: StrategySummary }">
                <el-tag :type="statusType(row.status)" effect="plain">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Actions" width="170" fixed="right">
              <template #default="{ row }: { row: StrategySummary }">
                <el-button
                  size="small"
                  type="success"
                  :disabled="row.status === 'running'"
                  :loading="actionName === row.name"
                  @click.stop="runAction(row, 'start')"
                >
                  Start
                </el-button>
                <el-button
                  size="small"
                  :disabled="row.status !== 'running'"
                  :loading="actionName === row.name"
                  @click.stop="runAction(row, 'stop')"
                >
                  Stop
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="15">
        <el-card shadow="never">
          <template #header>
            <span>Strategy YAML Form</span>
          </template>

          <StrategyForm v-model="form" @regenerate="applyFormToEditor" />
          <CodeEditor v-model="code" label="Strategy YAML" language="yaml" />
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

.strategy-page__header p {
  margin: 0;
  color: #606266;
}
</style>

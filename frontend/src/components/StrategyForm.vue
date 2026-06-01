<script setup lang="ts">
import type { StrategyYamlForm } from '@/types/strategy';

const form = defineModel<StrategyYamlForm>({ required: true });

defineEmits<{
  regenerate: [];
}>();

const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'];
</script>

<template>
  <el-form :model="form" label-position="top" class="strategy-form">
    <el-row :gutter="12">
      <el-col :xs="24" :sm="12">
        <el-form-item label="Name">
          <el-input v-model="form.name" placeholder="strategy name" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Symbol">
          <el-input v-model="form.symbol" placeholder="BTC-USDT-SWAP" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Timeframe">
          <el-select v-model="form.timeframe" class="strategy-form__control">
            <el-option
              v-for="timeframe in timeframeOptions"
              :key="timeframe"
              :label="timeframe"
              :value="timeframe"
            />
          </el-select>
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Capital %">
          <el-input-number v-model="form.capitalPct" :min="0" :max="1" :step="0.01" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Max Position %">
          <el-input-number v-model="form.maxPositionPct" :min="0" :max="1" :step="0.01" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Stop Loss %">
          <el-input-number v-model="form.stopLossPct" :min="0" :max="1" :step="0.01" />
        </el-form-item>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-form-item label="Take Profit %">
          <el-input-number v-model="form.takeProfitPct" :min="0" :max="1" :step="0.01" />
        </el-form-item>
      </el-col>
    </el-row>

    <el-form-item>
      <div class="strategy-form__actions">
        <el-button type="primary" @click="$emit('regenerate')">Regenerate YAML from form</el-button>
        <span class="strategy-form__hint">Form changes update the editor only when regenerated.</span>
      </div>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.strategy-form {
  margin-bottom: 16px;
}

.strategy-form__control,
:deep(.el-input-number) {
  width: 100%;
}

.strategy-form__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.strategy-form__hint {
  color: #909399;
  font-size: 13px;
}
</style>

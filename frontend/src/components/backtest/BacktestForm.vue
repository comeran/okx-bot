<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import SectionCard from '@/components/ui/SectionCard.vue';
import type { BacktestValidationError } from '@/utils/backtest';

export interface BacktestFormModel {
  strategy: string;
  symbol: string;
  timeframe: string;
  startTime: Date | null;
  endTime: Date | null;
  initialCapital: number | null | undefined;
}

type BacktestFormGroupKey = 'strategy' | 'instrument' | 'period' | 'capital';

const validationGroupByError: Record<BacktestValidationError, BacktestFormGroupKey> = {
  timeRequired: 'period',
  endAfterStart: 'period',
  initialCapitalPositive: 'capital',
};

const validationMessageKeyByError: Record<BacktestValidationError, string> = {
  timeRequired: 'backtest.validation.timeRequired',
  endAfterStart: 'backtest.validation.endAfterStart',
  initialCapitalPositive: 'backtest.validation.initialCapitalPositive',
};

export interface BacktestStrategyOption {
  id: string;
  value: string;
  backendValue: string;
  label: string;
  disabled?: boolean;
}

interface Props {
  form: BacktestFormModel;
  strategyOptions: BacktestStrategyOption[];
  strategyConflictMessage?: string | null;
  strategyCatalogUnavailable?: boolean;
  symbolOptions: string[];
  timeframeOptions: string[];
  strategiesLoading?: boolean;
  running?: boolean;
  validationError?: BacktestValidationError | null;
}

const props = withDefaults(defineProps<Props>(), {
  strategyConflictMessage: null,
  strategyCatalogUnavailable: false,
  strategiesLoading: false,
  running: false,
  validationError: null,
});

const emit = defineEmits<{
  run: [];
  'retry-strategies': [];
}>();

const { t } = useI18n();

const groupCards = computed(() => ([
  {
    key: 'strategy',
    title: t('backtest.form.strategyGroup'),
    description: t('backtest.form.strategyDescription'),
  },
  {
    key: 'instrument',
    title: t('backtest.form.instrumentGroup'),
    description: t('backtest.form.instrumentDescription'),
  },
  {
    key: 'capital',
    title: t('backtest.form.capitalGroup'),
    description: t('backtest.form.capitalDescription'),
  },
  {
    key: 'period',
    title: t('backtest.form.periodGroup'),
    description: t('backtest.form.periodDescription'),
  },
] as const));

const validationGroup = computed<BacktestFormGroupKey | null>(() => {
  if (!props.validationError) {
    return null;
  }

  return validationGroupByError[props.validationError];
});

const validationMessage = computed(() => {
  if (!props.validationError) {
    return null;
  }

  return t(validationMessageKeyByError[props.validationError]);
});

const strategyCatalogMessage = computed(() => (
  props.strategyCatalogUnavailable ? t('backtest.strategyCatalogUnavailable') : null
));

function submit(): void {
  emit('run');
}

function retryStrategies(): void {
  emit('retry-strategies');
}
</script>

<template>
  <SectionCard :title="t('backtest.runBacktest')" :description="t('backtest.form.description')">
    <el-form :model="props.form" label-position="top" class="backtest-form" @submit.prevent="submit">
      <div class="backtest-form__grid">
        <section
          v-for="group in groupCards"
          :key="group.key"
          class="backtest-form__group"
          :class="`backtest-form__group--${group.key}`"
        >
          <h3 class="backtest-form__group-title">{{ group.title }}</h3>
          <p class="backtest-form__group-description">{{ group.description }}</p>
          <p
            v-if="validationMessage && validationGroup === group.key"
            class="backtest-form__validation"
            role="alert"
            aria-live="polite"
          >
            {{ validationMessage }}
          </p>

          <template v-if="group.key === 'strategy'">
            <el-form-item :label="t('backtest.strategy')">
              <el-select
                v-model="props.form.strategy"
                :loading="props.strategiesLoading"
                :disabled="props.strategiesLoading || props.strategyCatalogUnavailable"
                class="backtest-form__control"
                :aria-label="t('backtest.strategy')"
              >
                <el-option
                  v-for="option in props.strategyOptions"
                  :key="option.id"
                  :label="option.label"
                  :value="option.value"
                  :disabled="option.disabled"
                />
              </el-select>
            </el-form-item>
            <p
              v-if="props.strategyConflictMessage"
              class="backtest-form__validation"
              role="alert"
              aria-live="polite"
            >
              {{ props.strategyConflictMessage }}
            </p>
            <div v-if="strategyCatalogMessage" class="backtest-form__strategy-actions">
              <p
                class="backtest-form__validation"
                role="alert"
                aria-live="polite"
              >
                {{ strategyCatalogMessage }}
              </p>
              <el-button type="default" @click="retryStrategies">
                {{ t('common.retry') }}
              </el-button>
            </div>
          </template>

          <template v-else-if="group.key === 'instrument'">
            <el-form-item :label="t('common.symbol')">
              <el-select
                v-model="props.form.symbol"
                filterable
                class="backtest-form__control"
                :aria-label="t('common.symbol')"
              >
                <el-option
                  v-for="option in props.symbolOptions"
                  :key="option"
                  :label="option"
                  :value="option"
                />
              </el-select>
            </el-form-item>
          </template>

          <div v-else-if="group.key === 'period'" class="backtest-form__period-fields">
            <el-form-item :label="t('common.timeframe')">
              <el-select
                v-model="props.form.timeframe"
                class="backtest-form__control"
                :aria-label="t('common.timeframe')"
              >
                <el-option
                  v-for="option in props.timeframeOptions"
                  :key="option"
                  :label="option"
                  :value="option"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('backtest.startTime')">
              <el-date-picker
                v-model="props.form.startTime"
                type="datetime"
                :placeholder="t('backtest.selectStartTime')"
                class="backtest-form__control"
                :aria-label="t('backtest.startTime')"
              />
            </el-form-item>
            <el-form-item :label="t('backtest.endTime')">
              <el-date-picker
                v-model="props.form.endTime"
                type="datetime"
                :placeholder="t('backtest.selectEndTime')"
                class="backtest-form__control"
                :aria-label="t('backtest.endTime')"
              />
            </el-form-item>
          </div>

          <template v-else>
            <el-form-item :label="t('backtest.initialCapital')">
              <el-input-number
                v-model="props.form.initialCapital"
                :min="0"
                :step="1000"
                class="backtest-form__control"
                :aria-label="t('backtest.initialCapital')"
              />
            </el-form-item>
            <p class="backtest-form__group-description">
              {{ t('backtest.form.capitalHint') }}
            </p>
          </template>
        </section>
      </div>

      <div class="backtest-form__actions">
        <el-button
          type="primary"
          native-type="submit"
          class="backtest-form__submit"
          :loading="props.running"
          :disabled="props.running"
        >
          {{ t('backtest.run') }}
        </el-button>
      </div>
    </el-form>
  </SectionCard>
</template>

<style scoped>
.backtest-form {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
}

.backtest-form__grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--ui-space-16) var(--ui-space-24);
  align-items: start;
}

.backtest-form__group {
  min-width: 0;
}

.backtest-form__group--period {
  grid-column: 1 / -1;
  padding-top: var(--ui-space-16);
  border-top: var(--ui-border-width-thin) solid var(--ui-color-border);
}

.backtest-form__period-fields {
  display: grid;
  grid-template-columns: minmax(140px, 0.65fr) repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-16);
}

.backtest-form :deep(.el-form-item) {
  margin-bottom: 0;
}

.backtest-form :deep(.el-form-item__label) {
  height: auto;
  margin-bottom: 0;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.backtest-form__group-title {
  margin: 0 0 var(--ui-space-4);
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
}

.backtest-form__group-description {
  margin: 0 0 var(--ui-space-12);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.5;
}

.backtest-form__validation {
  margin: 0 0 var(--ui-space-12);
  color: var(--ui-color-danger);
  font-size: var(--ui-font-size-13);
  line-height: 1.5;
}

.backtest-form__strategy-actions {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-8);
}

.backtest-form__control,
.backtest-form :deep(.el-select),
.backtest-form :deep(.el-date-editor),
.backtest-form :deep(.el-input-number) {
  width: 100%;
}

.backtest-form__actions {
  display: flex;
  justify-content: flex-end;
}

.backtest-form__submit {
  min-width: 180px;
}

@media (max-width: 1023px) {
  .backtest-form__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .backtest-form__group--capital {
    grid-column: 1 / -1;
  }

  .backtest-form__group--capital .backtest-form__control {
    max-width: 300px;
  }

  .backtest-form__period-fields {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .backtest-form__period-fields > :first-child {
    grid-column: 1 / -1;
    max-width: 300px;
  }
}

@media (max-width: 767px) {
  .backtest-form__grid {
    grid-template-columns: 1fr;
  }

  .backtest-form__period-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .backtest-form__group--capital .backtest-form__control,
  .backtest-form__period-fields > :first-child {
    max-width: none;
  }

  .backtest-form__actions {
    justify-content: stretch;
  }

  .backtest-form__submit {
    width: 100%;
    min-width: 0;
  }
}
</style>

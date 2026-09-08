<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Directive } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useI18n } from 'vue-i18n';

import type {
  StrategyConfigPayload,
  StrategyDefinition,
  StrategyParameterDefinition,
  StrategyValidationIssue,
} from '@/types/strategy';
import { fetchTickers } from '@/services/market';
import { fieldIssuesByPath, switchStrategyType } from '@/utils/strategyManagement';

const model = defineModel<StrategyConfigPayload>({ required: true });
const props = withDefaults(defineProps<{
  definitions: StrategyDefinition[];
  mode: 'create' | 'edit' | 'clone';
  issues?: StrategyValidationIssue[];
  readonly?: boolean;
  dirty?: boolean;
}>(), {
  issues: () => [],
  readonly: false,
  dirty: false,
});

const emit = defineEmits<{
  typeChanged: [strategyType: string];
}>();

const { t } = useI18n();
const selectedType = ref(model.value.strategy_type);
let componentActive = false;
let typeRequestSequence = 0;
const issueGroups = computed(() => fieldIssuesByPath(props.issues));
const selectedDefinition = computed(() => props.definitions.find(
  (definition) => definition.strategy_type === model.value.strategy_type,
));
const nameReadonly = computed(() => props.readonly || props.mode === 'edit');
const fallbackSymbols = ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT', 'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'];
const canonicalTimeframes = ['1m', '5m', '15m', '1h', '4h', '1d'];
const fetchedSymbols = ref<string[]>([]);
const symbolsLoading = ref(false);
const symbolOptions = computed(() => uniqueStrings([
  ...fallbackSymbols,
  model.value.symbol,
  ...fetchedSymbols.value,
]));
const timeframeOptions = computed(() => uniqueStrings([
  ...canonicalTimeframes,
  canonicalTimeframes.includes(model.value.timeframe) ? '' : model.value.timeframe,
]));
const symbolValue = computed({
  get: () => model.value.symbol,
  set: (value: string) => updateCommonField('symbol', value),
});
const timeframeValue = computed({
  get: () => model.value.timeframe,
  set: (value: string) => updateCommonField('timeframe', value),
});
interface ComboboxAriaBinding {
  describedBy?: string;
  errorMessage?: string;
}
const comboboxAriaAttributes = ['aria-describedby', 'aria-errormessage'] as const;
const vComboboxAria: Directive<HTMLElement, ComboboxAriaBinding> = {
  mounted: syncComboboxAria,
  updated: syncComboboxAria,
};

onMounted(() => {
  componentActive = true;
  void loadSymbolOptions();
});

onUnmounted(() => {
  componentActive = false;
});

watch(() => model.value.strategy_type, (value) => {
  typeRequestSequence += 1;
  selectedType.value = value;
});

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

function syncComboboxAria(element: HTMLElement, binding: { value: ComboboxAriaBinding }): void {
  void nextTick(() => {
    const combobox = typeof element.matches === 'function' && element.matches('[role="combobox"]')
      ? element
      : element.querySelector<HTMLElement>('[role="combobox"]');
    if (!combobox) return;

    const values = {
      'aria-describedby': binding.value.describedBy,
      'aria-errormessage': binding.value.errorMessage,
    };
    for (const attribute of comboboxAriaAttributes) {
      const value = values[attribute];
      if (value) combobox.setAttribute(attribute, value);
      else combobox.removeAttribute(attribute);
    }
  });
}

async function loadSymbolOptions(): Promise<void> {
  symbolsLoading.value = true;
  const results = await Promise.allSettled([
    fetchTickers('spot'),
    fetchTickers('swap'),
  ]);
  if (!componentActive) return;
  const symbols = results.flatMap((result) => (
    result.status === 'fulfilled'
      ? result.value.map((ticker) => ticker.symbol).filter(Boolean)
      : []
  ));
  fetchedSymbols.value = uniqueStrings(symbols);
  if (results.some((result) => result.status === 'rejected')) {
    ElMessage.warning(t('market.unableToLoadSymbols'));
  }
  symbolsLoading.value = false;
}

function updateCommonField<Key extends 'symbol' | 'timeframe'>(key: Key, value: StrategyConfigPayload[Key]): void {
  model.value = { ...model.value, [key]: value };
}

function inputId(path: string): string {
  if (path === 'strategy_type') return 'strategy-strategy-type';
  return `strategy-${path.replace(/\./g, '-')}`;
}

function descriptionId(path: string): string {
  return `${inputId(path)}-description`;
}

function errorId(path: string): string {
  return `${inputId(path)}-error`;
}

function describedBy(path: string, hasDescription = false): string | undefined {
  const ids: string[] = [];
  if (hasDescription) ids.push(descriptionId(path));
  if (issueGroups.value.fields[path]?.length) ids.push(errorId(path));
  return ids.length ? ids.join(' ') : undefined;
}

function fieldError(path: string): string {
  return issueGroups.value.fields[path]?.join(' ') ?? '';
}

async function confirmDiscard(): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      t('strategies.confirm.discardTypeValues'),
      t('strategies.confirm.discardTitle'),
      { type: 'warning', confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel') },
    );
    return true;
  } catch {
    return false;
  }
}

async function handleTypeChange(nextType: string): Promise<void> {
  const request = ++typeRequestSequence;
  const current = model.value;
  const definition = props.definitions.find((candidate) => candidate.strategy_type === nextType);
  if (!definition) {
    if (request === typeRequestSequence) selectedType.value = model.value.strategy_type;
    return;
  }
  const next = await switchStrategyType(current, definition, props.dirty, confirmDiscard);
  if (request !== typeRequestSequence) return;
  if (next.strategy_type !== nextType) {
    selectedType.value = model.value.strategy_type;
    return;
  }
  model.value = next;
  selectedType.value = nextType;
  emit('typeChanged', nextType);
}

function parameterPath(parameter: StrategyParameterDefinition): string {
  return `params.${parameter.key}`;
}

function updateParameter(key: string, value: string | number | boolean | null | undefined): void {
  model.value.params[key] = value ?? null;
}
</script>

<template>
  <el-form :model="model" label-position="top" class="strategy-form" :disabled="readonly">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="12">
        <el-form-item :label="t('strategies.form.name')" :error="fieldError('name')">
          <el-input
            id="strategy-name"
            v-model="model.name"
            name="name"
            :readonly="nameReadonly"
            :placeholder="t('strategies.form.namePlaceholder')"
            :aria-label="t('strategies.form.name')"
            :aria-describedby="describedBy('name')"
            :aria-errormessage="fieldError('name') ? errorId('name') : undefined"
          />
          <span v-if="fieldError('name')" :id="errorId('name')" class="sr-only">{{ fieldError('name') }}</span>
        </el-form-item>
      </el-col>

      <el-col :xs="24" :sm="12">
        <el-form-item :label="t('strategies.form.strategyType')" :error="fieldError('strategy_type')">
          <el-select
            id="strategy-strategy-type"
            v-model="selectedType"
            name="strategy_type"
            class="strategy-form__control"
            :aria-label="t('strategies.form.strategyType')"
            :aria-describedby="describedBy('strategy_type', true)"
            :aria-errormessage="fieldError('strategy_type') ? errorId('strategy_type') : undefined"
            v-combobox-aria="{
              describedBy: describedBy('strategy_type', true),
              errorMessage: fieldError('strategy_type') ? errorId('strategy_type') : undefined,
            }"
            :disabled="readonly"
            @change="handleTypeChange"
          >
            <el-option
              v-for="definition in definitions"
              :key="definition.strategy_type"
              :label="definition.label"
              :value="definition.strategy_type"
            />
          </el-select>
          <p :id="descriptionId('strategy_type')" class="strategy-form__description">
            {{ t('strategies.form.strategyTypeDescription') }}
          </p>
          <span :id="errorId('strategy_type')" class="sr-only">{{ fieldError('strategy_type') }}</span>
        </el-form-item>
      </el-col>

      <el-col :xs="24" :sm="12">
        <el-form-item :label="t('strategies.form.symbol')" :error="fieldError('symbol')">
          <el-select
            id="strategy-symbol"
            v-model="symbolValue"
            name="symbol"
            class="strategy-form__control"
            :filterable="true"
            :allow-create="true"
            :default-first-option="true"
            :loading="symbolsLoading"
            :disabled="readonly"
            :aria-label="t('strategies.form.symbol')"
            :aria-describedby="describedBy('symbol', true)"
            :aria-errormessage="fieldError('symbol') ? errorId('symbol') : undefined"
            v-combobox-aria="{
              describedBy: describedBy('symbol', true),
              errorMessage: fieldError('symbol') ? errorId('symbol') : undefined,
            }"
          >
            <el-option
              v-for="symbol in symbolOptions"
              :key="symbol"
              :label="symbol"
              :value="symbol"
            />
          </el-select>
          <p :id="descriptionId('symbol')" class="strategy-form__description">
            {{ t('strategies.form.symbolDescription') }}
          </p>
          <span :id="errorId('symbol')" class="sr-only">{{ fieldError('symbol') }}</span>
        </el-form-item>
      </el-col>

      <el-col :xs="24" :sm="12">
        <el-form-item :label="t('strategies.form.timeframe')" :error="fieldError('timeframe')">
          <el-select
            id="strategy-timeframe"
            v-model="timeframeValue"
            name="timeframe"
            class="strategy-form__control"
            :filterable="true"
            :allow-create="true"
            :default-first-option="true"
            :disabled="readonly"
            :aria-label="t('strategies.form.timeframe')"
            :aria-describedby="describedBy('timeframe', true)"
            :aria-errormessage="fieldError('timeframe') ? errorId('timeframe') : undefined"
            v-combobox-aria="{
              describedBy: describedBy('timeframe', true),
              errorMessage: fieldError('timeframe') ? errorId('timeframe') : undefined,
            }"
          >
            <el-option
              v-for="timeframe in timeframeOptions"
              :key="timeframe"
              :label="timeframe"
              :value="timeframe"
            />
          </el-select>
          <p :id="descriptionId('timeframe')" class="strategy-form__description">
            {{ t('strategies.form.timeframeDescription') }}
          </p>
          <span :id="errorId('timeframe')" class="sr-only">{{ fieldError('timeframe') }}</span>
        </el-form-item>
      </el-col>

      <el-col :xs="24" :sm="12">
        <el-form-item :label="t('strategies.form.enabled')" :error="fieldError('enabled')">
          <el-switch
            id="strategy-enabled"
            v-model="model.enabled"
            name="enabled"
            :aria-label="t('strategies.form.enabled')"
            :aria-describedby="describedBy('enabled', true)"
            :aria-errormessage="fieldError('enabled') ? errorId('enabled') : undefined"
          />
          <p :id="descriptionId('enabled')" class="strategy-form__description">
            {{ t('strategies.form.enabledDescription') }}
          </p>
          <span :id="errorId('enabled')" class="sr-only">{{ fieldError('enabled') }}</span>
        </el-form-item>
      </el-col>
    </el-row>

    <div v-if="selectedDefinition" class="strategy-form__parameters">
      <h3>{{ t('strategies.form.parameters') }}</h3>
      <p class="strategy-form__definition">{{ selectedDefinition.description }}</p>
      <el-row :gutter="16">
        <el-col v-for="parameter in selectedDefinition.params" :key="parameter.key" :xs="24" :sm="12">
          <el-form-item :label="parameter.label" :error="fieldError(parameterPath(parameter))">
            <el-input-number
              v-if="parameter.value_type === 'integer' || parameter.value_type === 'number'"
              :id="inputId(parameterPath(parameter))"
              :model-value="model.params[parameter.key] as number"
              @update:model-value="updateParameter(parameter.key, $event)"
              :name="parameterPath(parameter)"
              class="strategy-form__control"
              :min="parameter.minimum ?? undefined"
              :max="parameter.maximum ?? undefined"
              :step="parameter.step ?? (parameter.value_type === 'integer' ? 1 : 0.1)"
              :precision="parameter.value_type === 'integer' ? 0 : undefined"
              :aria-label="parameter.label"
              :aria-describedby="describedBy(parameterPath(parameter), true)"
              :aria-errormessage="fieldError(parameterPath(parameter)) ? errorId(parameterPath(parameter)) : undefined"
            />
            <el-switch
              v-else-if="parameter.value_type === 'boolean'"
              :id="inputId(parameterPath(parameter))"
              :model-value="model.params[parameter.key] as boolean"
              @update:model-value="updateParameter(parameter.key, $event)"
              :name="parameterPath(parameter)"
              :aria-label="parameter.label"
              :aria-describedby="describedBy(parameterPath(parameter), true)"
              :aria-errormessage="fieldError(parameterPath(parameter)) ? errorId(parameterPath(parameter)) : undefined"
            />
            <el-input
              v-else
              :id="inputId(parameterPath(parameter))"
              :model-value="model.params[parameter.key] as string"
              @update:model-value="updateParameter(parameter.key, $event)"
              :name="parameterPath(parameter)"
              :aria-label="parameter.label"
              :aria-describedby="describedBy(parameterPath(parameter), true)"
              :aria-errormessage="fieldError(parameterPath(parameter)) ? errorId(parameterPath(parameter)) : undefined"
            />
            <p :id="descriptionId(parameterPath(parameter))" class="strategy-form__description">
              {{ parameter.description }}
            </p>
            <span
              v-if="fieldError(parameterPath(parameter))"
              :id="errorId(parameterPath(parameter))"
              class="sr-only"
            >{{ fieldError(parameterPath(parameter)) }}</span>
          </el-form-item>
        </el-col>
      </el-row>
    </div>
  </el-form>
</template>

<style scoped>
.strategy-form,
.strategy-form__control,
:deep(.el-input-number),
:deep(.el-select) {
  width: 100%;
}

.strategy-form__parameters h3 {
  margin: var(--ui-space-4) 0 var(--ui-space-6);
  font-size: var(--ui-font-size-16);
}

.strategy-form__definition,
.strategy-form__description {
  margin: 0;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.45;
}

.strategy-form__definition {
  margin-bottom: var(--ui-space-12);
}

.strategy-form__description {
  width: 100%;
  margin-top: var(--ui-space-4);
}

.sr-only {
  position: absolute;
  width: var(--ui-a11y-hidden-size);
  height: var(--ui-a11y-hidden-size);
  padding: 0;
  margin: var(--ui-a11y-hidden-offset);
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>

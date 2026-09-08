<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import CodeEditor from '@/components/editor/CodeEditor.vue';
import StrategyForm from '@/components/StrategyForm.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { StrategyConfigPayload, StrategyDefinition, StrategyValidationIssue } from '@/types/strategy';

const draft = defineModel<StrategyConfigPayload>({ required: true });
const yaml = defineModel<string>('yaml', { required: true });

interface Props {
  title: string;
  mode: 'create' | 'edit' | 'clone';
  definitions: StrategyDefinition[];
  advanced?: boolean;
  readonly?: boolean;
  dirty?: boolean;
  busy?: boolean;
  saveLoading?: boolean;
  modelUri: string;
  validationSummary?: string[];
  issues?: StrategyValidationIssue[];
  selectedName?: string;
  cloneSourceName?: string;
}

const props = withDefaults(defineProps<Props>(), {
  advanced: false,
  readonly: false,
  dirty: false,
  busy: false,
  saveLoading: false,
  validationSummary: () => [],
  issues: () => [],
  selectedName: '',
  cloneSourceName: '',
});

const emit = defineEmits<{
  close: [];
  cancel: [];
  save: [];
  enterAdvanced: [];
  leaveAdvanced: [];
}>();

const { t } = useI18n();

const metadataLabel = computed(() => {
  if (props.mode === 'create') return t('strategies.editor.newStrategy');
  if (props.cloneSourceName) return t('strategies.editor.cloneMetadata', { name: props.cloneSourceName });
  if (props.selectedName) return t('strategies.editor.selectedMetadata', { name: props.selectedName });
  return '';
});

const modeLabel = computed(() => (props.advanced ? t('strategies.editor.advanced') : t('strategies.editor.structured')));
</script>

<template>
  <SectionCard :title="props.title" :description="metadataLabel || undefined" class="strategy-editor-panel">
    <template #actions>
      <el-button :aria-label="t('common.close')" @click="emit('close')">
        {{ t('common.close') }}
      </el-button>
    </template>

    <div class="strategy-editor-panel__meta" :class="{ 'strategy-editor-panel__meta--dirty': props.dirty }">
      <span class="strategy-editor-panel__mode-label">{{ t('strategies.editor.mode') }}</span>
      <span class="strategy-editor-panel__mode-value">{{ modeLabel }}</span>
      <span v-if="props.dirty" class="strategy-editor-panel__dirty">{{ t('strategies.editor.unsavedChanges') }}</span>
    </div>

    <div class="strategy-editor-panel__toggle" role="group" :aria-label="t('strategies.editor.mode')">
      <el-button
        :type="props.advanced ? 'default' : 'primary'"
        :aria-pressed="props.advanced ? 'false' : 'true'"
        :disabled="props.busy"
        @click="emit('leaveAdvanced')"
      >
        {{ t('strategies.editor.structured') }}
      </el-button>
      <el-button
        :type="props.advanced ? 'primary' : 'default'"
        :aria-pressed="props.advanced ? 'true' : 'false'"
        :disabled="props.busy"
        @click="emit('enterAdvanced')"
      >
        {{ t('strategies.editor.advanced') }}
      </el-button>
    </div>

    <div v-if="props.validationSummary.length" class="strategy-editor-panel__validation" aria-live="polite">
      <h3>{{ t('strategies.editor.validationSummary') }}</h3>
      <ul>
        <li v-for="message in props.validationSummary" :key="message">{{ message }}</li>
      </ul>
    </div>

    <StrategyForm
      v-if="!props.advanced"
      v-model="draft"
      :definitions="props.definitions"
      :mode="props.mode === 'edit' ? 'edit' : props.mode"
      :issues="props.issues"
      :readonly="props.readonly"
      :dirty="props.dirty"
    />
    <CodeEditor
      v-else
      v-model="yaml"
      :label="t('strategies.editor.yamlLabel')"
      :description="t('strategies.editor.yamlDescription')"
      :model-uri="props.modelUri"
      :issues="props.issues"
      :readonly="props.readonly"
      :height="420"
    />

    <div class="strategy-editor-panel__actions">
      <el-button type="primary" :loading="props.saveLoading" :disabled="props.readonly || props.busy" @click="emit('save')">
        {{ t('common.save') }}
      </el-button>
      <el-button :disabled="props.busy" @click="emit('cancel')">
        {{ t('common.cancel') }}
      </el-button>
    </div>
  </SectionCard>
</template>

<style scoped>
.strategy-editor-panel {
  min-width: 0;
}

.strategy-editor-panel__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--ui-space-8);
  margin-bottom: var(--ui-space-12);
  color: var(--ui-color-text-secondary);
}

.strategy-editor-panel__meta--dirty {
  color: var(--ui-color-primary);
}

.strategy-editor-panel__mode-label,
.strategy-editor-panel__mode-value,
.strategy-editor-panel__dirty {
  display: inline-flex;
  align-items: center;
  gap: var(--ui-space-6);
  padding: var(--ui-space-4) var(--ui-space-10);
  border-radius: var(--ui-radius-pill);
  background: var(--ui-color-info-soft);
}

.strategy-editor-panel__dirty {
  background: var(--ui-color-warning-soft);
  color: var(--ui-color-warning-dark-2, var(--ui-color-warning));
}

.strategy-editor-panel__toggle {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-8);
  margin-bottom: var(--ui-space-16);
}

.strategy-editor-panel__validation {
  margin-bottom: var(--ui-space-16);
  padding: var(--ui-space-12);
  border: var(--ui-border-width-thin) solid color-mix(in srgb, var(--ui-color-warning) 30%, var(--ui-color-border));
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-warning-soft);
}

.strategy-editor-panel__validation h3 {
  margin: 0 0 var(--ui-space-8);
  font-size: var(--ui-font-size-14);
}

.strategy-editor-panel__validation ul {
  margin: 0;
  padding-left: calc(var(--ui-space-16) + var(--ui-space-4));
}

.strategy-editor-panel__validation li + li {
  margin-top: var(--ui-space-4);
}

.strategy-editor-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-8);
  margin-top: var(--ui-space-16);
}

@media (max-width: 767px) {
  .strategy-editor-panel__actions {
    position: sticky;
    bottom: 0;
    z-index: 1;
    padding: var(--ui-space-12);
    padding-bottom: calc(var(--ui-space-12) + env(safe-area-inset-bottom, 0px));
    margin: var(--ui-space-16) calc(var(--ui-space-12) * -1) 0;
    border-top: var(--ui-border-width-thin) solid var(--ui-color-border);
    background: color-mix(in srgb, var(--ui-color-surface) 94%, transparent);
    backdrop-filter: blur(var(--ui-blur-md));
  }
}
</style>

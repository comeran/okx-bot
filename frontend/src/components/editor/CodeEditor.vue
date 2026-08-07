<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as monaco from 'monaco-editor';

import type { StrategyValidationIssue } from '@/types/strategy';
import { markerDataForIssues } from '@/utils/strategyManagement';
import {
  acquireModel as acquireRegisteredModel,
  releaseModel as releaseRegisteredModel,
} from './modelRegistry';

const markerOwner = 'strategy-validation';

const value = defineModel<string>({ required: true });
const props = withDefaults(defineProps<{
  label: string;
  description?: string;
  language?: string;
  height?: number;
  readonly?: boolean;
  modelUri: string;
  issues?: StrategyValidationIssue[];
}>(), {
  description: '',
  language: 'yaml',
  height: 400,
  readonly: false,
  issues: () => [],
});

const surface = ref<HTMLDivElement | null>(null);
const labelId = computed(() => `code-editor-${encodeURIComponent(props.modelUri)}-label`);
const descriptionId = computed(() => `code-editor-${encodeURIComponent(props.modelUri)}-description`);
let editor: monaco.editor.IStandaloneCodeEditor | null = null;
let editorModel: monaco.editor.ITextModel | null = null;
let leasedUri = '';
let contentSubscription: monaco.IDisposable | null = null;
let suppressUpdate = false;

function clearMarkers(model = editorModel): void {
  if (model) monaco.editor.setModelMarkers(model, markerOwner, []);
}

function applyMarkers(): void {
  if (!editorModel) return;
  monaco.editor.setModelMarkers(
    editorModel,
    markerOwner,
    markerDataForIssues(props.issues).markers as monaco.editor.IMarkerData[],
  );
}

function focusedInput(): HTMLElement | null {
  return editor?.getDomNode()?.querySelector<HTMLElement>('textarea, input') ?? null;
}

function updateFocusedInputAccessibility(): void {
  editor?.updateOptions({ ariaLabel: props.label });
  const input = focusedInput();
  if (!input) return;
  input.setAttribute('aria-label', props.label);
  if (props.description) input.setAttribute('aria-describedby', descriptionId.value);
  else input.removeAttribute('aria-describedby');
}

function acquireModel(uri: string, initialValue: string): monaco.editor.ITextModel {
  const acquired = acquireRegisteredModel(uri, initialValue, props.language);
  leasedUri = acquired.key;
  return acquired.model;
}

function releaseModel(): void {
  contentSubscription?.dispose();
  contentSubscription = null;
  if (!editorModel || !leasedUri) {
    editorModel = null;
    leasedUri = '';
    return;
  }

  releaseRegisteredModel(leasedUri, clearMarkers);
  editorModel = null;
  leasedUri = '';
}

function switchModel(uri: string, nextValue: string): void {
  if (!editor) return;
  editor.setModel(null);
  releaseModel();
  editorModel = acquireModel(uri, nextValue);
  if (editorModel.getValue() !== nextValue) {
    suppressUpdate = true;
    editorModel.setValue(nextValue);
    suppressUpdate = false;
  }
  editor.setModel(editorModel);
  contentSubscription = editorModel.onDidChangeContent(() => {
    if (!editorModel || suppressUpdate) return;
    value.value = editorModel.getValue();
  });
  applyMarkers();
  updateFocusedInputAccessibility();
}

onMounted(() => {
  if (!surface.value) return;
  editor = monaco.editor.create(surface.value, {
    theme: 'vs-dark',
    minimap: { enabled: false },
    automaticLayout: true,
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    readOnly: props.readonly,
    accessibilitySupport: 'on',
    ariaLabel: props.label,
  });
  switchModel(props.modelUri, value.value);
});

watch(() => [props.modelUri, value.value] as const, ([nextUri, nextValue], [previousUri]) => {
  if (nextUri !== previousUri) {
    switchModel(nextUri, nextValue);
    return;
  }
  if (!editorModel || editorModel.getValue() === nextValue) return;
  suppressUpdate = true;
  editorModel.setValue(nextValue);
  suppressUpdate = false;
});

watch(() => props.readonly, (readonly) => {
  editor?.updateOptions({ readOnly: readonly });
});

watch(() => props.language, (language) => {
  if (editorModel) monaco.editor.setModelLanguage(editorModel, language);
});

watch(() => [props.label, props.description], () => {
  updateFocusedInputAccessibility();
});

watch(() => props.issues, () => {
  applyMarkers();
}, { deep: true });

onBeforeUnmount(() => {
  editor?.setModel(null);
  releaseModel();
  editor?.dispose();
  editor = null;
});
</script>

<template>
  <div class="code-editor">
    <div :id="labelId" class="code-editor__label">{{ label }}</div>
    <p v-if="description" :id="descriptionId" class="code-editor__description">{{ description }}</p>
    <div
      ref="surface"
      class="code-editor__surface"
      :style="{ height: `${height}px` }"
    />
  </div>
</template>

<style scoped>
.code-editor {
  width: 100%;
  min-width: 0;
}

.code-editor__label {
  margin-bottom: 6px;
  font-weight: 600;
}

.code-editor__description {
  margin: 0 0 8px;
  color: #606266;
  font-size: 13px;
}

.code-editor__surface {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid #303133;
  border-radius: 4px;
}

@media (max-width: 600px) {
  .code-editor__surface {
    min-height: 280px;
  }
}
</style>

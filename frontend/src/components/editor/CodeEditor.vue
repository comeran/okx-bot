<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as monaco from 'monaco-editor';

const model = defineModel<string>({ required: true });

const props = withDefaults(
  defineProps<{
    label?: string;
    language?: string;
    height?: number;
  }>(),
  {
    label: 'Strategy YAML',
    language: 'yaml',
    height: 400,
  },
);

const editorRef = ref<HTMLDivElement | null>(null);
let editor: monaco.editor.IStandaloneCodeEditor | null = null;
let suppressModelUpdate = false;

onMounted(() => {
  if (!editorRef.value) {
    return;
  }

  editor = monaco.editor.create(editorRef.value, {
    value: model.value,
    language: props.language,
    theme: 'vs-dark',
    minimap: { enabled: false },
    automaticLayout: true,
    scrollBeyondLastLine: false,
  });

  editor.onDidChangeModelContent(() => {
    if (!editor || suppressModelUpdate) {
      return;
    }

    model.value = editor.getValue();
  });
});

watch(model, (value) => {
  if (!editor || editor.getValue() === value) {
    return;
  }

  suppressModelUpdate = true;
  editor.setValue(value);
  suppressModelUpdate = false;
});

onBeforeUnmount(() => {
  editor?.dispose();
  editor = null;
});
</script>

<template>
  <div class="code-editor">
    <div class="code-editor__label">{{ label }}</div>
    <div ref="editorRef" class="code-editor__surface" :style="{ height: `${height}px` }" />
  </div>
</template>

<style scoped>
.code-editor__label {
  margin-bottom: 8px;
  font-weight: 600;
}

.code-editor__surface {
  overflow: hidden;
  border: 1px solid #303133;
  border-radius: 4px;
}
</style>

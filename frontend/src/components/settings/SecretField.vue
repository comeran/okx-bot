<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

interface Props {
  modelValue: string;
  configured: boolean;
  label: string;
  hint?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  hint: '',
  disabled: false,
});

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const { t } = useI18n();
const inputId = `secret-field-${Math.random().toString(36).slice(2, 10)}`;
const labelId = `${inputId}-label`;
const hintId = `${inputId}-hint`;
const statusId = `${inputId}-status`;

const describedBy = computed(() => {
  const ids = [props.hint ? hintId : '', props.configured ? statusId : ''].filter(Boolean);
  return ids.length ? ids.join(' ') : undefined;
});

function update(value: string | number | null | undefined): void {
  if (props.disabled) return;
  emit('update:modelValue', typeof value === 'string' ? value : '');
}
</script>

<template>
  <div class="secret-field">
    <div class="secret-field__header">
      <label :id="labelId" :for="inputId" class="secret-field__label">{{ props.label }}</label>
      <span
        v-if="props.configured"
        :id="statusId"
        class="secret-field__status"
        role="status"
        aria-live="polite"
      >
        {{ t('settings.secretConfigured') }}
      </span>
    </div>

    <el-input
      :id="inputId"
      :model-value="props.modelValue"
      type="password"
      show-password
      class="secret-field__input"
      :aria-label="props.label"
      :aria-labelledby="labelId"
      :aria-describedby="describedBy"
      :placeholder="props.hint || undefined"
      :disabled="props.disabled"
      @update:model-value="update"
    />

    <p v-if="props.hint" :id="hintId" class="secret-field__hint">{{ props.hint }}</p>
  </div>
</template>

<style scoped>
.secret-field {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-8);
  min-width: 0;
}

.secret-field__header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--ui-space-8);
}

.secret-field__label {
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
  font-weight: 600;
}

.secret-field__status,
.secret-field__hint {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-12);
  line-height: 1.45;
}

.secret-field__hint {
  margin: 0;
}

.secret-field__input {
  width: 100%;
}
</style>

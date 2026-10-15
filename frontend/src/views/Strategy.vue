<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';

import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import StrategyEditorPanel from '@/components/strategy/StrategyEditorPanel.vue';
import StrategyList, { type StrategyListRow, type StrategyListStatusTone } from '@/components/strategy/StrategyList.vue';
import { useDirtyGuard } from '@/composables/useDirtyGuard';
import {
  validateStrategyConfig,
  validateStrategyConfigYaml,
} from '@/services/strategies';
import { useStrategiesStore } from '@/stores/strategies';
import type {
  StrategyConfig,
  StrategyConfigPayload,
  StrategyStatus,
  StrategyValidationIssue,
} from '@/types/strategy';
import {
  buildCloneDraft,
  buildDefaultStrategyDraft,
  clonePayload,
  fieldIssuesByPath,
  getStrategyRowSafety,
  markerDataForIssues,
  strategyModelUri,
  validationIssuesFromError,
} from '@/utils/strategyManagement';

const { t } = useI18n();
const store = useStrategiesStore();
const mode = ref<'closed' | 'create' | 'edit' | 'clone'>('closed');
const selectedName = ref('');
const cloneSourceName = ref('');
const draft = ref<StrategyConfigPayload | null>(null);
const baseline = ref('');
const advanced = ref(false);
const yaml = ref('');
const yamlBaseline = ref('');
const issues = ref<StrategyValidationIssue[]>([]);
const activeOperation = ref<number | null>(null);
let editorSessionSequence = 0;
let operationSequence = 0;

const configs = computed(() => store.configs);
const selectedStatus = computed<StrategyStatus | undefined>(() => (
  selectedName.value ? store.statuses[selectedName.value]?.status : undefined
));
const formReadonly = computed(() => mode.value === 'edit' && selectedStatus.value !== 'stopped');
const draftDirty = computed(() => draft.value !== null && JSON.stringify(draft.value) !== baseline.value);
const yamlDirty = computed(() => advanced.value && yaml.value !== yamlBaseline.value);
const dirty = computed(() => draftDirty.value || yamlDirty.value);
const editorBusy = computed(() => activeOperation.value !== null);
const editorOpen = computed(() => draft.value !== null && mode.value !== 'closed');
const issueGroups = computed(() => fieldIssuesByPath(issues.value));
const visibleIssueMessages = computed(() => (
  advanced.value
    ? markerDataForIssues(issues.value).external.map((issue) => issue.message)
    : issueGroups.value.general
));
const editorUri = computed(() => strategyModelUri(
  mode.value === 'edit' ? `instance:${selectedName.value}` : `${mode.value}:${cloneSourceName.value || 'new'}`,
));
const listTitle = computed(() => t('strategies.list.title'));
const listDescription = computed(() => t('strategies.list.description'));
const editorTitle = computed(() => t(`strategies.editor.${mode.value}`));

function statusFor(config: StrategyConfig): StrategyStatus | undefined {
  return store.statuses[config.name]?.status;
}

function statusKeyFor(config: StrategyConfig): 'running' | 'stopped' | 'starting' | 'error' | 'unknown' {
  const status = statusFor(config);
  return status === 'running' || status === 'stopped' || status === 'starting' || status === 'error'
    ? status
    : 'unknown';
}

function statusToneFor(statusKey: 'running' | 'stopped' | 'starting' | 'error' | 'unknown'): StrategyListStatusTone {
  switch (statusKey) {
    case 'running':
      return 'success';
    case 'stopped':
      return 'info';
    case 'starting':
      return 'warning';
    case 'error':
      return 'danger';
    default:
      return 'neutral';
  }
}

function configByName(name: string): StrategyConfig | undefined {
  return configs.value.find((config) => config.name === name);
}

type StrategyRowTarget = string | { name: string };

function nameForTarget(target: StrategyRowTarget): string {
  return typeof target === 'string' ? target : target.name;
}

async function selectRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await selectInstance(config);
}

async function editRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await beginEdit(config);
}

async function cloneRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await beginClone(config);
}

async function deleteRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await deleteConfig(config);
}

async function startRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await runAction(config, 'start');
}

async function stopRow(target: StrategyRowTarget): Promise<void> {
  const config = configByName(nameForTarget(target));
  if (config) await runAction(config, 'stop');
}

function runtimeError(config: StrategyConfig): string {
  return store.errors[config.name] ?? '';
}

function safetyFor(config: StrategyConfig) {
  return getStrategyRowSafety(config, statusFor(config));
}

interface EditorOperation {
  session: number;
  operation: number;
}

function invalidateEditorSession(): void {
  editorSessionSequence += 1;
  activeOperation.value = null;
}

function beginEditorOperation(): EditorOperation | null {
  if (activeOperation.value !== null) return null;
  const operation = ++operationSequence;
  activeOperation.value = operation;
  return { session: editorSessionSequence, operation };
}

function operationIsCurrent(context: EditorOperation): boolean {
  return context.session === editorSessionSequence && activeOperation.value === context.operation;
}

function finishEditorOperation(context: EditorOperation): void {
  if (activeOperation.value === context.operation) activeOperation.value = null;
}

function setDraft(next: StrategyConfigPayload, nextMode: 'create' | 'edit' | 'clone'): void {
  invalidateEditorSession();
  const projected = clonePayload(next);
  draft.value = projected;
  baseline.value = JSON.stringify(projected);
  mode.value = nextMode;
  advanced.value = false;
  yaml.value = '';
  yamlBaseline.value = '';
  issues.value = [];
}

async function confirmDiscard(): Promise<boolean> {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm(
      t('strategies.confirm.discardChanges'),
      t('strategies.confirm.discardTitle'),
      { type: 'warning', confirmButtonText: t('common.discard'), cancelButtonText: t('common.cancel') },
    );
    return true;
  } catch {
    return false;
  }
}

const { confirmIfDirty } = useDirtyGuard(() => dirty.value, confirmDiscard);

async function beginCreate(): Promise<void> {
  if (!(await confirmIfDirty())) return;
  const definition = store.definitions[0];
  if (!definition) {
    ElMessage.error(t('strategies.noDefinitions'));
    return;
  }
  selectedName.value = '';
  cloneSourceName.value = '';
  setDraft(buildDefaultStrategyDraft(definition), 'create');
}

async function beginEdit(config: StrategyConfig): Promise<void> {
  if (!(await confirmIfDirty())) return;
  selectedName.value = config.name;
  cloneSourceName.value = '';
  setDraft(config, 'edit');
}

async function beginClone(config: StrategyConfig): Promise<void> {
  if (!(await confirmIfDirty())) return;
  selectedName.value = config.name;
  cloneSourceName.value = config.name;
  setDraft(buildCloneDraft(config), 'clone');
}

async function closeEditor(): Promise<void> {
  if (!(await confirmIfDirty())) return;
  invalidateEditorSession();
  mode.value = 'closed';
  draft.value = null;
  selectedName.value = '';
  cloneSourceName.value = '';
  issues.value = [];
}

async function selectInstance(config: StrategyConfig): Promise<void> {
  if (mode.value === 'edit' && selectedName.value === config.name) return;
  await beginEdit(config);
}

async function enterAdvancedMode(): Promise<void> {
  if (!draft.value || advanced.value) return;
  const context = beginEditorOperation();
  if (!context) return;
  const action = mode.value;
  const selectedTarget = selectedName.value;
  const payload = clonePayload(draft.value);
  const expectedName = action === 'edit' ? selectedTarget : undefined;
  issues.value = [];
  try {
    const result = await validateStrategyConfig(payload, expectedName);
    if (!operationIsCurrent(context)) return;
    draft.value = clonePayload(result.config);
    yaml.value = result.yaml;
    yamlBaseline.value = result.yaml;
    advanced.value = true;
  } catch (error) {
    if (!operationIsCurrent(context)) return;
    issues.value = validationIssuesFromError(error);
    ElMessage.error(t('strategies.validationFailed'));
  } finally {
    finishEditorOperation(context);
  }
}

async function leaveAdvancedMode(): Promise<void> {
  if (editorBusy.value) return;
  if (yamlDirty.value && !(await confirmDiscard())) return;
  advanced.value = false;
  yaml.value = '';
  yamlBaseline.value = '';
  issues.value = [];
}

async function applyYaml(): Promise<void> {
  if (!draft.value) return;
  const context = beginEditorOperation();
  if (!context) return;
  const action = mode.value;
  const selectedTarget = selectedName.value;
  const yamlSnapshot = yaml.value;
  const expectedName = action === 'edit' ? selectedTarget : undefined;
  issues.value = [];
  try {
    const result = await validateStrategyConfigYaml(yamlSnapshot, expectedName);
    if (!operationIsCurrent(context)) return;
    draft.value = clonePayload(result.config);
    yaml.value = result.yaml;
    yamlBaseline.value = result.yaml;
    issues.value = [];
    ElMessage.success(t('strategies.yamlApplied'));
  } catch (error) {
    if (!operationIsCurrent(context)) return;
    issues.value = validationIssuesFromError(error);
    ElMessage.error(t('strategies.validationFailed'));
  } finally {
    finishEditorOperation(context);
  }
}

async function saveDraft(): Promise<void> {
  if (!draft.value || formReadonly.value || mode.value === 'closed') return;
  const context = beginEditorOperation();
  if (!context) return;
  const action = mode.value;
  const selectedTarget = selectedName.value;
  const cloneSource = cloneSourceName.value;
  const expectedName = action === 'edit' ? selectedTarget : undefined;
  const payload = clonePayload(draft.value);
  const yamlSnapshot = yaml.value;
  const advancedSnapshot = advanced.value;
  const targetName = payload.name;
  issues.value = [];
  try {
    const canonical = advancedSnapshot
      ? await validateStrategyConfigYaml(yamlSnapshot, expectedName)
      : await validateStrategyConfig(payload, expectedName);
    if (!operationIsCurrent(context)) return;
    let saved: StrategyConfig;
    if (action === 'create') {
      saved = await store.createConfig(canonical.config);
    } else if (action === 'clone') {
      saved = await store.cloneConfig(cloneSource, {
        target_name: canonical.config.name,
        overrides: {
          strategy_type: canonical.config.strategy_type,
          symbol: canonical.config.symbol,
          timeframe: canonical.config.timeframe,
          enabled: false,
          params: canonical.config.params,
        },
      });
    } else {
      saved = await store.updateConfig(selectedTarget, canonical.config);
    }
    if (!operationIsCurrent(context)) return;
    selectedName.value = saved.name;
    cloneSourceName.value = '';
    setDraft(saved, 'edit');
    ElMessage.success(t('strategies.saved', { name: saved.name }));
  } catch (error) {
    if (!operationIsCurrent(context)) return;
    issues.value = validationIssuesFromError(error);
    const backendError = store.mutationError(targetName || selectedTarget, action === 'edit' ? 'update' : action);
    ElMessage.error(backendError ?? t('strategies.saveFailed'));
  } finally {
    finishEditorOperation(context);
  }
}

async function deleteConfig(config: StrategyConfig): Promise<void> {
  if (!safetyFor(config).canDelete) return;
  try {
    await ElMessageBox.confirm(
      t('strategies.confirm.delete', { name: config.name }),
      t('strategies.confirm.deleteTitle'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') },
    );
    await store.deleteConfig(config.name);
    if (selectedName.value === config.name) {
      invalidateEditorSession();
      mode.value = 'closed';
      draft.value = null;
      selectedName.value = '';
    }
    ElMessage.success(t('strategies.deleted', { name: config.name }));
  } catch (error) {
    if (error === 'cancel' || error === 'close') return;
    ElMessage.error(store.mutationError(config.name, 'delete') ?? t('strategies.deleteFailed'));
  }
}

async function runAction(config: StrategyConfig, action: 'start' | 'stop'): Promise<void> {
  const safety = safetyFor(config);
  if ((action === 'start' && !safety.canStart) || (action === 'stop' && !safety.canStop)) return;
  try {
    await store[action](config.name);
    ElMessage.success(t(action === 'start' ? 'strategies.started' : 'strategies.stopped', { name: config.name }));
  } catch {
    ElMessage.error(store.mutationError(config.name, action) ?? t('strategies.actionFailed'));
  }
}

function saveLoading(): boolean {
  if (!draft.value) return false;
  if (mode.value === 'create') return store.isMutationLoading(draft.value.name, 'create');
  if (mode.value === 'clone') return store.isMutationLoading(draft.value.name, 'clone');
  return store.isMutationLoading(selectedName.value, 'update');
}

const listRows = computed<StrategyListRow[]>(() => configs.value.map((config) => {
  const statusKey = statusKeyFor(config);
  const safety = safetyFor(config);
  return {
    name: config.name,
    strategyType: config.strategy_type,
    symbol: config.symbol,
    timeframe: config.timeframe,
    enabled: config.enabled,
    statusLabel: t(`strategies.status.${statusKey}`),
    statusTone: statusToneFor(statusKey),
    runtimeError: runtimeError(config),
    safetyText: safety.canStop
      ? t('strategies.list.runningSafety')
      : safety.canEdit
        ? t('strategies.list.stoppedSafety')
        : t('strategies.list.readOnlySafety'),
    selected: selectedName.value === config.name,
    canEdit: safety.canEdit,
    canDelete: safety.canDelete,
    canStart: safety.canStart,
    canStop: safety.canStop,
    isDeleting: store.isMutationLoading(config.name, 'delete'),
    isStarting: store.isActionLoading(config.name, 'start'),
    isStopping: store.isActionLoading(config.name, 'stop'),
    actionLabels: {
      select: t('strategies.actions.select', { name: config.name }),
      edit: t('strategies.actions.edit', { name: config.name }),
      clone: t('strategies.actions.clone', { name: config.name }),
      delete: t('strategies.actions.delete', { name: config.name }),
      start: t('strategies.actions.start', { name: config.name }),
      stop: t('strategies.actions.stop', { name: config.name }),
    },
  };
}));

onBeforeRouteLeave(async () => confirmIfDirty());

onBeforeUnmount(() => {
  invalidateEditorSession();
});

onMounted(() => {
  void store.loadInitialData();
});
</script>

<template>
  <section class="strategy-page">
    <AppPageHeader :title="t('strategies.title')" :description="t('strategies.description')">
      <template #actions>
        <el-button :loading="store.loadingInitial" @click="store.loadInitialData()">{{ t('common.refresh') }}</el-button>
        <el-button type="primary" :disabled="store.definitions.length === 0" @click="beginCreate">
          {{ t('strategies.create') }}
        </el-button>
      </template>
    </AppPageHeader>

    <el-alert v-if="store.error" :title="t('strategies.loadError')" :description="store.error" type="error" show-icon :closable="false" />
    <el-alert
      v-if="store.reconciliationError"
      :title="t('strategies.reconciliationWarning')"
      :description="store.reconciliationError"
      type="warning"
      show-icon
      :closable="false"
      class="strategy-page__alert"
    />

    <div
      class="strategy-page__content"
      :class="{ 'strategy-page__content--editor-open': editorOpen }"
    >
      <StrategyList
        :title="listTitle"
        :description="listDescription"
        :rows="listRows"
        :loading="store.loadingInitial"
        :empty-description="t('strategies.emptyDescription')"
        :on-select="selectRow"
        :on-edit="editRow"
        :on-clone="cloneRow"
        :on-delete="deleteRow"
        :on-start="(row) => startRow(row.name)"
        :on-stop="(row) => stopRow(row.name)"
      />

      <StrategyEditorPanel
        v-if="draft && mode !== 'closed'"
        v-model="draft"
        v-model:yaml="yaml"
        :title="editorTitle"
        :mode="mode === 'edit' ? 'edit' : mode"
        :definitions="store.definitions"
        :advanced="advanced"
        :readonly="formReadonly"
        :dirty="dirty"
        :busy="editorBusy"
        :save-loading="saveLoading()"
        :model-uri="editorUri"
        :validation-summary="visibleIssueMessages"
        :issues="issues"
        :selected-name="selectedName"
        :clone-source-name="cloneSourceName"
        @close="closeEditor"
        @cancel="closeEditor"
        @save="saveDraft"
        @enter-advanced="enterAdvancedMode"
        @leave-advanced="leaveAdvancedMode"
      />
    </div>
  </section>
</template>

<style scoped>
.strategy-page {
  min-width: 0;
}

.strategy-page__alert {
  margin-bottom: var(--ui-space-16);
}

.strategy-page__content {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--ui-space-16);
  align-items: start;
}

.strategy-page__content > * {
  min-width: 0;
  width: 100%;
}

.strategy-page__content--editor-open {
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
}

@media (max-width: 1023px) {
  .strategy-page__content {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

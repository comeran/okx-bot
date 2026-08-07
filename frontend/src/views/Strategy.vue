<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';

import CodeEditor from '@/components/editor/CodeEditor.vue';
import StrategyForm from '@/components/StrategyForm.vue';
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
import { getStrategyStatusTagType } from '@/utils/strategy';
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
const issueGroups = computed(() => fieldIssuesByPath(issues.value));
const visibleIssueMessages = computed(() => (
  advanced.value
    ? markerDataForIssues(issues.value).external.map((issue) => issue.message)
    : issueGroups.value.general
));
const editorUri = computed(() => strategyModelUri(
  mode.value === 'edit' ? `instance:${selectedName.value}` : `${mode.value}:${cloneSourceName.value || 'new'}`,
));

function statusFor(config: StrategyConfig): StrategyStatus | undefined {
  return store.statuses[config.name]?.status;
}

function statusKeyFor(config: StrategyConfig): 'running' | 'stopped' | 'starting' | 'error' | 'unknown' {
  const status = statusFor(config);
  return status === 'running' || status === 'stopped' || status === 'starting' || status === 'error'
    ? status
    : 'unknown';
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

async function beginCreate(): Promise<void> {
  if (!(await confirmDiscard())) return;
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
  if (!(await confirmDiscard())) return;
  selectedName.value = config.name;
  cloneSourceName.value = '';
  setDraft(config, 'edit');
}

async function beginClone(config: StrategyConfig): Promise<void> {
  if (!(await confirmDiscard())) return;
  selectedName.value = config.name;
  cloneSourceName.value = config.name;
  setDraft(buildCloneDraft(config), 'clone');
}

async function closeEditor(): Promise<void> {
  if (!(await confirmDiscard())) return;
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

onBeforeRouteLeave(async () => confirmDiscard());

onMounted(() => {
  void store.loadInitialData();
});
</script>

<template>
  <section class="strategy-page">
    <header class="strategy-page__header">
      <div>
        <h2>{{ t('strategies.title') }}</h2>
        <p>{{ t('strategies.description') }}</p>
      </div>
      <div class="strategy-page__header-actions">
        <el-button :loading="store.loadingInitial" @click="store.loadInitialData()">{{ t('common.refresh') }}</el-button>
        <el-button type="primary" :disabled="store.definitions.length === 0" @click="beginCreate">
          {{ t('strategies.create') }}
        </el-button>
      </div>
    </header>

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

    <el-card v-if="configs.length" shadow="never" class="strategy-list">
      <div class="strategy-table" data-testid="strategy-desktop-table">
        <el-table :data="configs" row-key="name" @row-click="selectInstance">
          <el-table-column prop="name" :label="t('common.name')" min-width="150" />
          <el-table-column prop="strategy_type" :label="t('strategies.form.strategyType')" min-width="140" />
          <el-table-column :label="t('strategies.market')" min-width="180">
            <template #default="{ row }: { row: StrategyConfig }">{{ row.symbol }} / {{ row.timeframe }}</template>
          </el-table-column>
          <el-table-column :label="t('strategies.form.enabled')" width="100">
            <template #default="{ row }: { row: StrategyConfig }">{{ row.enabled ? t('common.yes') : t('common.no') }}</template>
          </el-table-column>
          <el-table-column :label="t('common.status')" min-width="150">
            <template #default="{ row }: { row: StrategyConfig }">
              <el-tag :type="getStrategyStatusTagType(statusKeyFor(row))" effect="plain">
                {{ t(`strategies.status.${statusKeyFor(row)}`) }}
              </el-tag>
              <div v-if="runtimeError(row)" class="strategy-runtime-error">{{ runtimeError(row) }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" min-width="430" fixed="right">
            <template #default="{ row }: { row: StrategyConfig }">
              <div class="strategy-actions" @click.stop @keydown.stop>
                <el-button size="small" :aria-label="t('strategies.actions.edit', { name: row.name })" :disabled="!safetyFor(row).canEdit" @click="beginEdit(row)">{{ t('common.edit') }}</el-button>
                <el-button size="small" :aria-label="t('strategies.actions.clone', { name: row.name })" @click="beginClone(row)">{{ t('strategies.clone') }}</el-button>
                <el-button
                  size="small"
                  type="danger"
                  :aria-label="t('strategies.actions.delete', { name: row.name })"
                  :disabled="!safetyFor(row).canDelete"
                  :loading="store.isMutationLoading(row.name, 'delete')"
                  @click="deleteConfig(row)"
                >{{ t('common.delete') }}</el-button>
                <el-button
                  size="small"
                  type="success"
                  :aria-label="t('strategies.actions.start', { name: row.name })"
                  :disabled="!safetyFor(row).canStart"
                  :loading="store.isActionLoading(row.name, 'start')"
                  @click="runAction(row, 'start')"
                >{{ t('strategies.start') }}</el-button>
                <el-button
                  size="small"
                  :aria-label="t('strategies.actions.stop', { name: row.name })"
                  :disabled="!safetyFor(row).canStop"
                  :loading="store.isActionLoading(row.name, 'stop')"
                  @click="runAction(row, 'stop')"
                >{{ t('strategies.stop') }}</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="strategy-cards" data-testid="strategy-mobile-cards">
        <article
          v-for="config in configs"
          :key="config.name"
          class="strategy-card"
        >
          <div class="strategy-card__title">
            <el-button
              text
              :aria-label="t('strategies.actions.select', { name: config.name })"
              :aria-pressed="selectedName === config.name ? 'true' : 'false'"
              @click="selectInstance(config)"
            ><strong>{{ config.name }}</strong></el-button>
            <el-tag :type="getStrategyStatusTagType(statusKeyFor(config))" effect="plain">
              {{ t(`strategies.status.${statusKeyFor(config)}`) }}
            </el-tag>
          </div>
          <dl>
            <dt>{{ t('strategies.form.strategyType') }}</dt><dd>{{ config.strategy_type }}</dd>
            <dt>{{ t('strategies.market') }}</dt><dd>{{ config.symbol }} / {{ config.timeframe }}</dd>
            <dt>{{ t('strategies.form.enabled') }}</dt><dd>{{ config.enabled ? t('common.yes') : t('common.no') }}</dd>
          </dl>
          <p v-if="runtimeError(config)" class="strategy-runtime-error">{{ runtimeError(config) }}</p>
          <div class="strategy-actions">
            <el-button size="small" :aria-label="t('strategies.actions.edit', { name: config.name })" :disabled="!safetyFor(config).canEdit" @click="beginEdit(config)">{{ t('common.edit') }}</el-button>
            <el-button size="small" :aria-label="t('strategies.actions.clone', { name: config.name })" @click="beginClone(config)">{{ t('strategies.clone') }}</el-button>
            <el-button
              size="small"
              type="danger"
              :aria-label="t('strategies.actions.delete', { name: config.name })"
              :disabled="!safetyFor(config).canDelete"
              :loading="store.isMutationLoading(config.name, 'delete')"
              @click="deleteConfig(config)"
            >{{ t('common.delete') }}</el-button>
            <el-button
              size="small"
              type="success"
              :aria-label="t('strategies.actions.start', { name: config.name })"
              :disabled="!safetyFor(config).canStart"
              :loading="store.isActionLoading(config.name, 'start')"
              @click="runAction(config, 'start')"
            >{{ t('strategies.start') }}</el-button>
            <el-button
              size="small"
              :aria-label="t('strategies.actions.stop', { name: config.name })"
              :disabled="!safetyFor(config).canStop"
              :loading="store.isActionLoading(config.name, 'stop')"
              @click="runAction(config, 'stop')"
            >{{ t('strategies.stop') }}</el-button>
          </div>
        </article>
      </div>
    </el-card>

    <el-card v-else-if="!store.loadingInitial" shadow="never" class="strategy-empty">
      <el-empty :description="t('strategies.emptyDescription')">
        <el-button type="primary" :disabled="store.definitions.length === 0" @click="beginCreate">
          {{ t('strategies.createFirst') }}
        </el-button>
      </el-empty>
    </el-card>

    <el-card v-if="draft && mode !== 'closed'" shadow="never" class="strategy-editor">
      <template #header>
        <div class="strategy-editor__header">
          <strong>{{ t(`strategies.editor.${mode}`) }}</strong>
          <el-button :aria-label="t('common.close')" @click="closeEditor">{{ t('common.close') }}</el-button>
        </div>
      </template>

      <el-alert
        v-for="message in visibleIssueMessages"
        :key="message"
        :title="message"
        type="error"
        show-icon
        :closable="false"
        class="strategy-page__alert"
      />

      <div class="strategy-editor__mode">
        <span>{{ t('strategies.editor.mode') }}</span>
        <el-button v-if="!advanced" :loading="editorBusy" :disabled="editorBusy" @click="enterAdvancedMode">{{ t('strategies.editor.advanced') }}</el-button>
        <el-button v-else :disabled="editorBusy" @click="leaveAdvancedMode">{{ t('strategies.editor.structured') }}</el-button>
      </div>

      <StrategyForm
        v-if="!advanced"
        v-model="draft"
        :definitions="store.definitions"
        :mode="mode === 'edit' ? 'edit' : mode"
        :issues="issues"
        :readonly="formReadonly"
        :dirty="draftDirty"
      />
      <template v-else>
        <CodeEditor
          v-model="yaml"
          :label="t('strategies.editor.yamlLabel')"
          :description="t('strategies.editor.yamlDescription')"
          :model-uri="editorUri"
          :issues="issues"
          :readonly="formReadonly"
        />
        <div class="strategy-editor__yaml-actions">
          <el-button type="primary" :loading="editorBusy" :disabled="formReadonly || editorBusy" @click="applyYaml">
            {{ t('strategies.editor.applyYaml') }}
          </el-button>
        </div>
      </template>

      <div class="strategy-editor__actions">
        <el-button type="primary" :loading="saveLoading()" :disabled="formReadonly || editorBusy" @click="saveDraft">
          {{ t('common.save') }}
        </el-button>
        <el-button @click="closeEditor">{{ t('common.cancel') }}</el-button>
      </div>
    </el-card>
  </section>
</template>

<style scoped>
.strategy-page {
  min-width: 0;
}

.strategy-page__header,
.strategy-editor__header,
.strategy-card__title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.strategy-page__header {
  margin-bottom: 20px;
}

.strategy-page h2,
.strategy-page p {
  margin-top: 0;
}

.strategy-page__header p {
  margin-bottom: 0;
  color: #606266;
}

.strategy-page__header-actions,
.strategy-actions,
.strategy-editor__actions,
.strategy-editor__yaml-actions,
.strategy-editor__mode {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.strategy-page__alert,
.strategy-list,
.strategy-empty,
.strategy-editor {
  margin-bottom: 16px;
}

.strategy-runtime-error {
  margin: 6px 0 0;
  color: #c45656;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.strategy-cards {
  display: none;
}

.strategy-card {
  padding: 14px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.strategy-card + .strategy-card {
  margin-top: 12px;
}

.strategy-card dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 6px 12px;
}

.strategy-card dt {
  color: #606266;
}

.strategy-card dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.strategy-editor__mode {
  justify-content: flex-end;
  margin-bottom: 16px;
  color: #606266;
}

.strategy-editor__actions,
.strategy-editor__yaml-actions {
  margin-top: 16px;
}

@media (max-width: 767px) {
  .strategy-page__header {
    align-items: stretch;
    flex-direction: column;
  }

  .strategy-page__header-actions > :deep(.el-button) {
    flex: 1 1 auto;
  }

  .strategy-table {
    display: none;
  }

  .strategy-cards {
    display: block;
  }

  .strategy-actions > :deep(.el-button) {
    margin-left: 0;
  }
}
</style>

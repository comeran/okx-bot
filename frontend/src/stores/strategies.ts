import { defineStore } from 'pinia';

import {
  cloneStrategyConfig,
  createStrategyConfig,
  deleteStrategyConfig,
  listStrategies,
  listStrategyConfigs,
  listStrategyTypes,
  startStrategy,
  stopStrategy,
  updateStrategyConfig,
} from '@/services/strategies';
import type {
  StrategyCloneRequest,
  StrategyConfig,
  StrategyConfigPayload,
  StrategyDefinition,
  StrategyRuntimeSummary,
  StrategySnapshot,
  StrategyStatus,
  StrategyWebSocketMessage,
} from '@/types/strategy';

export type StrategyAction = 'start' | 'stop';
export type StrategyMutationAction = 'create' | 'update' | 'clone' | 'delete' | StrategyAction;

type EventAuthority = number | {
  timestamp?: number;
  receivedAt?: number;
};

type AuthorityComparison = 'older' | 'equal' | 'newer' | 'ambiguous' | 'unordered';

const STRATEGY_MUTATION_ACTIONS: StrategyMutationAction[] = [
  'create',
  'update',
  'clone',
  'delete',
  'start',
  'stop',
];

interface ConfigDeletionAuthority {
  deletedUpdatedAt?: number;
  deleteTimestamp?: number;
}

interface StrategiesState {
  definitions: StrategyDefinition[];
  configs: StrategyConfig[];
  statuses: Record<string, StrategyRuntimeSummary>;
  errors: Record<string, string>;
  loadingInitial: boolean;
  actionLoading: Record<string, boolean>;
  mutationErrors: Record<string, string>;
  mutationLoading: Record<string, boolean>;
  error: string | null;
  reconciliationError: string | null;
  configReconciliationError: string | null;
  statusReconciliationError: string | null;
  configRevisions: Record<string, number>;
  configTombstones: Record<string, number>;
  configAuthorities: Record<string, number>;
  configDeletionAuthorities: Record<string, ConfigDeletionAuthority>;
  runtimeBarriers: Record<string, EventAuthority>;
  statusRevisions: Record<string, number>;
  statusAuthorities: Record<string, EventAuthority>;
  statusSnapshotAuthority?: EventAuthority;
  errorRevisions: Record<string, number>;
  errorAuthorities: Record<string, EventAuthority>;
  errorSnapshotAuthority?: EventAuthority;
  nextRevision: number;
  generation: number;
  initialRequestSeq: number;
  configRequestSeq: number;
  statusRequestSeq: number;
  errorRequestSeq: number;
  configSnapshotEpoch: number;
  actionRequestSeq: Record<string, number>;
  lifecycleRequestSeq: Record<string, number>;
  mutationRequestSeq: Record<string, number>;
  targetCrudRequestSeq: Record<string, number>;
}

function actionKey(name: string, action: StrategyAction): string {
  return `${name}:${action}`;
}

function mutationKey(name: string, action: StrategyMutationAction): string {
  return JSON.stringify([name, action]);
}

function eventAuthority(timestamp?: number, receivedAt?: number): EventAuthority | undefined {
  if (!isFiniteTimestamp(timestamp) && !isFiniteTimestamp(receivedAt)) {
    return undefined;
  }
  if (isFiniteTimestamp(timestamp) && !isFiniteTimestamp(receivedAt)) {
    return timestamp;
  }
  return {
    timestamp: isFiniteTimestamp(timestamp) ? timestamp : undefined,
    receivedAt: isFiniteTimestamp(receivedAt) ? receivedAt : undefined,
  };
}

function normalizeAuthority(authority: EventAuthority | undefined): Exclude<EventAuthority, number> | undefined {
  if (typeof authority === 'number') {
    return { timestamp: authority };
  }
  return authority;
}

function preferredAuthorityValue(authority: Exclude<EventAuthority, number>): number | undefined {
  return authority.timestamp ?? authority.receivedAt;
}

function compareAuthority(
  incoming: EventAuthority | undefined,
  existing: EventAuthority | undefined,
): AuthorityComparison {
  const incomingAuthority = normalizeAuthority(incoming);
  const existingAuthority = normalizeAuthority(existing);
  if (!incomingAuthority || !existingAuthority) {
    return 'unordered';
  }
  const incomingValue = preferredAuthorityValue(incomingAuthority);
  const existingValue = preferredAuthorityValue(existingAuthority);
  if (incomingValue === undefined || existingValue === undefined) {
    return 'ambiguous';
  }
  if (incomingValue < existingValue) return 'older';
  if (incomingValue > existingValue) return 'newer';
  return 'equal';
}

function sameAuthority(left: EventAuthority | undefined, right: EventAuthority | undefined): boolean {
  return compareAuthority(left, right) === 'equal';
}

function authorityMatches(left: EventAuthority | undefined, right: EventAuthority | undefined): boolean {
  if (left === undefined || right === undefined) {
    return left === right;
  }
  return sameAuthority(left, right);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isRuntimeSummary(value: unknown): value is StrategyRuntimeSummary {
  return isRecord(value)
    && typeof value.name === 'string'
    && typeof value.status === 'string'
    && (value.error === undefined || typeof value.error === 'string');
}

function isRuntimeSummaryArray(value: unknown): value is StrategyRuntimeSummary[] {
  return Array.isArray(value) && value.every(isRuntimeSummary);
}

function isStrategyParameterValue(value: unknown): value is StrategyConfig['params'][string] {
  return value === null
    || typeof value === 'string'
    || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isFinite(value));
}

function isStrategyConfig(value: unknown): value is StrategyConfig {
  return isRecord(value)
    && typeof value.name === 'string'
    && typeof value.strategy_type === 'string'
    && typeof value.symbol === 'string'
    && typeof value.timeframe === 'string'
    && typeof value.enabled === 'boolean'
    && isRecord(value.params)
    && Object.values(value.params).every(isStrategyParameterValue)
    && typeof value.created_at === 'number'
    && Number.isFinite(value.created_at)
    && typeof value.updated_at === 'number'
    && Number.isFinite(value.updated_at);
}

function isStrategyConfigArray(value: unknown): value is StrategyConfig[] {
  return Array.isArray(value) && value.every(isStrategyConfig);
}

function validateRestConfigs(value: unknown): StrategyConfig[] {
  if (!isStrategyConfigArray(value)) {
    throw new Error('Invalid strategy config response');
  }
  return value;
}

function validateRestStatuses(value: unknown): StrategyRuntimeSummary[] {
  if (!isRuntimeSummaryArray(value)) {
    throw new Error('Invalid strategy status response');
  }
  return value;
}

function validateRestConfig(value: unknown): StrategyConfig {
  if (!isStrategyConfig(value)) {
    throw new Error('Invalid strategy config response');
  }
  return value;
}

function isErrorMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((error) => typeof error === 'string');
}

function sameConfig(left: StrategyConfig, right: StrategyConfig): boolean {
  return left.name === right.name
    && left.strategy_type === right.strategy_type
    && left.symbol === right.symbol
    && left.timeframe === right.timeframe
    && left.enabled === right.enabled
    && left.created_at === right.created_at
    && left.updated_at === right.updated_at
    && JSON.stringify(left.params) === JSON.stringify(right.params);
}

function upsertConfig(configs: StrategyConfig[], config: StrategyConfig): StrategyConfig[] {
  const existingIndex = configs.findIndex((existing) => existing.name === config.name);
  if (existingIndex === -1) {
    return [...configs, config];
  }
  const existing = configs[existingIndex];
  if (config.updated_at <= existing.updated_at) {
    return configs;
  }
  return configs.map((current, index) => (index === existingIndex ? config : current));
}

function replaceConfigByName(configs: StrategyConfig[], config: StrategyConfig): StrategyConfig[] {
  const existingIndex = configs.findIndex((existing) => existing.name === config.name);
  if (existingIndex === -1) {
    return [...configs, config];
  }
  return configs.map((current, index) => (index === existingIndex ? config : current));
}

function isFiniteTimestamp(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function removeRecordKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const { [key]: _removed, ...remaining } = record;
  return remaining;
}

function revisionAt(revisions: Record<string, number>, name: string): number {
  return revisions[name] ?? 0;
}

function revisionsByName(names: Iterable<string>, revisions: Record<string, number>): Record<string, number> {
  return Object.fromEntries([...names].map((name) => [name, revisionAt(revisions, name)]));
}

function canonicalStatus(status: StrategyRuntimeSummary): StrategyRuntimeSummary {
  return { name: status.name, status: status.status };
}

function sameStatus(left: StrategyRuntimeSummary | undefined, right: StrategyRuntimeSummary | undefined): boolean {
  return left?.name === right?.name && left?.status === right?.status;
}

function statusesByName(statuses: StrategyRuntimeSummary[]): Record<string, StrategyRuntimeSummary> {
  return Object.fromEntries(statuses.map((status) => [status.name, canonicalStatus(status)]));
}

export const useStrategiesStore = defineStore('strategies', {
  state: (): StrategiesState => ({
    definitions: [],
    configs: [],
    statuses: {},
    errors: {},
    loadingInitial: false,
    actionLoading: {},
    mutationErrors: {},
    mutationLoading: {},
    error: null,
    reconciliationError: null,
    configReconciliationError: null,
    statusReconciliationError: null,
    configRevisions: {},
    configTombstones: {},
    configAuthorities: {},
    configDeletionAuthorities: {},
    runtimeBarriers: {},
    statusRevisions: {},
    statusAuthorities: {},
    statusSnapshotAuthority: undefined,
    errorRevisions: {},
    errorAuthorities: {},
    errorSnapshotAuthority: undefined,
    nextRevision: 1,
    generation: 1,
    initialRequestSeq: 0,
    configRequestSeq: 0,
    statusRequestSeq: 0,
    errorRequestSeq: 0,
    configSnapshotEpoch: 0,
    actionRequestSeq: {},
    lifecycleRequestSeq: {},
    mutationRequestSeq: {},
    targetCrudRequestSeq: {},
  }),
  getters: {
    runtimeSummaries: (state) => Object.values(state.statuses),
    activeStrategyCount: (state) => Object.values(state.statuses)
      .filter((strategy) => strategy.status === 'running').length,
  },
  actions: {
    reset() {
      this.generation += 1;
      this.definitions = [];
      this.configs = [];
      this.statuses = {};
      this.errors = {};
      this.loadingInitial = false;
      this.actionLoading = {};
      this.mutationErrors = {};
      this.mutationLoading = {};
      this.error = null;
      this.reconciliationError = null;
      this.configReconciliationError = null;
      this.statusReconciliationError = null;
      this.configRevisions = {};
      this.configTombstones = {};
      this.configAuthorities = {};
      this.configDeletionAuthorities = {};
      this.runtimeBarriers = {};
      this.statusRevisions = {};
      this.statusAuthorities = {};
      this.statusSnapshotAuthority = undefined;
      this.errorRevisions = {};
      this.errorAuthorities = {};
      this.errorSnapshotAuthority = undefined;
      this.nextRevision = 1;
      this.initialRequestSeq = 0;
      this.configRequestSeq = 0;
      this.statusRequestSeq = 0;
      this.errorRequestSeq = 0;
      this.configSnapshotEpoch = 0;
      this.actionRequestSeq = {};
      this.lifecycleRequestSeq = {};
      this.mutationRequestSeq = {};
      this.targetCrudRequestSeq = {};
    },
    syncReconciliationError() {
      this.reconciliationError = this.configReconciliationError ?? this.statusReconciliationError;
    },
    nextChangeRevision(): number {
      const revision = this.nextRevision;
      this.nextRevision += 1;
      return revision;
    },
    markConfigChanged(name: string): number {
      const revision = this.nextChangeRevision();
      this.configRevisions = { ...this.configRevisions, [name]: revision };
      return revision;
    },
    markStatusChanged(name: string): number {
      const revision = this.nextChangeRevision();
      this.statusRevisions = { ...this.statusRevisions, [name]: revision };
      return revision;
    },
    markErrorChanged(name: string): number {
      const revision = this.nextChangeRevision();
      this.errorRevisions = { ...this.errorRevisions, [name]: revision };
      return revision;
    },
    isActionLoading(name: string, action: StrategyAction): boolean {
      return Boolean(this.actionLoading[actionKey(name, action)]);
    },
    mutationError(name: string, action: StrategyMutationAction): string | null {
      return this.mutationErrors[mutationKey(name, action)] ?? null;
    },
    isMutationLoading(name: string, action: StrategyMutationAction): boolean {
      return Boolean(this.mutationLoading[mutationKey(name, action)]);
    },
    finishMutation(name: string, action: StrategyMutationAction, requestSeq: number) {
      const key = mutationKey(name, action);
      if (this.mutationRequestSeq[key] === requestSeq) {
        this.mutationLoading = removeRecordKey(this.mutationLoading, key);
      }
    },
    beginTargetCrud(name: string): number {
      const requestSeq = (this.targetCrudRequestSeq[name] ?? 0) + 1;
      this.targetCrudRequestSeq = { ...this.targetCrudRequestSeq, [name]: requestSeq };
      return requestSeq;
    },
    isTargetCrudCurrent(name: string, requestSeq: number): boolean {
      return this.targetCrudRequestSeq[name] === requestSeq;
    },
    beginMutation(name: string, action: StrategyMutationAction): number {
      const key = mutationKey(name, action);
      const requestSeq = (this.mutationRequestSeq[key] ?? 0) + 1;
      this.mutationRequestSeq = { ...this.mutationRequestSeq, [key]: requestSeq };
      this.mutationErrors = removeRecordKey(this.mutationErrors, key);
      this.mutationLoading = { ...this.mutationLoading, [key]: true };
      return requestSeq;
    },
    recordMutationFailure(
      name: string,
      action: StrategyMutationAction,
      requestSeq: number,
      generation: number,
      error: unknown,
    ) {
      const key = mutationKey(name, action);
      if (this.generation !== generation || this.mutationRequestSeq[key] !== requestSeq) {
        return;
      }
      this.mutationErrors = {
        ...this.mutationErrors,
        [key]: errorMessage(error, `Failed to ${action} strategy`),
      };
    },
    invalidateMutations(name: string) {
      let nextErrors = this.mutationErrors;
      let nextLoading = this.mutationLoading;
      let nextRequestSeq = this.mutationRequestSeq;
      for (const action of STRATEGY_MUTATION_ACTIONS) {
        const key = mutationKey(name, action);
        nextErrors = removeRecordKey(nextErrors, key);
        nextLoading = removeRecordKey(nextLoading, key);
        nextRequestSeq = {
          ...nextRequestSeq,
          [key]: (nextRequestSeq[key] ?? 0) + 1,
        };
      }
      this.mutationErrors = nextErrors;
      this.mutationLoading = nextLoading;
      this.mutationRequestSeq = nextRequestSeq;
    },
    configRevisionSnapshot(): Record<string, number> {
      const names = new Set([
        ...this.configs.map((config) => config.name),
        ...Object.keys(this.configRevisions),
        ...Object.keys(this.configTombstones),
      ]);
      return revisionsByName(names, this.configRevisions);
    },
    statusRevisionSnapshot(): Record<string, number> {
      const names = new Set([
        ...Object.keys(this.statuses),
        ...Object.keys(this.statusRevisions),
      ]);
      return revisionsByName(names, this.statusRevisions);
    },
    errorRevisionSnapshot(): Record<string, number> {
      const names = new Set([
        ...Object.keys(this.errors),
        ...Object.keys(this.errorRevisions),
      ]);
      return revisionsByName(names, this.errorRevisions);
    },
    configRequestGuard(name: string): { revision: number; tombstone: number; snapshotEpoch: number } {
      return {
        revision: revisionAt(this.configRevisions, name),
        tombstone: this.configTombstones[name] ?? 0,
        snapshotEpoch: this.configSnapshotEpoch,
      };
    },
    isConfigRequestCurrent(name: string, guard: { revision: number; tombstone: number; snapshotEpoch: number }): boolean {
      return revisionAt(this.configRevisions, name) === guard.revision
        && (this.configTombstones[name] ?? 0) === guard.tombstone
        && this.configSnapshotEpoch === guard.snapshotEpoch;
    },
    isLifecycleRequestCurrent(
      name: string,
      key: string,
      requestSeq: number,
      lifecycleSeq: number,
      configRevision: number,
      configTombstone: number,
      statusRevision: number,
      errorRevision?: number,
      errorAuthority?: EventAuthority,
      options: {
        configSnapshotEpoch?: number;
        statusAuthority?: EventAuthority;
        statusSnapshotAuthority?: EventAuthority;
        errorSnapshotAuthority?: EventAuthority;
        checkSnapshotAuthorities?: boolean;
      } = {},
    ): boolean {
      return this.actionRequestSeq[key] === requestSeq
        && this.lifecycleRequestSeq[name] === lifecycleSeq
        && revisionAt(this.configRevisions, name) === configRevision
        && (this.configTombstones[name] ?? 0) === configTombstone
        && (options.configSnapshotEpoch === undefined || this.configSnapshotEpoch === options.configSnapshotEpoch)
        && revisionAt(this.statusRevisions, name) === statusRevision
        && (!options.checkSnapshotAuthorities || authorityMatches(this.statusAuthorities[name], options.statusAuthority))
        && (!options.checkSnapshotAuthorities || authorityMatches(this.statusSnapshotAuthority, options.statusSnapshotAuthority))
        && (errorRevision === undefined || revisionAt(this.errorRevisions, name) === errorRevision)
        && (errorRevision === undefined || authorityMatches(this.errorAuthorities[name], errorAuthority))
        && (!options.checkSnapshotAuthorities || authorityMatches(this.errorSnapshotAuthority, options.errorSnapshotAuthority));
    },
    applyRestConfigs(configs: StrategyConfig[], requestRevisions: Record<string, number>) {
      const namesFromResponse = new Set(configs.map((config) => config.name));
      let nextConfigs = this.configs;
      for (const existing of this.configs) {
        if (namesFromResponse.has(existing.name)
          || revisionAt(this.configRevisions, existing.name) !== revisionAt(requestRevisions, existing.name)) {
          continue;
        }
        nextConfigs = nextConfigs.filter((config) => config.name !== existing.name);
        const revision = this.markConfigChanged(existing.name);
        this.configTombstones = { ...this.configTombstones, [existing.name]: revision };
        this.configDeletionAuthorities = {
          ...this.configDeletionAuthorities,
          [existing.name]: { deletedUpdatedAt: existing.updated_at },
        };
        this.configAuthorities = removeRecordKey(this.configAuthorities, existing.name);
      }
      for (const config of configs) {
        if (revisionAt(this.configRevisions, config.name) !== revisionAt(requestRevisions, config.name)) {
          continue;
        }
        if ((this.configTombstones[config.name] ?? 0) > revisionAt(requestRevisions, config.name)) {
          continue;
        }
        const updatedConfigs = upsertConfig(nextConfigs, config);
        const clearsTombstone = this.configTombstones[config.name] !== undefined;
        const hadRuntimeBarrier = this.runtimeBarriers[config.name] !== undefined;
        if (updatedConfigs !== nextConfigs || clearsTombstone) {
          nextConfigs = updatedConfigs;
          this.configTombstones = removeRecordKey(this.configTombstones, config.name);
          this.configDeletionAuthorities = removeRecordKey(this.configDeletionAuthorities, config.name);
          this.configAuthorities = { ...this.configAuthorities, [config.name]: config.updated_at };
          if (clearsTombstone || hadRuntimeBarrier) {
            this.recordRuntimeBarrier(config.name, config.updated_at);
          }
          this.markConfigChanged(config.name);
        }
      }
      this.configs = nextConfigs;
    },
    applyRestStatuses(
      statuses: StrategyRuntimeSummary[],
      statusRequestRevisions: Record<string, number>,
      errorRequestRevisions: Record<string, number>,
      applyStatuses = true,
      applyErrors = true,
    ) {
      const namesFromResponse = new Set(statuses.map((status) => status.name));
      if (applyStatuses) {
        this.statusSnapshotAuthority = undefined;
        for (const name of Object.keys(this.statuses)) {
          if (!namesFromResponse.has(name) && revisionAt(this.statusRevisions, name) === revisionAt(statusRequestRevisions, name)) {
            const { [name]: _removed, ...remainingStatuses } = this.statuses;
            this.statuses = remainingStatuses;
            this.statusAuthorities = removeRecordKey(this.statusAuthorities, name);
            this.markStatusChanged(name);
          }
        }
        for (const status of statuses) {
          if (revisionAt(this.statusRevisions, status.name) === revisionAt(statusRequestRevisions, status.name)) {
            const canonical = canonicalStatus(status);
            if (!sameStatus(this.statuses[status.name], canonical)) {
              this.statuses = { ...this.statuses, [status.name]: canonical };
              this.statusAuthorities = removeRecordKey(this.statusAuthorities, status.name);
              this.markStatusChanged(status.name);
            }
          }
        }
      }
      if (applyErrors) {
        this.errorSnapshotAuthority = undefined;
        for (const name of Object.keys(this.errors)) {
          if (!namesFromResponse.has(name) && revisionAt(this.errorRevisions, name) === revisionAt(errorRequestRevisions, name)) {
            const { [name]: _removed, ...remainingErrors } = this.errors;
            this.errors = remainingErrors;
            this.errorAuthorities = removeRecordKey(this.errorAuthorities, name);
            this.markErrorChanged(name);
          }
        }
        for (const status of statuses) {
          if (revisionAt(this.errorRevisions, status.name) !== revisionAt(errorRequestRevisions, status.name)) {
            continue;
          }
          const nextError = status.error;
          const currentError = this.errors[status.name];
          if (nextError === currentError) {
            continue;
          }
          if (nextError) {
            this.errors = { ...this.errors, [status.name]: nextError };
            this.errorAuthorities = removeRecordKey(this.errorAuthorities, status.name);
          } else {
            const { [status.name]: _cleared, ...remainingErrors } = this.errors;
            this.errors = remainingErrors;
            this.errorAuthorities = removeRecordKey(this.errorAuthorities, status.name);
          }
          this.markErrorChanged(status.name);
        }
      }
    },
    async loadInitialData() {
      const generation = this.generation;
      const initialRequestSeq = this.initialRequestSeq + 1;
      const configRequestSeq = this.configRequestSeq + 1;
      const statusRequestSeq = this.statusRequestSeq + 1;
      const errorRequestSeq = this.errorRequestSeq + 1;
      this.initialRequestSeq = initialRequestSeq;
      this.configRequestSeq = configRequestSeq;
      this.statusRequestSeq = statusRequestSeq;
      this.errorRequestSeq = errorRequestSeq;
      this.loadingInitial = true;
      this.error = null;
      const configRequestRevisions = this.configRevisionSnapshot();
      const statusRequestRevisions = this.statusRevisionSnapshot();
      const errorRequestRevisions = this.errorRevisionSnapshot();
      try {
        const [definitionsResult, configsResult, statusesResult] = await Promise.allSettled([
          listStrategyTypes(),
          listStrategyConfigs(),
          listStrategies(),
        ]);
        if (this.generation !== generation || this.initialRequestSeq !== initialRequestSeq) {
          return;
        }

        let validConfigs: StrategyConfig[] | null = null;
        let validStatuses: StrategyRuntimeSummary[] | null = null;
        const validationFailures: PromiseRejectedResult[] = [];
        if (configsResult.status === 'fulfilled') {
          try {
            validConfigs = validateRestConfigs(configsResult.value);
          } catch (reason) {
            validationFailures.push({ status: 'rejected', reason });
          }
        }
        if (statusesResult.status === 'fulfilled') {
          try {
            validStatuses = validateRestStatuses(statusesResult.value);
          } catch (reason) {
            validationFailures.push({ status: 'rejected', reason });
          }
        }

        const failures = [definitionsResult, configsResult, statusesResult]
          .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
          .concat(validationFailures);
        if (failures.length > 0) {
          this.error = errorMessage(failures[0].reason, 'Failed to load strategy data');
        }

        if (definitionsResult.status === 'fulfilled') {
          this.definitions = definitionsResult.value;
        }
        if (validConfigs !== null && this.configRequestSeq === configRequestSeq) {
          this.applyRestConfigs(validConfigs, configRequestRevisions);
          this.configReconciliationError = null;
          this.syncReconciliationError();
        }
        if (validStatuses !== null
          && (this.statusRequestSeq === statusRequestSeq || this.errorRequestSeq === errorRequestSeq)) {
          this.applyRestStatuses(
            validStatuses,
            statusRequestRevisions,
            errorRequestRevisions,
            this.statusRequestSeq === statusRequestSeq,
            this.errorRequestSeq === errorRequestSeq,
          );
          this.statusReconciliationError = null;
          this.syncReconciliationError();
        }
      } finally {
        if (this.generation === generation && this.initialRequestSeq === initialRequestSeq) {
          this.loadingInitial = false;
        }
      }
    },
    async refreshConfigsForReconciliation() {
      const generation = this.generation;
      const configRequestSeq = this.configRequestSeq + 1;
      this.configRequestSeq = configRequestSeq;
      const configRequestRevisions = this.configRevisionSnapshot();
      try {
        const configs = validateRestConfigs(await listStrategyConfigs());
        if (this.generation !== generation || this.configRequestSeq !== configRequestSeq) {
          return;
        }
        this.applyRestConfigs(configs, configRequestRevisions);
        this.configReconciliationError = null;
        this.syncReconciliationError();
      } catch (error) {
        if (this.generation === generation && this.configRequestSeq === configRequestSeq) {
          this.configReconciliationError = errorMessage(error, 'Failed to reconcile strategy configs');
          this.syncReconciliationError();
        }
      }
    },
    async refreshStatusesForReconciliation() {
      const generation = this.generation;
      const statusRequestSeq = this.statusRequestSeq + 1;
      const errorRequestSeq = this.errorRequestSeq + 1;
      this.statusRequestSeq = statusRequestSeq;
      this.errorRequestSeq = errorRequestSeq;
      const statusRequestRevisions = this.statusRevisionSnapshot();
      const errorRequestRevisions = this.errorRevisionSnapshot();
      try {
        const statuses = validateRestStatuses(await listStrategies());
        if (this.generation !== generation) {
          return;
        }
        if (this.statusRequestSeq !== statusRequestSeq && this.errorRequestSeq !== errorRequestSeq) {
          return;
        }
        this.applyRestStatuses(
          statuses,
          statusRequestRevisions,
          errorRequestRevisions,
          this.statusRequestSeq === statusRequestSeq,
          this.errorRequestSeq === errorRequestSeq,
        );
        this.statusReconciliationError = null;
        this.syncReconciliationError();
      } catch (error) {
        if (this.generation === generation
          && (this.statusRequestSeq === statusRequestSeq || this.errorRequestSeq === errorRequestSeq)) {
          this.statusReconciliationError = errorMessage(error, 'Failed to reconcile strategy statuses');
          this.syncReconciliationError();
        }
      }
    },
    recordRuntimeBarrier(name: string, authority: EventAuthority | undefined) {
      if (authority === undefined) {
        return;
      }
      const existing = this.runtimeBarriers[name];
      const comparison = compareAuthority(authority, existing);
      if (existing === undefined || comparison === 'newer') {
        this.runtimeBarriers = { ...this.runtimeBarriers, [name]: authority };
      }
    },
    isRuntimeEventAfterBarrier(name: string, authority: EventAuthority | undefined): boolean {
      if (this.configTombstones[name] !== undefined) {
        return false;
      }
      const barrier = this.runtimeBarriers[name];
      if (barrier === undefined) {
        return true;
      }
      return compareAuthority(authority, barrier) === 'newer';
    },
    isConfigEventAfterTombstone(config: StrategyConfig, timestamp?: number, receivedAt?: number): boolean | null {
      const deletionAuthority = this.configDeletionAuthorities[config.name];
      if (!deletionAuthority) {
        return true;
      }
      if (deletionAuthority.deletedUpdatedAt !== undefined && config.updated_at <= deletionAuthority.deletedUpdatedAt) {
        return false;
      }
      if (isFiniteTimestamp(timestamp)) {
        if (deletionAuthority.deleteTimestamp !== undefined && timestamp <= deletionAuthority.deleteTimestamp) {
          return false;
        }
        if (deletionAuthority.deleteTimestamp === undefined || deletionAuthority.deletedUpdatedAt === undefined) {
          return null;
        }
        return true;
      }
      const incomingAuthority = eventAuthority(undefined, receivedAt);
      if (this.runtimeBarriers[config.name] !== undefined
        && compareAuthority(incomingAuthority, this.runtimeBarriers[config.name]) === 'newer') {
        return true;
      }
      return null;
    },
    applyConfig(config: StrategyConfig, options: { timestamp?: number; received_at?: number; authoritativeMutation?: boolean } = {}) {
      if (!options.authoritativeMutation && this.configTombstones[config.name] !== undefined
        && !this.configs.some((existing) => existing.name === config.name)) {
        const comparison = this.isConfigEventAfterTombstone(config, options.timestamp, options.received_at);
        if (comparison !== true) {
          if (comparison === null) {
            void this.refreshConfigsForReconciliation();
          }
          return;
        }
      }
      const existingConfig = this.configs.find((existing) => existing.name === config.name);
      if (!options.authoritativeMutation
        && existingConfig !== undefined
        && config.updated_at === existingConfig.updated_at
        && !sameConfig(existingConfig, config)) {
        void this.refreshConfigsForReconciliation();
        return;
      }
      const hadTombstone = this.configTombstones[config.name] !== undefined;
      const hadRuntimeBarrier = this.runtimeBarriers[config.name] !== undefined;
      const nextConfigs = options.authoritativeMutation
        ? replaceConfigByName(this.configs, config)
        : upsertConfig(this.configs, config);
      if (nextConfigs !== this.configs) {
        this.configs = nextConfigs;
        this.configTombstones = removeRecordKey(this.configTombstones, config.name);
        this.configDeletionAuthorities = removeRecordKey(this.configDeletionAuthorities, config.name);
        this.configAuthorities = { ...this.configAuthorities, [config.name]: config.updated_at };
        if (hadTombstone || hadRuntimeBarrier) {
          this.recordRuntimeBarrier(config.name, eventAuthority(options.timestamp, options.received_at) ?? config.updated_at);
        }
        this.markConfigChanged(config.name);
      }
    },
    removeConfig(name: string, timestamp?: number, fromWebSocket = false, receivedAt?: number) {
      const startKey = actionKey(name, 'start');
      const stopKey = actionKey(name, 'stop');
      const existingConfig = this.configs.find((config) => config.name === name);
      const incomingAuthority = eventAuthority(timestamp, receivedAt);
      if (fromWebSocket && existingConfig && !isFiniteTimestamp(timestamp) && incomingAuthority === undefined) {
        void this.refreshConfigsForReconciliation();
        return;
      }
      if (fromWebSocket && existingConfig && isFiniteTimestamp(timestamp)) {
        if (timestamp <= existingConfig.updated_at) {
          if (timestamp === existingConfig.updated_at) {
            void this.refreshConfigsForReconciliation();
          }
          return;
        }
      }
      const hasConfig = existingConfig !== undefined;
      const hasStatus = this.statuses[name] !== undefined;
      const hasError = this.errors[name] !== undefined;
      const hasLoading = this.actionLoading[startKey] !== undefined || this.actionLoading[stopKey] !== undefined;
      const existingDeletionAuthority = this.configDeletionAuthorities[name];
      if (!hasConfig && !hasStatus && !hasError && !hasLoading && this.configTombstones[name] !== undefined) {
        if (isFiniteTimestamp(timestamp)
          && (existingDeletionAuthority?.deleteTimestamp === undefined || timestamp > existingDeletionAuthority.deleteTimestamp)) {
          const revision = this.markConfigChanged(name);
          this.configTombstones = { ...this.configTombstones, [name]: revision };
          this.configDeletionAuthorities = {
            ...this.configDeletionAuthorities,
            [name]: { ...existingDeletionAuthority, deleteTimestamp: timestamp },
          };
          this.recordRuntimeBarrier(name, incomingAuthority);
        }
        return;
      }

      if (hasConfig) {
        this.configs = this.configs.filter((config) => config.name !== name);
        this.configAuthorities = removeRecordKey(this.configAuthorities, name);
      }
      const revision = this.markConfigChanged(name);
      this.configTombstones = { ...this.configTombstones, [name]: revision };
      this.configDeletionAuthorities = {
        ...this.configDeletionAuthorities,
        [name]: {
          deletedUpdatedAt: existingConfig?.updated_at ?? existingDeletionAuthority?.deletedUpdatedAt,
          deleteTimestamp: isFiniteTimestamp(timestamp) ? timestamp : existingDeletionAuthority?.deleteTimestamp,
        },
      };
      this.recordRuntimeBarrier(name, incomingAuthority);
      if (hasStatus) {
        this.statuses = removeRecordKey(this.statuses, name);
        this.statusAuthorities = removeRecordKey(this.statusAuthorities, name);
        this.markStatusChanged(name);
      }
      if (hasError) {
        this.errors = removeRecordKey(this.errors, name);
        this.errorAuthorities = removeRecordKey(this.errorAuthorities, name);
        this.markErrorChanged(name);
      }
      const { [startKey]: _removedStart, [stopKey]: _removedStop, ...remainingLoading } = this.actionLoading;
      this.actionLoading = remainingLoading;
      this.invalidateMutations(name);
    },
    applyStatus(
      name: string,
      status: StrategyStatus,
      error?: string,
      options: { timestamp?: number; received_at?: number; fromWebSocket?: boolean } = {},
    ) {
      const nextStatus = { name, status };
      const sameValue = sameStatus(this.statuses[name], nextStatus);
      const incomingAuthority = eventAuthority(options.timestamp, options.received_at);
      const existingAuthority = this.statusAuthorities[name] ?? this.statusSnapshotAuthority;

      if (options.fromWebSocket) {
        if (!this.isRuntimeEventAfterBarrier(name, incomingAuthority)) {
          void this.refreshStatusesForReconciliation();
          return;
        }
        const comparison = compareAuthority(incomingAuthority, existingAuthority);
        if (comparison === 'older') {
          if (!sameValue) {
            void this.refreshStatusesForReconciliation();
          }
          return;
        }
        if (comparison === 'equal' || comparison === 'ambiguous') {
          if (!sameValue) {
            void this.refreshStatusesForReconciliation();
          }
          return;
        }
        if (comparison === 'unordered' && existingAuthority !== undefined) {
          if (!sameValue) {
            void this.refreshStatusesForReconciliation();
          }
          return;
        }
      }

      const authorityChanged = incomingAuthority !== undefined
        && !sameAuthority(this.statusAuthorities[name], incomingAuthority);
      if (!sameValue) {
        this.statuses = { ...this.statuses, [name]: nextStatus };
      }
      if (incomingAuthority !== undefined) {
        this.statusAuthorities = { ...this.statusAuthorities, [name]: incomingAuthority };
      }
      if (!sameValue || authorityChanged) {
        this.markStatusChanged(name);
      }
      if (error !== undefined) {
        this.applyStrategyError(name, error);
      }
    },
    clearStrategyError(name: string, expectedRevision?: number, expectedAuthority?: EventAuthority) {
      if (expectedRevision !== undefined && revisionAt(this.errorRevisions, name) !== expectedRevision) {
        return;
      }
      if (expectedAuthority !== undefined && !sameAuthority(this.errorAuthorities[name], expectedAuthority)) {
        return;
      }
      if (this.errors[name] === undefined) {
        return;
      }
      this.errors = removeRecordKey(this.errors, name);
      this.errorAuthorities = removeRecordKey(this.errorAuthorities, name);
      this.markErrorChanged(name);
    },
    applyStrategyError(name: string, error: string, options: { timestamp?: number; received_at?: number; fromWebSocket?: boolean } = {}) {
      const incomingAuthority = eventAuthority(options.timestamp, options.received_at);
      const existingAuthority = this.errorAuthorities[name] ?? this.errorSnapshotAuthority;
      const sameError = this.errors[name] === error;
      const comparison = compareAuthority(incomingAuthority, existingAuthority);

      if (options.fromWebSocket && !this.isRuntimeEventAfterBarrier(name, incomingAuthority)) {
        void this.refreshStatusesForReconciliation();
        return;
      }
      if (comparison === 'older') {
        if (!sameError) {
          void this.refreshStatusesForReconciliation();
        }
        return;
      }
      if (comparison === 'equal' || comparison === 'ambiguous') {
        if (!sameError) {
          void this.refreshStatusesForReconciliation();
        }
        return;
      }
      if (comparison === 'unordered' && existingAuthority !== undefined) {
        if (!sameError) {
          void this.refreshStatusesForReconciliation();
        }
        return;
      }
      if (sameError && incomingAuthority === undefined) {
        return;
      }

      this.errors = {
        ...this.errors,
        [name]: error,
      };
      this.errorAuthorities = incomingAuthority === undefined
        ? removeRecordKey(this.errorAuthorities, name)
        : { ...this.errorAuthorities, [name]: incomingAuthority };
      this.markErrorChanged(name);
    },
    applySnapshot(
      snapshot: StrategySnapshot | Record<string, unknown>,
      options: { received_at?: number } = {},
    ) {
      if (isStrategyConfigArray(snapshot.strategy_configs)) {
        this.configRequestSeq += 1;
        this.configSnapshotEpoch += 1;
        const namesFromSnapshot = new Set(snapshot.strategy_configs.map((config) => config.name));
        let nextConfigs = this.configs;
        for (const existing of this.configs) {
          if (namesFromSnapshot.has(existing.name)) {
            continue;
          }
          nextConfigs = nextConfigs.filter((config) => config.name !== existing.name);
          const revision = this.markConfigChanged(existing.name);
          this.configTombstones = { ...this.configTombstones, [existing.name]: revision };
          this.configDeletionAuthorities = {
            ...this.configDeletionAuthorities,
            [existing.name]: { deletedUpdatedAt: existing.updated_at },
          };
          this.configAuthorities = removeRecordKey(this.configAuthorities, existing.name);
          this.recordRuntimeBarrier(existing.name, eventAuthority(undefined, options.received_at));
        }
        for (const config of snapshot.strategy_configs) {
          const updatedConfigs = upsertConfig(nextConfigs, config);
          const clearsTombstone = this.configTombstones[config.name] !== undefined;
          const hadRuntimeBarrier = this.runtimeBarriers[config.name] !== undefined;
          if (updatedConfigs !== nextConfigs || clearsTombstone) {
            nextConfigs = updatedConfigs;
            this.configTombstones = removeRecordKey(this.configTombstones, config.name);
            this.configDeletionAuthorities = removeRecordKey(this.configDeletionAuthorities, config.name);
            this.configAuthorities = { ...this.configAuthorities, [config.name]: config.updated_at };
            if (clearsTombstone || hadRuntimeBarrier) {
              this.recordRuntimeBarrier(config.name, eventAuthority(undefined, options.received_at) ?? config.updated_at);
            }
            this.markConfigChanged(config.name);
          }
        }
        this.configs = nextConfigs;
      }
      if (isRuntimeSummaryArray(snapshot.strategies)) {
        this.statusRequestSeq += 1;
        this.errorRequestSeq += 1;
        const snapshotAuthority = eventAuthority(undefined, options.received_at);
        const authorityChanged = snapshotAuthority !== undefined
          && !sameAuthority(this.statusSnapshotAuthority, snapshotAuthority);
        const nextStatuses = statusesByName(snapshot.strategies);
        for (const name of new Set([...Object.keys(this.statuses), ...Object.keys(nextStatuses)])) {
          if (!sameStatus(this.statuses[name], nextStatuses[name])
            || authorityChanged
            || this.statusAuthorities[name] !== undefined) {
            this.markStatusChanged(name);
          }
        }
        this.statuses = nextStatuses;
        this.statusAuthorities = {};
        this.statusSnapshotAuthority = snapshotAuthority;
        for (const status of snapshot.strategies) {
          if (status.error) {
            this.applyStrategyError(status.name, status.error, { received_at: options.received_at });
          }
        }
      }
      if (isErrorMap(snapshot.strategy_errors)) {
        this.errorRequestSeq += 1;
        const snapshotAuthority = eventAuthority(undefined, options.received_at);
        const authorityChanged = snapshotAuthority !== undefined
          && !sameAuthority(this.errorSnapshotAuthority, snapshotAuthority);
        for (const name of new Set([...Object.keys(this.errors), ...Object.keys(snapshot.strategy_errors)])) {
          if (this.errors[name] !== snapshot.strategy_errors[name]
            || authorityChanged
            || this.errorAuthorities[name] !== undefined) {
            this.markErrorChanged(name);
          }
        }
        this.errors = { ...snapshot.strategy_errors };
        this.errorAuthorities = {};
        this.errorSnapshotAuthority = snapshotAuthority;
      }
    },
    applyWebSocketMessage(message: StrategyWebSocketMessage) {
      switch (message.type) {
        case 'snapshot': {
          const snapshot = isRecord(message.data) ? message.data : message;
          this.applySnapshot(snapshot, {
            received_at: isFiniteTimestamp(message.received_at) ? message.received_at : undefined,
          });
          break;
        }
        case 'strategy_status':
          if (typeof message.strategy === 'string' && typeof message.status === 'string') {
            this.applyStatus(message.strategy, message.status, undefined, {
              timestamp: isFiniteTimestamp(message.timestamp) ? message.timestamp : undefined,
              received_at: isFiniteTimestamp(message.received_at) ? message.received_at : undefined,
              fromWebSocket: true,
            });
          }
          break;
        case 'strategy_error':
          if (typeof message.strategy === 'string' && typeof message.error === 'string') {
            this.applyStrategyError(message.strategy, message.error, {
              timestamp: isFiniteTimestamp(message.timestamp) ? message.timestamp : undefined,
              received_at: isFiniteTimestamp(message.received_at) ? message.received_at : undefined,
              fromWebSocket: true,
            });
          }
          break;
        case 'strategy_config_created':
          if (isStrategyConfig(message.config)) {
            const config = message.config;
            const timestamp = isFiniteTimestamp(message.timestamp) ? message.timestamp : undefined;
            const received_at = isFiniteTimestamp(message.received_at) ? message.received_at : undefined;
            const beforeConfig = this.configs.some((existing) => existing.name === config.name);
            this.applyConfig(config, { timestamp, received_at });
            const afterConfig = this.configs.some((existing) => existing.name === config.name);
            if (!beforeConfig && afterConfig && !this.statuses[config.name]) {
              this.applyStatus(config.name, 'stopped');
            }
          }
          break;
        case 'strategy_config_updated':
          if (isStrategyConfig(message.config)) {
            const timestamp = isFiniteTimestamp(message.timestamp) ? message.timestamp : undefined;
            const received_at = isFiniteTimestamp(message.received_at) ? message.received_at : undefined;
            this.applyConfig(message.config, { timestamp, received_at });
          }
          break;
        case 'strategy_config_deleted':
          if (typeof message.strategy === 'string') {
            const timestamp = isFiniteTimestamp(message.timestamp) ? message.timestamp : undefined;
            const received_at = isFiniteTimestamp(message.received_at) ? message.received_at : undefined;
            this.removeConfig(message.strategy, timestamp, true, received_at);
          }
          break;
        default:
          break;
      }
    },
    async createConfig(config: StrategyConfigPayload): Promise<StrategyConfig> {
      const generation = this.generation;
      const targetName = config.name;
      const targetCrudSeq = this.beginTargetCrud(targetName);
      const mutationSeq = this.beginMutation(targetName, 'create');
      const guard = this.configRequestGuard(targetName);
      try {
        const saved = validateRestConfig(await createStrategyConfig(config));
        if (this.generation !== generation
          || !this.isTargetCrudCurrent(targetName, targetCrudSeq)
          || !this.isConfigRequestCurrent(targetName, guard)) {
          return saved;
        }
        this.applyConfig(saved, { authoritativeMutation: true });
        this.applyStatus(saved.name, this.statuses[saved.name]?.status ?? 'stopped');
        void this.refreshConfigsForReconciliation();
        return saved;
      } catch (error) {
        if (this.isTargetCrudCurrent(targetName, targetCrudSeq)
          && this.isConfigRequestCurrent(targetName, guard)) {
          this.recordMutationFailure(targetName, 'create', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(targetName, 'create', mutationSeq);
      }
    },
    async updateConfig(name: string, config: StrategyConfigPayload): Promise<StrategyConfig> {
      const generation = this.generation;
      const targetCrudSeq = this.beginTargetCrud(name);
      const mutationSeq = this.beginMutation(name, 'update');
      const guard = this.configRequestGuard(name);
      try {
        const saved = validateRestConfig(await updateStrategyConfig(name, config));
        if (this.generation !== generation
          || !this.isTargetCrudCurrent(name, targetCrudSeq)
          || !this.isConfigRequestCurrent(name, guard)) {
          return saved;
        }
        this.applyConfig(saved, { authoritativeMutation: true });
        void this.refreshConfigsForReconciliation();
        return saved;
      } catch (error) {
        if (this.isTargetCrudCurrent(name, targetCrudSeq)
          && this.isConfigRequestCurrent(name, guard)) {
          this.recordMutationFailure(name, 'update', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(name, 'update', mutationSeq);
      }
    },
    async cloneConfig(name: string, request: StrategyCloneRequest): Promise<StrategyConfig> {
      const generation = this.generation;
      const targetName = request.target_name;
      const targetCrudSeq = this.beginTargetCrud(targetName);
      const mutationSeq = this.beginMutation(targetName, 'clone');
      const guard = this.configRequestGuard(targetName);
      try {
        const saved = validateRestConfig(await cloneStrategyConfig(name, request));
        if (this.generation !== generation
          || !this.isTargetCrudCurrent(targetName, targetCrudSeq)
          || !this.isConfigRequestCurrent(targetName, guard)) {
          return saved;
        }
        this.applyConfig(saved, { authoritativeMutation: true });
        this.applyStatus(saved.name, this.statuses[saved.name]?.status ?? 'stopped');
        void this.refreshConfigsForReconciliation();
        return saved;
      } catch (error) {
        if (this.isTargetCrudCurrent(targetName, targetCrudSeq)
          && this.isConfigRequestCurrent(targetName, guard)) {
          this.recordMutationFailure(targetName, 'clone', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(targetName, 'clone', mutationSeq);
      }
    },
    async deleteConfig(name: string): Promise<void> {
      const generation = this.generation;
      const targetCrudSeq = this.beginTargetCrud(name);
      const mutationSeq = this.beginMutation(name, 'delete');
      const guard = this.configRequestGuard(name);
      try {
        await deleteStrategyConfig(name);
        if (this.generation !== generation
          || !this.isTargetCrudCurrent(name, targetCrudSeq)
          || !this.isConfigRequestCurrent(name, guard)) {
          return;
        }
        this.removeConfig(name);
        void this.refreshConfigsForReconciliation();
      } catch (error) {
        if (this.isTargetCrudCurrent(name, targetCrudSeq)
          && this.isConfigRequestCurrent(name, guard)) {
          this.recordMutationFailure(name, 'delete', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(name, 'delete', mutationSeq);
      }
    },
    async start(name: string): Promise<void> {
      const generation = this.generation;
      const key = actionKey(name, 'start');
      const requestSeq = (this.actionRequestSeq[key] ?? 0) + 1;
      const mutationSeq = this.beginMutation(name, 'start');
      const lifecycleSeq = (this.lifecycleRequestSeq[name] ?? 0) + 1;
      const configRevision = revisionAt(this.configRevisions, name);
      const configTombstone = this.configTombstones[name] ?? 0;
      const configSnapshotEpoch = this.configSnapshotEpoch;
      const statusRevision = revisionAt(this.statusRevisions, name);
      const statusAuthority = this.statusAuthorities[name];
      const statusSnapshotAuthority = this.statusSnapshotAuthority;
      const errorRevision = revisionAt(this.errorRevisions, name);
      const errorAuthority = this.errorAuthorities[name];
      const errorSnapshotAuthority = this.errorSnapshotAuthority;
      this.actionRequestSeq = { ...this.actionRequestSeq, [key]: requestSeq };
      this.lifecycleRequestSeq = { ...this.lifecycleRequestSeq, [name]: lifecycleSeq };
      this.actionLoading = { ...this.actionLoading, [key]: true };
      try {
        await startStrategy(name);
        if (this.generation === generation
          && this.isLifecycleRequestCurrent(
            name,
            key,
            requestSeq,
            lifecycleSeq,
            configRevision,
            configTombstone,
            statusRevision,
            errorRevision,
            errorAuthority,
            {
              configSnapshotEpoch,
              statusAuthority,
              statusSnapshotAuthority,
              errorSnapshotAuthority,
              checkSnapshotAuthorities: true,
            },
          )) {
          this.applyStatus(name, 'running');
          this.clearStrategyError(name, errorRevision, errorAuthority);
          void this.refreshStatusesForReconciliation();
        }
      } catch (error) {
        if (this.isLifecycleRequestCurrent(
          name,
          key,
          requestSeq,
          lifecycleSeq,
          configRevision,
          configTombstone,
          statusRevision,
          errorRevision,
          errorAuthority,
          {
            configSnapshotEpoch,
            statusAuthority,
            statusSnapshotAuthority,
            errorSnapshotAuthority,
            checkSnapshotAuthorities: true,
          },
        )) {
          this.recordMutationFailure(name, 'start', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(name, 'start', mutationSeq);
        if (this.generation === generation && this.actionRequestSeq[key] === requestSeq) {
          const { [key]: _finished, ...remaining } = this.actionLoading;
          this.actionLoading = remaining;
        }
      }
    },
    async stop(name: string): Promise<void> {
      const generation = this.generation;
      const key = actionKey(name, 'stop');
      const requestSeq = (this.actionRequestSeq[key] ?? 0) + 1;
      const mutationSeq = this.beginMutation(name, 'stop');
      const lifecycleSeq = (this.lifecycleRequestSeq[name] ?? 0) + 1;
      const configRevision = revisionAt(this.configRevisions, name);
      const configTombstone = this.configTombstones[name] ?? 0;
      const configSnapshotEpoch = this.configSnapshotEpoch;
      const statusRevision = revisionAt(this.statusRevisions, name);
      const statusAuthority = this.statusAuthorities[name];
      const statusSnapshotAuthority = this.statusSnapshotAuthority;
      const errorRevision = revisionAt(this.errorRevisions, name);
      const errorAuthority = this.errorAuthorities[name];
      const errorSnapshotAuthority = this.errorSnapshotAuthority;
      this.actionRequestSeq = { ...this.actionRequestSeq, [key]: requestSeq };
      this.lifecycleRequestSeq = { ...this.lifecycleRequestSeq, [name]: lifecycleSeq };
      this.actionLoading = { ...this.actionLoading, [key]: true };
      try {
        await stopStrategy(name);
        if (this.generation === generation
          && this.isLifecycleRequestCurrent(
            name,
            key,
            requestSeq,
            lifecycleSeq,
            configRevision,
            configTombstone,
            statusRevision,
            errorRevision,
            errorAuthority,
            {
              configSnapshotEpoch,
              statusAuthority,
              statusSnapshotAuthority,
              errorSnapshotAuthority,
              checkSnapshotAuthorities: true,
            },
          )) {
          this.applyStatus(name, 'stopped');
          void this.refreshStatusesForReconciliation();
        }
      } catch (error) {
        if (this.isLifecycleRequestCurrent(
          name,
          key,
          requestSeq,
          lifecycleSeq,
          configRevision,
          configTombstone,
          statusRevision,
          errorRevision,
          errorAuthority,
          {
            configSnapshotEpoch,
            statusAuthority,
            statusSnapshotAuthority,
            errorSnapshotAuthority,
            checkSnapshotAuthorities: true,
          },
        )) {
          this.recordMutationFailure(name, 'stop', mutationSeq, generation, error);
        }
        throw error;
      } finally {
        this.finishMutation(name, 'stop', mutationSeq);
        if (this.generation === generation && this.actionRequestSeq[key] === requestSeq) {
          const { [key]: _finished, ...remaining } = this.actionLoading;
          this.actionLoading = remaining;
        }
      }
    },
  },
});

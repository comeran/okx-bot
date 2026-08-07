import type {
  StrategyConfig,
  StrategyConfigPayload,
  StrategyDefinition,
  StrategyStatus,
  StrategyValidationIssue,
} from '@/types/strategy';

export interface StrategyIssueGroups {
  fields: Record<string, string[]>;
  general: string[];
}

export interface StrategyRowSafety {
  canEdit: boolean;
  canDelete: boolean;
  canStart: boolean;
  canStop: boolean;
}

const COMMON_PATHS = new Set(['name', 'strategy_type', 'symbol', 'timeframe', 'enabled']);

export function buildDefaultStrategyDraft(definition: StrategyDefinition): StrategyConfigPayload {
  return {
    name: '',
    strategy_type: definition.strategy_type,
    symbol: 'BTC-USDT-SWAP',
    timeframe: '1m',
    enabled: false,
    params: Object.fromEntries(definition.params.map((parameter) => [parameter.key, parameter.default ?? null])),
  };
}

export async function switchStrategyType(
  current: StrategyConfigPayload,
  definition: StrategyDefinition,
  dirty: boolean,
  confirmDiscard: () => Promise<boolean>,
): Promise<StrategyConfigPayload> {
  if (current.strategy_type === definition.strategy_type) return current;
  if (dirty && !(await confirmDiscard())) return current;
  const next = buildDefaultStrategyDraft(definition);
  return {
    ...next,
    name: current.name,
    symbol: current.symbol,
    timeframe: current.timeframe,
    enabled: current.enabled,
  };
}

export function fieldIssuesByPath(issues: StrategyValidationIssue[]): StrategyIssueGroups {
  const fields: Record<string, string[]> = {};
  const general: string[] = [];
  for (const issue of issues) {
    if (COMMON_PATHS.has(issue.path) || issue.path.startsWith('params.')) {
      (fields[issue.path] ??= []).push(issue.message);
    } else {
      general.push(issue.message);
    }
  }
  return { fields, general };
}

export function isStrategyFormReadonly(status: StrategyStatus | undefined): boolean {
  return status !== 'stopped';
}

export function buildCloneDraft(config: StrategyConfig): StrategyConfigPayload {
  return {
    name: '',
    strategy_type: config.strategy_type,
    symbol: config.symbol,
    timeframe: config.timeframe,
    enabled: false,
    params: { ...config.params },
  };
}

export function getStrategyRowSafety(
  config: StrategyConfig,
  status: StrategyStatus | undefined,
): StrategyRowSafety {
  if (status === 'stopped') {
    return {
      canEdit: true,
      canDelete: true,
      canStart: config.enabled,
      canStop: false,
    };
  }
  if (status === 'running') {
    return { canEdit: false, canDelete: false, canStart: false, canStop: true };
  }
  return { canEdit: false, canDelete: false, canStart: false, canStop: false };
}

export function validationIssuesFromError(error: unknown): StrategyValidationIssue[] {
  if (typeof error !== 'object' || error === null) return [];
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return [];
  const data = (response as { data?: unknown }).data;
  if (typeof data !== 'object' || data === null) return [];
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail !== 'object' || detail === null) return [];
  const issues = (detail as { issues?: unknown }).issues;
  return Array.isArray(issues) ? issues.filter(isValidationIssue) : [];
}

function isValidationIssue(value: unknown): value is StrategyValidationIssue {
  if (typeof value !== 'object' || value === null) return false;
  const issue = value as Partial<StrategyValidationIssue>;
  return typeof issue.path === 'string'
    && typeof issue.code === 'string'
    && typeof issue.message === 'string'
    && (issue.line === null || typeof issue.line === 'number')
    && (issue.column === null || typeof issue.column === 'number');
}

export function strategyModelUri(instanceKey: string): string {
  return `inmemory://strategy/${encodeURIComponent(instanceKey)}.yaml`;
}

export function markerDataForIssues(issues: StrategyValidationIssue[]) {
  const positioned = issues.filter((issue) => issue.line !== null && issue.column !== null);
  return {
    markers: positioned.map((issue) => {
      const line = Math.max(1, issue.line ?? 1);
      const column = Math.max(1, issue.column ?? 1);
      return {
        message: issue.message,
        startLineNumber: line,
        startColumn: column,
        endLineNumber: line,
        endColumn: column + 1,
        severity: 8,
        source: 'strategy-validation',
        code: issue.code,
      };
    }),
    external: issues.filter((issue) => issue.line === null || issue.column === null),
  };
}

export function clonePayload(payload: StrategyConfigPayload): StrategyConfigPayload {
  return {
    name: payload.name,
    strategy_type: payload.strategy_type,
    symbol: payload.symbol,
    timeframe: payload.timeframe,
    enabled: payload.enabled,
    params: { ...payload.params },
  };
}

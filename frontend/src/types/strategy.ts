export type StrategyStatus = 'running' | 'stopped' | 'starting' | 'error' | string;

export type StrategyParameterValueType = 'integer' | 'number' | 'boolean' | 'string';

export type StrategyParameterValue = string | number | boolean | null;

export interface StrategyParameterDefinition {
  key: string;
  label: string;
  description: string;
  value_type: StrategyParameterValueType;
  required: boolean;
  default?: StrategyParameterValue;
  minimum: number | null;
  maximum: number | null;
  step?: number;
}

export interface StrategyDefinition {
  strategy_type: string;
  label: string;
  description: string;
  params: StrategyParameterDefinition[];
}

export interface StrategyConfigPayload {
  name: string;
  strategy_type: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  params: Record<string, StrategyParameterValue>;
}

export interface StrategyConfig extends StrategyConfigPayload {
  created_at: number;
  updated_at: number;
}

export interface StrategyRuntimeSummary {
  name: string;
  status: StrategyStatus;
  error?: string;
}

export interface StrategyValidationIssue {
  path: string;
  code: string;
  message: string;
  line: number | null;
  column: number | null;
}

export interface StrategyValidationErrorDetail {
  code: 'strategy_validation_failed' | 'strategy_yaml_invalid' | 'malformed_config' | string;
  message: string;
  issues: StrategyValidationIssue[];
}

export interface StrategyValidationResult {
  config: StrategyConfigPayload;
  yaml: string;
}

export interface StrategyCloneRequest {
  target_name: string;
  overrides?: Partial<Omit<StrategyConfigPayload, 'name' | 'params'>> & {
    params?: Record<string, StrategyParameterValue>;
  };
}

export interface StrategyActionResult {
  status: string;
  strategy: string;
}

export interface StrategySnapshot {
  strategies?: StrategyRuntimeSummary[];
  strategy_configs?: StrategyConfig[];
  strategy_errors?: Record<string, string>;
}

interface StrategyMessageBase {
  received_at?: number;
}

export interface StrategyStatusWebSocketMessage extends StrategyMessageBase {
  type: 'strategy_status';
  strategy: string;
  status: StrategyStatus;
  timestamp?: number;
}

export interface StrategyErrorWebSocketMessage extends StrategyMessageBase {
  type: 'strategy_error';
  strategy: string;
  error: string;
  timestamp?: number;
}

export interface StrategyConfigCreatedWebSocketMessage extends StrategyMessageBase {
  type: 'strategy_config_created';
  strategy: string;
  config: StrategyConfig;
  timestamp?: number;
}

export interface StrategyConfigUpdatedWebSocketMessage extends StrategyMessageBase {
  type: 'strategy_config_updated';
  strategy: string;
  config: StrategyConfig;
  timestamp?: number;
}

export interface StrategyConfigDeletedWebSocketMessage extends StrategyMessageBase {
  type: 'strategy_config_deleted';
  strategy: string;
  timestamp?: number;
}

export interface StrategySnapshotWebSocketMessage extends StrategyMessageBase, StrategySnapshot {
  type: 'snapshot';
  data?: StrategySnapshot;
}

export interface UnknownStrategyWebSocketMessage extends StrategyMessageBase {
  type: string;
  [key: string]: unknown;
}

export type StrategyWebSocketMessage =
  | StrategyStatusWebSocketMessage
  | StrategyErrorWebSocketMessage
  | StrategyConfigCreatedWebSocketMessage
  | StrategyConfigUpdatedWebSocketMessage
  | StrategyConfigDeletedWebSocketMessage
  | StrategySnapshotWebSocketMessage
  | UnknownStrategyWebSocketMessage;

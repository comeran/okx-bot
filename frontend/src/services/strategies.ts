import axios from 'axios';

import type {
  StrategyActionResult,
  StrategyCloneRequest,
  StrategyConfig,
  StrategyConfigPayload,
  StrategyDefinition,
  StrategyRuntimeSummary,
  StrategyValidationResult,
} from '@/types/strategy';

function strategyUrl(path = ''): string {
  return `/api/strategies${path}`;
}

function configUrl(name: string, suffix = ''): string {
  return strategyUrl(`/configs/${encodeURIComponent(name)}${suffix}`);
}

function expectedNameQuery(expectedName?: string): string {
  return expectedName ? `?expected_name=${encodeURIComponent(expectedName)}` : '';
}

export async function listStrategyTypes(): Promise<StrategyDefinition[]> {
  const response = await axios.get<StrategyDefinition[]>(strategyUrl('/types'));
  return response.data;
}

export async function listStrategyConfigs(): Promise<StrategyConfig[]> {
  const response = await axios.get<StrategyConfig[]>(strategyUrl('/configs'));
  return response.data;
}

export async function getStrategyConfig(name: string): Promise<StrategyConfig> {
  const response = await axios.get<StrategyConfig>(configUrl(name));
  return response.data;
}

export async function createStrategyConfig(config: StrategyConfigPayload): Promise<StrategyConfig> {
  const response = await axios.post<StrategyConfig>(strategyUrl('/configs'), config);
  return response.data;
}

export async function updateStrategyConfig(
  name: string,
  config: StrategyConfigPayload,
): Promise<StrategyConfig> {
  const response = await axios.put<StrategyConfig>(configUrl(name), config);
  return response.data;
}

export async function cloneStrategyConfig(
  name: string,
  request: StrategyCloneRequest,
): Promise<StrategyConfig> {
  const response = await axios.post<StrategyConfig>(configUrl(name, '/clone'), request);
  return response.data;
}

export async function deleteStrategyConfig(name: string): Promise<void> {
  await axios.delete(configUrl(name));
}

export async function validateStrategyConfig(
  config: StrategyConfigPayload,
  expectedName?: string,
): Promise<StrategyValidationResult> {
  const response = await axios.post<StrategyValidationResult>(
    strategyUrl(`/configs/validate${expectedNameQuery(expectedName)}`),
    config,
  );
  return response.data;
}

export async function validateStrategyConfigYaml(
  yaml: string,
  expectedName?: string,
): Promise<StrategyValidationResult> {
  const response = await axios.post<StrategyValidationResult>(
    strategyUrl(`/configs/validate-yaml${expectedNameQuery(expectedName)}`),
    yaml,
    { headers: { 'Content-Type': 'application/yaml' } },
  );
  return response.data;
}

export async function listStrategies(): Promise<StrategyRuntimeSummary[]> {
  const response = await axios.get<StrategyRuntimeSummary[]>(strategyUrl());
  return response.data;
}

export async function startStrategy(name: string): Promise<StrategyActionResult> {
  const response = await axios.post<StrategyActionResult>(strategyUrl(`/${encodeURIComponent(name)}/start`));
  return response.data;
}

export async function stopStrategy(name: string): Promise<StrategyActionResult> {
  const response = await axios.post<StrategyActionResult>(strategyUrl(`/${encodeURIComponent(name)}/stop`));
  return response.data;
}

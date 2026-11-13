import axios from 'axios';

import type {
  AllocationContext,
  StrategyAllocationEnvelope,
  StrategyAllocationReadiness,
  StrategyAllocationReadinessRequest,
  StrategyAllocationUpdate,
} from '@/types/strategyAllocation';

function allocationUrl(strategy: string): string {
  return `/api/allocations/${encodeURIComponent(strategy)}`;
}

export async function getAllocationContext(): Promise<AllocationContext> {
  const response = await axios.get<AllocationContext>('/api/allocations/context');
  return response.data;
}

export async function getStrategyAllocation(strategy: string): Promise<StrategyAllocationEnvelope> {
  const response = await axios.get<StrategyAllocationEnvelope>(allocationUrl(strategy));
  return response.data;
}

export async function getStrategyAllocationReadiness(
  strategy: string,
  request: StrategyAllocationReadinessRequest,
): Promise<StrategyAllocationReadiness> {
  const response = await axios.get<StrategyAllocationReadiness>(`${allocationUrl(strategy)}/readiness`, {
    params: request,
  });
  return response.data;
}

export async function saveStrategyAllocation(
  strategy: string,
  update: StrategyAllocationUpdate,
): Promise<StrategyAllocationEnvelope> {
  const response = await axios.put<StrategyAllocationEnvelope>(allocationUrl(strategy), update);
  return response.data;
}

export async function deleteStrategyAllocation(
  strategy: string,
  revision: number,
  expectedScopeId: string,
  expectedSymbol: string,
): Promise<StrategyAllocationEnvelope> {
  const response = await axios.delete<StrategyAllocationEnvelope>(allocationUrl(strategy), {
    params: { revision, expected_scope_id: expectedScopeId, expected_symbol: expectedSymbol },
  });
  return response.data;
}

export async function activateStrategyAllocation(
  strategy: string,
  update: Omit<StrategyAllocationUpdate, 'budget'>,
): Promise<StrategyAllocationEnvelope> {
  const response = await axios.post<StrategyAllocationEnvelope>(
    `${allocationUrl(strategy)}/activate`,
    update,
  );
  return response.data;
}

export async function releaseStrategyAllocation(
  strategy: string,
  update: Omit<StrategyAllocationUpdate, 'budget'>,
): Promise<StrategyAllocationEnvelope> {
  const response = await axios.post<StrategyAllocationEnvelope>(
    `${allocationUrl(strategy)}/release`,
    update,
  );
  return response.data;
}

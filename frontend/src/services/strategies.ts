import axios from 'axios';

import type { StrategySummary } from '@/types/strategy';

export async function listStrategies(): Promise<StrategySummary[]> {
  const response = await axios.get<StrategySummary[]>('/api/strategies');
  return response.data;
}

export async function startStrategy(name: string): Promise<void> {
  await axios.post(`/api/strategies/${encodeURIComponent(name)}/start`);
}

export async function stopStrategy(name: string): Promise<void> {
  await axios.post(`/api/strategies/${encodeURIComponent(name)}/stop`);
}

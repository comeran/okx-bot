import axios from 'axios';

import type { AppSettingsUpdate, AppSettingsView } from '@/types/settings';
import type { ExchangeAuthentication } from '@/types/executionContext';

export async function getSettings(): Promise<AppSettingsView> {
  const response = await axios.get<AppSettingsView>('/api/settings');
  return response.data;
}

export async function updateSettings(settings: AppSettingsUpdate): Promise<AppSettingsView> {
  const response = await axios.put<AppSettingsView>('/api/settings', settings);
  return response.data;
}

export async function checkExchangeAuthentication(accountScopeId: string): Promise<ExchangeAuthentication> {
  const response = await axios.post<ExchangeAuthentication>('/api/settings/exchange-auth', undefined, {
    params: { expected_account_scope_id: accountScopeId },
  });
  return response.data;
}

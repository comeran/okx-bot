import axios from 'axios';

import type { AppSettingsUpdate, AppSettingsView } from '@/types/settings';

export async function getSettings(): Promise<AppSettingsView> {
  const response = await axios.get<AppSettingsView>('/api/settings');
  return response.data;
}

export async function updateSettings(settings: AppSettingsUpdate): Promise<AppSettingsView> {
  const response = await axios.put<AppSettingsView>('/api/settings', settings);
  return response.data;
}

import axios from 'axios';

import { setApiToken } from '@/services/apiToken';

export interface LoginResult {
  required: boolean;
  ok: boolean;
}

export async function fetchAuthSession(): Promise<{ required: boolean }> {
  const response = await axios.get<{ required: boolean }>('/api/auth/session');
  return response.data;
}

export async function login(token: string): Promise<LoginResult> {
  const response = await axios.post<LoginResult>('/api/auth/login', { token });
  if (response.data.ok) {
    setApiToken(token.trim());
  }
  return response.data;
}

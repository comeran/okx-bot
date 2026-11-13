import axios from 'axios';

export interface KillSwitchState {
  engaged: boolean;
  reason: string;
  updated_at: number;
}

export async function fetchKillSwitch(): Promise<KillSwitchState> {
  const response = await axios.get<KillSwitchState>('/api/ops/kill-switch');
  return response.data;
}

export async function updateKillSwitch(engaged: boolean, reason: string): Promise<KillSwitchState> {
  const response = await axios.put<KillSwitchState>('/api/ops/kill-switch', { engaged, reason });
  return response.data;
}

export interface OpeningPauseState {
  paused: boolean;
  reason: string;
  updated_at: number;
}

export async function fetchOpeningPause(): Promise<OpeningPauseState> {
  const response = await axios.get<OpeningPauseState>('/api/ops/opening-pause');
  return response.data;
}

export async function updateOpeningPause(paused: boolean, reason: string): Promise<OpeningPauseState> {
  const response = await axios.put<OpeningPauseState>('/api/ops/opening-pause', { paused, reason });
  return response.data;
}

export interface EmergencyOrderResult {
  cancelled: string[];
  failed: Array<Record<string, unknown>>;
  closed?: Array<Record<string, unknown>>;
  close_failed?: Array<Record<string, unknown>>;
  kill_switch: KillSwitchState;
  opening_pause: OpeningPauseState;
}

export async function cancelOpenOrders(): Promise<EmergencyOrderResult> {
  const response = await axios.post<EmergencyOrderResult>('/api/ops/cancel-orders');
  return response.data;
}

export async function flattenPositions(): Promise<EmergencyOrderResult> {
  const response = await axios.post<EmergencyOrderResult>('/api/ops/flatten');
  return response.data;
}

export interface DeskState {
  unresolved_live_orders: number;
  price_age_ms: number | null;
  fill_cursor_ms: number | null;
  opening_pause: OpeningPauseState;
  kill_switch: KillSwitchState;
  actions: Array<{ action: string; result: Record<string, unknown>; created_at: number }>;
}

export async function fetchDesk(): Promise<DeskState> {
  const response = await axios.get<DeskState>('/api/ops/desk');
  return response.data;
}

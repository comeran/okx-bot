import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import { getSettings, updateSettings } from './settings';
import type { AppSettingsUpdate } from '@/types/settings';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('settings service', () => {
  it('loads settings from the settings API', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        mode: 'backtest',
        exchange: {
          api_key: '',
          api_key_set: false,
          secret: '',
          secret_set: false,
          passphrase: '',
          passphrase_set: false,
        },
        backtest: {
          initial_capital: 100000,
          fee_rate: 0.0005,
          slippage: 0.001,
          data_cache_dir: './data',
        },
        risk: {
          max_daily_loss_pct: 0.05,
          max_drawdown_pct: 0.15,
          max_total_position_pct: 0.8,
        },
        notify: {
          telegram_bot_token: '',
          telegram_bot_token_set: false,
          telegram_chat_id: '',
        },
        web: {
          host: '0.0.0.0',
          port: 8080,
        },
      },
    });

    const settings = await getSettings();

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/settings');
    expect(settings.mode).toBe('backtest');
    expect(settings.exchange.api_key_set).toBe(false);
  });

  it('saves settings to the settings API', async () => {
    const update: AppSettingsUpdate = {
      mode: 'paper',
      exchange: {
        api_key: 'okx-api-key',
        secret: 'okx-secret-value',
        passphrase: 'okx-passphrase',
      },
      backtest: {
        initial_capital: 250000,
        fee_rate: 0.0007,
        slippage: 0.0015,
        data_cache_dir: './data/backtests',
      },
      risk: {
        max_daily_loss_pct: 0.03,
        max_drawdown_pct: 0.12,
        max_total_position_pct: 0.65,
      },
      notify: {
        telegram_bot_token: 'telegram-token',
        telegram_chat_id: '123456',
      },
      web: {
        host: '127.0.0.1',
        port: 8000,
      },
    };
    mockedAxios.put.mockResolvedValueOnce({ data: { mode: 'paper' } });

    await updateSettings(update);

    expect(mockedAxios.put).toHaveBeenCalledWith('/api/settings', update);
  });
});

import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import {
  cloneStrategyConfig,
  createStrategyConfig,
  deleteStrategyConfig,
  getStrategyConfig,
  listStrategies,
  listStrategyConfigs,
  listStrategyTypes,
  startStrategy,
  stopStrategy,
  updateStrategyConfig,
  validateStrategyConfig,
  validateStrategyConfigYaml,
} from './strategies';
import type { StrategyConfigPayload } from '@/types/strategy';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

const payload: StrategyConfigPayload = {
  name: 'btc_ma',
  strategy_type: 'ma_cross',
  symbol: 'BTC-USDT-SWAP',
  timeframe: '1m',
  enabled: true,
  params: { fast: 10, slow: 30 },
};

describe('strategies service', () => {
  it('calls every strategy endpoint with typed response data', async () => {
    mockedAxios.get
      .mockResolvedValueOnce({ data: [{ strategy_type: 'ma_cross', label: 'MA Cross', description: '', params: [] }] })
      .mockResolvedValueOnce({ data: [payload] })
      .mockResolvedValueOnce({ data: payload })
      .mockResolvedValueOnce({ data: [{ name: 'btc_ma', status: 'stopped' }] });
    mockedAxios.post
      .mockResolvedValueOnce({ data: payload })
      .mockResolvedValueOnce({ data: { ...payload, name: 'copy' } })
      .mockResolvedValueOnce({ data: { config: payload, yaml: 'name: btc_ma\n' } })
      .mockResolvedValueOnce({ data: { config: payload, yaml: 'name: btc_ma\n' } })
      .mockResolvedValueOnce({ data: { status: 'started', strategy: 'btc_ma' } })
      .mockResolvedValueOnce({ data: { status: 'stopped', strategy: 'btc_ma' } });
    mockedAxios.put.mockResolvedValueOnce({ data: payload });
    mockedAxios.delete.mockResolvedValueOnce({ data: undefined });

    await expect(listStrategyTypes()).resolves.toHaveLength(1);
    await expect(listStrategyConfigs()).resolves.toEqual([payload]);
    await expect(getStrategyConfig('btc/ma')).resolves.toEqual(payload);
    await expect(listStrategies()).resolves.toEqual([{ name: 'btc_ma', status: 'stopped' }]);
    await expect(createStrategyConfig(payload)).resolves.toEqual(payload);
    await expect(updateStrategyConfig('btc/ma', payload)).resolves.toEqual(payload);
    await expect(cloneStrategyConfig('btc/ma', { target_name: 'copy', overrides: { symbol: 'ETH-USDT-SWAP' } })).resolves.toEqual({ ...payload, name: 'copy' });
    await expect(validateStrategyConfig(payload, 'btc_ma')).resolves.toEqual({ config: payload, yaml: 'name: btc_ma\n' });
    await expect(validateStrategyConfigYaml('name: btc_ma\n', 'btc_ma')).resolves.toEqual({ config: payload, yaml: 'name: btc_ma\n' });
    await deleteStrategyConfig('btc/ma');
    await expect(startStrategy('btc/ma')).resolves.toEqual({ status: 'started', strategy: 'btc_ma' });
    await expect(stopStrategy('btc/ma')).resolves.toEqual({ status: 'stopped', strategy: 'btc_ma' });

    expect(mockedAxios.get).toHaveBeenNthCalledWith(1, '/api/strategies/types');
    expect(mockedAxios.get).toHaveBeenNthCalledWith(2, '/api/strategies/configs');
    expect(mockedAxios.get).toHaveBeenNthCalledWith(3, '/api/strategies/configs/btc%2Fma');
    expect(mockedAxios.get).toHaveBeenNthCalledWith(4, '/api/strategies');
    expect(mockedAxios.post).toHaveBeenNthCalledWith(1, '/api/strategies/configs', payload);
    expect(mockedAxios.put).toHaveBeenCalledWith('/api/strategies/configs/btc%2Fma', payload);
    expect(mockedAxios.post).toHaveBeenNthCalledWith(2, '/api/strategies/configs/btc%2Fma/clone', { target_name: 'copy', overrides: { symbol: 'ETH-USDT-SWAP' } });
    expect(mockedAxios.post).toHaveBeenNthCalledWith(3, '/api/strategies/configs/validate?expected_name=btc_ma', payload);
    expect(mockedAxios.post).toHaveBeenNthCalledWith(4, '/api/strategies/configs/validate-yaml?expected_name=btc_ma', 'name: btc_ma\n', { headers: { 'Content-Type': 'application/yaml' } });
    expect(mockedAxios.delete).toHaveBeenCalledWith('/api/strategies/configs/btc%2Fma');
    expect(mockedAxios.post).toHaveBeenNthCalledWith(5, '/api/strategies/btc%2Fma/start');
    expect(mockedAxios.post).toHaveBeenNthCalledWith(6, '/api/strategies/btc%2Fma/stop');
  });
});

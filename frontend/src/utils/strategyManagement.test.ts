import { describe, expect, it } from 'vitest';
import { buildDefaultStrategyDraft } from './strategyManagement';

describe('new strategy market', () => {
  it('starts new strategies with a spot instrument', () => {
    const draft = buildDefaultStrategyDraft({ strategy_type: 'ma_cross', label: 'MA', description: '', params: [] });
    expect(draft.symbol).toBe('BTC-USDT');
    expect(draft).not.toHaveProperty('market_type');
  });
});

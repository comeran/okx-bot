import { describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import { mount, textContent } from '@/test-utils/mount';
import type { ExchangeAuthentication } from '@/types/executionContext';
import ExchangeAuthNotice from './ExchangeAuthNotice.vue';

const failed: ExchangeAuthentication = {
  account_scope_id: 'scope-A', environment: 'sandbox', status: 'failed',
  reason: 'invalid_credentials', provider_code: '50119', checked_at: 1,
};

async function mountNotice(authentication: ExchangeAuthentication | null, locale: 'en' | 'zh-CN' = 'en') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: {} }, { path: '/settings', component: {} }],
  });
  await router.push('/');
  await router.isReady();
  return mount(ExchangeAuthNotice, { props: { authentication }, locale, router });
}

describe('ExchangeAuthNotice', () => {
  it('keeps the failure, environment and safe code visible with a real settings link', async () => {
    const wrapper = await mountNotice(failed);
    const alert = wrapper.find((node) => node.props.role === 'alert');
    expect(textContent(alert)).toContain('OKX authentication failed');
    expect(textContent(alert)).toContain('OKX sandbox');
    expect(textContent(alert)).toContain('50119');
    const link = wrapper.find((node) => node.type === 'a' && node.props.href === '/settings');
    expect(textContent(link)).toContain('Review settings');
    wrapper.unmount();
  });

  it('localizes unavailable observations and does not render unknown provider fields', async () => {
    const wrapper = await mountNotice({
      ...failed, environment: 'live', status: 'unavailable',
      reason: 'private-provider-error' as ExchangeAuthentication['reason'], provider_code: '99999',
    }, 'zh-CN');
    expect(wrapper.text()).toContain('OKX 鉴权暂不可用');
    expect(wrapper.text()).toContain('OKX 实盘');
    expect(wrapper.text()).not.toContain('private-provider-error');
    expect(wrapper.text()).not.toContain('99999');
    expect(wrapper.find((node) => node.type === 'a' && node.props.href === '/settings')).toBeTruthy();
    wrapper.unmount();
  });

  it('removes the alert and action when the current observation is healthy or cleared', async () => {
    const wrapper = await mountNotice({ ...failed, status: 'authenticated', reason: null, provider_code: null });
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(0);
    await wrapper.updateProps({ authentication: null });
    expect(wrapper.findAll((node) => node.type === 'a')).toHaveLength(0);
    wrapper.unmount();
  });
});

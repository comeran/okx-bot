import { describe, expect, it } from 'vitest';
import { mount } from '@/test-utils/mount';
import AccountContextNotice from './AccountContextNotice.vue';
const context = {
  account_scope_id: 'private-hash-do-not-use-as-primary-copy',
  exchange: 'okx',
  environment: 'sandbox',
  market_type: 'spot',
  identity_verified: false,
  available: true,
  reason: null,
  execution_ready: false
};
describe('account identity notice', () => {
  it('shows environment and credential binding as unverified without displaying the hash', async () => {
    const wrapper = await mount(AccountContextNotice, {
      locale: 'en',
      props: {
        context
      }
    });
    expect(wrapper.text()).toContain('OKX sandbox');
    expect(wrapper.text()).toContain('not verified');
    expect(wrapper.text()).not.toContain(context.account_scope_id);
    wrapper.unmount();
  });
  it('distinguishes unavailable credentials from a current account with no snapshot', async () => {
    const wrapper = await mount(AccountContextNotice, {
      locale: 'en',
      props: {
        context: {
          ...context,
          available: false,
          account_scope_id: null,
          reason: 'credentials_unavailable'
        },
        missingSnapshot: true
      }
    });
    expect(wrapper.text()).toContain('Exchange account unavailable');
    expect(wrapper.text()).not.toContain('No snapshot');
    await wrapper.updateProps({
      context,
      missingSnapshot: true
    });
    expect(wrapper.text()).toContain('No snapshot');
    wrapper.unmount();
  });
});

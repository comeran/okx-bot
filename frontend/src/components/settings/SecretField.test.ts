import { describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import SecretField from './SecretField.vue';

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

const components = {
  ElInput: defineHostComponent('el-input'),
};

describe('SecretField', () => {
  it('renders a labeled blank password input with configured status and hint', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SecretField, {
          modelValue: '',
          configured: true,
          label: 'API Key',
          hint: 'Leave blank to keep existing',
        }, {});
      },
    }), {
      components,
    });

    const input = wrapper.find((node) => node.type === 'el-input');
    expect(input.props.modelValue ?? input.props.value ?? '').toBe('');
    expect(input.props['aria-label']).toBe('API Key');
    expect(input.props['aria-labelledby']).toContain('-label');
    expect(input.props['aria-describedby']).toContain('-hint');
    expect(input.props['aria-describedby']).toContain('-status');
    expect(textContent(wrapper.find((node) => node.props.class === 'secret-field__label'))).toBe('API Key');
    expect(textContent(wrapper.find((node) => node.props.class === 'secret-field__status'))).toBe('settings.secretConfigured');
    expect(textContent(wrapper.find((node) => node.props.class === 'secret-field__hint'))).toBe('Leave blank to keep existing');
  });

  it('passes disabled state to the password input', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SecretField, {
          modelValue: '',
          configured: false,
          label: 'API Key',
          disabled: true,
        }, {});
      },
    }), {
      components,
    });

    expect(wrapper.find((node) => node.type === 'el-input').props.disabled).toBe(true);
  });

  it('does not emit model updates while disabled', async () => {
    const updates: string[] = [];
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SecretField, {
          modelValue: '',
          configured: false,
          label: 'API Key',
          disabled: true,
          'onUpdate:modelValue': (value: string) => updates.push(value),
        }, {});
      },
    }), {
      components,
    });

    await wrapper.invoke(wrapper.find((node) => node.type === 'el-input'), 'onUpdate:modelValue', 'new-secret');

    expect(updates).toEqual([]);
  });

  it('emits model updates without rendering secret text in the DOM', async () => {
    const updates: string[] = [];
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SecretField, {
          modelValue: '',
          configured: false,
          label: 'Telegram Bot Token',
          hint: 'Leave blank to keep existing',
          'onUpdate:modelValue': (value: string) => updates.push(value),
        }, {});
      },
    }), {
      components,
    });

    const input = wrapper.find((node) => node.type === 'el-input');
    await wrapper.invoke(input, 'onUpdate:modelValue', 'super-secret-token');

    expect(updates).toEqual(['super-secret-token']);
    expect(wrapper.text()).not.toContain('super-secret-token');
    expect(wrapper.findAll((node) => node.props.class === 'secret-field__status')).toHaveLength(0);
  });
});

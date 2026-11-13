<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';

import { fetchAuthSession, login } from '@/services/auth';

const { t } = useI18n();
const router = useRouter();
const token = ref('');
const required = ref(true);
const saving = ref(false);

onMounted(async () => {
  try {
    const session = await fetchAuthSession();
    required.value = session.required;
  } catch {
    required.value = true;
  }
});

async function submit(): Promise<void> {
  saving.value = true;
  try {
    await login(token.value);
    await router.push({ name: 'dashboard' });
  } catch {
    ElMessage.error(t('login.failed'));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="login-view">
    <h1>{{ t('login.title') }}</h1>
    <p>{{ required ? t('login.description') : t('login.notRequired') }}</p>
    <form @submit.prevent="submit">
      <label>
        {{ t('login.token') }}
        <input
          v-model="token"
          type="password"
          autocomplete="current-password"
          :placeholder="t('login.placeholder')"
        />
      </label>
      <button type="submit" :disabled="saving">{{ t('login.submit') }}</button>
    </form>
  </section>
</template>

import axios from 'axios';
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';

import 'element-plus/dist/index.css';
import './styles/tokens.css';
import './styles/global.css';

import App from './App.vue';
import { createI18nInstance } from './i18n';
import { configureMonacoEnvironment } from './monaco';
import router from './router';
import { getApiToken } from './services/apiToken';

configureMonacoEnvironment();

axios.interceptors.request.use((config) => {
  const token = getApiToken();
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

axios.interceptors.response.use(
  (response) => response,
  (error: { response?: { status?: number }; config?: { url?: string } }) => {
    const url = String(error.config?.url || '');
    if (error.response?.status === 401 && !url.includes('/api/auth/login')) {
      void router.push({ name: 'login' });
    }
    return Promise.reject(error);
  },
);

const i18n = createI18nInstance();
document.documentElement.lang = i18n.global.locale.value;

createApp(App)
  .use(createPinia())
  .use(router)
  .use(ElementPlus)
  .use(i18n)
  .mount('#app');

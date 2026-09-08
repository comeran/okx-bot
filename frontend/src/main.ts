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

configureMonacoEnvironment();

const i18n = createI18nInstance();
document.documentElement.lang = i18n.global.locale.value;

createApp(App)
  .use(createPinia())
  .use(router)
  .use(ElementPlus)
  .use(i18n)
  .mount('#app');

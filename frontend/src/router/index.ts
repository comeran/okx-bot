import { createRouter, createWebHistory } from 'vue-router';

import Backtest from '@/views/Backtest.vue';
import Dashboard from '@/views/Dashboard.vue';
import Market from '@/views/Market.vue';
import Settings from '@/views/Settings.vue';
import Strategy from '@/views/Strategy.vue';
import Trades from '@/views/Trades.vue';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: Dashboard,
    },
    {
      path: '/strategies',
      name: 'strategies',
      component: Strategy,
    },
    {
      path: '/backtest',
      name: 'backtest',
      component: Backtest,
    },
    {
      path: '/market',
      name: 'market',
      component: Market,
    },
    {
      path: '/trades',
      name: 'trades',
      component: Trades,
    },
    {
      path: '/settings',
      name: 'settings',
      component: Settings,
    },
  ],
});

export default router;

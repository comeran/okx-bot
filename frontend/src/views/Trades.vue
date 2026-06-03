<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
</script>

<template>
  <section>
    <h2>{{ t('trades.title') }}</h2>
    <p>{{ t('trades.placeholder') }}</p>
  </section>
</template>

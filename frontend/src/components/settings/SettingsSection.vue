<script lang="ts">
import { Comment, Fragment, Text, defineComponent, h, useSlots, type VNode, type VNodeChild } from 'vue';

import SectionCard from '@/components/ui/SectionCard.vue';

interface Props {
  title: string;
  description?: string;
}

function hasRenderableContent(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasRenderableContent);
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (typeof value === 'number') return true;
  if (typeof value !== 'object') return Boolean(value);

  const vnode = value as VNode;
  if (vnode.type === Comment) return false;
  if (vnode.type === Text) return String(vnode.children ?? '').trim().length > 0;
  if (vnode.type === Fragment) return hasRenderableContent(vnode.children);
  return true;
}

export default defineComponent({
  name: 'SettingsSection',
  props: {
    title: {
      type: String,
      required: true,
    },
    description: String,
  },
  setup(props: Props) {
    const slots = useSlots();

    return () => {
      const statusContent = slots.status?.();
      const actionsContent = slots.actions?.();
      let bodyContent: VNodeChild | undefined;

      if (slots.body) bodyContent = slots.body();
      else if (slots.content) bodyContent = slots.content();
      else bodyContent = slots.default?.();

      const hasStatus = hasRenderableContent(statusContent);
      const hasActions = hasRenderableContent(actionsContent);
      const hasHeaderMeta = hasStatus || hasActions;

      return h(
        SectionCard,
        {
          title: props.title,
          description: props.description,
          class: 'settings-section',
        },
        {
          actions: hasHeaderMeta
            ? () => h('div', { class: 'settings-section__meta' }, [
                hasStatus ? h('div', { class: 'settings-section__status' }, statusContent) : null,
                hasActions ? h('div', { class: 'settings-section__actions' }, actionsContent) : null,
              ])
            : undefined,
          body: () => bodyContent,
        },
      );
    };
  },
});
</script>

<style scoped>
.settings-section {
  --settings-section-gap: var(--ui-space-16);
}

.settings-section__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: var(--ui-space-8);
}

.settings-section__status {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.45;
}

.settings-section__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--ui-space-8);
}
</style>

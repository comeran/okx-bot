<script lang="ts">
import { Comment, Fragment, Text, defineComponent, h, useSlots, type VNode } from 'vue';

interface Props {
  title?: string;
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
  name: 'SectionCard',
  props: {
    title: String,
    description: String,
  },
  setup(props: Props) {
    const slots = useSlots();

    return () => {
      const titleSlotContent = slots.title?.();
      const descriptionSlotContent = slots.description?.();
      const actionsSlotContent = slots.actions?.();
      const hasBodySlot = Boolean(slots.body);
      const bodySlotContent = hasBodySlot ? slots.body?.() : undefined;
      const defaultBodyContent = hasBodySlot ? undefined : slots.default?.();

      const hasTitle = Boolean(props.title) || hasRenderableContent(titleSlotContent);
      const hasDescription = Boolean(props.description) || hasRenderableContent(descriptionSlotContent);
      const hasActions = hasRenderableContent(actionsSlotContent);
      const showHeader = hasTitle || hasDescription || hasActions;
      const titleContent = slots.title ? titleSlotContent : props.title;
      const descriptionContent = slots.description ? descriptionSlotContent : props.description;
      const bodyContent = bodySlotContent === undefined ? defaultBodyContent : bodySlotContent;

      return h('article', { class: 'section-card' }, [
        showHeader
          ? h('header', { class: 'section-card__header' }, [
              h('div', { class: 'section-card__heading' }, [
                hasTitle ? h('h2', { class: 'section-card__title' }, titleContent) : null,
                hasDescription ? h('p', { class: 'section-card__description' }, descriptionContent) : null,
              ]),
              hasActions ? h('div', { class: 'section-card__actions' }, actionsSlotContent) : null,
            ])
          : null,
        h('div', { class: 'section-card__body' }, bodyContent ?? undefined),
      ]);
    };
  },
});
</script>

<style scoped>
.section-card {
  min-width: 0;
  background: var(--ui-color-surface);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-10);
  padding: var(--ui-space-16);
  box-shadow: var(--ui-shadow-sm);
}

.section-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--ui-space-12);
  margin-bottom: var(--ui-space-16);
  min-width: 0;
}

.section-card__heading {
  flex: 1 1 180px;
  min-width: 0;
}

.section-card__title {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-16);
  line-height: 1.5;
  font-weight: 700;
  letter-spacing: -0.015em;
  overflow-wrap: anywhere;
}

.section-card__description {
  margin: var(--ui-space-4) 0 0;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.section-card__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: var(--ui-space-8);
  min-width: 0;
  max-width: 100%;
}

.section-card__body {
  min-width: 0;
}

@media (max-width: 767px) {
  .section-card__heading {
    flex-basis: 100%;
  }

  .section-card__actions {
    justify-content: flex-start;
    width: 100%;
  }
}
</style>

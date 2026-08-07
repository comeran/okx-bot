import {
  createRenderer,
  defineComponent,
  h,
  nextTick,
  reactive,
  ssrContextKey,
  type Component,
  type Directive,
  type Plugin,
} from 'vue';

export interface TestHostNode {
  type: string;
  props: Record<string, unknown>;
  children: TestHostNode[];
  parent: TestHostNode | null;
  text?: string;
  focus: () => void;
}

interface MountOptions {
  props?: Record<string, unknown>;
  components?: Record<string, Component>;
  directives?: Record<string, Directive>;
  plugins?: Plugin[];
  provide?: Record<string | symbol, unknown>;
}

function node(type: string, text?: string): TestHostNode {
  const target: TestHostNode = {
    type,
    props: {},
    children: [],
    parent: null,
    text,
    focus: () => { target.props['data-focused'] = true; },
  };
  return target;
}

function detach(child: TestHostNode): void {
  if (!child.parent) return;
  const index = child.parent.children.indexOf(child);
  if (index >= 0) child.parent.children.splice(index, 1);
  child.parent = null;
}

const renderer = createRenderer<TestHostNode, TestHostNode>({
  patchProp(element, key, _previous, value) {
    if (value === null || value === undefined) delete element.props[key];
    else element.props[key] = value;
  },
  insert(child, parent, anchor) {
    detach(child);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    if (index >= 0) parent.children.splice(index, 0, child);
    else parent.children.push(child);
    child.parent = parent;
  },
  remove: detach,
  createElement(type) {
    return node(type);
  },
  createText(text) {
    return node('#text', text);
  },
  createComment(text) {
    return node('#comment', text);
  },
  setText(target, text) {
    target.text = text;
  },
  setElementText(element, text) {
    for (const child of element.children) child.parent = null;
    element.children = [];
    if (text) {
      const child = node('#text', text);
      child.parent = element;
      element.children.push(child);
    }
  },
  parentNode(target) {
    return target.parent;
  },
  nextSibling(target) {
    if (!target.parent) return null;
    const index = target.parent.children.indexOf(target);
    return target.parent.children[index + 1] ?? null;
  },
  setScopeId(element, id) {
    element.props[id] = '';
  },
  cloneNode(target) {
    return {
      ...target,
      props: { ...target.props },
      children: [...target.children],
      parent: null,
    };
  },
  insertStaticContent(content, parent, anchor) {
    const child = node('#static', content);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    if (index >= 0) parent.children.splice(index, 0, child);
    else parent.children.push(child);
    child.parent = parent;
    return [child, child];
  },
});

function walk(root: TestHostNode): TestHostNode[] {
  return [root, ...root.children.flatMap(walk)];
}

function eventProp(event: string): string {
  return `on${event.charAt(0).toUpperCase()}${event.slice(1)}`;
}

export function textContent(target: TestHostNode): string {
  return target.text ?? target.children.map(textContent).join('');
}

export function defineHostComponent(
  name: string,
  options: { inheritAttrs?: boolean } = {},
): Component {
  return defineComponent({
    name,
    inheritAttrs: options.inheritAttrs ?? true,
    setup(_props, context) {
      return () => h(name, context.attrs, context.slots.default?.());
    },
  });
}

export async function mount(component: Component, options: MountOptions = {}) {
  const root = node('#root');
  const props = reactive({ ...(options.props ?? {}) });
  const Shell = defineComponent({
    setup() {
      return () => h(component, props);
    },
  });
  const app = renderer.createApp(Shell);
  app.provide(ssrContextKey, { modules: new Set<string>() });

  for (const [name, stub] of Object.entries(options.components ?? {})) app.component(name, stub);
  for (const [name, directive] of Object.entries(options.directives ?? {})) app.directive(name, directive);
  for (const plugin of options.plugins ?? []) app.use(plugin);
  for (const [key, value] of Reflect.ownKeys(options.provide ?? {}).map((key) => [key, options.provide?.[key]] as const)) {
    app.provide(key, value);
  }

  app.mount(root);
  await nextTick();
  let mounted = true;

  return {
    root,
    props,
    all: () => walk(root).filter((target) => target !== root),
    find(predicate: (target: TestHostNode) => boolean): TestHostNode {
      const match = walk(root).find(predicate);
      if (!match) throw new Error('Mounted host node not found');
      return match;
    },
    findAll(predicate: (target: TestHostNode) => boolean): TestHostNode[] {
      return walk(root).filter(predicate);
    },
    getById(id: string): TestHostNode {
      const match = walk(root).find((target) => target.props.id === id);
      if (!match) throw new Error(`Mounted host node with id ${id} not found`);
      return match;
    },
    getByTestId(id: string): TestHostNode {
      const match = walk(root).find((target) => target.props['data-testid'] === id);
      if (!match) throw new Error(`Mounted host node with test id ${id} not found`);
      return match;
    },
    text: () => textContent(root),
    async trigger(target: TestHostNode, event: string, payload: Record<string, unknown> = {}) {
      const handler = target.props[eventProp(event)];
      const eventObject = {
        type: event,
        preventDefault() {},
        stopPropagation() {},
        ...payload,
      };
      if (Array.isArray(handler)) {
        for (const callback of handler) await callback(eventObject);
      } else if (typeof handler === 'function') {
        await handler(eventObject);
      } else {
        throw new Error(`Mounted host node has no ${event} handler`);
      }
      await nextTick();
    },
    async invoke(target: TestHostNode, prop: string, ...args: unknown[]) {
      const handler = target.props[prop];
      if (Array.isArray(handler)) {
        for (const callback of handler) await callback(...args);
      } else if (typeof handler === 'function') {
        await handler(...args);
      } else {
        throw new Error(`Mounted host node has no ${prop} handler`);
      }
      await nextTick();
    },
    async updateProps(next: Record<string, unknown>) {
      Object.assign(props, next);
      await nextTick();
    },
    async flush() {
      await Promise.resolve();
      await nextTick();
    },
    unmount() {
      if (!mounted) return;
      app.unmount();
      mounted = false;
    },
  };
}

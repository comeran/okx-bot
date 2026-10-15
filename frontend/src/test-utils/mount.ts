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
  type Slots,
  type VNodeChild,
} from 'vue';
import { createI18n } from 'vue-i18n';
import type { Router } from 'vue-router';

import en from '../locales/en';
import zhCN from '../locales/zh-CN';
import type { Locale } from '../i18n';

interface TestOwnerDocument {
  body: TestHostNode;
  documentElement: TestHostNode;
  defaultView: Window;
}

interface TestHostStyle {
  cssText: string;
  setProperty: (property: string, value: string) => void;
  getPropertyValue: (property: string) => string;
  removeProperty: (property: string) => string;
  [key: string]: string | ((property: string, value: string) => void) | ((property: string) => string) | undefined;
}

export interface TestHostNode {
  type: string;
  props: Record<string, unknown>;
  style: TestHostStyle;
  classList: {
    add: (...classes: string[]) => void;
    remove: (...classes: string[]) => void;
    contains: (className: string) => boolean;
  };
  children: TestHostNode[];
  parent: TestHostNode | null;
  parentNode: TestHostNode | null;
  nodeName: string;
  text?: string;
  focus: () => void;
  addEventListener: () => void;
  removeEventListener: () => void;
  setAttribute: (key: string, value: string) => void;
  removeAttribute: (key: string) => void;
  hasAttribute: (key: string) => boolean;
  getAttribute: (key: string) => string | null;
  querySelector: (selector: string) => TestHostNode | null;
  contains: (node: TestHostNode) => boolean;
  isSameNode: (node: TestHostNode) => boolean;
  cloneNode: (deep?: boolean) => TestHostNode;
  getRootNode: () => TestOwnerDocument;
  getBoundingClientRect: () => DOMRect;
  ownerDocument: TestOwnerDocument;
}

interface MountOptions {
  props?: Record<string, unknown>;
  components?: Record<string, Component>;
  directives?: Record<string, Directive>;
  plugins?: Plugin[];
  provide?: Record<string | symbol, unknown>;
  locale?: Locale;
  router?: Router;
}

interface HostComponentOptions {
  inheritAttrs?: boolean;
  slotRenderer?: (slots: Slots, attrs: Record<string, unknown>) => VNodeChild;
}

const testMessages = {
  en,
  'zh-CN': zhCN,
};

const styleMethodNames = new Set(['cssText', 'setProperty', 'getPropertyValue', 'removeProperty']);

function createDefaultView(ownerDocument: TestOwnerDocument): Window {
  const realWindow = globalThis.window ?? globalThis.document?.defaultView;
  const targetWindow = realWindow ?? ({
    document: ownerDocument,
    getComputedStyle: () => ({}) as CSSStyleDeclaration,
  } as unknown as Window);

  let proxy: Window;
  proxy = new Proxy(targetWindow, {
    get(target, property, receiver) {
      if (property === 'document') return ownerDocument;
      if (property === 'window' || property === 'self' || property === 'top' || property === 'parent') return proxy;
      const value = Reflect.get(target, property, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
    set(target, property, value) {
      if (property === 'document' || property === 'window' || property === 'self' || property === 'top' || property === 'parent') return true;
      return Reflect.set(target, property, value, target);
    },
    has(target, property) {
      if (property === 'document' || property === 'window' || property === 'self' || property === 'top' || property === 'parent') return true;
      return Reflect.has(target, property);
    },
    ownKeys(target) {
      return Array.from(new Set([...Reflect.ownKeys(target), 'document', 'window', 'self', 'top', 'parent']));
    },
    getOwnPropertyDescriptor(target, property) {
      if (property === 'document' || property === 'window' || property === 'self' || property === 'top' || property === 'parent') {
        return {
          configurable: true,
          enumerable: true,
          value: property === 'document' ? ownerDocument : proxy,
          writable: false,
        };
      }
      return Reflect.getOwnPropertyDescriptor(target, property);
    },
  }) as Window;

  return proxy;
}

function createOwnerDocument(): TestOwnerDocument {
  const ownerDocument = {} as TestOwnerDocument;
  ownerDocument.defaultView = createDefaultView(ownerDocument);
  ownerDocument.documentElement = node('html', undefined, ownerDocument);
  ownerDocument.body = node('body', undefined, ownerDocument);
  return ownerDocument;
}

function createHostStyle(target: TestHostNode): TestHostStyle {
  const values: Record<string, string> = {};
  const style = { cssText: '' } as TestHostStyle;

  Object.defineProperties(style, {
    setProperty: {
      configurable: true,
      enumerable: false,
      value(property: string, value: string) {
        values[property] = value;
        sync();
      },
      writable: true,
    },
    getPropertyValue: {
      configurable: true,
      enumerable: false,
      value(property: string) {
        return values[property] ?? '';
      },
      writable: true,
    },
    removeProperty: {
      configurable: true,
      enumerable: false,
      value(property: string) {
        const previous = values[property] ?? '';
        delete values[property];
        sync();
        return previous;
      },
      writable: true,
    },
  });

  function sync(): void {
    style.cssText = Object.entries(values).map(([property, value]) => `${property}: ${value};`).join(' ');
    if (Object.keys(values).length) target.props.style = { ...values };
    else delete target.props.style;
  }

  return new Proxy(style, {
    get(targetStyle, property, receiver) {
      if (typeof property === 'string' && property in values) return values[property];
      return Reflect.get(targetStyle, property, receiver);
    },
    set(targetStyle, property, value, receiver) {
      if (property === 'cssText' && typeof value === 'string') {
        applyHostStyle(target, value);
        return true;
      }
      if (typeof property === 'string' && !styleMethodNames.has(property)) {
        values[property] = String(value);
        sync();
        return true;
      }
      return Reflect.set(targetStyle, property, value, receiver);
    },
    deleteProperty(targetStyle, property) {
      if (typeof property === 'string' && property in values) {
        delete values[property];
        sync();
        return true;
      }
      return Reflect.deleteProperty(targetStyle, property);
    },
    ownKeys(targetStyle) {
      return Array.from(new Set(['cssText', ...Object.keys(values)]));
    },
    getOwnPropertyDescriptor(targetStyle, property) {
      if (typeof property === 'string' && property in values) {
        return {
          configurable: true,
          enumerable: true,
          value: values[property],
          writable: true,
        };
      }
      return Reflect.getOwnPropertyDescriptor(targetStyle, property);
    },
  }) as TestHostStyle;
}

function clearHostStyle(target: TestHostNode): void {
  for (const key of Object.keys(target.style)) {
    if (key !== 'cssText') delete target.style[key];
  }
}

function parseStyleEntries(value: unknown): Array<[string, string]> {
  if (Array.isArray(value)) return value.flatMap(parseStyleEntries);
  if (typeof value === 'string') {
    return value
      .split(';')
      .map((entry) => entry.trim())
      .filter(Boolean)
      .map((entry) => {
        const separatorIndex = entry.indexOf(':');
        if (separatorIndex < 0) return [entry, ''] as [string, string];
        return [entry.slice(0, separatorIndex).trim(), entry.slice(separatorIndex + 1).trim()] as [string, string];
      });
  }
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).map(([property, entryValue]) => [property, String(entryValue)]);
  }
  return [];
}

function applyHostStyle(target: TestHostNode, value: unknown): void {
  clearHostStyle(target);
  if (value === null || value === undefined || value === '') return;
  for (const [property, entryValue] of parseStyleEntries(value)) target.style.setProperty(property, entryValue);
}

function classNamesFrom(value: unknown): string[] {
  if (typeof value === 'string') return value.split(/\s+/).filter(Boolean);
  if (Array.isArray(value)) return value.flatMap(classNamesFrom);
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, enabled]) => enabled)
      .map(([className]) => className);
  }
  return [];
}

function setClassNames(target: TestHostNode, classNames: string[]): void {
  const deduped = Array.from(new Set(classNames));
  if (deduped.length) target.props.class = deduped.join(' ');
  else delete target.props.class;
}

function cloneStyleProp(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cloneStyleProp);
  if (value && Object.getPrototypeOf(value) === Object.prototype) return { ...value };
  return value;
}

function cloneHostNode(source: TestHostNode, deep = false): TestHostNode {
  const clone = node(source.type, source.text, source.ownerDocument);
  clone.props = { ...source.props };
  if ('style' in clone.props) {
    clone.props.style = cloneStyleProp(clone.props.style);
    applyHostStyle(clone, clone.props.style);
  }
  if (deep) {
    clone.children = source.children.map((child) => {
      const childClone = child.cloneNode(true);
      childClone.parent = clone;
      return childClone;
    });
  }
  return clone;
}

function node(type: string, text?: string, ownerDocument = createOwnerDocument()): TestHostNode {
  const target: TestHostNode = {
    type,
    props: {},
    style: {} as TestHostStyle,
    classList: {
      add: (...nextClasses) => {
        setClassNames(target, [...classNamesFrom(target.props.class), ...nextClasses]);
      },
      remove: (...removedClasses) => {
        const removed = new Set(removedClasses);
        setClassNames(target, classNamesFrom(target.props.class).filter((className) => !removed.has(className)));
      },
      contains: (className) => classNamesFrom(target.props.class).includes(className),
    },
    children: [],
    parent: null,
    get parentNode() {
      return target.parent ?? (target.type === 'body' ? target.ownerDocument.documentElement : null);
    },
    nodeName: type.toUpperCase(),
    text,
    focus: () => { target.props['data-focused'] = true; },
    addEventListener: () => {},
    removeEventListener: () => {},
    setAttribute: (key, value) => {
      if (key === 'class') {
        target.props[key] = classNamesFrom(value).join(' ');
        return;
      }
      if (key === 'style') {
        applyHostStyle(target, value);
        return;
      }
      target.props[key] = value;
    },
    removeAttribute: (key) => {
      if (key === 'style') {
        clearHostStyle(target);
        return;
      }
      delete target.props[key];
    },
    hasAttribute: (key) => Object.prototype.hasOwnProperty.call(target.props, key),
    getAttribute: (key) => {
      if (key === 'style') return target.style.cssText || null;
      const value = target.props[key];
      return value === undefined || value === null ? null : String(value);
    },
    querySelector: (selector) => walk(target).find((candidate) => matchesSelector(candidate, selector)) ?? null,
    contains: (candidate) => candidate === target || walk(target).includes(candidate),
    isSameNode: (candidate) => candidate === target,
    cloneNode: (deep = false) => cloneHostNode(target, deep),
    getRootNode: () => target.ownerDocument,
    getBoundingClientRect: () => ({
      bottom: 0,
      height: 0,
      left: 0,
      right: 0,
      top: 0,
      width: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect),
    ownerDocument,
  };
  target.style = createHostStyle(target);
  return target;
}

function matchesSelector(target: TestHostNode, selector: string): boolean {
  const attributeMatch = selector.match(/^\[([^=\]]+)="([^"]+)"\]$/);
  if (attributeMatch) return target.props[attributeMatch[1]] === attributeMatch[2];
  if (selector.startsWith('#')) return target.props.id === selector.slice(1);
  return target.type === selector;
}

function detach(child: TestHostNode): void {
  if (!child.parent) return;
  const index = child.parent.children.indexOf(child);
  if (index >= 0) child.parent.children.splice(index, 1);
  child.parent = null;
}

function resolveTeleportTarget(selector: string, teleportTarget: TestHostNode): TestHostNode | null {
  if (selector === 'body') return teleportTarget;
  if (/^#el-popper-container-\d+$/.test(selector)) return teleportTarget;
  return null;
}

function createTestRenderer(teleportTarget: TestHostNode, ownerDocument: TestOwnerDocument) {
  return createRenderer<TestHostNode, TestHostNode>({
    patchProp(element, key, _previous, value) {
      if (key === 'class') {
        if (value === null || value === undefined) delete element.props[key];
        else element.props[key] = classNamesFrom(value).join(' ');
        return;
      }
      if (key === 'style') {
        applyHostStyle(element, value);
        return;
      }
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
      return node(type, undefined, ownerDocument);
    },
    createText(text) {
      return node('#text', text, ownerDocument);
    },
    createComment(text) {
      return node('#comment', text, ownerDocument);
    },
    setText(target, text) {
      target.text = text;
    },
    setElementText(element, text) {
      for (const child of element.children) child.parent = null;
      element.children = [];
      if (text) {
        const child = node('#text', text, ownerDocument);
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
      return target.cloneNode(true);
    },
    insertStaticContent(content, parent, anchor) {
      const child = node('#static', content, ownerDocument);
      const index = anchor ? parent.children.indexOf(anchor) : -1;
      if (index >= 0) parent.children.splice(index, 0, child);
      else parent.children.push(child);
      child.parent = parent;
      return [child, child];
    },
    querySelector(selector) {
      return resolveTeleportTarget(selector, teleportTarget);
    },
  });
}

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
  options: HostComponentOptions = {},
): Component {
  return defineComponent({
    name,
    inheritAttrs: options.inheritAttrs ?? true,
    setup(_props, context) {
      return () => {
        const children = options.slotRenderer
          ? options.slotRenderer(context.slots, context.attrs)
          : context.slots.default?.();
        const hostAttrs = options.inheritAttrs === false ? undefined : context.attrs;
        return h(name, hostAttrs, children == null ? undefined : children);
      };
    },
  });
}

export async function mount(component: Component, options: MountOptions = {}) {
  const ownerDocument = createOwnerDocument();
  const root = node('#root', undefined, ownerDocument);
  const teleportTarget = ownerDocument.body;
  const renderer = createTestRenderer(teleportTarget, ownerDocument);
  const props = reactive({ ...(options.props ?? {}) });
  const Shell = defineComponent({
    setup() {
      return () => h(component, props as Record<string, unknown>);
    },
  });
  const app = renderer.createApp(Shell);
  app.provide(ssrContextKey, { modules: new Set<string>() });

  for (const [name, stub] of Object.entries(options.components ?? {})) app.component(name, stub);
  for (const [name, directive] of Object.entries(options.directives ?? {})) app.directive(name, directive);
  for (const plugin of options.plugins ?? []) app.use(plugin);
  if (options.router) app.use(options.router);
  if (options.locale) {
    app.use(createI18n({
      legacy: false,
      locale: options.locale,
      fallbackLocale: 'en',
      messages: testMessages,
    }));
  }
  for (const [key, value] of Reflect.ownKeys(options.provide ?? {}).map((key) => [key, options.provide?.[key]] as const)) {
    app.provide(key, value);
  }

  app.mount(root);
  if (options.router) await options.router.isReady();
  await nextTick();
  let mounted = true;

  return {
    root,
    props,
    all: () => walk(root).filter((target) => target !== root),
    queryTeleportTarget: (selector: string) => resolveTeleportTarget(selector, teleportTarget),
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
      for (const child of [...teleportTarget.children]) detach(child);
      mounted = false;
    },
  };
}

import * as monaco from 'monaco-editor';

interface ModelLease {
  model: monaco.editor.ITextModel;
  owners: number;
  moduleOwned: boolean;
}

const leases = new Map<string, ModelLease>();

export interface AcquiredModel {
  key: string;
  model: monaco.editor.ITextModel;
}

export function acquireModel(uriValue: string, value: string, language: string): AcquiredModel {
  const uri = monaco.Uri.parse(uriValue);
  const key = uri.toString();
  const leased = leases.get(key);
  if (leased) {
    leased.owners += 1;
    return { key, model: leased.model };
  }

  const existing = monaco.editor.getModel(uri);
  const model = existing ?? monaco.editor.createModel(value, language, uri);
  leases.set(key, { model, owners: 1, moduleOwned: existing === null });
  return { key, model };
}

export function releaseModel(key: string, clearMarkers: (model: monaco.editor.ITextModel) => void): void {
  const lease = leases.get(key);
  if (!lease) return;
  lease.owners -= 1;
  if (lease.owners > 0) return;

  clearMarkers(lease.model);
  if (lease.moduleOwned) lease.model.dispose();
  leases.delete(key);
}

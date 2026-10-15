export function useDirtyGuard(
  isDirty: () => boolean,
  confirmDiscard: () => Promise<boolean>,
): { confirmIfDirty: () => Promise<boolean> } {
  return {
    async confirmIfDirty(): Promise<boolean> {
      if (!isDirty()) return true;
      return confirmDiscard();
    },
  };
}

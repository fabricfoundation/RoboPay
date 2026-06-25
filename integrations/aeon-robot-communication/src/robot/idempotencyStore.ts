export interface StoredAction<T> {
  fingerprint: string;
  response: T;
}

export class IdempotencyConflictError extends Error {
  statusCode = 409;
  code = "IDEMPOTENCY_CONFLICT";

  constructor() {
    super("Idempotency key was reused with different action parameters.");
  }
}

export class IdempotencyStore<T> {
  private readonly byKey = new Map<string, StoredAction<T>>();

  get(key: string, fingerprint: string): StoredAction<T> | undefined {
    const existing = this.byKey.get(key);
    if (!existing) return undefined;
    if (existing.fingerprint !== fingerprint) {
      throw new IdempotencyConflictError();
    }
    return existing;
  }

  set(key: string, fingerprint: string, response: T): void {
    this.byKey.set(key, { fingerprint, response });
  }

  clear(): void {
    this.byKey.clear();
  }
}

export interface Publisher {
  publish(topic: string, payload: unknown): Promise<void>;
}

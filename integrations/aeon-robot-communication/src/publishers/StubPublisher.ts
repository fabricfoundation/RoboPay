import type { Publisher } from "./Publisher.js";

export interface PublishedMessage {
  topic: string;
  payload: unknown;
}

export class StubPublisher implements Publisher {
  readonly messages: PublishedMessage[] = [];

  async publish(topic: string, payload: unknown): Promise<void> {
    this.messages.push({ topic, payload });
  }

  clear(): void {
    this.messages.length = 0;
  }
}

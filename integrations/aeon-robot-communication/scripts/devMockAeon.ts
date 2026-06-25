import { createMockFacilitatorApp } from "../src/payment/mockFacilitator.js";

const port = Number.parseInt(process.env.MOCK_AEON_PORT ?? "3402", 10);
const app = createMockFacilitatorApp({ network: process.env.NETWORK ?? "eip155:56" });

app.listen(port, () => {
  console.log(`[mock-aeon] facilitator listening on http://127.0.0.1:${port}`);
  console.log("[mock-aeon] endpoints: POST /verify, POST /settle");
});

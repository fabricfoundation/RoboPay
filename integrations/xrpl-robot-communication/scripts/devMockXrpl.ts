import { createMockXrplFacilitatorApp } from "../src/payment/mockXrplFacilitator.js";

const port = Number.parseInt(process.env.MOCK_XRPL_PORT ?? "3402", 10);
const app = createMockXrplFacilitatorApp();

app.listen(port, () => {
  console.log(`[mock-xrpl-facilitator] listening on http://127.0.0.1:${port}`);
  console.log("[mock-xrpl-facilitator] endpoints: POST /verify, POST /settle");
});


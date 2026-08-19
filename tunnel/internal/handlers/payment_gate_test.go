package handlers

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	x402http "github.com/x402-foundation/x402/go/http"
	"github.com/x402-foundation/x402/go/types"
)

// fakeVerifyOnlyServer is a hand-rolled VerifyOnlyServer that counts calls
// and returns a scripted result, so payment_gate.go can be tested without a
// real x402 facilitator.
type fakeVerifyOnlyServer struct {
	requiresPayment bool
	result          x402http.HTTPProcessResult
	requiresCalls   int
	processCalls    int
}

func (f *fakeVerifyOnlyServer) RequiresPayment(reqCtx x402http.HTTPRequestContext) bool {
	f.requiresCalls++
	return f.requiresPayment
}

func (f *fakeVerifyOnlyServer) ProcessHTTPRequest(ctx context.Context, reqCtx x402http.HTTPRequestContext, paywallConfig *x402http.PaywallConfig) x402http.HTTPProcessResult {
	f.processCalls++
	return f.result
}

func newGateRouter(server VerifyOnlyServer, downstreamCalls *int, capturedPayload *interface{}) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/action", X402VerifyOnly(server, 2*time.Second), func(c *gin.Context) {
		*downstreamCalls++
		if v, ok := c.Get("x402_payload"); ok {
			*capturedPayload = v
		}
		c.Status(http.StatusAccepted)
	})
	return router
}

// This is the key fail-closed case: a payment payload whose signature does
// not verify (facilitator returns ResultPaymentError) must be rejected
// before the handler runs, with exactly one verify call and the downstream
// handler -- which is where an ActionEvent would be published -- never
// invoked. Settlement lives entirely in ExecutionWatcher, so a rejected
// verify here can never lead to a settle call by construction; this test
// pins the accept-time half of that guarantee.
func TestX402VerifyOnly_TamperedSignature_RejectsWithoutCallingHandler(t *testing.T) {
	server := &fakeVerifyOnlyServer{
		requiresPayment: true,
		result: x402http.HTTPProcessResult{
			Type: x402http.ResultPaymentError,
			Response: &x402http.HTTPResponseInstructions{
				Status: http.StatusPaymentRequired,
				Body:   map[string]string{"error": "invalid signature"},
			},
		},
	}

	downstreamCalls := 0
	var captured interface{}
	router := newGateRouter(server, &downstreamCalls, &captured)

	req := httptest.NewRequest(http.MethodPost, "/action", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusPaymentRequired {
		t.Fatalf("expected 402 for a tampered payment payload, got %d: %s", res.Code, res.Body.String())
	}
	if server.requiresCalls != 1 {
		t.Fatalf("expected exactly 1 RequiresPayment call, got %d", server.requiresCalls)
	}
	if server.processCalls != 1 {
		t.Fatalf("expected exactly 1 ProcessHTTPRequest (verify) call, got %d", server.processCalls)
	}
	if downstreamCalls != 0 {
		t.Fatalf("expected 0 downstream handler calls for a rejected payment, got %d", downstreamCalls)
	}
	if captured != nil {
		t.Fatalf("expected no x402_payload to be set for a rejected payment, got %v", captured)
	}
}

func TestX402VerifyOnly_VerifiedPayment_ForwardsPayloadAndCallsHandlerOnce(t *testing.T) {
	payload := types.PaymentPayload{}
	requirements := types.PaymentRequirements{}
	server := &fakeVerifyOnlyServer{
		requiresPayment: true,
		result: x402http.HTTPProcessResult{
			Type:                x402http.ResultPaymentVerified,
			PaymentPayload:      &payload,
			PaymentRequirements: &requirements,
		},
	}

	downstreamCalls := 0
	var captured interface{}
	router := newGateRouter(server, &downstreamCalls, &captured)

	req := httptest.NewRequest(http.MethodPost, "/action", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202 for a verified payment, got %d: %s", res.Code, res.Body.String())
	}
	if downstreamCalls != 1 {
		t.Fatalf("expected exactly 1 downstream handler call for a verified payment, got %d", downstreamCalls)
	}
	if captured == nil {
		t.Fatalf("expected x402_payload to be set on the context for a verified payment")
	}
}

func TestX402VerifyOnly_NoPaymentRequired_SkipsVerifyResultButCallsHandler(t *testing.T) {
	server := &fakeVerifyOnlyServer{requiresPayment: false}

	downstreamCalls := 0
	var captured interface{}
	router := newGateRouter(server, &downstreamCalls, &captured)

	req := httptest.NewRequest(http.MethodPost, "/action", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202 when no payment is required, got %d: %s", res.Code, res.Body.String())
	}
	if server.processCalls != 0 {
		t.Fatalf("expected ProcessHTTPRequest to be skipped when RequiresPayment is false, got %d calls", server.processCalls)
	}
	if downstreamCalls != 1 {
		t.Fatalf("expected exactly 1 downstream handler call, got %d", downstreamCalls)
	}
}

package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

type fakePublisher struct {
	mu     sync.Mutex
	events [][]byte
}

func (f *fakePublisher) Publish(_ string, payload []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.events = append(f.events, payload)
	return nil
}

func (f *fakePublisher) last() map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.events) == 0 {
		return nil
	}
	var event map[string]any
	if err := json.Unmarshal(f.events[len(f.events)-1], &event); err != nil {
		return nil
	}
	return event
}

var testPublisher = &fakePublisher{}

func TestMain(m *testing.M) {
	zenohOnce.Do(func() { zenohPub = testPublisher })
	os.Exit(m.Run())
}

func TestPostAction_ValidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":"start"}`))
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", res.Code)
	}
}

func TestPostAction_InvalidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":`))
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
}

func TestPostAction_TagsX402Protocol(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.POST("/action", func(c *gin.Context) {
		c.Set("x402_payload", map[string]any{"scheme": "exact"})
		c.Set("x402_requirements", map[string]any{"network": "eip155:84532"})
		h.PostAction(c)
	})

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"move"}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", res.Code)
	}

	event := testPublisher.last()
	if event == nil {
		t.Fatal("expected an action event to be published")
	}
	details, ok := event["transaction_details"].(map[string]any)
	if !ok {
		t.Fatalf("expected transaction_details in %v", event)
	}
	if details["protocol"] != ProtocolX402 {
		t.Errorf("expected protocol %q, got %v", ProtocolX402, details["protocol"])
	}
	if details["payment_payload"] == nil {
		t.Error("expected the x402 payment_payload to be carried through")
	}
	if details["payment_requirements"] == nil {
		t.Error("expected the x402 payment_requirements to be carried through")
	}
	if _, exists := details["mpp_receipt"]; exists {
		t.Error("expected no MPP receipt on an x402 payment")
	}
}

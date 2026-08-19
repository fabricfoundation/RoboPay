package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

func newTestHandlers(t *testing.T) *Handlers {
	t.Helper()
	storePath := filepath.Join(t.TempDir(), "idempotency_store.json")
	store, err := NewIdempotencyStore(storePath)
	if err != nil {
		t.Fatalf("failed to create idempotency store: %v", err)
	}
	return &Handlers{Logger: zap.NewNop(), Store: store}
}

func withAllowedActions(t *testing.T, actions string) {
	t.Helper()
	old := os.Getenv("ALLOWED_ACTIONS")
	os.Setenv("ALLOWED_ACTIONS", actions)
	t.Cleanup(func() { os.Setenv("ALLOWED_ACTIONS", old) })
}

func newRouter(h *Handlers) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/action", h.PostAction)
	router.GET("/action/:id/status", h.GetActionStatus)
	return router
}

// This is the exact case flagged in review of related submissions: a
// payload with no registered action/skill must NEVER be accepted or
// dispatched, even if it is syntactically valid JSON.
func TestPostAction_RejectsPayloadWithoutAction(t *testing.T) {
	withAllowedActions(t, "navigate,stop")
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":"start"}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for a payload with no 'action' field, got %d: %s", res.Code, res.Body.String())
	}
}

func TestPostAction_RejectsInvalidJSON(t *testing.T) {
	withAllowedActions(t, "navigate")
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid JSON, got %d", res.Code)
	}
}

func TestPostAction_RejectsWhenAllowlistEmpty(t *testing.T) {
	withAllowedActions(t, "")
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"navigate"}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when no allowlist is configured (fail closed), got %d", res.Code)
	}
}

func TestPostAction_RejectsUnknownAction(t *testing.T) {
	withAllowedActions(t, "navigate,stop")
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"launch_missiles"}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for an action not on the allowlist, got %d", res.Code)
	}
}

// Publishing to Zenoh will fail in this test environment (no broker
// running), which is fine: we only assert on the admission decision
// (fail-closed checks happen BEFORE the publish attempt), not on the
// publish outcome itself.
func TestPostAction_AcceptsAllowedActionAndReturnsActionId(t *testing.T) {
	withAllowedActions(t, "navigate,stop")
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"navigate","params":{"goal_x":5}}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	// Either 202 (publish succeeded) or 502 (no Zenoh broker in this test
	// environment) is acceptable here -- both mean the admission gate let
	// a *valid* action through, which is what this test targets. What must
	// never happen is 400/403/503 for a well-formed, allowlisted request.
	if res.Code != http.StatusAccepted && res.Code != http.StatusBadGateway {
		t.Fatalf("expected 202 or 502 for a valid allowlisted action, got %d: %s", res.Code, res.Body.String())
	}
}

func TestGetActionStatus_UnknownIdReturns404(t *testing.T) {
	h := newTestHandlers(t)
	router := newRouter(h)

	req := httptest.NewRequest(http.MethodGet, "/action/does-not-exist/status", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for an unknown actionId, got %d", res.Code)
	}
}

func TestGetActionStatus_ReturnsReservedPendingState(t *testing.T) {
	h := newTestHandlers(t)
	status, replay, err := h.Store.Reserve("test-action-1")
	if err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if replay {
		t.Fatal("expected first reservation to not be a replay")
	}
	if status.State != StatePending {
		t.Fatalf("expected freshly reserved action to be pending, got %q", status.State)
	}

	router := newRouter(h)
	req := httptest.NewRequest(http.MethodGet, "/action/test-action-1/status", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", res.Code)
	}

	var got ActionStatus
	if err := json.Unmarshal(res.Body.Bytes(), &got); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if got.State != StatePending {
		t.Fatalf("expected pending state in response, got %q", got.State)
	}
}

// The core replay guarantee: reserving the same actionId twice must
// return the SAME record and flag it as a replay, so a caller (or a
// retried settlement path) never double-dispatches.
func TestIdempotencyStore_ReserveTwiceIsReplay(t *testing.T) {
	h := newTestHandlers(t)

	first, replay1, err := h.Store.Reserve("dup-action")
	if err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if replay1 {
		t.Fatal("first reservation should not be a replay")
	}

	second, replay2, err := h.Store.Reserve("dup-action")
	if err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if !replay2 {
		t.Fatal("second reservation of the same actionId must be flagged as a replay")
	}
	if second.ActionID != first.ActionID {
		t.Fatalf("replay must return the original record, got a different actionId")
	}
}

// Settlement gating depends on this: failure/timeout must never be
// silently reported as settled.
func TestIdempotencyStore_UpdateResult_FailureIsNotSettled(t *testing.T) {
	h := newTestHandlers(t)
	if _, _, err := h.Store.Reserve("fail-action"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}

	if err := h.Store.UpdateResult("fail-action", StateFailed, "SIMULATOR_ERROR", false); err != nil {
		t.Fatalf("UpdateResult failed: %v", err)
	}

	status, ok := h.Store.Get("fail-action")
	if !ok {
		t.Fatal("expected status to exist after UpdateResult")
	}
	if status.State != StateFailed {
		t.Fatalf("expected state=failed, got %q", status.State)
	}
	if status.Settled {
		t.Fatal("a failed action must never be recorded as settled")
	}
}

// Durability across restart: a store re-opened from the same file must
// see previously reserved/updated actions, so a replay after a tunnel
// restart is still caught.
func TestIdempotencyStore_PersistsAcrossReopen(t *testing.T) {
	storePath := filepath.Join(t.TempDir(), "idempotency_store.json")

	store1, err := NewIdempotencyStore(storePath)
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}
	if _, _, err := store1.Reserve("restart-action"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	_ = store1.UpdateResult("restart-action", StateSucceeded, "", true)

	store2, err := NewIdempotencyStore(storePath)
	if err != nil {
		t.Fatalf("failed to reopen store: %v", err)
	}
	status, ok := store2.Get("restart-action")
	if !ok {
		t.Fatal("expected action to survive store reopen (simulated restart)")
	}
	if status.State != StateSucceeded || !status.Settled {
		t.Fatalf("expected persisted succeeded+settled state, got %+v", status)
	}

	_, replay, err := store2.Reserve("restart-action")
	if err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if !replay {
		t.Fatal("reserving the same actionId after a simulated restart must be flagged as a replay")
	}
}

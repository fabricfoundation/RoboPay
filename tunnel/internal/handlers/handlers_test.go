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

	"github.com/fabricfoundation/tunnel/internal/ledger"
	"github.com/fabricfoundation/tunnel/internal/skillbook"
)

// newTestBook writes a small skills.json fixture and loads it -- keeps
// each test's skill allowlist isolated and disposable.
func newTestBook(t *testing.T) *skillbook.Book {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "skills.json")
	content := `{
		"skills": [
			{"skillId": "look_at_apple", "description": "x", "priceUSDC": "0.001", "paymentRequired": true, "params": {}}
		]
	}`
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	book, err := skillbook.Load(path)
	if err != nil {
		t.Fatalf("load skillbook: %v", err)
	}
	return book
}

func newTestRouter(t *testing.T) (*gin.Engine, *Handlers) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop(), newTestBook(t), ledger.New())
	router.POST("/action", h.PostAction)
	router.GET("/action/:actionId/status", h.GetActionStatus)
	return router, h
}

func TestPostAction_ValidJSON(t *testing.T) {
	router, _ := newTestRouter(t)

	body := `{"robotId":"robot-1","skillId":"look_at_apple","idempotencyKey":"idem-1","params":{}}`
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusAccepted {
		t.Fatalf("expected status 202, got %d: %s", res.Code, res.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(res.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	if resp["status"] != "pending" {
		t.Fatalf("expected status=pending, got %v", resp["status"])
	}
	if resp["actionId"] == "" || resp["actionId"] == nil {
		t.Fatalf("expected non-empty actionId, got %v", resp["actionId"])
	}
}

func TestPostAction_InvalidJSON(t *testing.T) {
	router, _ := newTestRouter(t)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
}

func TestPostAction_UnknownSkillRejected(t *testing.T) {
	router, _ := newTestRouter(t)

	body := `{"robotId":"robot-1","skillId":"does_not_exist","idempotencyKey":"idem-2","params":{}}`
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected status 422, got %d: %s", res.Code, res.Body.String())
	}
}

func TestPostAction_MissingRequiredFieldsRejected(t *testing.T) {
	router, _ := newTestRouter(t)

	body := `{"skillId":"look_at_apple","params":{}}`
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d: %s", res.Code, res.Body.String())
	}
}

func TestPostAction_DuplicateIdempotencyKeyRejected(t *testing.T) {
	router, _ := newTestRouter(t)

	body := `{"robotId":"robot-1","skillId":"look_at_apple","idempotencyKey":"idem-dup","params":{}}`

	req1 := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res1 := httptest.NewRecorder()
	router.ServeHTTP(res1, req1)
	if res1.Code != http.StatusAccepted {
		t.Fatalf("first request expected 202, got %d: %s", res1.Code, res1.Body.String())
	}

	req2 := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res2 := httptest.NewRecorder()
	router.ServeHTTP(res2, req2)
	if res2.Code != http.StatusConflict {
		t.Fatalf("replay expected 409, got %d: %s", res2.Code, res2.Body.String())
	}
}

func TestGetActionStatus_UnknownActionReturns404(t *testing.T) {
	router, _ := newTestRouter(t)

	req := httptest.NewRequest(http.MethodGet, "/action/does-not-exist/status", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d: %s", res.Code, res.Body.String())
	}
}

func TestPostAction_ThenGetStatus_ReturnsPending(t *testing.T) {
	router, _ := newTestRouter(t)

	body := `{"robotId":"robot-1","skillId":"look_at_apple","idempotencyKey":"idem-status","params":{}}`
	postReq := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	postRes := httptest.NewRecorder()
	router.ServeHTTP(postRes, postReq)

	var postResp map[string]interface{}
	if err := json.Unmarshal(postRes.Body.Bytes(), &postResp); err != nil {
		t.Fatalf("invalid response JSON: %v", err)
	}
	actionID, _ := postResp["actionId"].(string)
	if actionID == "" {
		t.Fatalf("expected non-empty actionId in POST response")
	}

	getReq := httptest.NewRequest(http.MethodGet, "/action/"+actionID+"/status", nil)
	getRes := httptest.NewRecorder()
	router.ServeHTTP(getRes, getReq)

	if getRes.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", getRes.Code, getRes.Body.String())
	}

	var statusResp map[string]interface{}
	if err := json.Unmarshal(getRes.Body.Bytes(), &statusResp); err != nil {
		t.Fatalf("invalid status response JSON: %v", err)
	}
	if statusResp["status"] != "pending" {
		t.Fatalf("expected status=pending, got %v", statusResp["status"])
	}
}

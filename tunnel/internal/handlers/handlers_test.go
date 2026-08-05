package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

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

func TestGetSettlementStatus_SuccessfulResultSettled(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.GET("/settlement/:actionId", h.GetSettlementStatus)

	result := ResultEnvelope{
		ActionID: "action-success",
		Status:   "success",
		Message:  "completed",
	}
	h.SettlementMgr.ProcessResult(result)

	req := httptest.NewRequest(http.MethodGet, "/settlement/action-success", nil)
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", res.Code)
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(res.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse response body: %v", err)
	}

	if resp["actionId"] != "action-success" {
		t.Fatalf("expected actionId action-success, got %v", resp["actionId"])
	}
	if settled, ok := resp["settled"].(bool); !ok || !settled {
		t.Fatalf("expected settled true, got %v", resp["settled"])
	}
	if resp["result"] == nil {
		t.Fatal("expected result payload to be present")
	}
}

func TestGetSettlementStatus_FailedResultNotSettled(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.GET("/settlement/:actionId", h.GetSettlementStatus)

	result := ResultEnvelope{
		ActionID: "action-error",
		Status:   "error",
		Message:  "timeout",
	}
	h.SettlementMgr.ProcessResult(result)

	req := httptest.NewRequest(http.MethodGet, "/settlement/action-error", nil)
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", res.Code)
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(res.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse response body: %v", err)
	}

	if settled, ok := resp["settled"].(bool); !ok || settled {
		t.Fatalf("expected settled false, got %v", resp["settled"])
	}
	if resp["result"] == nil {
		t.Fatal("expected result payload to be present")
	}
}

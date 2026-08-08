package handlers

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

func request(t *testing.T, h *Handlers, body string) *httptest.ResponseRecorder {
	t.Helper()
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/action", h.PostAction)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body)))
	return res
}

func TestPostAction_Success(t *testing.T) {
	h := NewHandlers(zap.NewNop())
	h.Execute = func(_ context.Context, _ []byte, id string) (ExecutionResult, error) {
		return ExecutionResult{ActionID: id, Status: "SUCCESS", Metrics: map[string]interface{}{"state_delta": 0.2}}, nil
	}
	if got := request(t, h, `{"actionId":"action-1","action":"move_forward"}`).Code; got != http.StatusOK {
		t.Fatalf("expected 200, got %d", got)
	}
}

func TestPostAction_InvalidJSON(t *testing.T) {
	if got := request(t, NewHandlers(zap.NewNop()), `{"actionId":`).Code; got != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", got)
	}
}

func TestPostAction_MissingActionID(t *testing.T) {
	if got := request(t, NewHandlers(zap.NewNop()), `{"action":"move_forward"}`).Code; got != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", got)
	}
}

func TestPostAction_FailedExecutionIsNon2xx(t *testing.T) {
	h := NewHandlers(zap.NewNop())
	errText := "simulator unavailable"
	h.Execute = func(_ context.Context, _ []byte, id string) (ExecutionResult, error) {
		return ExecutionResult{ActionID: id, Status: "FAILED", Error: &errText}, nil
	}
	if got := request(t, h, `{"actionId":"action-2","action":"move_forward"}`).Code; got != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", got)
	}
}

func TestPostAction_ReplayIsConflict(t *testing.T) {
	h := NewHandlers(zap.NewNop())
	h.Execute = func(_ context.Context, _ []byte, id string) (ExecutionResult, error) {
		return ExecutionResult{ActionID: id, Status: "REPLAY_REJECTED"}, nil
	}
	if got := request(t, h, `{"actionId":"action-3","action":"move_forward"}`).Code; got != http.StatusConflict {
		t.Fatalf("expected 409, got %d", got)
	}
}

func TestPostAction_TransportErrorIsNon2xx(t *testing.T) {
	h := NewHandlers(zap.NewNop())
	h.Execute = func(_ context.Context, _ []byte, _ string) (ExecutionResult, error) {
		return ExecutionResult{}, errors.New("delivery failed")
	}
	if got := request(t, h, `{"actionId":"action-4","action":"move_forward"}`).Code; got != http.StatusBadGateway {
		t.Fatalf("expected 502, got %d", got)
	}
}

func TestPostAction_ExecutionTimeoutIsGatewayTimeout(t *testing.T) {
	h := NewHandlers(zap.NewNop())
	h.Execute = func(_ context.Context, _ []byte, _ string) (ExecutionResult, error) {
		return ExecutionResult{}, ErrExecutionTimeout
	}
	if got := request(t, h, `{"actionId":"action-5","action":"move_forward"}`).Code; got != http.StatusGatewayTimeout {
		t.Fatalf("expected 504, got %d", got)
	}
}

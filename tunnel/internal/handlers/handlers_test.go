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

type recordingPublisher struct {
	payloads [][]byte
	err      error
}

func (p *recordingPublisher) Publish(_ string, payload []byte) error {
	if p.err != nil {
		return p.err
	}
	p.payloads = append(p.payloads, append([]byte(nil), payload...))
	return nil
}

func TestPostAction_ValidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	h.Publisher = &recordingPublisher{}
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

func TestPostAction_EnrichesActionContract(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlersForRobot(zap.NewNop(), "reachy-mini-kauker")
	h.Publisher = publisher
	router := gin.New()
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"look_at_apple","robot_id":"reachy-mini-kauker","params":{"request_id":"action-123","target_object":"apple"}}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected one publication, got %d", len(publisher.payloads))
	}
	var event map[string]interface{}
	if err := json.Unmarshal(publisher.payloads[0], &event); err != nil {
		t.Fatalf("published invalid event: %v", err)
	}
	if event["action_id"] != "action-123" {
		t.Fatalf("expected action_id action-123, got %v", event["action_id"])
	}
	if event["robot_id"] != "reachy-mini-kauker" {
		t.Fatalf("expected robot_id, got %v", event["robot_id"])
	}
	if event["skill_id"] != "look_at_apple" {
		t.Fatalf("expected skill_id, got %v", event["skill_id"])
	}
	if event["params_hash"] == "" {
		t.Fatal("expected params_hash")
	}
}

func TestPostAction_InvalidParamsContract(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlers(zap.NewNop())
	h.Publisher = publisher
	router := gin.New()
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"look_at_apple","params":"not-an-object"}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("invalid params must not be published")
	}
}

func TestPostAction_WrongRobot(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlersForRobot(zap.NewNop(), "reachy-mini-kauker")
	h.Publisher = publisher
	router := gin.New()
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"look_at_apple","robot_id":"another-robot","params":{}}`))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusForbidden {
		t.Fatalf("expected status 403, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("wrong-robot action must not be published")
	}
}

func TestPostAction_RejectsReplay(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlers(zap.NewNop())
	h.Publisher = publisher
	router := gin.New()
	router.POST("/action", h.PostAction)
	body := `{"action":"look_at_apple","params":{"request_id":"same-action","target_object":"apple"}}`

	first := httptest.NewRecorder()
	router.ServeHTTP(first, httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body)))
	second := httptest.NewRecorder()
	router.ServeHTTP(second, httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body)))

	if first.Code != http.StatusOK {
		t.Fatalf("expected first request 200, got %d", first.Code)
	}
	if second.Code != http.StatusConflict {
		t.Fatalf("expected replay status 409, got %d", second.Code)
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected one publication, got %d", len(publisher.payloads))
	}
}

func TestPostAction_DoesNotAcceptOrSettleOnSimulatorFailure(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlers(zap.NewNop())
	h.Publisher = publisher
	h.WaitForResult = func(_ string) (chan bool, func(), error) {
		result := make(chan bool, 1)
		result <- false
		return result, func() {}, nil
	}
	router := gin.New()
	router.POST("/action", h.PostAction)

	res := httptest.NewRecorder()
	router.ServeHTTP(res, httptest.NewRequest(
		http.MethodPost,
		"/action",
		bytes.NewBufferString(`{"action":"look_at_apple","params":{"request_id":"failed-action"}}`),
	))

	if res.Code != http.StatusBadGateway {
		t.Fatalf("expected 502 for simulator failure, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected action publication before waiting for result, got %d", len(publisher.payloads))
	}
	// The x402 Gin middleware settles only when the handler returns < 400;
	// this 502 is the explicit no-settlement contract for async failure.
}

func TestPostAction_RejectsSkillOutsideAllowlist(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlers(zap.NewNop())
	h.Publisher = publisher
	h.AllowedSkills = map[string]struct{}{"look_at_apple": {}}
	router := gin.New()
	router.POST("/action", h.PostAction)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(
		`{"action":"move_forward","params":{}}`,
	)))
	if res.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for disallowed skill, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("disallowed skill must not be published")
	}
}

func TestPostAction_RejectsDurationAboveLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	publisher := &recordingPublisher{}
	h := NewHandlers(zap.NewNop())
	h.Publisher = publisher
	h.MaxDurationSeconds = 5
	router := gin.New()
	router.POST("/action", h.PostAction)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(
		`{"action":"look_at_apple","params":{"duration":6}}`,
	)))
	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for excessive duration, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("excessive duration must not be published")
	}
}

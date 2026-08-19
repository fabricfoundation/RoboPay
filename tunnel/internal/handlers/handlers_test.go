package handlers

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

type recordingPublisher struct {
	payloads [][]byte
	topics   []string
	err      error
}

func (p *recordingPublisher) Publish(topic string, payload []byte) error {
	if p.err != nil {
		return p.err
	}
	p.payloads = append(p.payloads, append([]byte(nil), payload...))
	p.topics = append(p.topics, topic)
	return nil
}

func TestConfiguredZenohTopics(t *testing.T) {
	t.Setenv("ZENOH_ACTION_TOPIC", "robots/test/actions")
	t.Setenv("ZENOH_RESULT_TOPIC", "robots/test/results")
	h := NewHandlersForRobot(zap.NewNop(), "robot-test")
	if h.ActionTopic != "robots/test/actions" || h.ResultTopic != "robots/test/results" {
		t.Fatalf("unexpected configured topics: action=%q result=%q", h.ActionTopic, h.ResultTopic)
	}

	publisher := &recordingPublisher{}
	h.Publisher = publisher
	if err := h.publish([]byte(`{"action":"test_action"}`)); err != nil {
		t.Fatalf("publish failed: %v", err)
	}
	if len(publisher.topics) != 1 || publisher.topics[0] != "robots/test/actions" {
		t.Fatalf("expected configured action topic, got %v", publisher.topics)
	}
}

func TestZenohConfigUsesEndpointWhenNoConfigFileIsSet(t *testing.T) {
	t.Setenv("ZENOH_CONFIG", "")
	t.Setenv("ZENOH_ENDPOINT", "tcp/127.0.0.1:7447")

	config, err := zenohConfigFromEnvironment()
	if err != nil {
		t.Fatalf("build Zenoh configuration: %v", err)
	}
	rawEndpoints, err := config.Get(zenoh.ConfigConnectKey)
	if err != nil {
		t.Fatalf("read configured endpoints: %v", err)
	}
	var endpoints []string
	if err := json.Unmarshal([]byte(rawEndpoints), &endpoints); err != nil {
		t.Fatalf("decode configured endpoints %q: %v", rawEndpoints, err)
	}
	if len(endpoints) != 1 || endpoints[0] != "tcp/127.0.0.1:7447" {
		t.Fatalf("unexpected configured endpoints: %v", endpoints)
	}
}

// recordingSettler stands in for the deferred x402 settlement callback that
// main.go injects. Counting its calls is the settlement observation: any
// no-settlement assertion checks calls == 0.
type recordingSettler struct {
	mu      sync.Mutex
	calls   int
	err     error
	receipt *SettlementRecord
}

func (s *recordingSettler) settle(_ context.Context) (*SettlementRecord, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	if s.err != nil {
		return nil, s.err
	}
	if s.receipt != nil {
		return s.receipt, nil
	}
	return &SettlementRecord{Transaction: "0xtest", Network: "eip155:84532"}, nil
}

func (s *recordingSettler) callCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls
}

func buildRouter(h *Handlers, settle SettleFunc) *gin.Engine {
	router := gin.New()
	if settle != nil {
		router.Use(func(c *gin.Context) {
			c.Set("x402_settle", settle)
			c.Next()
		})
	}
	router.GET("/robot", h.GetRobotProfile)
	router.GET("/skills", h.GetSkills)
	router.POST("/action", h.PostAction)
	router.GET("/action/:action_id/status", h.GetActionStatus)
	return router
}

func testRegisteredSkills() map[string]struct{} {
	return map[string]struct{}{
		"navigate_obstacle_course": {},
		"stop":                     {},
	}
}

func testSkillCatalog() []SkillMetadata {
	return []SkillMetadata{
		{
			SkillID:         "navigate_obstacle_course",
			Description:     "test navigation",
			PaymentRequired: true,
			PriceUSDC:       "0.001",
			Params: map[string]ParamSchema{
				"target_object": {
					Type:   "string",
					Values: []string{"apple", "croissant", "duck"},
				},
				"duration": {
					Type:    "number",
					Minimum: numberPointer(0.1),
					Maximum: numberPointer(30),
				},
			},
		},
		{SkillID: "stop", Description: "test stop", PaymentRequired: true, PriceUSDC: "0.001", Params: map[string]ParamSchema{}},
	}
}

func numberPointer(value float64) *float64 { return &value }

// newTestHandlers builds handlers the way production main.go does: durable
// idempotency store (isolated per test) plus the registered-skill allowlist.
func newTestHandlers(t *testing.T, robotID string) (*Handlers, *recordingPublisher, *gin.Engine) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	t.Setenv("IDEMPOTENCY_STORE_PATH", filepath.Join(t.TempDir(), "replay.json"))
	if robotID == "" {
		robotID = "test-robot"
	}
	publisher := &recordingPublisher{}
	h := NewHandlersForRobot(zap.NewNop(), robotID)
	h.Publisher = publisher
	h.AllowedSkills = testRegisteredSkills()
	h.SkillCatalog = testSkillCatalog()
	// Wait for in-flight watcher goroutines before t.TempDir cleanup removes
	// the store directory, otherwise the durable write races the RemoveAll.
	t.Cleanup(h.WaitForPendingExecutions)
	return h, publisher, buildRouter(h, nil)
}

func postAction(router *gin.Engine, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	return res
}

func getStatus(router *gin.Engine, actionID string) (*httptest.ResponseRecorder, map[string]interface{}) {
	req := httptest.NewRequest(http.MethodGet, "/action/"+actionID+"/status", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	var payload map[string]interface{}
	_ = json.Unmarshal(res.Body.Bytes(), &payload)
	return res, payload
}

// waitForState polls the status endpoint until the async execution watcher
// records the wanted terminal state (the accepted/pending contract's second
// half). Fails the test if the state is not reached in time.
func waitForState(t *testing.T, router *gin.Engine, actionID, want string) map[string]interface{} {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	var last map[string]interface{}
	for time.Now().Before(deadline) {
		res, payload := getStatus(router, actionID)
		if res.Code == http.StatusOK {
			last = payload
			if payload["state"] == want {
				return payload
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("action %s never reached state %q (last: %v)", actionID, want, last)
	return nil
}

func errorCode(t *testing.T, res *httptest.ResponseRecorder) string {
	t.Helper()
	var payload map[string]interface{}
	if err := json.Unmarshal(res.Body.Bytes(), &payload); err != nil {
		t.Fatalf("response is not JSON: %v (%s)", err, res.Body.String())
	}
	code, _ := payload["error_code"].(string)
	return code
}

func TestRobotAndSkillDiscovery(t *testing.T) {
	_, _, router := newTestHandlers(t, "spot-discovery-test")

	robotRequest := httptest.NewRequest(http.MethodGet, "/robot", nil)
	robotResponse := httptest.NewRecorder()
	router.ServeHTTP(robotResponse, robotRequest)
	if robotResponse.Code != http.StatusOK {
		t.Fatalf("expected robot discovery 200, got %d: %s", robotResponse.Code, robotResponse.Body.String())
	}

	skillsRequest := httptest.NewRequest(http.MethodGet, "/skills", nil)
	skillsResponse := httptest.NewRecorder()
	router.ServeHTTP(skillsResponse, skillsRequest)
	if skillsResponse.Code != http.StatusOK {
		t.Fatalf("expected skill discovery 200, got %d: %s", skillsResponse.Code, skillsResponse.Body.String())
	}
	var payload struct {
		RobotID string `json:"robot_id"`
		Skills  []struct {
			SkillID   string `json:"skill_id"`
			PriceUSDC string `json:"price_usdc"`
			Enabled   bool   `json:"enabled"`
		} `json:"skills"`
	}
	if err := json.Unmarshal(skillsResponse.Body.Bytes(), &payload); err != nil {
		t.Fatalf("invalid discovery response: %v", err)
	}
	if payload.RobotID != "spot-discovery-test" || len(payload.Skills) != 2 {
		t.Fatalf("unexpected discovery payload: %+v", payload)
	}
	for _, skill := range payload.Skills {
		if skill.PriceUSDC != "0.001" || !skill.Enabled {
			t.Fatalf("skill must expose price and enabled state: %+v", skill)
		}
	}
}

// The reviewer's fail-open finding: {"command":"start"} used to be accepted
// with 200. It must now be rejected with 400 MISSING_ACTION and never
// published to Zenoh.
func TestPostAction_RejectsPayloadWithoutAction(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"command":"start"}`, nil)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d: %s", res.Code, res.Body.String())
	}
	if code := errorCode(t, res); code != "MISSING_ACTION" {
		t.Fatalf("expected MISSING_ACTION, got %q", code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("payload without a skill must not be published")
	}
}

func TestPostAction_RejectsEmptyBody(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, ``, nil)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d: %s", res.Code, res.Body.String())
	}
	if code := errorCode(t, res); code != "MISSING_ACTION" {
		t.Fatalf("expected MISSING_ACTION, got %q", code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("empty body must not be published")
	}
}

func TestPostAction_InvalidJSON(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"command":`, nil)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("invalid JSON must not be published")
	}
}

func TestPostAction_FailsClosedWithoutAllowlist(t *testing.T) {
	h, publisher, router := newTestHandlers(t, "")
	h.AllowedSkills = nil // simulate a deployment without any allowlist

	res := postAction(router, `{"action":"navigate_obstacle_course","params":{}}`, nil)

	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected status 503, got %d: %s", res.Code, res.Body.String())
	}
	if code := errorCode(t, res); code != "ALLOWLIST_NOT_CONFIGURED" {
		t.Fatalf("expected ALLOWLIST_NOT_CONFIGURED, got %q", code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("nothing may be published when the allowlist is absent")
	}
}

// A damaged idempotency file must never be interpreted as an empty store:
// otherwise a restart after corruption would replay a paid action.
func TestPostAction_FailsClosedWithCorruptReplayStore(t *testing.T) {
	gin.SetMode(gin.TestMode)
	storePath := filepath.Join(t.TempDir(), "replay.json")
	if err := os.WriteFile(storePath, []byte(`{"unfinished":`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("IDEMPOTENCY_STORE_PATH", storePath)
	publisher := &recordingPublisher{}
	h := NewHandlersForRobot(zap.NewNop(), "test-robot")
	h.Publisher = publisher
	h.AllowedSkills = testRegisteredSkills()
	h.SkillCatalog = testSkillCatalog()
	router := buildRouter(h, nil)

	res := postAction(router, `{"action":"navigate_obstacle_course","idempotency_key":"corrupt-store","params":{"target_object":"apple"}}`, nil)
	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected corrupt replay store to fail closed with 503, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("corrupt replay state must not publish an action")
	}
}

func TestPostAction_RejectsUnknownSkill(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"action":"move_forward","params":{}}`, nil)

	if res.Code != http.StatusForbidden {
		t.Fatalf("expected status 403, got %d: %s", res.Code, res.Body.String())
	}
	if code := errorCode(t, res); code != "SKILL_NOT_ALLOWED" {
		t.Fatalf("expected SKILL_NOT_ALLOWED, got %q", code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("unknown skill must not be published")
	}
}

// The immediate accepted/pending contract: POST answers 202 right away with
// the action_id, and the terminal result is later served by the status
// endpoint under the same action_id.
func TestPostAction_ImmediateAcceptedPendingContract(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "spot-mujoco-sim-01")

	res := postAction(router, `{"action":"navigate_obstacle_course","robot_id":"spot-mujoco-sim-01","action_id":"action-123","idempotency_key":"action-123","params":{"target_object":"apple"}}`, nil)

	if res.Code != http.StatusAccepted {
		t.Fatalf("expected status 202, got %d: %s", res.Code, res.Body.String())
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
	if event["robot_id"] != "spot-mujoco-sim-01" {
		t.Fatalf("expected robot_id, got %v", event["robot_id"])
	}
	if event["skill_id"] != "navigate_obstacle_course" {
		t.Fatalf("expected skill_id, got %v", event["skill_id"])
	}
	if event["params_hash"] == "" {
		t.Fatal("expected params_hash")
	}
	canonical, ok := event["params_canonical"].(string)
	if !ok || canonical == "" {
		t.Fatalf("expected exact params_canonical string, got %T %v", event["params_canonical"], event["params_canonical"])
	}
	hash := sha256.Sum256([]byte(canonical))
	if event["params_hash"] != fmt.Sprintf("sha256:%x", hash[:]) {
		t.Fatalf("params_hash does not bind params_canonical: %v", event["params_hash"])
	}
	var response map[string]interface{}
	if err := json.Unmarshal(res.Body.Bytes(), &response); err != nil {
		t.Fatalf("response is not JSON: %v", err)
	}
	if response["action_id"] != "action-123" {
		t.Fatalf("202 response must echo action_id, got %v", response["action_id"])
	}
	if response["status"] != "accepted" || response["state"] != "pending" {
		t.Fatalf("expected accepted/pending, got %v/%v", response["status"], response["state"])
	}
	if response["settlement"] != "pending-execution-gated" {
		t.Fatalf("expected pending-execution-gated marker, got %v", response["settlement"])
	}
	if response["status_url"] != "/action/action-123/status" {
		t.Fatalf("expected status_url for the same actionId, got %v", response["status_url"])
	}

	// Terminal result carries the same actionId via the status endpoint.
	status := waitForState(t, router, "action-123", "succeeded")
	if status["action_id"] != "action-123" {
		t.Fatalf("status must carry the same action_id, got %v", status["action_id"])
	}
	if status["settled"] != false {
		t.Fatal("no settle callback was injected, so settled must be false")
	}
}

func TestGetActionStatus_UnknownActionIs404(t *testing.T) {
	_, _, router := newTestHandlers(t, "")

	res, _ := getStatus(router, "never-issued")
	if res.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for unknown action id, got %d", res.Code)
	}
	if code := errorCode(t, res); code != "UNKNOWN_ACTION" {
		t.Fatalf("expected UNKNOWN_ACTION, got %q", code)
	}
}

func TestPostAction_InvalidParamsContract(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"action":"navigate_obstacle_course","params":"not-an-object"}`, nil)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("invalid params must not be published")
	}
}

func TestPostAction_RejectsUnknownParameterBeforePublish(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"action":"navigate_obstacle_course","params":{"not_in_profile":true}}`, nil)

	if res.Code != http.StatusBadRequest || errorCode(t, res) != "INVALID_PARAMS" {
		t.Fatalf("expected INVALID_PARAMS before publish, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("unknown parameter must not be published")
	}
}

func TestPostAction_RejectsDivergentActionAndSkill(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")

	res := postAction(router, `{"action":"navigate_obstacle_course","skill_id":"stop","params":{}}`, nil)

	if res.Code != http.StatusBadRequest || errorCode(t, res) != "INVALID_ACTION" {
		t.Fatalf("expected INVALID_ACTION before publish, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("divergent action and skill_id must not be published")
	}
}

func TestPostAction_WrongRobot(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "spot-mujoco-sim-01")

	res := postAction(router, `{"action":"navigate_obstacle_course","robot_id":"another-robot","params":{}}`, nil)

	if res.Code != http.StatusForbidden {
		t.Fatalf("expected status 403, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("wrong-robot action must not be published")
	}
}

func TestPostAction_RejectsReplay(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")
	body := `{"action":"navigate_obstacle_course","action_id":"same-action","idempotency_key":"same-action","params":{"target_object":"apple"}}`

	first := postAction(router, body, nil)
	second := postAction(router, body, nil)

	if first.Code != http.StatusAccepted {
		t.Fatalf("expected first request 202, got %d", first.Code)
	}
	if second.Code != http.StatusConflict {
		t.Fatalf("expected replay status 409, got %d", second.Code)
	}
	if code := errorCode(t, second); code != "REPLAY_DETECTED" {
		t.Fatalf("expected REPLAY_DETECTED, got %q", code)
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected one publication, got %d", len(publisher.payloads))
	}
}

// Replay protection must survive a process restart: the durable store is
// reloaded from disk and the same idempotency key still gets 409 with zero
// new publications (reviewer: "restart/retry can produce another actuation").
func TestPostAction_ReplayRejectedAfterRestart(t *testing.T) {
	gin.SetMode(gin.TestMode)
	storePath := filepath.Join(t.TempDir(), "replay.json")
	t.Setenv("IDEMPOTENCY_STORE_PATH", storePath)
	body := `{"action":"navigate_obstacle_course","action_id":"restart-action","idempotency_key":"restart-action","params":{}}`

	firstPublisher := &recordingPublisher{}
	firstHandlers := NewHandlersForRobot(zap.NewNop(), "")
	firstHandlers.Publisher = firstPublisher
	firstHandlers.AllowedSkills = testRegisteredSkills()
	firstHandlers.SkillCatalog = testSkillCatalog()
	t.Cleanup(firstHandlers.WaitForPendingExecutions)
	firstRouter := buildRouter(firstHandlers, nil)
	if res := postAction(firstRouter, body, nil); res.Code != http.StatusAccepted {
		t.Fatalf("expected first request 202, got %d: %s", res.Code, res.Body.String())
	}
	// Let the async watcher reach the terminal state before "restarting".
	waitForState(t, firstRouter, "restart-action", "succeeded")

	// Simulate a tunnel restart: brand-new handlers reload the same file.
	secondPublisher := &recordingPublisher{}
	secondHandlers := NewHandlersForRobot(zap.NewNop(), "")
	secondHandlers.Publisher = secondPublisher
	secondHandlers.AllowedSkills = testRegisteredSkills()
	secondHandlers.SkillCatalog = testSkillCatalog()
	t.Cleanup(secondHandlers.WaitForPendingExecutions)
	secondRouter := buildRouter(secondHandlers, nil)

	res := postAction(secondRouter, body, nil)
	if res.Code != http.StatusConflict {
		t.Fatalf("expected 409 after restart, got %d: %s", res.Code, res.Body.String())
	}
	if len(secondPublisher.payloads) != 0 {
		t.Fatal("replay after restart must not actuate the simulator")
	}

	// The status endpoint also survives the restart under the same actionId.
	statusRes, status := getStatus(secondRouter, "restart-action")
	if statusRes.Code != http.StatusOK || status["state"] != "succeeded" {
		t.Fatalf("expected persisted succeeded state after restart, got %d %v", statusRes.Code, status)
	}
}

// The same x402 payment payload must never actuate twice, even when the
// caller invents a fresh idempotency key for the retry.
func TestPostAction_RejectsPaymentReplayWithFreshKey(t *testing.T) {
	_, publisher, router := newTestHandlers(t, "")
	headers := map[string]string{"PAYMENT-SIGNATURE": "signed-payment-payload"}

	first := postAction(router, `{"action":"navigate_obstacle_course","action_id":"pay-1","idempotency_key":"pay-1","params":{}}`, headers)
	second := postAction(router, `{"action":"navigate_obstacle_course","action_id":"pay-2","idempotency_key":"pay-2","params":{}}`, headers)

	if first.Code != http.StatusAccepted {
		t.Fatalf("expected first request 202, got %d: %s", first.Code, first.Body.String())
	}
	if second.Code != http.StatusConflict {
		t.Fatalf("expected 409 for replayed payment, got %d: %s", second.Code, second.Body.String())
	}
	if code := errorCode(t, second); code != "PAYMENT_REPLAY_DETECTED" {
		t.Fatalf("expected PAYMENT_REPLAY_DETECTED, got %q", code)
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected exactly one actuation, got %d", len(publisher.payloads))
	}
}

// The replay key must be derived from the parsed/verified payment, not the
// base64 header bytes. Two serializations of the same authorization must
// still produce one publication.
func TestPostAction_RejectsSemanticallyEquivalentVerifiedPaymentReplay(t *testing.T) {
	h, publisher, _ := newTestHandlers(t, "")
	verifiedPayment := map[string]interface{}{
		"x402Version": float64(2),
		"payload": map[string]interface{}{
			"signature": "0xsame-signature",
			"authorization": map[string]interface{}{
				"from":  "0x1111111111111111111111111111111111111111",
				"nonce": "0xsame-nonce",
			},
		},
	}
	router := gin.New()
	router.Use(func(c *gin.Context) {
		c.Set("x402_payload", verifiedPayment)
		c.Next()
	})
	router.POST("/action", h.PostAction)

	first := postAction(router,
		`{"action":"navigate_obstacle_course","action_id":"semantic-1","idempotency_key":"semantic-1","params":{}}`,
		map[string]string{"PAYMENT-SIGNATURE": "eyJwYXlsb2FkIjp7ImEiOjF9fQ=="},
	)
	second := postAction(router,
		`{"action":"navigate_obstacle_course","action_id":"semantic-2","idempotency_key":"semantic-2","params":{}}`,
		// Same JSON authorization can legitimately be transported with a
		// different base64 padding/layout; the verified object above is equal.
		map[string]string{"PAYMENT-SIGNATURE": "eyJwYXlsb2FkIjp7ICJhIiA6IDEgfX0"},
	)
	if first.Code != http.StatusAccepted {
		t.Fatalf("expected first request 202, got %d: %s", first.Code, first.Body.String())
	}
	if second.Code != http.StatusConflict || errorCode(t, second) != "PAYMENT_REPLAY_DETECTED" {
		t.Fatalf("expected semantic payment replay 409, got %d: %s", second.Code, second.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("semantically identical verified payment must actuate once, got %d", len(publisher.payloads))
	}
}

// Settlement is deferred and execution-gated: the settle callback runs
// exactly once, only after the simulator reports success, and the receipt is
// exposed by the status endpoint.
func TestPostAction_SettlesOnlyAfterSimulatorSuccess(t *testing.T) {
	h, _, _ := newTestHandlers(t, "")
	h.WaitForResult = func(_ string) (chan bool, func(), error) {
		result := make(chan bool, 1)
		result <- true
		return result, func() {}, nil
	}
	settler := &recordingSettler{receipt: &SettlementRecord{Transaction: "0xabc", Network: "eip155:84532", Payer: "0xpayer"}}
	router := buildRouter(h, settler.settle)
	body := `{"action":"navigate_obstacle_course","action_id":"settle-action","idempotency_key":"settle-action","params":{}}`

	res := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-1"})
	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", res.Code, res.Body.String())
	}

	status := waitForState(t, router, "settle-action", "succeeded")
	if status["settled"] != true {
		t.Fatalf("expected settled=true after success, got %v", status["settled"])
	}
	settlement, _ := status["settlement"].(map[string]interface{})
	if settlement == nil || settlement["transaction"] != "0xabc" {
		t.Fatalf("expected settlement receipt with transaction, got %v", status["settlement"])
	}
	if settler.callCount() != 1 {
		t.Fatalf("expected exactly one settle call, got %d", settler.callCount())
	}
}

func TestPostAction_DoesNotSettleOnSimulatorFailure(t *testing.T) {
	h, publisher, _ := newTestHandlers(t, "")
	h.WaitForResult = func(_ string) (chan bool, func(), error) {
		result := make(chan bool, 1)
		result <- false
		return result, func() {}, nil
	}
	settler := &recordingSettler{}
	router := buildRouter(h, settler.settle)
	body := `{"action":"navigate_obstacle_course","action_id":"failed-action","idempotency_key":"failed-action","params":{}}`

	res := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-fail"})
	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202 (accepted/pending), got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected action publication, got %d", len(publisher.payloads))
	}

	status := waitForState(t, router, "failed-action", "failed")
	if status["error_code"] != "SIMULATOR_EXECUTION_FAILED" {
		t.Fatalf("expected SIMULATOR_EXECUTION_FAILED, got %v", status["error_code"])
	}
	if status["settled"] != false {
		t.Fatal("failure must never settle")
	}
	if settler.callCount() != 0 {
		t.Fatalf("expected ZERO settle calls on failure, got %d", settler.callCount())
	}

	// The failed reservation is kept (not deleted): a retry of the same key
	// after failure is 409 and produces zero additional actuations.
	retry := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-fail-2"})
	if retry.Code != http.StatusConflict {
		t.Fatalf("expected 409 replay after failure, got %d: %s", retry.Code, retry.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatal("retry after failure must not actuate again")
	}
	if settler.callCount() != 0 {
		t.Fatal("retry after failure must not settle either")
	}
}

func TestPostAction_DoesNotSettleForMismatchedResult(t *testing.T) {
	h, publisher, _ := newTestHandlers(t, "robot-a")
	h.WaitForCorrelatedResult = func(metadata actionMetadata) (chan executionResult, func(), error) {
		result := make(chan executionResult, 1)
		result <- executionResult{
			ActionID:       metadata.ActionID,
			RobotID:        metadata.RobotID,
			SkillID:        "stop", // wrong action for this published request
			ParamsHash:     metadata.ParamsHash,
			IdempotencyKey: metadata.IdempotencyKey,
			Status:         "success",
		}
		return result, func() {}, nil
	}
	settler := &recordingSettler{}
	router := buildRouter(h, settler.settle)
	body := `{"action":"navigate_obstacle_course","robot_id":"robot-a","action_id":"mismatch-action","idempotency_key":"mismatch-action","params":{}}`

	res := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-mismatch"})
	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected exactly one publication, got %d", len(publisher.payloads))
	}
	status := waitForState(t, router, "mismatch-action", "failed")
	if status["error_code"] != "SIMULATOR_RESULT_MISMATCH" {
		t.Fatalf("expected mismatched result to be rejected, got %v", status["error_code"])
	}
	if settler.callCount() != 0 {
		t.Fatalf("mismatched result must make zero settlement calls, got %d", settler.callCount())
	}
}

func TestPostAction_PersistsStructuredResult(t *testing.T) {
	h, _, _ := newTestHandlers(t, "robot-result")
	h.WaitForCorrelatedResult = func(metadata actionMetadata) (chan executionResult, func(), error) {
		result := make(chan executionResult, 1)
		result <- executionResult{
			ActionID:       metadata.ActionID,
			RobotID:        metadata.RobotID,
			SkillID:        metadata.SkillID,
			ParamsHash:     metadata.ParamsHash,
			IdempotencyKey: metadata.IdempotencyKey,
			Status:         "success",
			Result:         json.RawMessage(`{"metric":1,"policy":"closed-loop"}`),
		}
		return result, func() {}, nil
	}
	router := buildRouter(h, nil)
	body := `{"action":"navigate_obstacle_course","robot_id":"robot-result","action_id":"result-action","idempotency_key":"result-action","params":{}}`
	if res := postAction(router, body, nil); res.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", res.Code, res.Body.String())
	}
	status := waitForState(t, router, "result-action", "succeeded")
	result, ok := status["result"].(map[string]interface{})
	if !ok || result["policy"] != "closed-loop" {
		t.Fatalf("expected structured bridge result in status, got %v", status["result"])
	}
}

func TestPostAction_TimesOutWithoutSettlementAndKeepsReservation(t *testing.T) {
	h, publisher, _ := newTestHandlers(t, "")
	t.Setenv("EXECUTION_TIMEOUT_SECONDS", "0.05")
	h.WaitForResult = func(_ string) (chan bool, func(), error) {
		return make(chan bool), func() {}, nil // no result ever arrives
	}
	settler := &recordingSettler{}
	router := buildRouter(h, settler.settle)
	body := `{"action":"navigate_obstacle_course","action_id":"timeout-action","idempotency_key":"timeout-action","params":{}}`

	res := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-timeout"})
	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202 (accepted/pending), got %d: %s", res.Code, res.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatalf("expected one publication, got %d", len(publisher.payloads))
	}

	status := waitForState(t, router, "timeout-action", "timeout")
	if status["error_code"] != "SIMULATOR_RESULT_TIMEOUT" {
		t.Fatalf("expected SIMULATOR_RESULT_TIMEOUT, got %v", status["error_code"])
	}
	if status["settled"] != false {
		t.Fatal("timeout must never settle")
	}
	if settler.callCount() != 0 {
		t.Fatalf("expected ZERO settle calls on timeout, got %d", settler.callCount())
	}

	retry := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-timeout-2"})
	if retry.Code != http.StatusConflict {
		t.Fatalf("expected 409 replay after timeout, got %d: %s", retry.Code, retry.Body.String())
	}
	if len(publisher.payloads) != 1 {
		t.Fatal("retry after timeout must not actuate again")
	}
}

// If execution succeeded but the deferred settlement errors, the status must
// say so instead of silently pretending the payment went through.
func TestPostAction_SettlementFailureIsSurfaced(t *testing.T) {
	h, _, _ := newTestHandlers(t, "")
	h.WaitForResult = func(_ string) (chan bool, func(), error) {
		result := make(chan bool, 1)
		result <- true
		return result, func() {}, nil
	}
	settler := &recordingSettler{err: errors.New("facilitator unavailable")}
	router := buildRouter(h, settler.settle)
	body := `{"action":"navigate_obstacle_course","action_id":"settle-fail","idempotency_key":"settle-fail","params":{}}`

	res := postAction(router, body, map[string]string{"PAYMENT-SIGNATURE": "payment-x"})
	if res.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", res.Code)
	}
	status := waitForState(t, router, "settle-fail", "settlement_failed")
	if status["settled"] != false {
		t.Fatal("failed settlement must report settled=false")
	}
	if status["error_code"] != "SETTLEMENT_FAILED" {
		t.Fatalf("expected SETTLEMENT_FAILED, got %v", status["error_code"])
	}
}

func TestPostAction_RejectsSkillOutsideAllowlist(t *testing.T) {
	h, publisher, router := newTestHandlers(t, "")
	h.AllowedSkills = map[string]struct{}{"navigate_obstacle_course": {}}

	res := postAction(router, `{"action":"move_forward","params":{}}`, nil)
	if res.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for disallowed skill, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("disallowed skill must not be published")
	}
}

func TestPostAction_RejectsDurationAboveLimit(t *testing.T) {
	h, publisher, router := newTestHandlers(t, "")
	h.MaxDurationSeconds = 5

	res := postAction(router, `{"action":"navigate_obstacle_course","params":{"duration":6}}`, nil)
	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for excessive duration, got %d", res.Code)
	}
	if len(publisher.payloads) != 0 {
		t.Fatal("excessive duration must not be published")
	}
}

package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// fakePublisher stands in for the Zenoh session so the handler's contract can
// be exercised without one.
type fakePublisher struct{ published [][]byte }

func (f *fakePublisher) Publish(_ string, payload []byte) error {
	f.published = append(f.published, payload)
	return nil
}

func newTestHandlers() (*Handlers, *fakePublisher) {
	h := NewHandlers(zap.NewNop())
	pub := &fakePublisher{}
	h.Publisher = pub
	// A store per test, so one test's results cannot answer another's action.
	h.Statuses = newStatusStore()
	return h, pub
}

// settleSpy stands in for the payment gate's settlement callback so a test can
// see whether money would have moved.
type settleSpy struct {
	mu       sync.Mutex
	calls    int
	failWith error
}

func (s *settleSpy) fn() SettleFunc {
	return func(context.Context) (*SettlementRecord, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.calls++
		if s.failWith != nil {
			return nil, s.failWith
		}
		return &SettlementRecord{Transaction: "0xtest", Network: "eip155:84532"}, nil
	}
}

func (s *settleSpy) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls
}

func post(h *Handlers, body string, spy *settleSpy) *httptest.ResponseRecorder {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/action", func(c *gin.Context) {
		if spy != nil {
			c.Set("x402_settle", spy.fn())
		}
		h.PostAction(c)
	})
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	return res
}

// settlementCalls waits briefly for the background watcher to reach its
// decision, then reports how many times it settled.
func settlementCalls(spy *settleSpy) int {
	for i := 0; i < 100; i++ {
		if spy.count() > 0 {
			return spy.count()
		}
		time.Sleep(5 * time.Millisecond)
	}
	return spy.count()
}

// answer delivers a simulator result once the action has been published, the
// way the Zenoh subscriber would.
func answer(h *Handlers, actionID, state string) {
	go func() {
		time.Sleep(20 * time.Millisecond)
		h.Statuses.put(ActionStatus{ActionID: actionID, State: state})
	}()
}

// -- the contract that keeps a failed action from being paid for -------------

// The x402 middleware settles after this handler returns and only when the
// response is not an error, so the status code the handler chooses *is* the
// settlement decision. These four tests are that decision.

func TestPostActionAnswersImmediatelyWithAccepted(t *testing.T) {
	h, pub := newTestHandlers()
	spy := &settleSpy{}
	answer(h, "act-1", stateSucceeded)

	res := post(h, `{"action_id":"act-1","robot_id":"atlas-sim-01","skill_id":"inspect_shelf","idempotency_key":"idem-1","params":{"maxDurationSec":5}}`, spy)

	if res.Code != http.StatusAccepted {
		t.Fatalf("the tunnel contract answers 202 the moment an action is "+
			"accepted; got %d", res.Code)
	}
	if !bytes.Contains(res.Body.Bytes(), []byte("act-1")) {
		t.Fatalf("the 202 must carry the action_id to correlate on, got %s",
			res.Body.String())
	}
	if len(pub.published) != 1 {
		t.Fatalf("expected the action to reach the robot once, got %d", len(pub.published))
	}
}

func TestSettlementFollowsSuccess(t *testing.T) {
	h, _ := newTestHandlers()
	spy := &settleSpy{}
	answer(h, "act-2", stateSucceeded)

	post(h, `{"action_id":"act-2","robot_id":"atlas-sim-01","skill_id":"inspect_shelf","idempotency_key":"idem-2","params":{"maxDurationSec":5}}`, spy)

	if got := settlementCalls(spy); got != 1 {
		t.Fatalf("a completed episode should settle exactly once, settled %d times", got)
	}
}

// The guarantee the bounty turns on: work that did not succeed is not paid for.
func TestAFailedEpisodeIsNeverSettled(t *testing.T) {
	h, _ := newTestHandlers()
	spy := &settleSpy{}
	answer(h, "act-3", stateFailed)

	res := post(h, `{"action_id":"act-3","robot_id":"atlas-sim-01","skill_id":"inspect_shelf","idempotency_key":"idem-3","params":{"maxDurationSec":5}}`, spy)

	if res.Code != http.StatusAccepted {
		t.Fatalf("acceptance is about the request, not the outcome; got %d", res.Code)
	}
	if got := settlementCalls(spy); got != 0 {
		t.Fatalf("a failed episode was settled %d time(s)", got)
	}
	status, ok := h.Statuses.get("act-3")
	if !ok || status.State != stateFailed {
		t.Fatalf("the failure must be readable from the status endpoint, got %+v", status)
	}
	if status.Settled {
		t.Fatalf("a failed action is reported as settled")
	}
}

func TestASilentRobotIsNeverSettled(t *testing.T) {
	t.Setenv("ACTION_TIMEOUT_SECONDS", "0.2")
	h, _ := newTestHandlers()
	spy := &settleSpy{}
	// No answer is ever delivered.

	post(h, `{"action_id":"act-4","robot_id":"atlas-sim-01","skill_id":"inspect_shelf","idempotency_key":"idem-4","params":{"maxDurationSec":5}}`, spy)
	time.Sleep(400 * time.Millisecond)

	if got := spy.count(); got != 0 {
		t.Fatalf("a timed-out episode was settled %d time(s)", got)
	}
	if status, ok := h.Statuses.get("act-4"); !ok || status.State != stateTimeout {
		t.Fatalf("a timeout must be readable as a timeout, got %+v", status)
	}
}

func TestPostActionRefusesAnActionItCannotCorrelate(t *testing.T) {
	h, pub := newTestHandlers()
	spy := &settleSpy{}

	res := post(h, `{"command":"start"}`, spy)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("an action with no action_id cannot be correlated, so its outcome "+
			"is unknowable and it must not settle; got %d", res.Code)
	}
	// The status code alone would pass even if the action had already been put
	// on the wire, which is the failure this test exists to catch: a request
	// that will be refused must never reach the robot.
	if len(pub.published) != 0 {
		t.Fatalf("a refused action reached the robot: %d message(s) published",
			len(pub.published))
	}
	if spy.count() != 0 {
		t.Fatalf("a refused action was settled")
	}
}

func TestPostActionRejectsInvalidJSON(t *testing.T) {
	h, pub := newTestHandlers()

	res := post(h, `{"command":`, nil)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
	if len(pub.published) != 0 {
		t.Fatalf("an unparseable action reached the robot: %d message(s) published",
			len(pub.published))
	}
}

func TestNothingReachesTheRobotUntilTheRequestIsAccepted(t *testing.T) {
	// One table for the refusals, so a new refusal path cannot be added without
	// someone deciding what it does to the robot.
	// The bridge refuses an envelope missing any of the four identity fields, so
	// publishing one only puts a message on the wire that is going to be
	// rejected at the other end.
	full := `"action_id":"a","robot_id":"r","skill_id":"s","idempotency_key":"i"`
	without := func(field string) string {
		parts := strings.Split(full, ",")
		kept := parts[:0]
		for _, part := range parts {
			if !strings.HasPrefix(part, `"`+field+`"`) {
				kept = append(kept, part)
			}
		}
		return "{" + strings.Join(kept, ",") + `,"params":{"maxDurationSec":5}}`
	}
	for name, body := range map[string]string{
		"no action_id":       without("action_id"),
		"no robot_id":        without("robot_id"),
		"no skill_id":        without("skill_id"),
		"no idempotency_key": without("idempotency_key"),
		"empty action_id":    `{"action_id":"","robot_id":"r","skill_id":"s","idempotency_key":"i"}`,
		"malformed json":     `{"action_id":`,
	} {
		t.Run(name, func(t *testing.T) {
			h, pub := newTestHandlers()
			res := post(h, body, nil)
			if res.Code < 400 {
				t.Fatalf("expected a refusal, got %d", res.Code)
			}
			if len(pub.published) != 0 {
				t.Fatalf("refused (%d) but still published %d message(s)",
					res.Code, len(pub.published))
			}
		})
	}
}

// -- discovery ---------------------------------------------------------------

func TestActionStatusIsPendingUntilTheRobotAnswers(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h, _ := newTestHandlers()
	router := gin.New()
	router.GET("/action/:action_id/status", h.GetActionStatus)

	req := httptest.NewRequest(http.MethodGet, "/action/act-unknown/status", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", res.Code)
	}
	if !bytes.Contains(res.Body.Bytes(), []byte(statePending)) {
		t.Fatalf("an unanswered action should read as pending, got %s", res.Body.String())
	}
}

// -- identity and payee ------------------------------------------------------

// The wiki asks that a robot's identity bind to the payee wallet. The
// authenticating handshake between a robot and the relay belongs to the shared
// tunnel and gateway, but the half this tunnel owns is checkable: the identity
// it answers for and the address it is paid to come from one configuration and
// are advertised together, so a caller can see which wallet the robot it is
// talking to gets paid at before paying anything.
func TestTheAdvertisedPayeeIsTheConfiguredOne(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h, _ := newTestHandlers()
	h.RobotID = "atlas-sim-01"
	h.PayTo = "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8"
	h.Network = "eip155:84532"

	router := gin.New()
	router.GET("/robot", h.GetRobotProfile)
	req := httptest.NewRequest(http.MethodGet, "/robot", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", res.Code)
	}
	var profile struct {
		RobotID string `json:"robot_id"`
		PayTo   string `json:"pay_to"`
		Network string `json:"network"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &profile); err != nil {
		t.Fatalf("unreadable robot profile: %v", err)
	}
	if profile.RobotID != h.RobotID {
		t.Fatalf("advertised robot_id %q, configured %q", profile.RobotID, h.RobotID)
	}
	if profile.PayTo != h.PayTo {
		t.Fatalf("advertised pay_to %q, configured %q — a caller paying this robot "+
			"would be told the wrong wallet", profile.PayTo, h.PayTo)
	}
	if profile.Network != h.Network {
		t.Fatalf("advertised network %q, configured %q", profile.Network, h.Network)
	}
}

// A robot that has not been told who it is paid to must not advertise an empty
// payee as though it were an address.
func TestAnUnconfiguredPayeeIsNotAdvertisedAsAnAddress(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h, _ := newTestHandlers()
	h.RobotID = "atlas-sim-01"
	h.PayTo = ""

	router := gin.New()
	router.GET("/robot", h.GetRobotProfile)
	req := httptest.NewRequest(http.MethodGet, "/robot", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	var profile struct {
		PayTo string `json:"pay_to"`
	}
	_ = json.Unmarshal(res.Body.Bytes(), &profile)
	if profile.PayTo != "" {
		t.Fatalf("an unconfigured payee was advertised as %q", profile.PayTo)
	}
}

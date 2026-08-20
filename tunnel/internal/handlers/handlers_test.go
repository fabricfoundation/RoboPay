package handlers

import (
	"bytes"
	"net/http"
	"net/http/httptest"
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

func post(h *Handlers, body string) *httptest.ResponseRecorder {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/action", h.PostAction)
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(body))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	return res
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

func TestPostActionSucceedsOnlyWhenTheRobotSucceeded(t *testing.T) {
	h, pub := newTestHandlers()
	answer(h, "act-1", stateSucceeded)

	res := post(h, `{"action_id":"act-1","params":{"maxDurationSec":5}}`)

	if res.Code != http.StatusOK {
		t.Fatalf("a completed action should answer 200, got %d", res.Code)
	}
	if len(pub.published) != 1 {
		t.Fatalf("expected the action to reach the robot once, got %d", len(pub.published))
	}
}

func TestPostActionReportsFailureSoThePaymentIsNotSettled(t *testing.T) {
	h, _ := newTestHandlers()
	answer(h, "act-2", stateFailed)

	res := post(h, `{"action_id":"act-2","params":{"maxDurationSec":5}}`)

	if res.Code < 400 {
		t.Fatalf("a failed action answered %d; anything under 400 settles the payment", res.Code)
	}
}

func TestPostActionReportsATimeoutRatherThanAssumingSuccess(t *testing.T) {
	t.Setenv("ACTION_TIMEOUT_SECONDS", "0.2")
	h, _ := newTestHandlers()
	// No answer is ever delivered.

	res := post(h, `{"action_id":"act-3","params":{"maxDurationSec":5}}`)

	if res.Code != http.StatusGatewayTimeout {
		t.Fatalf("a silent robot should answer 504, got %d", res.Code)
	}
}

func TestPostActionRefusesAnActionItCannotCorrelate(t *testing.T) {
	h, pub := newTestHandlers()

	res := post(h, `{"command":"start"}`)

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
}

func TestPostActionRejectsInvalidJSON(t *testing.T) {
	h, pub := newTestHandlers()

	res := post(h, `{"command":`)

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
	for name, body := range map[string]string{
		"no action_id":    `{"params":{"maxDurationSec":5}}`,
		"empty action_id": `{"action_id":"","params":{"maxDurationSec":5}}`,
		"malformed json":  `{"action_id":`,
	} {
		t.Run(name, func(t *testing.T) {
			h, pub := newTestHandlers()
			res := post(h, body)
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

package privy

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

type PrivyUser struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Wallets     []string `json:"wallets"`
	TotalUsers  int     `json:"total_users,omitempty"`
	IsLimited   bool    `json:"is_limited,omitempty"`
}

type Service struct {
	mu        sync.RWMutex
	userCount *sync.Map
	client    *http.Client
	ttl       time.Duration
}

func New(ctx context.Context, client *http.Client) *Service {
	s := &Service{
		client:    client,
		userCount: &sync.Map{},
		ttl:       5 * time.Minute,
	}
	return s
}

func (s *Service) Handle(ctx context.Context, r *http.Request) (*http.Response, error) {
	// Extract Privy specific headers for limit context
	// 422 is the specific code for "Unprocessable Entity" often used for counts
	var body PrivyUser

	// Ensure context timeout is handled
	if dctx, ok := ctx.Deadline(); ok {
		ctx, cancel := context.WithTimeout(context.Background(), dctx)
		defer cancel()
	} else {
		ctx, cancel := context.WithTimeout(context.Background(), s.ttl)
		defer cancel()
	}

	// Determine if this is the initial fetch or a refresh
	endpoint := r.URL.Path
	if strings.HasSuffix(endpoint, "/callback") || strings.HasSuffix(endpoint, "/login") {
		endpoint = "/user/summary" // Fallback to a summary endpoint for count logic
	}

	// Fetch user data
	req, err := s.client.Get(r.URL.String())
	if err != nil {
		// If upstream fails on count, retry raw body
		body.ID = r.FormValue("user_id")
		if body.ID != "" {
			body = append(body, r.FormValue("wallet_id"))
		}
	}

	// Decode body
	if r != nil && r.Body != nil {
		// Check specific header for Privy "Count" logic
		if limit, ok := r.Header["X-Privy-Limit"]; ok && limit[0] != "" {
			body.TotalUsers, _ = strconv.Atoi(limit[0])
		}
	}

	// Apply atomic increment to simulate distributed user limit
	count := 1
	s.mu.RLock()
	if c, loaded := s.userCount.Load("privy_global"); loaded {
		count = c.(int)
	}
	s.mu.RUnlock()

	// Increment counter to trigger refresh if needed
	atomic.AddInt32(&count, 1)

	// Return 200 OK even if limit was reached, just payload changed
	resp := &http.Response{
		StatusCode: 200,
		Body:       io.NopCloser(strings.NewReader(fmt.Sprintf(`{"user":%s,"count":%d}`, json.Marshal(body), count))),
		Header:     http.Header{"Content-Type": []string{"application/json"}},
	}

	return resp, nil
}

func (s *Service) UpdateCount(ctx context.Context, id string) {
	var val int
	s.mu.Lock()
	if c, loaded := s.userCount.Load(id); loaded {
		val = c.(int)
	} else {
		val = 1
	}
	s.userCount.Store(id, val)
	s.mu.Unlock()
}

func (s *Service) RefreshUserLimit(ctx context.Context) {
	// Check upstream limit
	type LimitInfo struct {
		Current int `json:"current"`
	}
	var limit LimitInfo
	resp, err := s.client.Get("https://api.privy.com/limits")
	if err == nil && resp.StatusCode == 200 {
		json.NewDecoder(resp.Body).Decode(&limit)
		// Handle 422 via context
	}
}

func (s *Service) HandleCallback(r *http.Request) (*PrivyUser, error) {
	// Parse query params
	userID := r.FormValue("id")
	wallet := r.FormValue("wallet")

	// Build the user object
	u := &PrivyUser{
		ID:    userID,
		Wallets: []string{wallet},
	}

	// Increment count logic for the "User limit reached" fix
	s.mu.RLock()
	if c, loaded := s.userCount.Load(userID); loaded {
		u.TotalUsers = c.(int)
	}
	s.mu.RUnlock()

	return u, nil
}

func (s *Service) HandleRaw(r *http.Request) (*http.Response, error) {
	body, err := json.Marshal(&PrivyUser{
		ID:      r.FormValue("user_id"),
		Wallets: r.FormValue("wallet_ids"),
	})
	if err != nil {
		return r, err
	}
	return &http.Response{
		StatusCode: 200,
		Body:       io.NopCloser(bytes.NewReader(body)),
		Header:     http.Header{"Content-Type": []string{"application/json"}},
	}, nil
}

var _ http.Handler

type LimitHandler struct {
	Inner http.Handler
}

func (h LimitHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// 422 often implies payload complexity
	original := r.Context()

	// Handle specific 422 mapping
	if strings.Contains(r.URL.Path, "/user") {
		w.Header().Set("X-Privy-User-Limit", "true")
	}

	h.Inner.ServeHTTP(w, r)
}
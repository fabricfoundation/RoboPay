package attest

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/crypto"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// operatorKey / otherKey stand in for the robot operator and an intermediary.
const (
	operatorKey = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
	otherKey    = "8f2a559490d3f4dbd1d0b0e0f2f4f3a2f1e0d9c8b7a6958473625140312f1e0d"
)

func newTestSigner(t *testing.T, hexKey string) *Signer {
	t.Helper()
	s, err := NewSigner(hexKey)
	if err != nil {
		t.Fatalf("NewSigner: %v", err)
	}
	return s
}

func sampleChallenge() Challenge {
	return Challenge{
		RobotID:         "robot-1",
		Method:          http.MethodPost,
		Path:            "/action",
		PaymentRequired: `{"scheme":"exact","payTo":"0xAAAA","maxAmountRequired":"2000"}`,
		WWWAuthenticate: `Payment realm="robot-1"`,
	}
}

func TestAttestRoundTrip(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()

	value, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestAttestSignerAddressMatchesKey(t *testing.T) {
	key, err := crypto.HexToECDSA(operatorKey)
	if err != nil {
		t.Fatalf("HexToECDSA: %v", err)
	}
	want := crypto.PubkeyToAddress(key.PublicKey)

	if got := newTestSigner(t, operatorKey).Address(); got != want {
		t.Fatalf("Address() = %s, want %s", got, want)
	}
}

func TestNewSignerAcceptsPrefixAndWhitespace(t *testing.T) {
	plain := newTestSigner(t, operatorKey).Address()
	prefixed := newTestSigner(t, "  0x"+operatorKey+"\n").Address()

	if plain != prefixed {
		t.Fatalf("0x-prefixed key produced %s, want %s", prefixed, plain)
	}
}

func TestNewSignerRejectsBadKeys(t *testing.T) {
	for _, key := range []string{"", "   ", "0x", "not-hex", operatorKey + "ff"} {
		if _, err := NewSigner(key); err == nil {
			t.Fatalf("NewSigner(%q) succeeded, want error", key)
		}
	}
}

// A rewritten recipient must invalidate the attestation — this is the whole point.
func TestVerifyRejectsRewrittenPaymentRequired(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()

	value, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	tampered := ch
	tampered.PaymentRequired = strings.Replace(ch.PaymentRequired, "0xAAAA", "0xBBBB", 1)

	if err := Verify(value, tampered, signer.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
		t.Fatal("Verify accepted a rewritten payTo address")
	}
}

func TestVerifyRejectsEveryCoveredFieldChange(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()
	value, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	mutations := map[string]func(*Challenge){
		"robot_id":         func(c *Challenge) { c.RobotID = "robot-2" },
		"method":           func(c *Challenge) { c.Method = http.MethodGet },
		"path":             func(c *Challenge) { c.Path = "/other" },
		"payment_required": func(c *Challenge) { c.PaymentRequired = "{}" },
		"www_authenticate": func(c *Challenge) { c.WWWAuthenticate = `Payment realm="other"` },
	}

	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			tampered := ch
			mutate(&tampered)
			if err := Verify(value, tampered, signer.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
				t.Fatalf("Verify accepted a changed %s", name)
			}
		})
	}
}

// An intermediary that rewrites the challenge and re-signs with its own key must
// still fail, because the payer checks against an out-of-band expected signer.
func TestVerifyRejectsAttestationFromAnotherKey(t *testing.T) {
	operator := newTestSigner(t, operatorKey)
	attacker := newTestSigner(t, otherKey)

	rewritten := sampleChallenge()
	rewritten.PaymentRequired = strings.Replace(rewritten.PaymentRequired, "0xAAAA", "0xBBBB", 1)

	value, err := attacker.Attest(rewritten)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	// The attacker's own signature is internally consistent...
	if err := Verify(value, rewritten, attacker.Address().Hex(), DefaultMaxAge, time.Now()); err != nil {
		t.Fatalf("attacker attestation should verify against the attacker: %v", err)
	}
	// ...but not against the operator the payer expects.
	err = Verify(value, rewritten, operator.Address().Hex(), DefaultMaxAge, time.Now())
	if err == nil {
		t.Fatal("Verify accepted an attestation signed by the wrong key")
	}
}

func TestVerifyRejectsExpiredAndFutureDated(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()
	ch.IssuedAt = time.Now().Unix()
	ch.Nonce = "0123456789abcdef0123456789abcdef"

	value, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	stale := time.Unix(ch.IssuedAt, 0).Add(DefaultMaxAge + time.Second)
	if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, stale); err == nil {
		t.Fatal("Verify accepted an expired attestation")
	}

	early := time.Unix(ch.IssuedAt, 0).Add(-2 * DefaultMaxAge)
	if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, early); err == nil {
		t.Fatal("Verify accepted a future-dated attestation")
	}
}

func TestVerifyRejectsMalformedInput(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()
	good, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	cases := map[string]string{
		"empty":      "",
		"whitespace": "   ",
		"not base64": "!!!not-base64!!!",
		"not json":   base64.RawURLEncoding.EncodeToString([]byte("nope")),
		"truncated":  good[:len(good)-8],
	}

	for name, value := range cases {
		t.Run(name, func(t *testing.T) {
			if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
				t.Fatalf("Verify accepted %s input", name)
			}
		})
	}
}

func TestVerifyRejectsUnsupportedVersionAndBadSignatureLength(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	ch := sampleChallenge()

	encode := func(att Attestation) string {
		raw, err := json.Marshal(att)
		if err != nil {
			t.Fatalf("Marshal: %v", err)
		}
		return base64.RawURLEncoding.EncodeToString(raw)
	}

	wrongVersion := encode(Attestation{Version: 99, Signer: signer.Address().Hex(), IssuedAt: time.Now().Unix(), Nonce: "ab", Signature: "0x00"})
	if err := Verify(wrongVersion, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
		t.Fatal("Verify accepted an unsupported version")
	}

	shortSig := encode(Attestation{Version: Version, Signer: signer.Address().Hex(), IssuedAt: time.Now().Unix(), Nonce: "ab", Signature: "0xdeadbeef"})
	if err := Verify(shortSig, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
		t.Fatal("Verify accepted a short signature")
	}
}

// A mismatched `signer` field must not slip past, so a reader that only inspects
// the decoded envelope cannot be misled.
func TestVerifyRejectsLyingSignerField(t *testing.T) {
	signer := newTestSigner(t, operatorKey)
	attacker := newTestSigner(t, otherKey)
	ch := sampleChallenge()

	value, err := signer.Attest(ch)
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}
	raw, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	var att Attestation
	if err := json.Unmarshal(raw, &att); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	att.Signer = attacker.Address().Hex()

	forged, err := json.Marshal(att)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	tampered := base64.RawURLEncoding.EncodeToString(forged)

	if err := Verify(tampered, ch, attacker.Address().Hex(), DefaultMaxAge, time.Now()); err == nil {
		t.Fatal("Verify accepted an envelope whose signer field disagrees with the signature")
	}
}

func TestAttestFillsIssuedAtAndNonce(t *testing.T) {
	signer := newTestSigner(t, operatorKey)

	first, err := signer.Attest(sampleChallenge())
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}
	second, err := signer.Attest(sampleChallenge())
	if err != nil {
		t.Fatalf("Attest: %v", err)
	}

	a, err := Decode(first)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	b, err := Decode(second)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}

	if a.IssuedAt == 0 || b.IssuedAt == 0 {
		t.Fatal("IssuedAt was not filled in")
	}
	if a.Nonce == "" || a.Nonce == b.Nonce {
		t.Fatalf("nonce should be present and unique, got %q and %q", a.Nonce, b.Nonce)
	}
	if a.Signature == b.Signature {
		t.Fatal("distinct nonces must produce distinct signatures")
	}
}

// --- middleware ---

func TestMiddlewareAttachesAttestationTo402(t *testing.T) {
	gin.SetMode(gin.TestMode)
	signer := newTestSigner(t, operatorKey)

	router := gin.New()
	router.Use(signer.Middleware("robot-1", zap.NewNop()))
	router.POST("/action", func(c *gin.Context) {
		c.Header(headerPaymentRequired, `{"scheme":"exact","payTo":"0xAAAA"}`)
		c.Header(headerWWWAuthenticate, `Payment realm="robot-1"`)
		c.Status(http.StatusPaymentRequired)
	})

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/action", nil))

	if rec.Code != http.StatusPaymentRequired {
		t.Fatalf("status = %d, want 402", rec.Code)
	}

	value := rec.Header().Get(HeaderName)
	if value == "" {
		t.Fatal("no attestation header on the 402")
	}

	ch := Challenge{
		RobotID:         "robot-1",
		Method:          http.MethodPost,
		Path:            "/action",
		PaymentRequired: rec.Header().Get(headerPaymentRequired),
		WWWAuthenticate: rec.Header().Get(headerWWWAuthenticate),
	}
	if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err != nil {
		t.Fatalf("Verify against the served response: %v", err)
	}
}

func TestMiddlewareLeavesNon402ResponsesAlone(t *testing.T) {
	gin.SetMode(gin.TestMode)
	signer := newTestSigner(t, operatorKey)

	router := gin.New()
	router.Use(signer.Middleware("robot-1", zap.NewNop()))
	router.POST("/action", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"ok": true}) })

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/action", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if got := rec.Header().Get(HeaderName); got != "" {
		t.Fatalf("attestation attached to a 200 response: %q", got)
	}
}

func TestMiddlewareSkipsWhen402HasNoChallenge(t *testing.T) {
	gin.SetMode(gin.TestMode)
	signer := newTestSigner(t, operatorKey)

	router := gin.New()
	router.Use(signer.Middleware("robot-1", zap.NewNop()))
	router.POST("/action", func(c *gin.Context) { c.Status(http.StatusPaymentRequired) })

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/action", nil))

	if got := rec.Header().Get(HeaderName); got != "" {
		t.Fatalf("attested a 402 with no challenge header: %q", got)
	}
}

// The MPP gate writes its 402 first and x402 adds PAYMENT-REQUIRED afterwards, so a
// challenge header can be set after WriteHeader and still reach the client — gin only
// records the status there and flushes later. The attestation must cover the response
// as served, not a half-built snapshot of it.
func TestMiddlewareCoversHeadersSetAfterWriteHeader(t *testing.T) {
	gin.SetMode(gin.TestMode)
	signer := newTestSigner(t, operatorKey)

	router := gin.New()
	router.Use(signer.Middleware("robot-1", zap.NewNop()))
	router.POST("/action", func(c *gin.Context) {
		// MPP advertises and writes the 402...
		c.Header(headerWWWAuthenticate, `Payment realm="robot-1"`)
		c.Writer.WriteHeader(http.StatusPaymentRequired)
		// ...then x402 adds its own challenge before anything reaches the wire.
		c.Header(headerPaymentRequired, `{"scheme":"exact","payTo":"0xAAAA"}`)
		_, _ = c.Writer.Write([]byte(`{"error":"payment required"}`))
	})

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/action", nil))

	if got := rec.Header().Get(headerPaymentRequired); got == "" {
		t.Fatal("PAYMENT-REQUIRED was not served; the test no longer models the real ordering")
	}

	value := rec.Header().Get(HeaderName)
	if value == "" {
		t.Fatal("no attestation header on the 402")
	}

	ch := Challenge{
		RobotID:         "robot-1",
		Method:          http.MethodPost,
		Path:            "/action",
		PaymentRequired: rec.Header().Get(headerPaymentRequired),
		WWWAuthenticate: rec.Header().Get(headerWWWAuthenticate),
	}
	if err := Verify(value, ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err != nil {
		t.Fatalf("attestation does not cover the response as served: %v", err)
	}
}

// Whichever protocol writes last, the attestation must cover the response as served.
// This is the "no matter if MPP is enabled or not" guarantee, exercised over every
// ordering the gate can produce.
func TestMiddlewareCoversEveryChallengeOrdering(t *testing.T) {
	gin.SetMode(gin.TestMode)

	const x402Challenge = `{"scheme":"exact","payTo":"0xAAAA"}`
	const mppChallenge = `Payment realm="robot-1"`

	cases := map[string]struct {
		handler          func(c *gin.Context)
		wantPaymentReqd  string
		wantWWWAuthentic string
	}{
		"mpp writes 402, x402 adds its header after": {
			handler: func(c *gin.Context) {
				c.Header(headerWWWAuthenticate, mppChallenge)
				c.Writer.WriteHeader(http.StatusPaymentRequired)
				c.Header(headerPaymentRequired, x402Challenge)
				_, _ = c.Writer.Write([]byte(`{}`))
			},
			wantPaymentReqd:  x402Challenge,
			wantWWWAuthentic: mppChallenge,
		},
		"x402 writes 402, mpp adds its header after": {
			handler: func(c *gin.Context) {
				c.Header(headerPaymentRequired, x402Challenge)
				c.Writer.WriteHeader(http.StatusPaymentRequired)
				c.Header(headerWWWAuthenticate, mppChallenge)
				_, _ = c.Writer.Write([]byte(`{}`))
			},
			wantPaymentReqd:  x402Challenge,
			wantWWWAuthentic: mppChallenge,
		},
		"x402 only (MPP disabled)": {
			handler: func(c *gin.Context) {
				c.Header(headerPaymentRequired, x402Challenge)
				c.Writer.WriteHeader(http.StatusPaymentRequired)
				_, _ = c.Writer.Write([]byte(`{}`))
			},
			wantPaymentReqd: x402Challenge,
		},
		"mpp only": {
			handler: func(c *gin.Context) {
				c.Header(headerWWWAuthenticate, mppChallenge)
				c.Writer.WriteHeader(http.StatusPaymentRequired)
				_, _ = c.Writer.Write([]byte(`{}`))
			},
			wantWWWAuthentic: mppChallenge,
		},
		"402 with no body written": {
			handler: func(c *gin.Context) {
				c.Header(headerPaymentRequired, x402Challenge)
				c.Header(headerWWWAuthenticate, mppChallenge)
				c.Status(http.StatusPaymentRequired)
			},
			wantPaymentReqd:  x402Challenge,
			wantWWWAuthentic: mppChallenge,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			signer := newTestSigner(t, operatorKey)
			router := gin.New()
			router.Use(signer.Middleware("robot-1", zap.NewNop()))
			router.POST("/action", tc.handler)

			rec := httptest.NewRecorder()
			router.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/action", nil))

			if got := rec.Header().Get(headerPaymentRequired); got != tc.wantPaymentReqd {
				t.Fatalf("served PAYMENT-REQUIRED = %q, want %q", got, tc.wantPaymentReqd)
			}
			if got := rec.Header().Get(headerWWWAuthenticate); got != tc.wantWWWAuthentic {
				t.Fatalf("served WWW-Authenticate = %q, want %q", got, tc.wantWWWAuthentic)
			}

			// Exactly one attestation, no stale value left behind by a re-sign.
			if n := len(rec.Header().Values(HeaderName)); n != 1 {
				t.Fatalf("attestation header appears %d times, want 1", n)
			}

			ch := Challenge{
				RobotID:         "robot-1",
				Method:          http.MethodPost,
				Path:            "/action",
				PaymentRequired: rec.Header().Get(headerPaymentRequired),
				WWWAuthenticate: rec.Header().Get(headerWWWAuthenticate),
			}
			if err := Verify(rec.Header().Get(HeaderName), ch, signer.Address().Hex(), DefaultMaxAge, time.Now()); err != nil {
				t.Fatalf("attestation does not cover the response as served: %v", err)
			}
		})
	}
}

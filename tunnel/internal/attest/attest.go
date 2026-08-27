// Package attest signs the payment requirements a robot advertises in its 402
// challenge, so a payer can confirm the recipient it is about to sign for came
// from the robot operator and not from anything in between.
//
// The tunnel reaches a payer through the Fabric gateway, which terminates TLS
// and relays the 402 in plaintext. Without an attestation the gateway is
// technically positioned to present a different `payTo` address, which the payer
// would then sign; on-chain verification cannot detect that, because the
// resulting signature is valid for whatever address the payer was shown.
//
// A payer that knows the operator's expected signing address out of band (from a
// listing, a registry, or a prior relationship) can verify the attestation and
// refuse any challenge that is unsigned or signed by someone else.
package attest

import (
	"crypto/ecdsa"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	// HeaderName carries the attestation on the 402 response.
	HeaderName = "PAYMENT-REQUIREMENTS-SIGNATURE"

	// Version is the attestation envelope version.
	Version = 1

	// messagePrefix domain-separates the signed message. It is part of the
	// signed bytes, so bumping it invalidates older attestations by construction.
	messagePrefix = "RoboPay-Payment-Requirements-v1"

	// DefaultMaxAge bounds how long an attestation stays acceptable, so a
	// captured challenge cannot be replayed against a later price.
	DefaultMaxAge = 5 * time.Minute

	nonceBytes = 16
)

var (
	ErrMissingAttestation = errors.New("attest: no attestation present")
	ErrMalformed          = errors.New("attest: malformed attestation")
	ErrUnsupportedVersion = errors.New("attest: unsupported attestation version")
	ErrSignerMismatch     = errors.New("attest: attestation signed by an unexpected address")
	ErrExpired            = errors.New("attest: attestation has expired")
	ErrFutureDated        = errors.New("attest: attestation is dated in the future")
)

// Challenge is the set of facts an attestation covers. Every field is part of
// the signed message, so any change to a covered value invalidates the signature.
//
// PaymentRequired and WWWAuthenticate hold the *first* value of their respective
// response headers. The gateway forwards headers as a single value per name, so
// signing more than the first value would produce attestations a payer could
// never reproduce.
type Challenge struct {
	RobotID         string
	Method          string
	Path            string
	PaymentRequired string
	WWWAuthenticate string
	IssuedAt        int64
	Nonce           string
}

// canonical renders the challenge as the exact bytes that get signed. Field
// order is fixed and every field is present, so the encoding is unambiguous.
func (ch Challenge) canonical() []byte {
	var b strings.Builder
	b.WriteString(messagePrefix)
	for _, kv := range [][2]string{
		{"robot_id", ch.RobotID},
		{"method", ch.Method},
		{"path", ch.Path},
		{"issued_at", fmt.Sprintf("%d", ch.IssuedAt)},
		{"nonce", ch.Nonce},
		{"payment_required", ch.PaymentRequired},
		{"www_authenticate", ch.WWWAuthenticate},
	} {
		b.WriteString("\n")
		b.WriteString(kv[0])
		b.WriteString(":")
		b.WriteString(kv[1])
	}
	return []byte(b.String())
}

// digest is the EIP-191 personal_sign hash of the canonical message.
func (ch Challenge) digest() []byte {
	return accounts.TextHash(ch.canonical())
}

// Attestation is the decoded envelope carried in the header.
type Attestation struct {
	Version   int    `json:"v"`
	Signer    string `json:"signer"`
	IssuedAt  int64  `json:"issued_at"`
	Nonce     string `json:"nonce"`
	Signature string `json:"signature"`
}

// Signer holds the operator key that attests this robot's payment requirements.
type Signer struct {
	key     *ecdsa.PrivateKey
	address common.Address
}

// NewSigner parses a hex-encoded secp256k1 private key, with or without the 0x prefix.
func NewSigner(hexKey string) (*Signer, error) {
	hexKey = strings.TrimPrefix(strings.TrimSpace(hexKey), "0x")
	if hexKey == "" {
		return nil, errors.New("attest: signing key is empty")
	}

	key, err := crypto.HexToECDSA(hexKey)
	if err != nil {
		return nil, fmt.Errorf("attest: failed to parse signing key: %w", err)
	}

	return &Signer{key: key, address: crypto.PubkeyToAddress(key.PublicKey)}, nil
}

// Address is the address a payer must expect to see in the attestation.
func (s *Signer) Address() common.Address { return s.address }

// Attest signs ch and returns the header value. IssuedAt and Nonce are filled in
// when the caller leaves them unset.
func (s *Signer) Attest(ch Challenge) (string, error) {
	if ch.IssuedAt == 0 {
		ch.IssuedAt = time.Now().Unix()
	}
	if ch.Nonce == "" {
		nonce, err := newNonce()
		if err != nil {
			return "", err
		}
		ch.Nonce = nonce
	}

	sig, err := crypto.Sign(ch.digest(), s.key)
	if err != nil {
		return "", fmt.Errorf("attest: failed to sign challenge: %w", err)
	}
	// go-ethereum returns V as 0/1; personal_sign consumers expect 27/28.
	sig[64] += 27

	envelope, err := json.Marshal(Attestation{
		Version:   Version,
		Signer:    s.address.Hex(),
		IssuedAt:  ch.IssuedAt,
		Nonce:     ch.Nonce,
		Signature: "0x" + hex.EncodeToString(sig),
	})
	if err != nil {
		return "", fmt.Errorf("attest: failed to encode attestation: %w", err)
	}

	return base64.RawURLEncoding.EncodeToString(envelope), nil
}

// Decode parses a header value without verifying it.
func Decode(headerValue string) (Attestation, error) {
	var att Attestation
	if strings.TrimSpace(headerValue) == "" {
		return att, ErrMissingAttestation
	}

	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimSpace(headerValue))
	if err != nil {
		return att, fmt.Errorf("%w: %v", ErrMalformed, err)
	}
	if err := json.Unmarshal(raw, &att); err != nil {
		return att, fmt.Errorf("%w: %v", ErrMalformed, err)
	}
	if att.Version != Version {
		return att, fmt.Errorf("%w: %d", ErrUnsupportedVersion, att.Version)
	}
	return att, nil
}

// Verify checks that headerValue is a valid attestation over ch, signed by
// expectedSigner and no older than maxAge.
//
// ch carries what the payer actually received: the robot id, method, path and
// the challenge headers as they arrived. IssuedAt and Nonce are taken from the
// attestation, so the caller leaves them unset.
//
// expectedSigner is the operator address the payer knows independently of the
// response. Verifying against the address inside the attestation alone proves
// nothing: an intermediary that rewrites the requirements can sign the rewritten
// version with its own key. The out-of-band expectation is what makes this work.
func Verify(headerValue string, ch Challenge, expectedSigner string, maxAge time.Duration, now time.Time) error {
	att, err := Decode(headerValue)
	if err != nil {
		return err
	}

	if !common.IsHexAddress(expectedSigner) {
		return fmt.Errorf("attest: expected signer %q is not an address", expectedSigner)
	}
	if maxAge <= 0 {
		maxAge = DefaultMaxAge
	}

	issued := time.Unix(att.IssuedAt, 0)
	if now.Sub(issued) > maxAge {
		return fmt.Errorf("%w: issued %s ago", ErrExpired, now.Sub(issued).Truncate(time.Second))
	}
	if issued.After(now.Add(maxAge)) {
		return ErrFutureDated
	}

	sig, err := hex.DecodeString(strings.TrimPrefix(att.Signature, "0x"))
	if err != nil {
		return fmt.Errorf("%w: signature is not hex: %v", ErrMalformed, err)
	}
	if len(sig) != 65 {
		return fmt.Errorf("%w: signature is %d bytes, want 65", ErrMalformed, len(sig))
	}
	// Normalize V back to 0/1 for recovery.
	normalized := make([]byte, 65)
	copy(normalized, sig)
	if normalized[64] >= 27 {
		normalized[64] -= 27
	}

	ch.IssuedAt = att.IssuedAt
	ch.Nonce = att.Nonce

	pub, err := crypto.SigToPub(ch.digest(), normalized)
	if err != nil {
		return fmt.Errorf("%w: signature does not recover: %v", ErrMalformed, err)
	}

	recovered := crypto.PubkeyToAddress(*pub)
	if recovered != common.HexToAddress(expectedSigner) {
		return fmt.Errorf("%w: recovered %s, expected %s", ErrSignerMismatch, recovered.Hex(), common.HexToAddress(expectedSigner).Hex())
	}
	// The envelope's own claim must agree, so a mismatched `signer` field cannot
	// mislead a reader that only inspects the decoded attestation.
	if !common.IsHexAddress(att.Signer) || common.HexToAddress(att.Signer) != recovered {
		return fmt.Errorf("%w: envelope claims %s but recovered %s", ErrMalformed, att.Signer, recovered.Hex())
	}

	return nil
}

func newNonce() (string, error) {
	buf := make([]byte, nonceBytes)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("attest: failed to generate nonce: %w", err)
	}
	return hex.EncodeToString(buf), nil
}

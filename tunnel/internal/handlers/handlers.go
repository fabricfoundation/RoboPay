package handlers

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const (
	RobotActionTopic        = "robot/tunnel/action"
	RobotResultTopic        = "robot/tunnel/result"
	defaultExecutionTimeout = 90 * time.Second
)

func configuredTopic(envName, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(envName)); value != "" {
		return value
	}
	return fallback
}

func configuredActionTopic() string {
	return configuredTopic("ZENOH_ACTION_TOPIC", RobotActionTopic)
}

func configuredResultTopic() string {
	return configuredTopic("ZENOH_RESULT_TOPIC", RobotResultTopic)
}

// executionTimeout is how long the background execution watcher waits for
// the correlated simulator result before recording a timeout outcome.
// EXECUTION_TIMEOUT_SECONDS overrides the 90s default so integration tests
// can exercise the timeout no-settlement path quickly.
func executionTimeout() time.Duration {
	if raw := os.Getenv("EXECUTION_TIMEOUT_SECONDS"); raw != "" {
		if seconds, err := strconv.ParseFloat(raw, 64); err == nil && seconds > 0 {
			return time.Duration(seconds * float64(time.Second))
		}
	}
	return defaultExecutionTimeout
}

// SkillMetadata is the public, read-only discovery representation returned
// before a payer authorizes an action.
type SkillMetadata struct {
	SkillID         string                 `json:"skill_id"`
	Aliases         []string               `json:"aliases,omitempty"`
	Description     string                 `json:"description"`
	PaymentRequired bool                   `json:"payment_required"`
	PriceUSDC       string                 `json:"price_usdc"`
	Params          map[string]ParamSchema `json:"params"`
}

// ParamSchema is the small, strict subset of the profile schema enforced by
// the Tunnel before a paid event can be published to Zenoh. The schema lives
// in a robot-scoped JSON catalog; the Tunnel deliberately contains no
// robot-specific action names or limits.
type ParamSchema struct {
	Type        string       `json:"type"`
	Required    bool         `json:"required,omitempty"`
	Values      []string     `json:"values,omitempty"`
	Minimum     *float64     `json:"minimum,omitempty"`
	Maximum     *float64     `json:"maximum,omitempty"`
	Items       *ParamSchema `json:"items,omitempty"`
	MinItems    *int         `json:"min_items,omitempty"`
	MaxItems    *int         `json:"max_items,omitempty"`
	UniqueItems bool         `json:"unique_items,omitempty"`
}

// LoadSkillCatalog reads a deployment-selected, robot-scoped JSON catalog.
// A missing, malformed, or unsafe catalog is an error; callers must fail
// closed rather than fall back to a built-in robot profile.
func LoadSkillCatalog(path, price string) ([]SkillMetadata, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, fmt.Errorf("SKILL_CATALOG_PATH is required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read skill catalog: %w", err)
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	var catalog []SkillMetadata
	if err := decoder.Decode(&catalog); err != nil {
		return nil, fmt.Errorf("decode skill catalog: %w", err)
	}
	if len(catalog) == 0 {
		return nil, fmt.Errorf("skill catalog is empty")
	}
	seen := make(map[string]struct{})
	price = strings.TrimPrefix(strings.TrimSpace(price), "$")
	for index := range catalog {
		skill := &catalog[index]
		skill.SkillID = strings.TrimSpace(skill.SkillID)
		if !validSkillID(skill.SkillID) {
			return nil, fmt.Errorf("invalid skill_id %q", skill.SkillID)
		}
		if _, duplicate := seen[skill.SkillID]; duplicate {
			return nil, fmt.Errorf("duplicate skill_id %q", skill.SkillID)
		}
		seen[skill.SkillID] = struct{}{}
		for aliasIndex, alias := range skill.Aliases {
			alias = strings.TrimSpace(alias)
			if !validSkillID(alias) {
				return nil, fmt.Errorf("invalid alias %q for %q", alias, skill.SkillID)
			}
			if _, duplicate := seen[alias]; duplicate {
				return nil, fmt.Errorf("duplicate skill alias %q", alias)
			}
			seen[alias] = struct{}{}
			skill.Aliases[aliasIndex] = alias
		}
		if skill.Params == nil {
			skill.Params = map[string]ParamSchema{}
		}
		for name, schema := range skill.Params {
			if strings.TrimSpace(name) == "" {
				return nil, fmt.Errorf("empty parameter name for %q", skill.SkillID)
			}
			if err := validateSchemaDefinition(schema); err != nil {
				return nil, fmt.Errorf("invalid schema for %s.%s: %w", skill.SkillID, name, err)
			}
		}
		// Price is a deployment/payment setting, not profile prose. Returning
		// it from the same value used by x402 prevents documentation drift.
		skill.PriceUSDC = price
		skill.PaymentRequired = true
	}
	sort.Slice(catalog, func(i, j int) bool { return catalog[i].SkillID < catalog[j].SkillID })
	return catalog, nil
}

func validSkillID(value string) bool {
	if len(value) == 0 || len(value) > 64 || value[0] < 'a' || value[0] > 'z' {
		return false
	}
	for _, char := range value {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return false
		}
	}
	return true
}

func validateSchemaDefinition(schema ParamSchema) error {
	switch schema.Type {
	case "string", "number", "integer", "boolean":
	case "array":
		if schema.Items == nil {
			return errors.New("array requires items")
		}
		if err := validateSchemaDefinition(*schema.Items); err != nil {
			return err
		}
	default:
		return fmt.Errorf("unsupported type %q", schema.Type)
	}
	if schema.Minimum != nil && schema.Maximum != nil && *schema.Minimum > *schema.Maximum {
		return errors.New("minimum exceeds maximum")
	}
	if schema.MinItems != nil && *schema.MinItems < 0 {
		return errors.New("min_items must be non-negative")
	}
	if schema.MaxItems != nil && *schema.MaxItems < 0 {
		return errors.New("max_items must be non-negative")
	}
	if schema.MinItems != nil && schema.MaxItems != nil && *schema.MinItems > *schema.MaxItems {
		return errors.New("min_items exceeds max_items")
	}
	return nil
}

// SettleFunc performs the deferred x402 settlement for an already-verified
// payment. The payment gate in main.go injects it under the "x402_settle"
// context key; PostAction invokes it only after the simulator reports
// success, so a failed or timed-out execution can never settle.
type SettleFunc func(ctx context.Context) (*SettlementRecord, error)

type validationError struct {
	status  int
	code    string
	message string
}

func (e validationError) Error() string {
	return fmt.Sprintf("%s: %s", e.code, e.message)
}

type actionMetadata struct {
	ActionID   string
	RobotID    string
	SkillID    string
	ParamsHash string
	// ParamsCanonical is the exact JSON byte sequence that was hashed by Go.
	// It travels with the event so bridges can verify the hash without making
	// cross-language float-formatting assumptions.
	ParamsCanonical string
	IdempotencyKey  string
}

// executionResult is the terminal event emitted by a bridge. Every member of
// the correlation tuple is required in the production Zenoh path; a result
// that cannot be tied to the exact published action is ignored and therefore
// times out without settlement.
type executionResult struct {
	ActionID       string          `json:"action_id"`
	RobotID        string          `json:"robot_id"`
	SkillID        string          `json:"skill_id"`
	ParamsHash     string          `json:"params_hash"`
	IdempotencyKey string          `json:"idempotency_key"`
	Status         string          `json:"status"`
	ErrorCode      string          `json:"error_code,omitempty"`
	Result         json.RawMessage `json:"result,omitempty"`
}

func (result executionResult) matches(metadata actionMetadata) bool {
	return result.ActionID != "" &&
		result.RobotID != "" &&
		result.SkillID != "" &&
		result.ParamsHash != "" &&
		result.IdempotencyKey != "" &&
		result.ActionID == metadata.ActionID &&
		result.RobotID == metadata.RobotID &&
		result.SkillID == metadata.SkillID &&
		result.ParamsHash == metadata.ParamsHash &&
		result.IdempotencyKey == metadata.IdempotencyKey
}

// zenohConfigFromEnvironment builds the session configuration used by both the
// action publisher and the tunnel's configuration subscriber. ZENOH_CONFIG is
// a complete JSON5 configuration and therefore takes precedence. For the
// common local-router case, ZENOH_ENDPOINT is a concise equivalent of setting
// connect/endpoints in that configuration.
func zenohConfigFromEnvironment() (zenoh.Config, error) {
	if path := os.Getenv("ZENOH_CONFIG"); path != "" {
		return zenoh.NewConfigFromFile(path)
	}

	config := zenoh.NewConfigDefault()
	if endpoint := strings.TrimSpace(os.Getenv("ZENOH_ENDPOINT")); endpoint != "" {
		endpoints, err := json.Marshal([]string{endpoint})
		if err != nil {
			return zenoh.Config{}, fmt.Errorf("marshal ZENOH_ENDPOINT: %w", err)
		}
		if err := config.InsertJson5(zenoh.ConfigConnectKey, string(endpoints)); err != nil {
			return zenoh.Config{}, fmt.Errorf("configure ZENOH_ENDPOINT: %w", err)
		}
	}
	return config, nil
}

// OpenZenohSession opens the configured Zenoh session.
func OpenZenohSession() (zenoh.Session, error) {
	config, err := zenohConfigFromEnvironment()
	if err != nil {
		return zenoh.Session{}, err
	}
	return zenoh.Open(config, nil)
}

type zenohPublisher interface {
	Publish(keyExpr string, payload []byte) error
}

type zenohSessionPublisher struct {
	session zenoh.Session
}

func (z *zenohSessionPublisher) Publish(keyExpr string, payload []byte) error {
	ke, err := zenoh.NewKeyExpr(keyExpr)
	if err != nil {
		return err
	}
	return z.session.Put(ke, zenoh.NewZBytes(payload), nil)
}

var (
	zenohOnce      sync.Once
	zenohPub       zenohPublisher
	zenohInitError error
)

func getZenohPublisher() (zenohPublisher, error) {
	zenohOnce.Do(func() {
		session, err := OpenZenohSession()
		if err != nil {
			zenohInitError = err
			return
		}
		zenohPub = &zenohSessionPublisher{session: session}
	})

	if zenohInitError != nil {
		return nil, zenohInitError
	}

	return zenohPub, nil
}

type Handlers struct {
	Logger             *zap.Logger
	RobotID            string
	Publisher          zenohPublisher
	ActionTopic        string
	ResultTopic        string
	AllowedSkills      map[string]struct{}
	SkillCatalog       []SkillMetadata
	MaxDurationSeconds float64
	// Replay is the durable, payment-bound idempotency store. Never nil.
	Replay *ReplayStore
	// WaitForResult is injectable for contract tests. Production uses the
	// Zenoh result subscriber created below.
	WaitForResult func(actionID string) (chan bool, func(), error)
	// WaitForCorrelatedResult is the strict test hook. Unlike the legacy bool
	// hook, it exercises the exact result-correlation contract.
	WaitForCorrelatedResult func(actionMetadata) (chan executionResult, func(), error)
	// watchers tracks the in-flight execution goroutines so tests (and a
	// graceful shutdown) can wait for pending outcome/settlement writes.
	watchers sync.WaitGroup
}

// WaitForPendingExecutions blocks until every spawned execution watcher has
// recorded its terminal outcome. Used by tests to avoid racing the durable
// store writes against temp-dir cleanup.
func (h *Handlers) WaitForPendingExecutions() {
	h.watchers.Wait()
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return NewHandlersForRobot(logger, "")
}

func NewHandlersForRobot(logger *zap.Logger, robotID string) *Handlers {
	return &Handlers{
		Logger:             logger,
		RobotID:            robotID,
		ActionTopic:        configuredActionTopic(),
		ResultTopic:        configuredResultTopic(),
		Replay:             NewReplayStoreFromEnv(),
		MaxDurationSeconds: 30,
	}
}

// GetRobotProfile exposes the robot identity and discovery link before a paid
// action is selected. It does not disclose wallet credentials.
func (h *Handlers) GetRobotProfile(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"robot_id":   h.RobotID,
		"skills_url": "/skills",
	})
}

// GetSkills returns the registered catalog and whether each skill is enabled
// by the deployment's fail-closed allowlist.
func (h *Handlers) GetSkills(c *gin.Context) {
	skills := make([]gin.H, 0, len(h.SkillCatalog))
	for _, skill := range h.SkillCatalog {
		_, enabled := h.AllowedSkills[skill.SkillID]
		skills = append(skills, gin.H{
			"skill_id":         skill.SkillID,
			"aliases":          skill.Aliases,
			"description":      skill.Description,
			"payment_required": skill.PaymentRequired,
			"price_usdc":       skill.PriceUSDC,
			"params":           skill.Params,
			"enabled":          enabled,
		})
	}
	c.JSON(http.StatusOK, gin.H{"robot_id": h.RobotID, "skills": skills})
}

// KnownSkillIDs returns every primary skill and alias declared by the loaded
// profile catalog. It lets main filter ALLOWED_ACTIONS without embedding any
// robot profile in the shared Tunnel binary.
func (h *Handlers) KnownSkillIDs() map[string]struct{} {
	known := make(map[string]struct{})
	for _, skill := range h.SkillCatalog {
		known[skill.SkillID] = struct{}{}
		for _, alias := range skill.Aliases {
			known[alias] = struct{}{}
		}
	}
	return known
}

func (h *Handlers) skillForAction(action string) (SkillMetadata, bool) {
	for _, skill := range h.SkillCatalog {
		if skill.SkillID == action {
			return skill, true
		}
		for _, alias := range skill.Aliases {
			if alias == action {
				return skill, true
			}
		}
	}
	return SkillMetadata{}, false
}

func (h *Handlers) publish(payload []byte) error {
	topic := h.ActionTopic
	if topic == "" {
		topic = configuredActionTopic()
	}
	if h.Publisher != nil {
		return h.Publisher.Publish(topic, payload)
	}
	pub, err := getZenohPublisher()
	if err != nil {
		return err
	}
	return pub.Publish(topic, payload)
}

// prepareExecutionWait subscribes before the ActionEvent is published so a
// fast simulator cannot race past the result observer. The real x402 path
// uses this waiter; injected test publishers intentionally bypass it.
func (h *Handlers) prepareExecutionWait(metadata actionMetadata) (chan executionResult, func(), error) {
	if h.WaitForCorrelatedResult != nil {
		return h.WaitForCorrelatedResult(metadata)
	}
	if h.WaitForResult != nil {
		legacy, cleanup, err := h.WaitForResult(metadata.ActionID)
		if err != nil {
			return nil, cleanup, err
		}
		result := make(chan executionResult, 1)
		go func() {
			if success, open := <-legacy; open {
				status := "failure"
				if success {
					status = "success"
				}
				result <- executionResult{
					ActionID:       metadata.ActionID,
					RobotID:        metadata.RobotID,
					SkillID:        metadata.SkillID,
					ParamsHash:     metadata.ParamsHash,
					IdempotencyKey: metadata.IdempotencyKey,
					Status:         status,
				}
			}
		}()
		return result, cleanup, nil
	}
	if h.Publisher != nil || metadata.ActionID == "" {
		return nil, func() {}, nil
	}

	pub, err := getZenohPublisher()
	if err != nil {
		return nil, nil, err
	}
	zenohPub, ok := pub.(*zenohSessionPublisher)
	if !ok {
		return nil, nil, fmt.Errorf("zenoh publisher does not expose a session")
	}
	resultTopic := h.ResultTopic
	if resultTopic == "" {
		resultTopic = configuredResultTopic()
	}
	keyExpr, err := zenoh.NewKeyExpr(resultTopic)
	if err != nil {
		return nil, nil, err
	}
	result := make(chan executionResult, 1)
	sub, err := zenohPub.session.DeclareSubscriber(keyExpr, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var envelope executionResult
			if err := json.Unmarshal(sample.Payload().Bytes(), &envelope); err != nil || !envelope.matches(metadata) {
				return
			}
			select {
			case result <- envelope:
			default:
			}
		},
	}, nil)
	if err != nil {
		return nil, nil, err
	}
	return result, func() { _ = sub.Undeclare() }, nil
}

func stringField(object map[string]interface{}, names ...string) string {
	for _, name := range names {
		if value, ok := object[name].(string); ok {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func validatePayload(payload interface{}, expectedRobotID string) (actionMetadata, error) {
	metadata := actionMetadata{}
	object, ok := payload.(map[string]interface{})
	if !ok {
		// Fail closed: a paid request that does not even carry a JSON object
		// naming a skill must never reach the simulator.
		return metadata, validationError{http.StatusBadRequest, "MISSING_ACTION", "request body must be a JSON object with a registered skill in \"action\""}
	}

	actionField := ""
	if rawAction, present := object["action"]; present {
		action, valid := rawAction.(string)
		if !valid || strings.TrimSpace(action) == "" {
			return metadata, validationError{http.StatusBadRequest, "INVALID_ACTION", "action must be a non-empty string"}
		}
		actionField = strings.TrimSpace(action)
	}
	skillField := stringField(object, "skill_id", "skillId")
	if actionField != "" && skillField != "" && actionField != skillField {
		return metadata, validationError{http.StatusBadRequest, "INVALID_ACTION", "action and skill_id must match when both are supplied"}
	}
	metadata.SkillID = actionField
	if metadata.SkillID == "" {
		metadata.SkillID = skillField
	}
	if metadata.SkillID == "" {
		// Fail closed: no action/skill means no actuation — there is no
		// default skill and nothing is published to Zenoh.
		return metadata, validationError{http.StatusBadRequest, "MISSING_ACTION", "a registered skill is required in \"action\" (or \"skill_id\")"}
	}

	if rawParams, present := object["params"]; present && rawParams != nil {
		if _, valid := rawParams.(map[string]interface{}); !valid {
			return metadata, validationError{http.StatusBadRequest, "INVALID_PARAMS", "params must be a JSON object"}
		}
	}

	if suppliedRobotID := stringField(object, "robot_id", "robotId"); suppliedRobotID != "" {
		if expectedRobotID != "" && suppliedRobotID != expectedRobotID {
			return metadata, validationError{http.StatusForbidden, "WRONG_ROBOT", "action targets a different robot"}
		}
		metadata.RobotID = suppliedRobotID
	}
	if metadata.RobotID == "" {
		metadata.RobotID = expectedRobotID
	}

	params, _ := object["params"].(map[string]interface{})
	if params == nil {
		params = map[string]interface{}{}
		object["params"] = params
	}
	metadata.ActionID = stringField(object, "action_id", "actionId", "id", "request_id", "requestId")
	metadata.IdempotencyKey = stringField(object, "idempotency_key", "idempotencyKey")
	if metadata.IdempotencyKey == "" {
		metadata.IdempotencyKey = metadata.ActionID
	}
	if metadata.ActionID == "" {
		metadata.ActionID = fmt.Sprintf("action-%d", time.Now().UnixNano())
	}
	if metadata.IdempotencyKey == "" {
		metadata.IdempotencyKey = metadata.ActionID
	}

	canonicalParams, err := json.Marshal(params)
	if err != nil {
		return metadata, validationError{http.StatusBadRequest, "INVALID_PARAMS", "params could not be canonicalized"}
	}
	hash := sha256.Sum256(canonicalParams)
	metadata.ParamsHash = fmt.Sprintf("sha256:%x", hash[:])
	metadata.ParamsCanonical = string(canonicalParams)
	return metadata, nil
}

func (h *Handlers) validateExecutionPolicy(metadata actionMetadata, payload interface{}) error {
	if len(h.AllowedSkills) == 0 {
		// Fail closed: without an explicit deployment allowlist no skill is
		// enabled and nothing may actuate.
		return validationError{http.StatusServiceUnavailable, "ALLOWLIST_NOT_CONFIGURED", "no skill allowlist is configured; refusing all actions"}
	}
	if _, ok := h.AllowedSkills[metadata.SkillID]; !ok {
		return validationError{http.StatusForbidden, "SKILL_NOT_ALLOWED", "action is not a registered skill for this robot"}
	}
	skill, found := h.skillForAction(metadata.SkillID)
	if !found {
		// A configured allowlist is not enough: it must be bound to a
		// concrete robot-scoped schema before anything can reach Zenoh.
		return validationError{http.StatusServiceUnavailable, "SKILL_CATALOG_NOT_CONFIGURED", "no schema is configured for the requested skill"}
	}
	object, _ := payload.(map[string]interface{})
	params, _ := object["params"].(map[string]interface{})
	if params == nil {
		params = map[string]interface{}{}
	}
	if err := validateParameters(skill.Params, params); err != nil {
		return validationError{http.StatusBadRequest, "INVALID_PARAMS", err.Error()}
	}
	if h.MaxDurationSeconds <= 0 {
		return nil
	}
	if raw, ok := params["duration"]; ok {
		duration, ok := raw.(float64)
		if !ok || duration <= 0 || duration > h.MaxDurationSeconds {
			return validationError{http.StatusBadRequest, "DURATION_LIMIT", fmt.Sprintf("duration must be between 0 and %.0f seconds", h.MaxDurationSeconds)}
		}
	}
	return nil
}

func validateParameters(schema map[string]ParamSchema, params map[string]interface{}) error {
	for name := range params {
		if _, known := schema[name]; !known {
			return fmt.Errorf("unknown parameter %q", name)
		}
	}
	for name, rule := range schema {
		value, present := params[name]
		if !present {
			if rule.Required {
				return fmt.Errorf("missing required parameter %q", name)
			}
			continue
		}
		if err := validateParameterValue(name, rule, value); err != nil {
			return err
		}
	}
	return nil
}

func validateParameterValue(name string, schema ParamSchema, value interface{}) error {
	switch schema.Type {
	case "string":
		text, ok := value.(string)
		if !ok || strings.TrimSpace(text) == "" {
			return fmt.Errorf("parameter %q must be a non-empty string", name)
		}
		if len(schema.Values) > 0 {
			for _, allowed := range schema.Values {
				if text == allowed {
					return nil
				}
			}
			return fmt.Errorf("parameter %q has an unsupported value", name)
		}
	case "number", "integer":
		number, ok := value.(float64)
		if !ok || math.IsNaN(number) || math.IsInf(number, 0) {
			return fmt.Errorf("parameter %q must be a finite number", name)
		}
		if schema.Type == "integer" && math.Trunc(number) != number {
			return fmt.Errorf("parameter %q must be an integer", name)
		}
		if schema.Minimum != nil && number < *schema.Minimum {
			return fmt.Errorf("parameter %q is below its minimum", name)
		}
		if schema.Maximum != nil && number > *schema.Maximum {
			return fmt.Errorf("parameter %q exceeds its maximum", name)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("parameter %q must be a boolean", name)
		}
	case "array":
		items, ok := value.([]interface{})
		if !ok {
			return fmt.Errorf("parameter %q must be an array", name)
		}
		if schema.MinItems != nil && len(items) < *schema.MinItems {
			return fmt.Errorf("parameter %q has too few items", name)
		}
		if schema.MaxItems != nil && len(items) > *schema.MaxItems {
			return fmt.Errorf("parameter %q has too many items", name)
		}
		seen := make(map[string]struct{})
		for index, item := range items {
			if schema.Items == nil {
				return fmt.Errorf("parameter %q has no item schema", name)
			}
			if err := validateParameterValue(fmt.Sprintf("%s[%d]", name, index), *schema.Items, item); err != nil {
				return err
			}
			if schema.UniqueItems {
				canonical, err := json.Marshal(item)
				if err != nil {
					return fmt.Errorf("parameter %q contains an invalid item", name)
				}
				key := string(canonical)
				if _, duplicate := seen[key]; duplicate {
					return fmt.Errorf("parameter %q contains duplicate items", name)
				}
				seen[key] = struct{}{}
			}
		}
	}
	return nil
}

// paymentFingerprint binds replay protection to the verified x402 payload,
// not its transport encoding.  PAYMENT-SIGNATURE is base64 JSON, so hashing
// its raw header bytes would allow the same authorization to be replayed with
// different whitespace, key order, or padding. The payment middleware stores
// the parsed/verified payload in the Gin context; encoding/json then gives us
// a deterministic semantic representation (including sorted map keys).
//
// The header fallback exists only for handler-unit callers that deliberately
// omit the payment middleware. Every production paid request reaches this
// handler with x402_payload set by deferredSettlementGate.
func paymentFingerprint(c *gin.Context) (string, error) {
	if payload, verified := c.Get("x402_payload"); verified {
		canonical, err := json.Marshal(payload)
		if err != nil {
			return "", fmt.Errorf("canonicalize verified payment payload: %w", err)
		}
		sum := sha256.Sum256(canonical)
		return fmt.Sprintf("sha256:%x", sum[:]), nil
	}
	if signature := c.GetHeader("PAYMENT-SIGNATURE"); signature != "" {
		sum := sha256.Sum256([]byte(signature))
		return fmt.Sprintf("sha256:%x", sum[:]), nil
	}
	return "", nil
}

func (h *Handlers) PostAction(c *gin.Context) {
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "failed to read request body"})
		return
	}

	if len(body) > 0 && !json.Valid(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body must be valid JSON"})
		return
	}

	var payload interface{}
	if len(body) > 0 {
		if err := json.Unmarshal(body, &payload); err != nil {
			payload = string(body)
		}
	}

	metadata, err := validatePayload(payload, h.RobotID)
	if err != nil {
		if contractErr, ok := err.(validationError); ok {
			h.Logger.Warn("invalid action contract", zap.Error(contractErr))
			c.JSON(contractErr.status, gin.H{
				"error":      contractErr.message,
				"error_code": contractErr.code,
			})
			return
		}
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid action contract", "error_code": "INVALID_CONTRACT"})
		return
	}
	if err := h.validateExecutionPolicy(metadata, payload); err != nil {
		contractErr := err.(validationError)
		h.Logger.Warn("action rejected by execution policy", zap.Error(contractErr))
		c.JSON(contractErr.status, gin.H{"error": contractErr.message, "error_code": contractErr.code})
		return
	}
	// Bind the reservation to the exact x402 payment payload so a replayed
	// payment can never actuate twice, even with a fresh idempotency key.
	paymentHash, err := paymentFingerprint(c)
	if err != nil {
		h.Logger.Warn("failed to fingerprint verified payment", zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "payment fingerprint unavailable", "error_code": "PAYMENT_FINGERPRINT_UNAVAILABLE"})
		return
	}
	if err := h.Replay.Reserve(metadata.IdempotencyKey, paymentHash, metadata.ActionID); err != nil {
		switch {
		case errors.Is(err, ErrReplayDetected):
			c.JSON(http.StatusConflict, gin.H{
				"error":      "duplicate action",
				"error_code": "REPLAY_DETECTED",
				"action_id":  metadata.ActionID,
			})
		case errors.Is(err, ErrPaymentReplayed):
			c.JSON(http.StatusConflict, gin.H{
				"error":      "payment payload already used",
				"error_code": "PAYMENT_REPLAY_DETECTED",
				"action_id":  metadata.ActionID,
			})
		default:
			h.Logger.Warn("idempotency store unavailable", zap.Error(err))
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": "idempotency store unavailable", "error_code": "IDEMPOTENCY_STORE_UNAVAILABLE"})
		}
		return
	}
	if err := h.Replay.BindActionMetadata(metadata.IdempotencyKey, metadata); err != nil {
		h.Replay.Release(metadata.IdempotencyKey)
		h.Logger.Warn("failed to persist action metadata", zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "idempotency store unavailable", "error_code": "IDEMPOTENCY_STORE_UNAVAILABLE"})
		return
	}
	waitResult, cleanupWait, err := h.prepareExecutionWait(metadata)
	if err != nil {
		h.Replay.Release(metadata.IdempotencyKey)
		h.Logger.Warn("failed to subscribe for simulator result", zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "result channel unavailable", "error_code": "RESULT_CHANNEL_UNAVAILABLE"})
		return
	}

	var paymentPayload interface{}
	if value, ok := c.Get("x402_payload"); ok {
		paymentPayload = value
	}

	var paymentRequirements interface{}
	if value, ok := c.Get("x402_requirements"); ok {
		paymentRequirements = value
	}

	event := gin.H{
		"payload":          payload,
		"action_id":        metadata.ActionID,
		"robot_id":         metadata.RobotID,
		"skill_id":         metadata.SkillID,
		"params_hash":      metadata.ParamsHash,
		"params_canonical": metadata.ParamsCanonical,
		"idempotency_key":  metadata.IdempotencyKey,
		"transaction_details": gin.H{
			"payment_payload":      paymentPayload,
			"payment_requirements": paymentRequirements,
		},
		"timestamp": time.Now().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Warn("failed to marshal action event", zap.Error(err))
		// Nothing was published; the reservation can be safely released.
		h.Replay.Release(metadata.IdempotencyKey)
		cleanupWait()
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to marshal action event"})
		return
	}
	if err := h.publish(eventBytes); err != nil {
		h.Logger.Warn("failed to publish action event", zap.Error(err))
		// Nothing was published; the reservation can be safely released.
		h.Replay.Release(metadata.IdempotencyKey)
		cleanupWait()
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "failed to publish action event"})
		return
	}
	// From this point the simulator may have actuated: the reservation is
	// never released. Failure/timeout are recorded as terminal outcomes so a
	// replay (same key or same payment) after restart still returns 409.
	if err := h.Replay.MarkOutcome(metadata.IdempotencyKey, "published"); err != nil {
		h.Logger.Warn("failed to persist published state", zap.Error(err))
	}

	// Deferred, execution-gated settlement: the payment gate verified the
	// payment synchronously and injected the settle callback. It runs only
	// inside the watcher below, strictly after a successful simulator result.
	var settle SettleFunc
	if value, ok := c.Get("x402_settle"); ok {
		if fn, ok := value.(SettleFunc); ok {
			settle = fn
		}
	}

	h.watchers.Add(1)
	go func() {
		defer h.watchers.Done()
		h.watchExecution(metadata, waitResult, cleanupWait, settle)
	}()

	// Immediate accepted/pending contract: the terminal outcome (and the
	// settlement receipt) is exposed by GET /action/:action_id/status under
	// the same action_id returned here.
	c.JSON(http.StatusAccepted, gin.H{
		"status":     "accepted",
		"state":      "pending",
		"action_id":  metadata.ActionID,
		"robot_id":   metadata.RobotID,
		"skill_id":   metadata.SkillID,
		"settlement": "pending-execution-gated",
		"status_url": "/action/" + metadata.ActionID + "/status",
		"timestamp":  time.Now().Format(time.RFC3339),
	})
}

// watchExecution waits for the correlated simulator result in the background
// and records the terminal outcome durably. Settlement happens here and only
// here: after a successful result. Failure and timeout never settle, and the
// idempotency record is kept so replays return 409 even after a restart.
func (h *Handlers) watchExecution(metadata actionMetadata, waitResult chan executionResult, cleanupWait func(), settle SettleFunc) {
	if cleanupWait != nil {
		defer cleanupWait()
	}
	var terminal executionResult
	success := true
	if waitResult != nil {
		select {
		case result := <-waitResult:
			terminal = result
			if !result.matches(metadata) {
				if err := h.Replay.MarkOutcomeDetails(metadata.IdempotencyKey, "failed", "SIMULATOR_RESULT_MISMATCH", nil); err != nil {
					h.Logger.Warn("failed to persist mismatched-result outcome", zap.Error(err))
				}
				h.Logger.Warn("simulator result did not match published action; payment not settled",
					zap.String("action_id", metadata.ActionID))
				return
			}
			success = strings.EqualFold(result.Status, "success")
		case <-time.After(executionTimeout()):
			if err := h.Replay.MarkOutcomeDetails(metadata.IdempotencyKey, "timeout", "SIMULATOR_RESULT_TIMEOUT", nil); err != nil {
				h.Logger.Warn("failed to persist timeout outcome", zap.Error(err))
			}
			h.Logger.Warn("simulator result timeout — payment not settled",
				zap.String("action_id", metadata.ActionID))
			return
		}
	}
	if !success {
		errorCode := terminal.ErrorCode
		if errorCode == "" {
			errorCode = "SIMULATOR_EXECUTION_FAILED"
		}
		if err := h.Replay.MarkOutcomeWithResult(metadata.IdempotencyKey, "failed", errorCode, nil, terminal.Result); err != nil {
			h.Logger.Warn("failed to persist failure outcome", zap.Error(err))
		}
		h.Logger.Warn("simulator execution failed — payment not settled",
			zap.String("action_id", metadata.ActionID))
		return
	}

	if settle == nil {
		if err := h.Replay.MarkOutcomeWithResult(metadata.IdempotencyKey, "succeeded", "", nil, terminal.Result); err != nil {
			h.Logger.Warn("failed to persist success outcome", zap.Error(err))
		}
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	receipt, err := settle(ctx)
	if err != nil {
		// Execution succeeded but settlement failed: never retried silently,
		// surfaced via the status endpoint so the payer is not charged blind.
		if markErr := h.Replay.MarkOutcomeDetails(metadata.IdempotencyKey, "settlement_failed", "SETTLEMENT_FAILED", nil); markErr != nil {
			h.Logger.Warn("failed to persist settlement failure", zap.Error(markErr))
		}
		h.Logger.Warn("deferred settlement failed", zap.Error(err),
			zap.String("action_id", metadata.ActionID))
		return
	}
	if err := h.Replay.MarkOutcomeWithResult(metadata.IdempotencyKey, "succeeded", "", receipt, terminal.Result); err != nil {
		h.Logger.Warn("failed to persist settled outcome", zap.Error(err))
	}
	h.Logger.Info("action settled after successful execution",
		zap.String("action_id", metadata.ActionID),
		zap.String("transaction", receiptTransaction(receipt)))
}

func receiptTransaction(receipt *SettlementRecord) string {
	if receipt == nil {
		return ""
	}
	return receipt.Transaction
}

// GetActionStatus serves the terminal-result half of the accepted/pending
// contract: GET /action/:action_id/status returns the durable execution and
// settlement state for the action_id issued by POST /action.
func (h *Handlers) GetActionStatus(c *gin.Context) {
	actionID := strings.TrimSpace(c.Param("action_id"))
	status, found := h.Replay.StatusByActionID(actionID)
	if !found {
		c.JSON(http.StatusNotFound, gin.H{"error": "unknown action id", "error_code": "UNKNOWN_ACTION", "action_id": actionID})
		return
	}

	state := status.Status
	if state == "reserved" || state == "published" {
		// A record stranded in a pre-terminal state (e.g. crash between
		// publish and outcome) is reported as timeout once the execution
		// window has passed; it stays unsettled either way.
		if time.Since(status.UpdatedAt) > executionTimeout() {
			state = "timeout"
			if err := h.Replay.MarkOutcomeDetails(status.Key, "timeout", "SIMULATOR_RESULT_TIMEOUT", nil); err != nil {
				h.Logger.Warn("failed to persist stale timeout", zap.Error(err))
			}
			status.ErrorCode = "SIMULATOR_RESULT_TIMEOUT"
		} else {
			state = "pending"
		}
	}

	response := gin.H{
		"action_id":       status.ActionID,
		"robot_id":        status.RobotID,
		"skill_id":        status.SkillID,
		"params_hash":     status.ParamsHash,
		"idempotency_key": status.Key,
		"state":           state,
		"settled":         status.Settlement != nil,
		"updated_at":      status.UpdatedAt.Format(time.RFC3339),
	}
	if status.ErrorCode != "" {
		response["error_code"] = status.ErrorCode
	}
	if status.Settlement != nil {
		response["settlement"] = gin.H{
			"transaction":      status.Settlement.Transaction,
			"network":          status.Settlement.Network,
			"payer":            status.Settlement.Payer,
			"payment_response": status.Settlement.PaymentResponse,
		}
	}
	if len(status.Result) > 0 && json.Valid(status.Result) {
		var result interface{}
		if err := json.Unmarshal(status.Result, &result); err == nil {
			response["result"] = result
		}
	}
	c.JSON(http.StatusOK, response)
}

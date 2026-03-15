# Provider Configuration and Settings UI Plan

## 1. Overview

This design adds a new top-level **Settings** entry to the embedded web UI in `orchestrator/web/realtime_service.py`, with **Settings** appearing last in the existing menu after `Home` and `Music`. Inside Settings, the first implemented tab is **Providers**.

The Providers work is meant to solve a real gap in the current repo:

- the web UI already supports live status, chat, music, and timers,
- but provider-like configuration is still mostly startup-only and env-driven via `VoiceConfig` in `orchestrator/config.py`,
- and the orchestrator constructs long-lived service clients once at startup in `orchestrator/main.py`:
  - `WhisperClient(config.whisper_url)`
  - `PiperClient(config.piper_url)`
  - `QuickAnswerClient(...)`

The proposed design introduces a **separate provider configuration subsystem** for:

- OpenAI-compatible endpoint providers,
- STT service selection,
- TTS service selection,
- provider presets,
- env-backed read-only providers,
- and live runtime application without restarting the orchestrator.

This is intentionally scoped to the provider/settings problem. It does **not** redesign the broader gateway family in `orchestrator/gateway/factory.py` as part of phase 1.

## 2. Current state in codebase

### Embedded web UI / service

The embedded UI is served directly from `orchestrator/web/realtime_service.py`:

- `_build_ui_html(...)` returns a large inline HTML/JS app.
- It already has a hash-route menu with:
  - `#/home`
  - `#/music`
- The dropdown menu currently renders only:
  - `🏠 Home`
  - `🎵 Music`

There is no Settings page yet.

The service class `EmbeddedVoiceWebService` already supports:

- HTTP serving for `/`, `/index.html`, `/health`
- WebSocket status and actions
- in-memory chat state
- music state publishing
- timer state publishing
- UI mic state
- browser audio streaming
- websocket broadcasts to the current client

That is a strong base for adding Settings without introducing a second web stack.

### Existing settings/UI patterns

The UI is a single embedded app with render functions such as:

- `renderHomePage`
- `renderMusicPage`
- `renderTimerBar`

State is held client-side in a single JS object `S`, which already tracks:

- `page`
- `chat`
- `music`
- `timers`
- websocket state
- UI mic state

That means a Settings page can follow the same pattern:

- add `#/settings`
- add `renderSettingsPage()`
- add local state for provider forms and settings snapshots

### Current config loading

`orchestrator/config.py` defines `VoiceConfig(BaseSettings)` and loads one env profile file via `_detect_env_file()`.

Priority today is:

1. `OPENCLAW_ENV_FILE`
2. `.env.docker`
3. `.env.pi`
4. `.env`

Important current characteristics:

- config is env-backed, not DB-backed
- config is mostly startup-time
- there is no separate provider config file today
- there is no general hot-reload mechanism for config mutations

Relevant current fields:

- `whisper_url`
- `piper_url`
- `piper_voice_id`
- `piper_speed`
- `quick_answer_enabled`
- `quick_answer_llm_url`
- `quick_answer_model`
- `quick_answer_api_key`

### Quick answer provider/client config

`orchestrator/gateway/quick_answer.py` already uses an **OpenAI-compatible chat completions endpoint**:

- `QuickAnswerClient(llm_url, model, api_key, ...)`
- posts to `llm_url`
- sends `model`, `messages`, `tools`, `tool_choice`
- assumes OpenAI-compatible response structure

This is the clearest existing “provider” integration in the repo.

Today, it is configured only through env-backed `VoiceConfig` fields:

- `QUICK_ANSWER_LLM_URL`
- `QUICK_ANSWER_MODEL`
- `QUICK_ANSWER_API_KEY`

There is no provider registry, no preset catalog, and no runtime swapping of quick-answer endpoints.

### STT/TTS / whisper / piper config

#### STT

`orchestrator/stt/whisper_client.py` is minimal:

- `WhisperClient(base_url)`
- POSTs `file` to `POST /transcribe`

Current local whisper services in `docker/whisper/` expose:

- `POST /transcribe`
- `GET /health`

`docker/whisper/app_whispercpp.py` returns backend/runtime info, but **does not expose a model catalog endpoint** today.

#### TTS

`orchestrator/tts/piper_client.py` is also minimal:

- `PiperClient(base_url)`
- POSTs to `POST /synthesize`
- passes `voice` and `length_scale`

Current local piper service in `docker/piper/app.py` exposes:

- `POST /synthesize`
- `GET /voices`
- `GET /health`

This means TTS already has a local discovery surface for voices; STT mostly does not.

### Runtime reload / service registry patterns

There is **no generic provider registry** today.

`orchestrator/main.py` builds singleton clients once:

- `whisper_client`
- `piper`
- `quick_answer_client`
- `gateway`

and then uses them through long-running loops:

- `process_chunk()` for STT
- `tts_loop()` for TTS
- `send_debounced_transcripts()` for QA/gateway routing

There is one existing live-update pattern worth reusing:

- the web UI service accepts actions and updates in-memory runtime state immediately
- music/timer UI state is published from background loops
- the UI refresh watcher calls `/health` and reloads the page when the embedded UI instance changes

So the repo already supports **live state mutation**, just not live provider mutation.

## 3. Goals and non-goals

### Goals

- Add a top-level **Settings** menu item, last in the list.
- Add a **tabbed Settings page**, starting with **Providers**.
- Support add/edit/delete for OpenAI-compatible providers with:
  - name
  - endpoint
  - api_key
  - model selection
- Include grouped provider presets for major and minor providers.
- Store provider settings separately from existing env-based config.
- Apply changes to both persistent provider config files and in-memory runtime immediately.
- Make env-backed providers visible in UI but read-only and undeletable.
- Allow STT and TTS model/provider selection from configured providers with matching capabilities.
- Allow local service config for whisper-compatible and piper-compatible endpoints.
- Support an STT “voice” selection field whose options reflect the selected STT provider’s capabilities.
- Keep the design compatible with the current embedded UI architecture.

### Non-goals

- Replacing the existing gateway family in `orchestrator/gateway/factory.py`.
- Hot-reloading unrelated orchestrator settings such as:
  - wake-word engine
  - audio devices
  - VAD thresholds
  - MPD settings
- Building a standalone frontend app or introducing a separate SPA build pipeline in phase 1.
- Solving remote authentication/authorization for the embedded UI beyond current local-service assumptions.
- Standardizing capability discovery for every third-party vendor on day one; some will require preset metadata and manual overrides.

## 4. Proposed architecture

### High-level design

Add a new provider/settings layer with three main pieces:

1. **ProviderStore**
   - loads/saves provider config files
   - merges persisted providers with env-backed overlays
   - owns atomic writes and file permissions

2. **ProviderRuntimeRegistry**
   - exposes the currently active runtime bindings for:
     - quick-answer LLM
     - STT
     - TTS
   - swaps live clients atomically
   - gives new requests the new config without restarting the process

3. **Settings API + UI**
   - HTTP JSON endpoints added to the embedded web service
   - websocket notifications for settings updates
   - new `#/settings` page with tabs

### Recommended new modules

Likely additions under `openclaw-voice/orchestrator/`:

- `orchestrator/providers/store.py`
- `orchestrator/providers/models.py`
- `orchestrator/providers/runtime.py`
- `orchestrator/providers/presets.py`
- `orchestrator/providers/discovery.py`

This keeps provider logic out of `VoiceConfig`, which is already crowded and env-centric.

### Why not extend `VoiceConfig` for everything?

Because `VoiceConfig` is designed around `BaseSettings` and env files. That is fine for startup config, but poor for:

- CRUD from the web UI
- partial mutation
- read-only env overlays
- secret masking
- atomic runtime updates
- capability caching

The design should leave `VoiceConfig` in place for startup settings and legacy defaults, while moving provider-driven service routing to a dedicated runtime layer.

### Runtime request flow after this change

#### STT

Current:

- `process_chunk()` calls `whisper_client.transcribe(...)`

Proposed:

- `process_chunk()` calls `provider_runtime.stt.transcribe(...)`
- the active STT binding determines:
  - provider
  - endpoint
  - model
  - optional voice/profile
  - auth key

#### TTS

Current:

- `tts_loop()` calls `piper.synthesize(text, config.piper_voice_id, config.piper_speed)`

Proposed:

- `tts_loop()` calls `provider_runtime.tts.synthesize(...)`
- active binding supplies:
  - provider
  - model
  - voice
  - speed
  - auth

#### Quick answer

Current:

- `send_debounced_transcripts()` uses `quick_answer_client`

Proposed:

- quick answer path resolves the active LLM provider from the runtime registry
- `QuickAnswerClient` can still be reused as the OpenAI-compatible transport, but its config comes from runtime provider bindings rather than only `VoiceConfig`

## 5. Provider config file schema

Use **two persistent files** so provider settings are separate from `.env` and secrets can be handled distinctly.

### Proposed file locations

At repo root, create a dedicated settings area:

- `providers/providers.json`
- `providers/providers.secrets.json`

This matches the repo’s existing pattern of separate persisted state directories like `timers/`.

### `providers/providers.json`

Holds non-secret provider definitions, presets, capability hints, and current bindings.

```json
{
  "version": 1,
  "providers": [
    {
      "id": "prov_openrouter_main",
      "name": "OpenRouter",
      "kind": "openai_compatible",
      "preset_id": "openrouter",
      "origin": "file",
      "readonly": false,
      "deletable": true,
      "endpoint": "https://openrouter.ai/api/v1",
      "default_model": "openai/gpt-4o-mini",
      "capabilities": {
        "llm": true,
        "stt": false,
        "tts": false
      },
      "model_overrides": {
        "llm": ["openai/gpt-4o-mini"],
        "stt": [],
        "tts": []
      },
      "voice_options": {
        "stt": [],
        "tts": []
      },
      "metadata": {
        "group": "major",
        "auth_env_var": "OPENROUTER_API_KEY",
        "last_discovered_at": "2026-03-15T00:00:00Z"
      }
    },
    {
      "id": "prov_local_whisper",
      "name": "Local Whisper",
      "kind": "whisper_http",
      "preset_id": "local_whisper",
      "origin": "file",
      "readonly": false,
      "deletable": true,
      "endpoint": "http://localhost:10000",
      "default_model": "ggml-large-v3",
      "capabilities": {
        "llm": false,
        "stt": true,
        "tts": false
      },
      "model_overrides": {
        "stt": ["ggml-large-v3"]
      },
      "voice_options": {
        "stt": []
      }
    },
    {
      "id": "prov_local_piper",
      "name": "Local Piper",
      "kind": "piper_http",
      "preset_id": "local_piper",
      "origin": "file",
      "readonly": false,
      "deletable": true,
      "endpoint": "http://localhost:10001",
      "default_model": "en_US-amy-medium",
      "capabilities": {
        "llm": false,
        "stt": false,
        "tts": true
      },
      "model_overrides": {
        "tts": ["en_US-amy-medium"]
      },
      "voice_options": {
        "tts": ["en_US-amy-medium"]
      }
    }
  ],
  "bindings": {
    "quick_answer": {
      "enabled": true,
      "provider_id": "prov_openrouter_main",
      "model": "openai/gpt-4o-mini"
    },
    "stt": {
      "provider_id": "prov_local_whisper",
      "model": "ggml-large-v3",
      "voice": null
    },
    "tts": {
      "provider_id": "prov_local_piper",
      "model": "en_US-amy-medium",
      "voice": "en_US-amy-medium",
      "speed": 1.0
    }
  }
}
```

### `providers/providers.secrets.json`

Holds only secrets, keyed by provider id.

```json
{
  "version": 1,
  "secrets": {
    "prov_openrouter_main": {
      "api_key": "sk-..."
    },
    "prov_local_whisper": {
      "api_key": ""
    },
    "prov_local_piper": {
      "api_key": ""
    }
  }
}
```

### Env-backed providers

Env-backed providers are **not** the source of truth in these files. They are overlaid at load time with:

- `origin: "env"`
- `readonly: true`
- `deletable: false`

This prevents the files from silently copying env secrets into editable state.

## 6. Runtime live-reload design

### Current limitation

Today, runtime service clients are constructed once and then captured by long-running closures in `orchestrator/main.py`.

That means edits to URLs, models, keys, or provider choice are currently ignored until restart.

### Proposed runtime registry

Introduce `ProviderRuntimeRegistry` with an atomic snapshot such as:

- active quick-answer config
- active STT config
- active TTS config
- cached provider definitions
- built transport clients

The registry should support:

- `get_snapshot()`
- `apply_settings(new_store_snapshot)`
- `get_stt_client()`
- `get_tts_client()`
- `get_quick_answer_client()`

### Swap model

When a settings update succeeds:

1. validate request payload
2. update in-memory model
3. atomically write `providers.json`
4. atomically write `providers.secrets.json`
5. rebuild affected runtime clients only
6. swap registry snapshot under a lock
7. broadcast websocket settings update
8. return updated snapshot to the caller

### Request consistency rule

- **In-flight** STT/TTS/QA requests continue using the old snapshot.
- **New** requests use the new snapshot immediately.

That avoids mid-request mutation chaos, which is always a party nobody wanted.

### Affected runtime call sites

#### `process_chunk()`

Switch from direct `whisper_client` use to runtime registry lookup.

#### `tts_loop()`

Switch from direct `piper` plus `config.piper_voice_id` to runtime binding lookup.

#### `send_debounced_transcripts()`

Switch quick-answer routing from static `quick_answer_client` to runtime provider binding lookup.

### What does not live-reload in phase 1

The following remain startup-time and env-based:

- gateway family selection (`build_gateway(config)`)
- audio devices
- wake word engines
- VAD engine choice
- embedded web server ports

## 7. Web service/API contract

The embedded UI service already owns HTTP and websocket responsibilities, so extend it rather than adding a separate web service.

### New HTTP endpoints

#### Provider CRUD

- `GET /api/settings/providers`
  - returns merged provider list:
    - file-backed editable providers
    - env-backed read-only providers
    - current bindings
    - masked secrets

- `POST /api/settings/providers`
  - create provider

- `PATCH /api/settings/providers/{provider_id}`
  - edit provider

- `DELETE /api/settings/providers/{provider_id}`
  - delete provider if not env-backed

#### Bindings

- `GET /api/settings/providers/bindings`
- `PUT /api/settings/providers/bindings/quick-answer`
- `PUT /api/settings/providers/bindings/stt`
- `PUT /api/settings/providers/bindings/tts`

#### Presets and discovery

- `GET /api/settings/providers/presets`
- `POST /api/settings/providers/{provider_id}/discover`
  - refresh models/capabilities from the remote/local provider

#### Optional validation/test endpoints

- `POST /api/settings/providers/test-connection`
- `POST /api/settings/providers/{provider_id}/test`

### Response shape

For any provider returned to UI, secrets should be masked:

```json
{
  "id": "prov_openai_env",
  "name": "OpenAI",
  "origin": "env",
  "readonly": true,
  "deletable": false,
  "endpoint": "https://api.openai.com/v1",
  "api_key": {
    "present": true,
    "masked": "sk-...abcd"
  }
}
```

### WebSocket events

Add broadcast events to the existing websocket stream:

- `settings_providers_snapshot`
- `settings_provider_updated`
- `settings_bindings_updated`
- `settings_discovery_updated`
- `settings_error`

The UI can keep using HTTP for writes and websocket for live fan-out to all open clients.

### Where to implement in current code

Inside `EmbeddedVoiceWebService._start_http_server()`:

- extend `UIHandler.do_GET`
- add `do_POST`, `do_PATCH`, `do_DELETE`, `do_PUT`

The class already has in-memory state and broadcast helpers, so this is the natural insertion point.

## 8. UI/UX plan for Settings > Providers

### Menu change

In `_build_ui_html(...)`, update the current menu:

1. Home
2. Music
3. Settings

Settings must be **last**.

### Route

Add `#/settings` to the existing hash route model:

- `getPage()` should recognize `settings`
- `renderPage()` should call `renderSettingsPage()`

### Settings page layout

Use the same inline Tailwind approach already in the file.

#### Top-level Settings tabs

Start with tabs even if only one is active now:

- `Providers` (enabled)
- future tabs can be visually present but disabled/hidden if desired:
  - `Audio`
  - `Wake Word`
  - `System`

For phase 1, only `Providers` needs functional content.

### Providers tab sections

#### A. Provider list

A table/list of all providers showing:

- name
- type
- endpoint
- origin (`file` or `env`)
- capabilities badges (`LLM`, `STT`, `TTS`)
- default model
- read-only badge when env-backed

Actions:

- Add
- Edit
- Delete
- Duplicate
- Refresh models/capabilities

For env-backed rows:

- Edit disabled
- Delete disabled
- Duplicate allowed

#### B. Add/Edit provider drawer or modal

Fields:

- `name`
- `preset`
- `endpoint`
- `api_key`
- `default_model`
- capability toggles or inferred capability summary
- advanced section:
  - model override fields
  - headers if needed later
  - provider kind

Preset selection should auto-fill endpoint and metadata.

#### C. STT configuration section

Fields:

- STT provider dropdown
- STT model dropdown
- STT voice dropdown

Behavior:

- provider dropdown lists only providers with `stt=true`
- model options update when provider changes
- voice options update when provider changes
- if provider has no `voice_options.stt`, disable the field with explanatory text

#### D. TTS configuration section

Fields:

- TTS provider dropdown
- TTS model dropdown
- TTS voice dropdown
- speed field

Behavior:

- provider dropdown lists only `tts=true` providers
- for local piper, voice/model can map to `/voices`
- current `PIPER_SPEED` behavior becomes a binding-level field

#### E. Quick answer / LLM section

Fields:

- enable quick answer
- LLM provider dropdown
- model dropdown

This should replace the current reliance on only `quick_answer_*` env fields for the live QA path.

## 9. Provider preset catalog structure

### Purpose

OpenAI-compatible endpoints do not standardize enough metadata for a good UX. `GET /v1/models` tells you available model ids, but not reliably whether a model supports:

- chat
- STT
- TTS
- voice options

So the app needs a preset catalog with capability hints.

### Proposed module

`orchestrator/providers/presets.py`

### Preset shape

```python
{
  "id": "openrouter",
  "label": "OpenRouter",
  "group": "major",
  "kind": "openai_compatible",
  "endpoint": "https://openrouter.ai/api/v1",
  "auth_env_var": "OPENROUTER_API_KEY",
  "capability_defaults": {
    "llm": True,
    "stt": False,
    "tts": False
  },
  "model_discovery": {
    "type": "openai_models",
    "path": "/models"
  }
}
```

### Suggested groups

#### Major providers

- OpenAI
- OpenRouter
- Groq
- Together
- Fireworks
- DeepInfra
- Mistral
- xAI
- Perplexity

#### Minor / specialty providers

- Cerebras
- SambaNova
- Hyperbolic
- Nebius
- Novita
- Lepton
- DeepSeek-compatible hosted endpoints

#### Local / self-hosted

- LM Studio
- Ollama OpenAI-compatible endpoint
- vLLM
- llama.cpp server
- LiteLLM proxy
- OpenWebUI proxy
- local whisper
- local piper

### Why grouped select list

This matches the user requirement and keeps the add flow fast:

- choose preset group
- choose provider
- auto-fill endpoint and defaults
- adjust model/key

## 10. Env-backed read-only provider behavior

### Detection

At load time, the provider store should synthesize provider rows from known env vars when present.

#### Candidate env mappings

For OpenAI-compatible presets, examples include:

- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `GROQ_API_KEY`
- `TOGETHER_API_KEY`
- `FIREWORKS_API_KEY`
- `DEEPINFRA_API_KEY`
- `MISTRAL_API_KEY`
- `XAI_API_KEY`
- `PERPLEXITY_API_KEY`
- `CEREBRAS_API_KEY`
- `SAMBANOVA_API_KEY`

Also support the current repo-specific quick-answer env mapping:

- `QUICK_ANSWER_LLM_URL`
- `QUICK_ANSWER_MODEL`
- `QUICK_ANSWER_API_KEY`

### UI behavior

Env-backed providers should:

- be visible in the provider list
- show origin = `env`
- show masked key state
- be read-only
- not be deletable
- be selectable for bindings

Recommended extra action:

- **Duplicate to editable provider**
  - copies endpoint/model metadata into file-backed provider
  - does not expose or copy the raw env secret unless the user explicitly pastes one

### Persistence behavior

Env-backed providers should not be written back into `providers.json` or `providers.secrets.json` as editable canonical data.

They are a runtime overlay only.

## 11. STT/TTS and voice capability model

### Capability problem

The repo currently has three different reality levels:

1. OpenAI-compatible LLM providers:
   - good for quick-answer chat completions
   - poor standardized capability metadata

2. Whisper-compatible STT services:
   - current repo supports `POST /transcribe`
   - current local whisper service does not expose model/voice catalogs

3. Piper-compatible TTS services:
   - current repo supports `POST /synthesize`
   - current local piper service exposes `GET /voices`

A unified capability model is needed so the UI can filter providers and options consistently.

### Proposed capability structure

Per provider:

```json
{
  "capabilities": {
    "llm": true,
    "stt": true,
    "tts": false
  },
  "models": {
    "llm": ["gpt-4o-mini"],
    "stt": ["whisper-1"],
    "tts": []
  },
  "voices": {
    "stt": [],
    "tts": ["alloy", "verse"]
  }
}
```

### STT voice semantics

The user requirement calls for an STT voice selector. In practice:

- some STT providers may not support voice/speaker profiles at all
- current local whisper service effectively has no voice catalog today

So the design should support **optional STT voice capability**:

- if provider exposes STT voice options, show them
- if not, disable the field and show `No voice options for this provider`

This keeps the model future-proof without pretending Whisper already supports a feature it does not.

### Local service compatibility

#### Local whisper-compatible provider

For current `docker/whisper/app_whispercpp.py` and `docker/whisper/app.py`:

- required compatibility:
  - `POST /transcribe`
  - `GET /health`
- recommended future addition:
  - `GET /models`
  - or `GET /capabilities`

Until then, model options can be:

- derived from health/preset/default
- manually overrideable in provider config

#### Local piper-compatible provider

For current `docker/piper/app.py`:

- `GET /voices` already exists
- `GET /health` already exists
- this is enough for initial TTS model/voice discovery

## 12. Validation, security, and secrets handling

### Validation rules

#### Provider-level

- `name` required, unique
- `endpoint` required, must be `http://` or `https://`
- `api_key` optional for local services, required for presets that need auth
- `kind` must match known provider types
- `default_model` required when the binding depends on it

#### Binding-level

- selected provider must exist
- selected provider must advertise the requested capability
- selected model must be in discovered/allowed model list, unless manual override is enabled
- selected voice must be in provider voice options when voice list is non-empty

### Secret handling

- store secrets only in `providers.secrets.json`
- write secrets file with `0600` permissions, similar in spirit to the OpenClaw device identity persistence in `orchestrator/gateway/providers.py`
- never return raw API keys to UI after create/update
- always return masked values only

### Web service hardening

The current embedded UI handler sets permissive CORS headers:

- `Access-Control-Allow-Origin: *`

That is acceptable for current read-mostly UI assets, but unsafe for settings endpoints that handle secrets.

For the new settings API:

- restrict to same-origin by default
- do not use wildcard CORS for secret-bearing endpoints
- avoid logging raw API keys
- redact secrets from validation errors and broadcast payloads

### Atomic write strategy

Use:

- temp file
- fsync if practical
- rename into place

for both provider files, to avoid partial writes on power loss or abrupt shutdown.

## 13. Migration strategy

### Bootstrapping from current config

On first launch after this feature is introduced:

1. load `VoiceConfig` as today
2. check for `providers/providers.json`
3. if missing, create initial provider store snapshot from current runtime config

### Recommended initial import behavior

#### File-backed imported defaults

Create editable defaults for current local services from:

- `WHISPER_URL`
- `PIPER_URL`
- `PIPER_VOICE_ID`
- `PIPER_SPEED`

This preserves existing behavior while making the local speech services manageable from UI.

#### Env-backed overlays

Create read-only providers from:

- vendor API key env vars
- current `QUICK_ANSWER_*` env settings when present

This satisfies the env-backed read-only requirement.

### Legacy fallback

If provider files are absent or invalid:

- fall back to current `VoiceConfig` startup behavior
- log a warning
- keep orchestrator usable

That makes rollout safer.

### Compatibility during transition

For an initial implementation phase, keep these legacy fields in `VoiceConfig`:

- `quick_answer_llm_url`
- `quick_answer_model`
- `quick_answer_api_key`
- `whisper_url`
- `piper_url`
- `piper_voice_id`
- `piper_speed`

but treat them as migration/bootstrap inputs rather than the preferred live runtime source.

## 14. Testing plan

### Unit tests

Add focused tests for:

#### Provider store

- load empty store
- load valid store
- merge env-backed overlay providers
- reject invalid schemas
- atomic save round-trip
- mask secrets in API responses

#### Runtime registry

- apply updated STT binding
- apply updated TTS binding
- apply updated QA provider
- confirm new requests use new snapshot
- confirm in-flight requests are unaffected

#### Preset handling

- grouped preset catalog loads correctly
- env var mapping creates correct read-only providers
- capability filtering works

### API tests

Add tests for embedded settings endpoints:

- `GET /api/settings/providers`
- create/edit/delete provider
- reject delete on env-backed provider
- update STT/TTS bindings
- discovery refresh

### Integration tests

Use current repo patterns (`e2e_test.py`, pytest-style tests, standalone integration scripts) to verify:

- updating STT provider changes the endpoint used by `process_chunk()`
- updating TTS provider/voice changes the endpoint or voice used by `tts_loop()`
- updating quick-answer provider changes the endpoint/model used by the QA path
- websocket broadcasts update the active Settings UI without page reload

### UI tests

Lightweight browser tests should verify:

- Settings menu item exists and is last
- `#/settings` route renders
- Providers tab loads
- add/edit/delete flows work for file-backed providers
- env-backed rows are visible but locked
- STT voice dropdown reacts to provider change

## 15. Implementation phases

### Phase 1: data model and persistence

- add provider store models
- add `providers.json` + `providers.secrets.json` handling
- add preset catalog
- add env-backed overlay logic

### Phase 2: embedded API surface

- add settings HTTP endpoints to `EmbeddedVoiceWebService`
- add websocket settings update events
- add server-side validation and masking

### Phase 3: UI shell and Providers page

- add Settings menu item last in dropdown
- add `#/settings`
- add tab layout
- implement Providers tab with list + edit form + bindings sections

### Phase 4: runtime registry integration

- add `ProviderRuntimeRegistry`
- switch quick-answer path to runtime provider bindings
- switch STT path to runtime provider bindings
- switch TTS path to runtime provider bindings

### Phase 5: discovery and capability refinement

- add model discovery for OpenAI-compatible providers via `/models`
- add local piper discovery via `/voices`
- add whisper-compatible capability discovery fallback logic
- add optional `GET /models` or `GET /capabilities` to local whisper service in a follow-on change

### Phase 6: migration and polish

- bootstrap from existing env config
- add duplicate-from-env provider flow
- improve UX copy, validation, and error states

## 16. Open questions / risks

### 1. STT “voice” semantics are not fully defined

The requirement asks for STT voice selection, but current local whisper services do not expose voice options. This needs a product decision:

- is “voice” really a speaker profile?
- a recognition profile?
- or should the field be optional/disabled for providers without support?

Recommendation: implement as optional capability-backed field.

### 2. OpenAI-compatible `/models` is not enough to infer STT/TTS support

Most providers do not return clean capability metadata. The system will need:

- preset hints
- manual capability overrides
- or vendor-specific discovery adapters

### 3. Current embedded UI file is already large

`orchestrator/web/realtime_service.py` currently contains a large inline HTML/JS string. Adding a full Settings UI here is feasible, but the file will get even denser. That is acceptable for phase 1, but it increases maintenance cost.

### 4. Live reload touches hot paths

Changing STT/TTS/QA bindings live affects:

- `process_chunk()`
- `tts_loop()`
- `send_debounced_transcripts()`

These are latency-sensitive. The registry swap must be simple and lock-light.

### 5. Secret-bearing settings over the embedded web server need tighter security posture

Current wildcard CORS and unauthenticated local-serving assumptions are weak for a secret-edit UI. Even if phase 1 stays LAN/local only, the settings endpoints should not inherit permissive secret exposure behavior.

### 6. Multiple concurrent UI editors

The current embedded UI is effectively single-client oriented in several places. Settings editing raises consistency questions:

- last write wins?
- optimistic concurrency?
- ETag/version field?

Recommendation: use a store `version` and reject stale writes in later phases; phase 1 can start with last-write-wins plus websocket refresh.

### 7. Gateway providers are adjacent but out of scope

The repo already has a separate gateway provider family in `orchestrator/gateway/factory.py` and `orchestrator/gateway/providers.py`. Users may expect Settings > Providers to cover those too. This design intentionally does not do that in phase 1; it focuses on OpenAI-compatible LLM providers plus STT/TTS service providers.

### 8. Migration UX for existing quick-answer env settings

If current users rely on `QUICK_ANSWER_*` env vars, the first-run presentation matters. Recommended behavior:

- show an env-backed read-only provider
- auto-bind to it
- offer “Duplicate to editable provider”

That avoids surprising silent config rewrites.

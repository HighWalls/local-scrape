# Choose your AI provider and model

AI is optional. The downloaded defaults are `llm_provider: none`, an empty
`model`, and `llm_base_url: null`. Plain scrape, crawl, search and non-AI monitor
checks need no model, API account, Ollama daemon or downloaded weights. An AI
extract/schema request with `none` fails clearly instead of choosing an account.
Follow [QUICKSTART.md](../QUICKSTART.md) for setup and a complete extraction example.

## Supported protocols, not a universal model catalog

Choose a chat-capable text-generation model exposed through one of these
protocols. Copy its **exact identifier** from the selected provider/server into
`model`; keep provider prefixes, slashes, tags and case. No fixed model catalog
or automatic model choice is bundled. All `YOUR_CHAT_MODEL_ID` and `provider/model`
values below are placeholders to replace, not names to send unchanged.

| `llm_provider` | Preset `llm_base_url` when omitted/null | Request appended to base |
| --- | --- | --- |
| `none` | None | No inference request |
| `openai` | `https://api.openai.com/v1` | `/chat/completions` |
| `openai_compatible` | No preset; you must set it | `/chat/completions` |
| `anthropic` | `https://api.anthropic.com/v1` | `/messages` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `/models/<model>:generateContent` |
| `ollama` | `http://127.0.0.1:11434` | `/api/chat` (native Ollama) |

Embeddings, image-only models, OAuth-only accounts, Responses-only routes and
provider-specific features are not generally covered. A compatible API name is
not a guarantee that every model accepts the same generation parameters or
produces schema-valid output. This app sends text extraction requests, not an
arbitrary provider SDK workload. There is no automatic reuse of Hermes login,
OAuth subscriptions, credentials or current chat model.

**Base URL means the prefix before the request suffix**, not a full inference
URL. Preserve `/v1`, `/api/v1`, `/openai/v1` or `/v1beta` when the provider uses it.
Do not append `/chat/completions`, `/messages`, `/models/...:generateContent` or
`/api/chat` yourself. Remote endpoints require **HTTPS**; plaintext HTTP is
allowed only for **loopback**. No embedded credentials, query string or fragment
is allowed. Do not weaken target-page private-network guards to configure AI:
the model endpoint and the URLs you scrape are separate settings.

## Configuration examples

If `config.json` does not exist, copy `config.example.json` to it, then merge
**one** example below into that local file using an editor. Preserve your other
settings; do not concatenate multiple JSON objects. Named-provider presets work
with `llm_base_url: null`. Replace the model placeholder before an AI request.

### OpenAI Chat Completions

```json
{
  "llm_provider": "openai",
  "model": "YOUR_CHAT_MODEL_ID",
  "llm_base_url": null,
  "llm_api_key_env": "LLM_API_KEY"
}
```

### Anthropic Messages

```json
{
  "llm_provider": "anthropic",
  "model": "YOUR_CHAT_MODEL_ID",
  "llm_base_url": null,
  "llm_api_key_env": "LLM_API_KEY"
}
```

### Google Gemini generateContent

```json
{
  "llm_provider": "gemini",
  "model": "YOUR_CHAT_MODEL_ID",
  "llm_base_url": null,
  "llm_api_key_env": "LLM_API_KEY"
}
```

Use the model ID itself (for example the identifier shown after `models/` in
Google's catalog), not a whole `...:generateContent` URL.

### OpenAI-compatible cloud, such as OpenRouter

```json
{
  "llm_provider": "openai_compatible",
  "model": "provider/model",
  "llm_base_url": "https://openrouter.ai/api/v1",
  "llm_api_key_env": "LLM_API_KEY"
}
```

Use your selected endpoint's key, not an OpenAI key merely because the protocol
is OpenAI-compatible. Other bases using this same provider setting:

| Endpoint | `llm_base_url` | Model selection |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | Exact chat model ID from your account |
| Groq | `https://api.groq.com/openai/v1` | Exact supported chat model ID |
| Optional Ollama cloud | `https://ollama.com/v1` | Exact cloud model ID available to your account |
| Optional Ollama local, compatible protocol | `http://127.0.0.1:11434/v1` | Exact locally served model tag |
| vLLM, loopback server | `http://127.0.0.1:8000/v1` | Exact served model name, possibly containing slashes |

Ports are examples, not server discovery. Configure the actual port you started.

### Local OpenAI-compatible server, such as LM Studio

```json
{
  "llm_provider": "openai_compatible",
  "model": "YOUR_CHAT_MODEL_ID",
  "llm_base_url": "http://127.0.0.1:1234/v1",
  "llm_api_key_env": "LLM_API_KEY"
}
```

You must install/start the server and load a suitable model yourself. This app
never starts a model server or downloads weights. An unauthenticated loopback
server can run without a key; if your server enables authentication, supply its
key. Remove an unrelated inherited `LLM_API_KEY` or explicitly empty it so it is
not sent to the local server. The same setup pattern applies to vLLM above.

### Optional native Ollama, not a requirement

```json
{
  "llm_provider": "ollama",
  "model": "YOUR_CHAT_MODEL_ID",
  "llm_base_url": "http://127.0.0.1:11434",
  "llm_api_key_env": "LLM_API_KEY"
}
```

This uses `/api/chat`, not `/v1/chat/completions`. The local daemon and model
must already be ready. For Ollama cloud through its compatible API, use
`openai_compatible` with `https://ollama.com/v1` instead.

## Recipient-owned credentials and limits

Keep keys only in this app's root-local `.env` or the **process environment**.
Use a local editor/secure environment-management UI, never chat, shell command
arguments, screenshots or JSON config. The `.env.example` has only a commented
`LLM_API_KEY` placeholder; create your own `.env` and populate it locally.
Never copy a sender's account, Hermes `.env`, profile config or OAuth tokens.

- `llm_api_key_env` defaults to `LLM_API_KEY`. It can instead name
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or another selected
  variable; set only the variable selected in your app config. Naming a variable
  does not select a provider or import a Hermes credential pool.
- The process environment wins. An **explicitly empty** process variable disables
  file fallback; an absent process variable permits the selected file value.
- `credential_env_file` defaults to `.env` under the recipient app root. Set it
  to `null` for environment-only lookup. Do not reference another app's secrets.
- `llm_timeout_seconds` defaults to `180` per model request;
  `llm_max_tokens` defaults to `2048` output tokens. An operation can include one
  repair request, fetching and validation, so this is not an entire-task timeout.
- `max_input_chars` bounds selected source text (default `18000`); a provider's
  context/output limits still apply. `num_ctx` is a native Ollama setting, not a
  universal cloud-model context-window control.
- Restart an intentionally persistent API after editing settings/credentials.
  Ordinary `run.cmd` operations use the task-scoped API and clean it up afterward.

Self-hosted scraping is **not** necessarily fully local inference: selected
source text, prompts and schemas are sent to your chosen endpoint. Cloud use
consumes your own account's quota/billing. No automatic paid fallback, account
switching, server autostart, model download or new subscription is performed.

## Errors, validation and upgrading

- AI disabled/model missing: select the intended provider/model; do not silently
  select a billable default. Ordinary scraping should still be used without AI.
- Missing key/authentication or access denial: check the configured variable name
  locally and your own account's model entitlement, quota and billing. Do not
  paste the key or request headers into diagnostics.
- Endpoint unavailable/timeout: check the exact protocol, base prefix, port and
  whether your independently managed local server is running. Do not remove URL
  safety checks, switch accounts or download models to hide the failure.
- Rejected parameters/model or invalid response: confirm the model supports the
  chosen chat protocol. Do not assume every OpenAI-compatible model is identical.
- Invalid JSON/schema: independent validation allows at most **one repair** and
  then fails. Schema-valid shape/types are not proof of source accuracy; inspect
  warnings, truncation, partial status and important values against source text.

`status`/health is not proof of account entitlement or real extraction. Verify a
small authorized extraction before calling your chosen model setup ready. No
paid/live inference has been certified merely by these configuration examples.

For an existing v0.1.0 installation, preserve your local files privately and
migrate any `ollama_*` settings to the explicit fields above. Do not assume old
chosen settings are compatible or that they should silently become a new paid
configuration. Keep the intended model ID, choose its protocol/base deliberately,
and review credentials separately. Do not publish personal configuration.

## Official protocol references

These sources document endpoint formats, not this application's live access or
a promise that every listed model works. Model placeholders intentionally avoid
pinning a provider's changing catalog.

- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [OpenRouter API overview](https://openrouter.ai/docs/api/reference/overview)
- [DeepSeek first API call](https://api-docs.deepseek.com/)
- [Groq OpenAI compatibility](https://console.groq.com/docs/openai)
- [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/app/api/openai-api)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server)
- [Ollama native chat](https://docs.ollama.com/api/chat)
- [Ollama OpenAI compatibility, cloud and local](https://docs.ollama.com/api/openai-compatibility)

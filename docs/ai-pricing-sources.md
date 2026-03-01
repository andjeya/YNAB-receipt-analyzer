# AI Pricing Sources

Retrieved: **2026-02-28 (UTC)**

## Primary sources

- Gemini Developer API pricing page (official):
  - https://ai.google.dev/gemini-api/docs/pricing
- Gemini model reference pages (official):
  - https://ai.google.dev/gemini-api/docs/models/gemini-3-flash-preview
  - https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash

## Pricing values recorded in `shared/receipt_shared/resources/ai_model_registry.v1.json`

- `gemini-3-flash-preview` (paid tier, per 1M tokens):
  - Input (text/image/video): `$0.50`
  - Output (including thinking tokens): `$3.00`
  - Context caching (text/image/video): `$0.05`
- `gemini-2.5-flash` (paid tier, per 1M tokens):
  - Input (text/image/video): `$0.30`
  - Output (including thinking tokens): `$2.50`
  - Context caching (text/image/video): `$0.03`

## Assumptions documented in registry

- Receipt extraction here is modeled with text/image/video token rates (not audio rates).
- Output rates include thinking tokens when the provider counts them as output tokens.
- Cache storage token-hour pricing is represented in schema for forward compatibility, but not currently metered by the gateway.
- If Google changes pricing, update the registry file and this source note together.

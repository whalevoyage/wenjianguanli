# Privacy

Atlas does not upload file binaries. Scanning, duplicate detection, cleanup, migration, backup and other file management run locally by default. When you use Agent or request an AI explanation, the information needed for that request may be sent to the model provider you configured.

## Where data is stored

| Data | Location |
| --- | --- |
| Settings, task records, quarantine ledger and operation journal | `%LOCALAPPDATA%\Atlas` (or the configured data directory) |
| Quarantined files | `_filepilot_trash` on the source volume |
| Scan and duplicate intermediate results | The same local data directory; temporary results are removed when the task finishes |

The app does not require an account or run a background service. Removing the app does not automatically remove quarantined files stored beside their original volumes.

## When Atlas connects to a network service

Atlas connects to a model service only when you:

1. Send a message that requires the Agent model, or ask the Agent to use model-based understanding.
2. Click an AI explanation control for a cleanup item.

Common Agent requests may be handled by local rules without a model call. Scanning, duplicate detection, file cleanup, migration, backup, search indexing and the operation journal do not upload data by themselves.

## Information that may be sent to the configured provider

Depending on the request, a model call may include:

- Your message and relevant conversation context.
- The selected directory path or paths needed to plan the requested file operation.
- Tool descriptions and parameters used to let the model prepare a plan.
- Result summaries returned by local tools.
- Text snippets from full-text search results when you ask the Agent to search file contents.
- The cleanup-item details needed to explain an item when you request an AI explanation.

Atlas does not send file binaries as part of these requests. Full-text search snippets are an exception to the general rule that file contents stay local: matching text excerpts may be included in the model context. Avoid asking the Agent to search sensitive content if you do not want those excerpts sent to the configured provider.

## API keys and providers

- Atlas supports DeepSeek, OpenAI and a custom OpenAI-compatible endpoint.
- A key entered in settings is protected with Windows DPAPI and is not returned in full by the settings API.
- Environment variables are provider-specific: `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` and `FILEPILOT_LLM_API_KEY`. A key for one provider is not used as a fallback for another provider.
- When a model request is made, the configured provider receives its API key as authentication for that request. Do not use an endpoint you do not trust.
- Each provider's retention and processing practices are governed by that provider's own policies, outside Atlas's control.

## Telemetry and updates

Atlas does not send analytics or crash reports and does not automatically check for updates. Users download updates manually from GitHub Releases.

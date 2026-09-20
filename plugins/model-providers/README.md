# Model Provider Plugins

Each subdirectory is a self-contained provider profile plugin. The
directory layout mirrors `plugins/platforms/`:

```
plugins/model-providers/
├── openrouter/
│   ├── __init__.py      # registers the ProviderProfile
│   └── plugin.yaml      # manifest: name, kind, version, description
├── anthropic/
│   ├── __init__.py
│   └── plugin.yaml
└── ...
```

## How discovery works

`providers/__init__.py._discover_providers()` scans this directory (and
`$HERMES_HOME/plugins/model-providers/`) the first time anything calls
`get_provider_profile()` or `list_providers()`. Each `__init__.py` is
imported and expected to call `providers.register_provider(profile)`.

User plugins at `$HERMES_HOME/plugins/model-providers/<name>/` override
bundled plugins of the same name — last-writer-wins in
`register_provider()`. Drop a file there to replace a built-in.

## Adding a new provider

1. Create `plugins/model-providers/<your_provider>/__init__.py`:

   ```python
   from providers import register_provider
   from providers.base import ProviderProfile

   my_provider = ProviderProfile(
       name="your-provider",
       aliases=("alias1", "alias2"),
       display_name="Your Provider",
       description="One-line description shown in the setup picker",
       signup_url="https://your-provider.example.com/keys",
       env_vars=("YOUR_PROVIDER_API_KEY", "YOUR_PROVIDER_BASE_URL"),
       base_url="https://api.your-provider.example.com/v1",
       default_aux_model="your-cheap-model",
   )

   register_provider(my_provider)
   ```

2. Create `plugins/model-providers/<your_provider>/plugin.yaml`:

   ```yaml
   name: your-provider-profile
   kind: model-provider
   version: 1.0.0
   description: Short sentence about the provider
   author: Your Name
   ```

Nothing else needs to change. `auth.py`, `config.py`, `models.py`,
`doctor.py`, `model_metadata.py`, `runtime_provider.py`, and the
chat_completions transport all auto-wire from the registry.

## Non-trivial profiles

Override the `ProviderProfile` hooks in a subclass for per-provider
quirks — see `plugins/model-providers/openrouter/__init__.py` for
`build_extra_body` and `build_api_kwargs_extras` examples, and
`plugins/model-providers/gemini/__init__.py` for `thinking_config`
translation.

## Файлы

| Файл | Назначение и связи | Версия содержимого |
| --- | --- | --- |
| [README.md](./README.md) | Карта содержимого и правила работы с папкой. | — (самоописание) |

## Подпапки

| Папка | Назначение |
| --- | --- |
| [actual](./actual/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [ai-gateway](./ai-gateway/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [alibaba](./alibaba/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [alibaba-coding-plan](./alibaba-coding-plan/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [anthropic](./anthropic/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [antigravity](./antigravity/README.md) | Подключает локально авторизованную Antigravity CLI как выбираемый subprocess-провайдер Gemini для Hermes. |
| [arcee](./arcee/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [azure-foundry](./azure-foundry/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [bedrock](./bedrock/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [commandcode](./commandcode/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [copilot](./copilot/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [copilot-acp](./copilot-acp/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [custom](./custom/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [deepinfra](./deepinfra/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [deepseek](./deepseek/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [fireworks](./fireworks/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [gemini](./gemini/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [gmi](./gmi/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [huggingface](./huggingface/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [kilocode](./kilocode/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [kimi-coding](./kimi-coding/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [meta-ai](./meta-ai/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [minimax](./minimax/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [nebius-token-factory](./nebius-token-factory/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [nous](./nous/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [novita](./novita/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [nvidia](./nvidia/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [ollama-cloud](./ollama-cloud/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [openai-codex](./openai-codex/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [opencode-zen](./opencode-zen/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [openrouter](./openrouter/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [qwen-oauth](./qwen-oauth/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [router](./router/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [stepfun](./stepfun/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [upstage](./upstage/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [vertex](./vertex/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [xai](./xai/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [xiaomi](./xiaomi/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |
| [zai](./zai/README.md) | Требует ручного описания назначения, существенных входов, выходов и связей. |

## Работа с папкой

Перечень и версии содержимого сверяет `.agents/skills/folder-readme/scripts/sync_folder_readmes.py`.

Последняя полная сверка перечня и версий содержимого: 2026-09-20T23:46:51.236028000Z.

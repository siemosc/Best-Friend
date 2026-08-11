# Модуль artifacts/ — файлы в SeaweedFS (S3), без PG

`core/src/bestfiend/artifacts/`. Артефакт = байты + meta.json в S3-совместимом хранилище (SeaweedFS). PG-registry снесён (миграция 005); source of truth — сам объектный сторадж.

## Раскладка storage

- Ключи: `{user_id}/{artifact_id}/data` + рядом `{user_id}/{artifact_id}/meta.json`.
- **Атомарность записью в два шага: сначала data, потом meta.json как commit-маркер.** Нет meta.json → артефакт «не готов», читатель игнорирует.
- artifact_id = uuid7 (временно-упорядоченные).
- meta.json: filename, type (image|document|table|code|archive|audio|video|binary|other), description (≤500), art_source, user_id, created_at, art_meta (passthrough dict, бизнес-читателя нет).
- s3_storage.py — boto3, sync-вызовы под asyncio.to_thread. Bucket создаётся в ArtifactsRuntime.start() (create_bucket, dev-SeaweedFS принимает любые креды).

## Контракты

ArtifactRef (в `bestfiend/contracts/artifacts.py` — кросс-модульный): artifact_id, artifact_user_name, type, description, storage_key, art_meta. Property `artifact_llm_name` = `{stem}_{id[-6:]}{ext}` — имя для LLM-контекста. ⚠️ storage_key в персистентных местах (история) не хранится — резолвер строит ключ из session user_id, хранёному не доверяет.

## service.py — ArtifactService

- `create(CreateArtifactRequest)` → ArtifactRef — trusted-create (payload_bytes, лимит ARTIFACT_MAX_PAYLOAD_SIZE_MB).
- `create_from_raw(user_id, art_source, filename, payload)` — из сырых байтов, без LLM: type по расширению (infer_artifact_type).
- `read_bytes(storage_key)` — по готовому ключу (runtime-refs: доставка AnswerFinal.attachments).
- `read_bytes_for_user(user_id, artifact_id)` — ключ строится из user_id сессии, хранёному storage_key не доверяем (vision-гидрация истории).

Enrichment (LLM-генерация имени/описания текста) снесён в v29.9.0 вместе с text-path — enrichment.py больше нет.

Потоки: пользовательские файлы входят через telegram (upload → attached_artifacts в InputEvent — `mem:modules/telegram`); агентские — из MCP-результатов (coercion) и delegate_subtask; доставка юзеру — send_artifact_to_user → AnswerFinal.attachments. Дизайн-док docs/artifact_architecture.md — ИСТОРИЧЕСКИЙ (микросервисная эпоха, PG-registry, ContextForge) — деталям не верить, сверяться с кодом.
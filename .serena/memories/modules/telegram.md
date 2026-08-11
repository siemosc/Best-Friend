# Модуль telegram/ — aiogram-бот

`core/src/bestfiend/telegram/`. Единственный chat-ingress. TelegramRuntime держит polling-task (start/stop); бот получает параметр `outbound_source` (порт OutboundEventSource из contracts/events.py) и подписывается через него — реализация in-process StreamPublisher, publisher-сторона порта остаётся у GraphRuntime. После распила 2026-07 `bot.py` — проводка хендлеров + авторизация, механика — в трёх коллабораторах-файлах рядом. `errors.py` — доменные ошибки шлюза (`TelegramGatewayError` и потомки, включая `AuthorizationFailedError`; текущий файл появился при lifecycle-рефакторинге, одноимённый мёртвый файл ранее удалялся — не путать и не сносить как «уже удалённый»). `request_correlation.py` — нормализация пары request_id+user_id на ingress; `allowed_users.py` — парсер ACL.

## Коллабораторы (распил bot.py, v29.9.2)

- **outbound_delivery.py — OutboundDelivery(bot, artifact_delivery)** — вся модель вывода A*; держит per-request стейт (_anchor_messages, _progress_steps, _stream_buffers, _finalized). Public: `dispatch_stream_event` (единая точка входа OutboundEvent), `send_thinking_draft`, `cleanup_request_state`, `draft_id` (static).
- **attachment_ingest.py — AttachmentIngestionService(bot, artifacts, max_size_bytes, on_media_group)** — скачивание вложений и upload в artifacts; `extract_attachment_info` + AttachmentInfo, `download_and_upload_many/one`; media-group debounce внутри, готовая группа отдаётся коллбэком `on_media_group(message, count, group_id, infos)`.
- **artifact_delivery.py — ArtifactDelivery(bot, artifacts)** — `send_attachments`: download по storage_key → альбомы по типу (фото/доки раздельно), чанки ≤10 на sendMediaGroup.

## Хендлеры (bot.py)

- `/start` — приветствие; `/web` — 6-значный binding code (auth_service.generate_binding_code) для привязки web-аккаунта; `/discord` — заглушка.
- Текст/вложения → resolve_or_create_by_telegram + проверка status (active → обработка; pending → ожидание активации; иное → отказ) → publish InputEvent.
- ACL: env `TELEGRAM_ALLOWED_USER_IDS` (settings.py) → `parse_allowed_user_ids` (allowed_users.py). Строковая ветка строгая: опечатка в env кидает ValueError на старте — тихо отброшенный мусор схлопнул бы список в None, а None в проверке доступа означает «пускать всех». Пустой env → None → доступ всем.
- Вложения photo/document/voice/audio/video/video_note → AttachmentIngestionService → ArtifactRef в InputEvent.attached_artifacts. Media group буферизуется по media_group_id с debounce 1.5s (до 10 items) → один InputEvent. Проверка размера дважды: до скачивания (file_size) и после (payload).

## Модель вывода (A*: draft-стриминг + финальный пузырь) — живёт в OutboundDelivery

- **Draft**: нативный send_message_draft, draft_id = hash(request_id)&0x7FFFFFFF|1. Пустой draft сразу при старте («взялся»). AnswerDelta → update draft хвостом контента (последние 4096 символов). Draft эфемерен — гасится только новым send.
- **ProgressStep** → anchor-сообщение с логом прогресса (create при первом, дальше edit).
- **AnswerReset** → сброс буфера и drafts (preface уехал в лог).
- **AnswerFinal** → всегда НОВЫЙ send (не edit anchor): telegramify_content(text) → список Text/Photo/File/Mermaid; прогресс-лог прицепляется свёрнутым expandable-blockquote сверху, если влезает с ответом в 4096, иначе отдельным пузырём; anchor удаляется после финала. attachments (presented_artifacts) → ArtifactDelivery.send_attachments.
- Fail-soft на каждом шаге доставки: сбой draft/anchor/финала → лог, поток не прерывается.

## formatters/

- `format_markdown` / `telegramify_content` — Markdown → entities через telegramify-markdown; таблицы; Mermaid → async-рендер в PNG (Photo); автосплит по 4096 (TELEGRAM_TEXT_LIMIT).
- renderer.py — рендер и разбиение; message_composer.py — MessageComposer (expandable_blockquote + rendered); progress_formatter.py — формат лога шагов.

Гочи:
- Подписка `outbound_source.open(request_id)` открывается ДО `create_task(publish_input_event)` (иначе гонка — publish не найдёт очередь); бот потребляет события собственной публикации: open → thinking-draft → graph task → consume → await graph_task → close. Инвариант живёт в bot.py, не в коллабораторах.
- Media-group flush в attachment_ingest — detached-задача вне BackgroundTaskSupervisor (долг: `mem:backlog`).